"""Vertex integration tested against a loopback server with fake ADC credentials."""
import asyncio
import base64
import importlib
import io
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from aiohttp import web
from PIL import Image

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(log_level="INFO")

plugin = importlib.import_module("src.plugins.chat_and_draw")
vertex = importlib.import_module("src.plugins.chat_and_draw.vertex")
images = importlib.import_module("src.plugins.chat_and_draw.image_history")
llm = importlib.import_module("src.plugins.chat_and_draw.llm")
from src.plugins.chat_and_draw.sessions import Session, SessionStore
from google.oauth2.credentials import Credentials
from nonebot.adapters.onebot.v11 import Message, MessageSegment


def png():
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), "red").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


class ImageHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_survives_database_reload(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp) / "images"):
            identity = images.save_image(png())
            store = SessionStore(Path(temp) / "session.db")
            await store.save(Session("test", history=[{"role": "user", "content": "看这张图", "images": [identity]}]))
            restored = await SessionStore(store.path).load("test")
            self.assertEqual(restored.history[0]["images"], [identity])
            self.assertNotIn(png(), store.path.read_bytes().decode(errors="ignore"))
            payload = vertex.build_payload("persona", restored.history + [{"role": "user", "content": "刚才那张呢"}])
            self.assertEqual(payload["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/png")

    async def test_cache_expiry_and_path_restriction(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp)):
            identity = images.save_image(png())
            file = Path(temp) / f"{identity}.img"
            os.utime(file, (1, 1))
            self.assertIsNone(images.load_image(identity))
            self.assertIsNone(images.load_image("../secret"))
            images.cleanup_images()
            self.assertFalse(file.exists())

    async def test_direct_images_bypass_glm(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(images, "IMAGE_CACHE_DIR", Path(temp) / "images"), \
             patch.object(plugin, "store", SessionStore(Path(temp) / "sessions.db")), \
             patch.dict(os.environ, {"LLM_PROVIDER": "vertex"}), \
             patch.object(plugin, "get_image_description", AsyncMock()) as glm, \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("看到了", None, None, False))) as chat, \
             patch.object(plugin, "send_reply", AsyncMock()):
            event = SimpleNamespace(
                group_id=None, get_user_id=lambda: "42",
                get_message=lambda: Message([MessageSegment.image("base64://" + png())]),
            )
            await plugin.handle_message(SimpleNamespace(self_id="bot"), event)
            glm.assert_not_awaited()
            history = chat.call_args.args[0]
            self.assertEqual(len(history[0]["images"]), 1)
            self.assertIsNotNone(images.load_image(history[0]["images"][0]))


