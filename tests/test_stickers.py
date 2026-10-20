import importlib
import io
import os
import unittest
from unittest.mock import patch

from aiohttp import web
from PIL import Image

stickers = importlib.import_module("src.plugins.chat_and_draw.stickers")


class StickerHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "normal"
        self.categories = []
        self.headers = []
        data = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(data, format="GIF")
        self.image = data.getvalue()
        app = web.Application()
        app.router.add_get("/api/v2/endpoints", self.endpoints)
        app.router.add_get("/api/v2/{category}", self.category)
        app.router.add_get("/api/v2/{category}/asset.gif", self.asset)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/v2"
        self.patches = [patch.object(stickers, "API_URL", self.url),
                        patch.object(stickers, "_endpoints", None),
                        patch.dict(stickers._cache, {}, clear=True),
                        patch.dict(os.environ, {"STICKER_ENABLED": "true", "STICKER_USER_AGENT": "AnimaQQTestSuite (local integration test)"})]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.runner.cleanup()

    async def endpoints(self, request):
        return web.json_response({"happy": {"format": "gif"}, "blush": {"format": "gif"}})

    async def category(self, request):
        self.categories.append(request.match_info["category"])
        self.headers.append(request.headers.get("User-Agent"))
        if self.mode == "rate_limited":
            return web.json_response({}, status=429)
        url = self.url + "/" + request.match_info["category"] + "/asset.gif"
        if self.mode == "bad_url":
            url = "http://169.254.169.254/metadata"
        return web.json_response({"results": [{"url": url, "anime_name": "test"}]})

    async def asset(self, request):
        return web.Response(body=b"not an image" if self.mode == "invalid" else self.image, content_type="image/gif")

    async def test_category_and_validated_gif(self):
        self.assertEqual(await stickers.get_sticker("害羞"), self.image)
        self.assertEqual(self.categories, ["blush"])
        self.assertEqual(self.headers, [os.environ["STICKER_USER_AGENT"]])

    async def test_rate_limit_uses_cached_reaction(self):
        await stickers.get_sticker("happy")
        self.mode = "rate_limited"
        self.assertEqual(await stickers.get_sticker("happy"), self.image)

    async def test_unknown_category_makes_no_request(self):
        self.assertIsNone(await stickers.get_sticker("../anything"))
        self.assertEqual(self.categories, [])

    async def test_missing_category_from_provider_returns_none(self):
        self.assertIsNone(await stickers.get_sticker("hug"))
        self.assertEqual(self.categories, [])

    async def test_unexpected_asset_host_is_rejected(self):
        self.mode = "bad_url"
        self.assertIsNone(await stickers.get_sticker("happy"))

    async def test_non_image_payload_is_rejected(self):
        self.mode = "invalid"
        self.assertIsNone(await stickers.get_sticker("happy"))

    async def test_download_size_is_bounded(self):
        with patch.object(stickers, "MAX_BYTES", 1):
            self.assertIsNone(await stickers.get_sticker("happy"))

    async def test_disabled_provider_makes_no_request(self):
        with patch.dict(os.environ, {"STICKER_ENABLED": "false"}):
            self.assertIsNone(await stickers.get_sticker("happy"))
        self.assertEqual(self.categories, [])
