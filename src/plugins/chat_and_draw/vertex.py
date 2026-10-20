"""Vertex AI generateContent with ADC. No API keys or exported access tokens."""
import asyncio
import os
import re
import threading
import time
from urllib.parse import urlsplit

import aiohttp
import google.auth
from google.auth.transport.requests import Request
from nonebot import logger

from .config import LLM_TIMEOUT, VERTEX_HISTORY_IMAGES
from .image_history import load_image
from .runtime import record


class VertexError(RuntimeError):
    """Contains only a safe diagnostic code, not response bodies or credentials."""

def proxy_for_url(url):
    if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}:
        return None
    return os.getenv("VERTEX_PROXY") or None


class ADCAuth:
    def __init__(self):
        self.credentials = None
        self.project = None
        # The refresh happens in worker threads, including during task cancellation.
        self.lock = threading.Lock()

    def headers(self, force=False):
        with self.lock:
            if self.credentials is None:
                self.credentials, self.project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if force or not self.credentials.valid:
                request = Request()
                proxy = os.getenv("VERTEX_PROXY")
                if proxy:
                    request.session.trust_env = False
                    request.session.proxies.update({"http": proxy, "https": proxy})
                def bounded_request(*args, **kwargs):
                    kwargs["timeout"] = min(float(kwargs.get("timeout") or 10), 10)
                    return request(*args, **kwargs)
                try:
                    self.credentials.refresh(bounded_request)
                finally:
                    request.session.close()
            headers = {}
            self.credentials.apply(headers)
            return headers, self.project


auth = ADCAuth()


def endpoint(project: str) -> str:
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip()
    model = os.getenv("VERTEX_MODEL", "gemini-3.8-flash").strip()
    for name, value in (("project", project), ("location", location), ("model", model)):
        if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise VertexError(f"INVALID_{name.upper()}_CONFIG")
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"


def build_payload(system_prompt: str, history: list[dict]) -> dict:
    # Select the most recent attachments without allowing older images to consume
    # the request's entire image budget.
    chosen = set()
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") != "user":
            continue
        images = history[index].get("images", [])
        for image_index in range(len(images) - 1, -1, -1):
            if len(chosen) < VERTEX_HISTORY_IMAGES:
                chosen.add((index, image_index))
    contents = []
    for index, message in enumerate(history):
        role = "model" if message["role"] == "assistant" else "user"
        parts = []
        text = str(message.get("content", "")).strip()
        if text:
            parts.append({"text": text})
        for image_index, identity in enumerate(message.get("images", [])):
            if (index, image_index) in chosen:
                blob = load_image(identity)
                parts.append({"inlineData": blob} if blob else {"text": "[历史图片已过期或不可用，请用户重新发送]"})
            else:
                parts.append({"text": "[较早的图片未附带；若需要查看细节，请用户重新发送]"})
        if not parts:
            continue
        # Merge consecutive identical roles after reset, delivery failure, or trimming.
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    if not contents or contents[-1]["role"] != "user":
        raise VertexError("HISTORY_MUST_END_WITH_USER")
    level = os.getenv("VERTEX_THINKING_LEVEL", "LOW").upper()
    if level not in {"LOW", "MEDIUM", "HIGH"}:
        raise VertexError("INVALID_THINKING_LEVEL")
    max_tokens = int(os.getenv("VERTEX_MAX_OUTPUT_TOKENS", "2048"))
    if not 1 <= max_tokens <= 65536:
        raise VertexError("INVALID_OUTPUT_TOKEN_LIMIT")
    return {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingLevel": level},
        },
    }


def response_text(data: dict) -> str:
    if data.get("promptFeedback", {}).get("blockReason"):
        raise VertexError("PROMPT_BLOCKED")
    candidates = data.get("candidates", [])
    if not candidates:
        raise VertexError("EMPTY_CANDIDATES")
    candidate = candidates[0]
    # Do not execute incomplete action tags or return partially blocked content.
    if candidate.get("finishReason") not in {None, "STOP"}:
        raise VertexError("INCOMPLETE_OR_BLOCKED_RESPONSE")
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise VertexError("EMPTY_TEXT")
    return text


async def _generate(system_prompt, history):
    started = time.monotonic()
    payload = await asyncio.to_thread(build_payload, system_prompt, history)
    headers, default_project = await asyncio.to_thread(auth.headers)
    logger.info("Vertex authentication ready: elapsed={:.1f}s", time.monotonic() - started)
    await record("authentication", "completed", elapsed=round(time.monotonic() - started, 3))
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or default_project
    url = endpoint(project)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT)) as client:
        for attempt in range(2):
            logger.info("Vertex model request submitted: attempt={}", attempt + 1)
            async with client.post(url, json=payload, headers=headers, proxy=proxy_for_url(url)) as response:
                if response.status == 401 and attempt == 0:
                    headers, _ = await asyncio.to_thread(auth.headers, True)
                    continue
                if response.status != 200:
                    raise VertexError(f"HTTP_{response.status}")
                data = await response.json()
                usage = data.get("usageMetadata") or {}
                await record("usage", "completed", provider="vertex", model=os.getenv("VERTEX_MODEL", "gemini-3.8-flash"),
                             input_tokens=usage.get("promptTokenCount", 0), output_tokens=usage.get("candidatesTokenCount", 0),
                             thought_tokens=usage.get("thoughtsTokenCount", 0), cached_tokens=usage.get("cachedContentTokenCount", 0),
                             total_tokens=usage.get("totalTokenCount", 0))
                return response_text(data)
    raise VertexError("AUTHENTICATION_FAILED")


async def generate(system_prompt: str, history: list[dict]) -> str:
    return await asyncio.wait_for(_generate(system_prompt, history), timeout=LLM_TIMEOUT)
