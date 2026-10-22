"""Chirp 3 HD REST adapter. Credentials belong to deployment configuration."""
import asyncio
import base64
import importlib.util
import json
import math
import os
import re
from pathlib import Path

import aiohttp

from .config import ROOT, TTS_TIMEOUT

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
_providers = {}


def voice_settings(character=None):
    settings = {
        "name": os.getenv("GOOGLE_TTS_VOICE", "cmn-CN-Chirp3-HD-Kore"),
        "speaking_rate": float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1")),
        "volume_gain_db": float(os.getenv("GOOGLE_TTS_VOLUME_GAIN_DB", "0")),
    }
    path = Path(os.getenv("TTS_VOICES_FILE", "data/tts_voices.json"))
    if not path.is_absolute():
        path = ROOT / path
    if path.exists():
        profiles = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(profiles, dict):
            raise ValueError("INVALID_TTS_VOICES_FILE")
        profile = profiles.get(character, {}) if character else {}
        if not isinstance(profile, dict):
            raise ValueError("INVALID_TTS_VOICE_PROFILE")
        settings.update({key: profile[key] for key in settings if key in profile})
    if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}-Chirp3-HD-[A-Za-z]+", settings["name"]):
        raise ValueError("INVALID_CHIRP_VOICE")
    for key, low, high in (("speaking_rate", .25, 2), ("volume_gain_db", -96, 16)):
        value = float(settings[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("INVALID_TTS_VOICE_CONTROL")
        settings[key] = value
    return settings


async def request_headers(force=False):
    filename = os.getenv("TTS_HEADERS_FACTORY", "").strip()
    if filename:
        path = Path(filename)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if path not in _providers:
            spec = importlib.util.spec_from_file_location("bot_private_tts_headers", path)
            if spec is None or spec.loader is None:
                raise ValueError("INVALID_TTS_HEADERS_FACTORY")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _providers[path] = module.headers
        try:
            return await asyncio.to_thread(_providers[path], force=force)
        except Exception:
            raise ValueError("TTS_AUTH_FAILED") from None
    key = os.getenv("GOOGLE_TTS_API_KEY", "").strip()
    if not key:
        raise ValueError("MISSING_GOOGLE_TTS_API_KEY")
    return {"x-goog-api-key": key}


async def synthesize(text, character=None):
    # Google's synchronous input limit is 5,000 UTF-8 bytes. Leave headroom.
    if len(text.encode("utf-8")) > 4500:
        raise ValueError("TTS_TEXT_TOO_LONG")
    voice = voice_settings(character)
    language = voice["name"].split("-Chirp3-HD-", 1)[0]
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language, "name": voice["name"]},
        "audioConfig": {"audioEncoding": "LINEAR16",
                        "speakingRate": voice["speaking_rate"],
                        "volumeGainDb": voice["volume_gain_db"]},
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TTS_TIMEOUT)) as client:
        for attempt in range(2):
            headers = await request_headers(force=bool(attempt))
            async with client.post(ENDPOINT, json=payload, headers=headers,
                                   proxy=os.getenv("TTS_PROXY") or None) as response:
                if response.status == 401 and not attempt and os.getenv("TTS_HEADERS_FACTORY"):
                    continue
                if response.status != 200:
                    # Do not include credentials, request text or response bodies in logs.
                    raise ValueError(f"GOOGLE_TTS_HTTP_{response.status}")
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_AUDIO_BYTES * 4 // 3 + 65536:
                        raise ValueError("TTS_AUDIO_TOO_LARGE")
                result = json.loads(chunks)
                audio = base64.b64decode(result.get("audioContent", ""), validate=True)
                if not audio or len(audio) > MAX_AUDIO_BYTES or not audio.startswith(b"RIFF"):
                    raise ValueError("INVALID_GOOGLE_TTS_AUDIO")
                return audio
    raise ValueError("GOOGLE_TTS_AUTH_FAILED")
