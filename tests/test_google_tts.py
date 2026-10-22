"""Offline Chirp REST, role selection and container delivery regressions."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from test_bot import media, plugin
from src.plugins.chat_and_draw import google_tts


class ChirpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audio = b"RIFF" + b"test-wave"
        self.mode = "ok"
        self.calls = []

        async def synthesize(request):
            self.calls.append(await request.json())
            if self.mode == "denied":
                return web.json_response({"error": "private diagnostic"}, status=403)
            if self.mode == "refresh" and len(self.calls) == 1:
                return web.Response(status=401)
            if self.mode == "bad":
                return web.json_response({"audioContent": "invalid!"})
            return web.json_response({"audioContent": base64.b64encode(self.audio).decode()})

        app = web.Application()
        app.router.add_post("/tts", synthesize)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.patches = [
            patch.dict(os.environ, {"TTS_BACKEND": "google-chirp3", "TTS_ENABLED": "true",
                       "TTS_DELIVERY": "base64", "GOOGLE_TTS_API_KEY": "offline-fixture",
                       "TTS_HEADERS_FACTORY": "", "TTS_PROXY": "",
                       "GOOGLE_TTS_VOICE": "cmn-CN-Chirp3-HD-Kore",
                       "GOOGLE_TTS_SPEAKING_RATE": "1", "GOOGLE_TTS_VOLUME_GAIN_DB": "0",
                       "TTS_VOICES_FILE": str(Path(self.temp.name) / "voices.json")}),
            patch.object(google_tts, "ENDPOINT", f"http://127.0.0.1:{port}/tts"),
            patch.object(media, "TTS_CACHE_DIR", Path(self.temp.name)),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_role_voice_and_audio_round_trip(self):
        Path(os.environ["TTS_VOICES_FILE"]).write_text(json.dumps({"ling": {
            "name": "cmn-CN-Chirp3-HD-Leda", "speaking_rate": 1.1}}))
        result = await media.get_tts_audio("你好（挥手）", character="ling")
        self.assertEqual(base64.b64decode(result.removeprefix("base64://")), self.audio)
        self.assertEqual(self.calls[0]["input"], {"text": "你好"})
        self.assertEqual(self.calls[0]["voice"]["name"], "cmn-CN-Chirp3-HD-Leda")
        self.assertEqual(self.calls[0]["audioConfig"]["speakingRate"], 1.1)
        self.assertEqual(next(Path(self.temp.name).glob("tts_*.wav")).read_bytes(), self.audio)

    async def test_unknown_role_uses_default(self):
        await media.get_tts_audio("hello", character="unknown")
        self.assertEqual(self.calls[0]["voice"]["name"], "cmn-CN-Chirp3-HD-Kore")

    async def test_service_denied_falls_back(self):
        self.mode = "denied"
        self.assertIsNone(await media.get_tts_audio("hello"))
        self.assertEqual(list(Path(self.temp.name).glob("tts_*.wav")), [])

    async def test_invalid_audio_is_not_cached(self):
        self.mode = "bad"
        self.assertIsNone(await media.get_tts_audio("hello"))
        self.assertEqual(list(Path(self.temp.name).glob("tts_*.wav")), [])

    async def test_invalid_controls_do_not_contact_service(self):
        with patch.dict(os.environ, {"GOOGLE_TTS_SPEAKING_RATE": "nan"}):
            self.assertIsNone(await media.get_tts_audio("hello"))
        self.assertFalse(self.calls)

    async def test_oversized_input_does_not_contact_service(self):
        self.assertIsNone(await media.get_tts_audio("中" * 1501))
        self.assertFalse(self.calls)

    async def test_private_header_refresh_once(self):
        self.mode = "refresh"
        headers = AsyncMock(return_value={"Authorization": "Bearer offline-fixture"})
        with patch.dict(os.environ, {"TTS_HEADERS_FACTORY": "configured-private-adapter"}), \
             patch.object(google_tts, "request_headers", headers):
            self.assertIsNotNone(await media.get_tts_audio("hello"))
        self.assertEqual(headers.await_count, 2)
        self.assertEqual(headers.await_args.kwargs, {"force": True})

    async def test_delivery_passes_current_character(self):
        with patch.object(plugin, "get_tts_audio", AsyncMock(return_value=None)) as tts, \
             patch.object(plugin, "try_send", AsyncMock(return_value=True)):
            result = await plugin.send_reply("hello", None, None, True, character="ling")
        tts.assert_awaited_once_with("hello", character="ling")
        self.assertTrue(result.text_sent)
