import asyncio
import base64
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
from nonebot import logger

from .config import MAX_IMAGE_BYTES, TTS_CACHE_DIR, TTS_CACHE_TTL, TTS_TIMEOUT, feature_enabled
from .incoming_images import normalize_image
from PIL import Image


def clean_speech(text: str) -> str:
    text = re.sub(r"\*.*?\*|\(.*?\)|（.*?）|\[.*?\]", "", text, flags=re.S)
    text = re.sub(r"[~～]", "，", text)
    text = re.sub(r"(?<![A-Za-z])[wW]{2,}(?![A-Za-z])", "", text)
    text = re.sub(r"""[*#_♡❤♥<>"'“”‘’「」]""", "", text)
    return "，".join(line.strip() for line in text.splitlines() if line.strip())


def cleanup_tts_cache():
    if not TTS_CACHE_DIR.exists():
        return
    cutoff = time.time() - TTS_CACHE_TTL
    for path in TTS_CACHE_DIR.glob("tts_*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logger.warning("Unable to remove expired TTS cache file")


async def get_tts_audio(text: str, *, character=None) -> str | None:
    if not feature_enabled("TTS_ENABLED"):
        return None
    text = clean_speech(text)
    if not text:
        return None
    try:
        return await asyncio.wait_for(_tts_audio(text, character), timeout=TTS_TIMEOUT)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError, ImportError) as exc:
        # Only safe codes are logged, never response bodies or credentials.
        code = str(exc) if type(exc) is ValueError and re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
        logger.warning("TTS failed: {}", code)
        return None


async def _tts_audio(text, character):
    backend = os.getenv("TTS_BACKEND", "gpt-sovits").strip().lower()
    if backend == "google-chirp3":
        from .google_tts import synthesize
        data = await synthesize(text, character)
    elif backend == "gpt-sovits":
        data = await _sovits_audio(text)
    else:
        raise ValueError("UNKNOWN_TTS_BACKEND")

    def save():
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_tts_cache()
        path = TTS_CACHE_DIR / f"tts_{uuid.uuid4().hex}.wav"
        path.write_bytes(data)
        return path.resolve().as_uri()
    uri = await asyncio.to_thread(save)
    if os.getenv("TTS_DELIVERY", "file").lower() == "base64":
        return "base64://" + base64.b64encode(data).decode("ascii")
    return uri


async def _sovits_audio(text):
    params = {"text": text, "text_lang": "zh"}
    for key, env, default in (
        ("ref_audio_path", "TTS_REF_AUDIO", ""),
        ("prompt_text", "TTS_PROMPT_TEXT", ""),
        ("prompt_lang", "TTS_PROMPT_LANG", "zh"),
    ):
        if value := os.getenv(env, default):
            params[key] = value
    timeout = aiohttp.ClientTimeout(total=TTS_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.get(os.getenv("TTS_API_URL", "http://127.0.0.1:9880/tts"),
                              params=params) as response:
            response.raise_for_status()
            if "json" in response.headers.get("Content-Type", "").lower():
                raise ValueError("TTS returned JSON instead of audio")
            data = await response.read()
            if not data:
                raise ValueError("TTS returned empty audio")
            return data


async def get_image_base64(url: str) -> str | None:
    try:
        if url.startswith(("https://", "http://")):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
                async with client.get(url) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_IMAGE_BYTES:
                            raise ValueError("Image exceeds configured limit")
                    data = bytes(chunks)
        elif url.startswith("base64://"):
            encoded = url[9:]
            if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError("Image exceeds configured limit")
            data = base64.b64decode(encoded, validate=True)
        else:
            if url.startswith("file://"):
                parsed = urlsplit(url)
                if parsed.netloc and parsed.netloc != "localhost":
                    return None
                url = unquote(parsed.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", url):
                    url = url[1:]
            def read():
                with Path(url).open("rb") as file:
                    return file.read(MAX_IMAGE_BYTES + 1)
            data = await asyncio.to_thread(read)
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        data = await asyncio.to_thread(normalize_image, data)
        return base64.b64encode(data).decode("ascii")
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, EOFError, Image.DecompressionBombError) as exc:
        logger.warning("Image download failed: {}", type(exc).__name__)
        return None
