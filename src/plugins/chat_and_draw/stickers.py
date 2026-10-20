"""Categorized anime reactions from nekos.best; no conversation text is uploaded."""
import asyncio
from collections import OrderedDict
import io
import os
import time
from urllib.parse import urlsplit

import aiohttp
from nonebot import logger
from PIL import Image

API_URL = "https://nekos.best/api/v2"
MAX_BYTES = 5 * 1024 * 1024
CACHE_BYTES = 16 * 1024 * 1024
TIMEOUT = 8
_cache = OrderedDict()
_endpoints = None
_endpoint_time = 0

CATEGORIES = {
    "angry", "blush", "bored", "clap", "confused", "cry", "cuddle", "dance",
    "facepalm", "happy", "highfive", "hug", "laugh", "nod", "nope", "pat",
    "pout", "shrug", "sip", "sleep", "smile", "smug", "stare", "think",
    "thumbsup", "wave", "wink", "yawn",
}
ALIASES = {
    "shy": "blush", "害羞": "blush", "开心": "happy", "大哭": "cry", "哭": "cry",
    "生气": "angry", "思考": "think", "得意": "smug", "笑哭": "laugh",
    "捂脸": "facepalm", "抱抱": "hug", "摸头": "pat", "晚安": "sleep",
    "打招呼": "wave", "疑惑": "confused", "无奈": "shrug",
}


def normalize_category(value):
    key = str(value).strip().lower()
    key = ALIASES.get(key, key)
    return key if key in CATEGORIES else None


def sticker_instructions():
    return """
【二次元情景表情包】
你可以在回复末尾用一个 <sticker: 类别> 发送与对话情景一致的二次元动图。
仅在有自然情绪或互动需要时使用，不要每句都发；默认最多每 30 秒一张，用户明确要求时可发送。
根据双方前文和角色当下情绪选择：被夸可 blush；开心 happy/smile；得意 smug；
委屈 cry/pout；尴尬 facepalm；疑惑 confused/think；安慰或拥抱 hug/cuddle；
摸头 pat；笑 laugh；问候 wave；晚安 sleep/yawn；赞同 nod/thumbsup；无奈 shrug。
用户难过时应安慰，不能发嘲笑或庆祝的动图；用户要求不发表情包时不输出此标签。
类别仅限：angry, blush, bored, clap, confused, cry, cuddle, dance, facepalm, happy,
highfive, hug, laugh, nod, nope, pat, pout, shrug, sip, sleep, smile, smug, stare,
think, thumbsup, wave, wink, yawn。
表情包与 ComfyUI 绘图独立：表情包使用 sticker 标签，自拍/插画使用 draw 标签。
只输出类别，不要编造表情包网址，不要把解释或标签本身发给用户。
"""


def _cached(category):
    item = _cache.get(category)
    if item and time.monotonic() - item[0] < 3600:
        _cache.move_to_end(category)
        return item[1]
    return None


def _remember(category, data):
    _cache[category] = (time.monotonic(), data)
    _cache.move_to_end(category)
    while sum(len(value[1]) for value in _cache.values()) > CACHE_BYTES:
        _cache.popitem(last=False)


def _proxy(url):
    if urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}:
        return None
    return os.getenv("STICKER_PROXY") or os.getenv("VERTEX_PROXY") or None


def _validate_image(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"GIF", "PNG", "JPEG", "WEBP"}:
            raise ValueError("Unsupported sticker format")
        image.verify()


async def _fetch(category, user_agent):
    global _endpoints, _endpoint_time
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                     headers={"User-Agent": user_agent}) as client:
        if _endpoints is None or time.monotonic() - _endpoint_time > 1800:
            async with client.get(API_URL + "/endpoints", proxy=_proxy(API_URL), allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("Sticker category discovery failed")
                _endpoints = await response.json()
                if not isinstance(_endpoints, dict):
                    _endpoints = None
                    raise ValueError("Invalid sticker category response")
                _endpoint_time = time.monotonic()
        if category not in _endpoints:
            return None
        async with client.get(API_URL + "/" + category, proxy=_proxy(API_URL), allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Sticker API unavailable")
            metadata = await response.json()
        url = metadata["results"][0]["url"]
        parsed, base = urlsplit(url), urlsplit(API_URL)
        # Only fetch assets belonging to this API, never arbitrary model-generated URLs.
        if parsed.scheme != base.scheme or parsed.netloc != base.netloc or not parsed.path.startswith(base.path + "/" + category + "/"):
            raise ValueError("Unexpected sticker asset URL")
        async with client.get(url, proxy=_proxy(url), allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Sticker download failed")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("Sticker exceeds size limit")
        data = bytes(data)
        await asyncio.to_thread(_validate_image, data)
        _remember(category, data)
        return data


async def get_sticker(category, *, character=None, context=""):
    category = normalize_category(category)
    if category is None:
        return None
    from .sticker_library import library
    try:
        local = await asyncio.to_thread(library.select, category, character, context)
        if local:
            return local
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Local sticker library unavailable: {}", type(exc).__name__)
    if os.getenv("STICKER_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return None
    user_agent = os.getenv("STICKER_USER_AGENT", "").strip()
    if not user_agent:
        logger.warning("STICKER_USER_AGENT requires application name and contact information")
        return None
    try:
        return await asyncio.wait_for(_fetch(category, user_agent), timeout=TIMEOUT)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Anime sticker unavailable: category={}, error={}", category, type(exc).__name__)
        return _cached(category)
