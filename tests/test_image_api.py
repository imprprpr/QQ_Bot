import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from PIL import Image

from test_bot import comfy
from src.plugins.chat_and_draw import image_api


class ImageApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        buffer = io.BytesIO()
        Image.new("RGB", (3, 3), "blue").save(buffer, format="PNG")
        self.image = buffer.getvalue()
        self.calls = 0
        self.mode = "ok"
        async def handler(request):
            self.calls += 1
            self.payload = await request.json()
            if self.mode == "denied":
                return web.json_response({}, status=403)
            data = self.image if self.mode == "ok" else b"not an image"
            return web.json_response({"data": [{"b64_json": base64.b64encode(data).decode()}]})
        app = web.Application()
        app.router.add_post("/images/generations", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.env = patch.dict(os.environ, {"DRAW_ENABLED": "true", "DRAW_BACKEND": "api",
            "DRAW_CLIENT_FACTORY": "", "DRAW_API_KEY": "offline-fixture", "DRAW_API_MODEL": "test",
            "DRAW_API_BASE_URL": f"http://127.0.0.1:{port}"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        image_api._adapters.clear()
        await self.runner.cleanup()

    async def test_api_backend_never_submits_comfy_workflow(self):
        with patch.object(comfy, "_generate", AsyncMock()) as old:
            self.assertEqual(await comfy.generate_image("blue sky"), self.image)
        old.assert_not_awaited()
        self.assertEqual(self.payload["n"], 1)
        self.assertIn('blue sky', self.payload['prompt'])
        self.assertIn('Japanese lifestyle portrait photography', self.payload['prompt'])

    async def test_bad_image_is_rejected(self):
        self.mode = "bad"
        self.assertIsNone(await comfy.generate_image("test"))

    async def test_permission_error_does_not_repeat_paid_generation(self):
        self.mode = "denied"
        self.assertIsNone(await comfy.generate_image("test"))
        self.assertEqual(self.calls, 1)

    async def test_trusted_adapter_returns_validated_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "image.py"
            file.write_text(f"async def generate_image(prompt):\n    return {self.image!r}\n")
            with patch.dict(os.environ, {"DRAW_CLIENT_FACTORY": str(file)}):
                self.assertEqual(await comfy.generate_image("test"), self.image)
        self.assertEqual(self.calls, 0)

    async def test_missing_adapter_fails_closed(self):
        with patch.dict(os.environ, {"DRAW_CLIENT_FACTORY": "missing-image-adapter.py"}):
            self.assertIsNone(await comfy.generate_image("test"))
        self.assertEqual(self.calls, 0)

    async def test_disabled_feature_does_not_load_adapter(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false", "DRAW_CLIENT_FACTORY": "missing.py"}):
            self.assertIsNone(await comfy.generate_image("test"))
        self.assertEqual(self.calls, 0)
