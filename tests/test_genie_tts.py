"""No real voice models are loaded; exercise the local HTTP bridge."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from aiohttp import web
from test_bot import media


class GenieTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(32000)
            audio.writeframes(b"\0\0" * 100)
        self.audio = buffer.getvalue()
        self.calls = []
        self.mode = "ok"

        async def handler(request):
            self.calls.append(await request.json())
            if self.mode == "busy":
                return web.Response(status=429)
            if self.mode == "bad":
                return web.json_response({"error": "bad audio"})
            return web.Response(body=self.audio, content_type="audio/wav")

        app = web.Application()
        app.router.add_post("/tts", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.path = Path(self.temp.name) / "voices.json"
        self.patches = [patch.dict(os.environ, {
            "TTS_BACKEND": "genie", "TTS_DELIVERY": "base64", "TTS_ENABLED": "true",
            "GENIE_CHARACTER": "dania", "TTS_CHARACTERS_FILE": str(self.path),
            "TTS_API_URL": f"http://127.0.0.1:{port}/tts"}),
            patch.object(media, "TTS_CACHE_DIR", Path(self.temp.name))]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_role_mapping_and_container_audio(self):
        self.path.write_text(json.dumps({"ling": "custom"}))
        result = await media.get_tts_audio("你好（挥手）", character="ling")
        self.assertEqual(self.calls, [{"text": "你好", "character": "custom"}])
        self.assertEqual(base64.b64decode(result[9:]), self.audio)

    async def test_unmapped_role_uses_default(self):
        self.assertIsNotNone(await media.get_tts_audio("hello", character="other"))
        self.assertEqual(self.calls[0]["character"], "dania")

    async def test_busy_returns_text_fallback_signal(self):
        self.mode = "busy"
        self.assertIsNone(await media.get_tts_audio("hello"))

    async def test_error_body_is_not_cached_as_audio(self):
        self.mode = "bad"
        self.assertIsNone(await media.get_tts_audio("hello"))
        self.assertFalse(list(Path(self.temp.name).glob("tts_*.wav")))

    async def test_bad_voice_config_does_not_contact_service(self):
        self.path.write_text(json.dumps({"ling": "../other"}))
        self.assertIsNone(await media.get_tts_audio("hello", character="ling"))
        self.assertFalse(self.calls)