class VertexPayloadTests(unittest.TestCase):
    def test_proxy_applies_to_google_but_not_loopback(self):
        with patch.dict(os.environ, {"VERTEX_PROXY": "http://127.0.0.1:10808"}):
            self.assertEqual(vertex.proxy_for_url("https://aiplatform.googleapis.com"),
                             "http://127.0.0.1:10808")
            self.assertIsNone(vertex.proxy_for_url("http://127.0.0.1:1234"))

    def test_endpoint_uses_global_and_real_model_id(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_LOCATION": "global", "VERTEX_MODEL": "gemini-3.8-flash"}):
            self.assertEqual(vertex.endpoint("project-test"),
                "https://aiplatform.googleapis.com/v1/projects/project-test/locations/global/publishers/google/models/gemini-3.8-flash:generateContent")
        with self.assertRaises(vertex.VertexError):
            vertex.endpoint("../escape")

    def test_no_deprecated_sampling_parameters(self):
        body = vertex.build_payload("角色设定", [{"role": "user", "content": "你好"}])
        self.assertEqual(body["systemInstruction"]["parts"], [{"text": "角色设定"}])
        self.assertEqual(set(body["generationConfig"]), {"maxOutputTokens", "thinkingConfig"})

    def test_recent_image_budget_and_expired_placeholder(self):
        history = [{"role": "user", "content": "old", "images": ["old"]},
                   {"role": "assistant", "content": "okay"},
                   {"role": "user", "content": "new", "images": ["new"]}]
        with patch.object(vertex, "VERTEX_HISTORY_IMAGES", 1), patch.object(vertex, "load_image", return_value=None) as load:
            body = vertex.build_payload("persona", history)
        load.assert_called_once_with("new")
        self.assertIn("历史图片已过期", body["contents"][-1]["parts"][1]["text"])

    def test_model_history_cannot_end_in_model_turn(self):
        with self.assertRaises(vertex.VertexError):
            vertex.build_payload("persona", [{"role": "assistant", "content": "hi"}])

    def test_safety_block_and_truncated_actions_are_not_executed(self):
        for reason in ["SAFETY", "MAX_TOKENS", "RECITATION"]:
            with self.assertRaises(vertex.VertexError):
                vertex.response_text({"candidates": [{"finishReason": reason,
                    "content": {"parts": [{"text": "<draw: incomplete"}]}}]})
        with self.assertRaises(vertex.VertexError):
            vertex.response_text({"promptFeedback": {"blockReason": "SAFETY"}})

    def test_thought_text_is_not_sent_to_user(self):
        self.assertEqual(vertex.response_text({"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "hidden", "thought": True}, {"text": "hello"}]}}]}), "hello")


class ADCTests(unittest.TestCase):
    def test_refresh_and_quota_headers(self):
        credentials = Credentials(token="expired", expiry=datetime.utcnow() - timedelta(hours=1),
                                  quota_project_id="quota-project")
        def refresh(request):
            credentials.token = "fake-fresh-token"
            credentials.expiry = datetime.utcnow() + timedelta(hours=1)
        credentials.refresh = Mock(side_effect=refresh)
        auth = vertex.ADCAuth()
        with patch.object(vertex.google.auth, "default", return_value=(credentials, "adc-project")) as default:
            headers, project = auth.headers()
            self.assertEqual(headers["authorization"], "Bearer fake-fresh-token")
            self.assertEqual(headers["x-goog-user-project"], "quota-project")
            self.assertEqual(project, "adc-project")
            auth.headers()
            credentials.refresh.assert_called_once()
            default.assert_called_once()
            auth.headers(force=True)
            self.assertEqual(credentials.refresh.call_count, 2)


class VertexHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.statuses = [200]
        self.requests = []
        self.app = web.Application()
        self.app.router.add_post("/generate", self.handler)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/generate"
        self.auth = SimpleNamespace(headers=Mock(return_value=({"Authorization": "Bearer fake"}, "test-project")))
        self.patches = [
            patch.object(vertex, "auth", self.auth),
            patch.object(vertex, "endpoint", return_value=self.url),
            patch.dict(os.environ, {"LLM_PROVIDER": "vertex"}),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.runner.cleanup()

    async def handler(self, request):
        self.requests.append((dict(request.headers), await request.json()))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status != 200:
            return web.json_response({"error": "do not log private server details"}, status=status)
        return web.json_response({"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5,
                                                   "thoughtsTokenCount": 2, "totalTokenCount": 19},
                                  "candidates": [{"finishReason": "STOP", "content": {
            "role": "model", "parts": [{"text": "hello<sticker: happy><voice>"}]}}]})

    async def test_role_memory_and_provider_usage_are_recorded(self):
        import json
        from src.plugins.chat_and_draw.runtime import RuntimeStore, operation
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeStore(Path(temp) / "runs.db")
            async with operation(runtime, "chat", "ling", "hello") as scope:
                await llm.get_chat_response([{"role": "user", "content": "hello"}],
                                            memory_context={"memories": ["喜欢海边"], "summary": "穿白裙"})
            events = (await runtime.run_detail(scope.run_id))["events"]
            usage = json.loads(next(e["detail"] for e in events if e["stage"] == "usage"))
            self.assertEqual(usage["total_tokens"], 19)
            self.assertEqual(usage["thought_tokens"], 2)
            prompt = self.requests[0][1]["systemInstruction"]["parts"][0]["text"]
            self.assertIn("喜欢海边", prompt)
            self.assertIn("穿白裙", prompt)

    async def test_unified_text_image_request(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp)), \
             patch.object(llm, "get_client", side_effect=AssertionError("OpenAI backend must not be called")):
            identity = images.save_image(png())
            reply = await llm.get_chat_response([{"role": "user", "content": "看看", "images": [identity]}])
        self.assertEqual(reply, ("hello", None, "happy", True))
        self.assertEqual(len(self.requests), 1)
        headers, body = self.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer fake")
        self.assertEqual(body["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/png")
        self.assertIn("角色", body["systemInstruction"]["parts"][0]["text"])

    async def test_401_refreshes_once(self):
        self.statuses = [401, 200]
        self.assertTrue(await vertex.generate("persona", [{"role": "user", "content": "hi"}]))
        self.assertEqual(self.auth.headers.call_count, 2)
        self.assertEqual(self.auth.headers.call_args.args, (True,))
        self.assertEqual(len(self.requests), 2)

    async def test_persistent_401_stops_after_one_retry(self):
        self.statuses = [401]
        with self.assertRaisesRegex(vertex.VertexError, "HTTP_401"):
            await vertex.generate("persona", [{"role": "user", "content": "hi"}])
        self.assertEqual(len(self.requests), 2)

    async def test_permission_denied_does_not_retry_or_leak_body(self):
        self.statuses = [403]
        with self.assertRaisesRegex(vertex.VertexError, "^HTTP_403$"):
            await vertex.generate("persona", [{"role": "user", "content": "hi"}])
        self.assertEqual(len(self.requests), 1)

    async def test_overall_deadline(self):
        async def hang(*args):
            await asyncio.sleep(10)
        with patch.object(vertex, "_generate", hang), patch.object(vertex, "LLM_TIMEOUT", 0.01):
            with self.assertRaises(asyncio.TimeoutError):
                await vertex.generate("persona", [])


if __name__ == "__main__":
    unittest.main()
