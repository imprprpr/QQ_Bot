"""Portable API-key backend regressions; only loopback HTTP and fake credentials."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
import httpx
from openai import AsyncOpenAI
from PIL import Image
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from test_bot import llm, plugin, tavern_manager
from src.plugins.chat_and_draw import image_history as images
from src.plugins.chat_and_draw.sessions import Session, SessionStore
from src.plugins.chat_and_draw.runtime import RuntimeStore, operation


def png():
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), "red").save(stream, "PNG")
    return base64.b64encode(stream.getvalue()).decode()


class APIConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_vision_inherits_main_credentials_and_url(self):
        client = SimpleNamespace(close=AsyncMock())
        with patch.dict(os.environ, {"LLM_API_KEY": "test-main", "LLM_BASE_URL": "http://127.0.0.1:9999/v1",
                                     "VISION_API_KEY": "", "VISION_BASE_URL": "", "VISION_MODEL": ""}), \
             patch.dict(llm._clients, {}, clear=True), patch.object(llm, "AsyncOpenAI", return_value=client) as constructor:
            self.assertIs(llm.get_client(vision=True), client)
            self.assertEqual(constructor.call_args.kwargs["api_key"], "test-main")
            self.assertEqual(constructor.call_args.kwargs["base_url"], "http://127.0.0.1:9999/v1")
            self.assertEqual(llm.vision_model(), llm.MODEL)
            self.assertNotIn("http_client", constructor.call_args.kwargs)
            await llm.close_clients()

    async def test_separate_vision_credentials_are_used(self):
        client = SimpleNamespace(close=AsyncMock())
        with patch.dict(os.environ, {"LLM_API_KEY": "main", "VISION_API_KEY": "test-vision",
                                     "VISION_BASE_URL": "http://127.0.0.1:9998/v1", "VISION_MODEL": "vision-model"}), \
             patch.dict(llm._clients, {}, clear=True), patch.object(llm, "AsyncOpenAI", return_value=client) as constructor:
            llm.get_client(vision=True)
            self.assertEqual(constructor.call_args.kwargs["api_key"], "test-vision")
            self.assertEqual(constructor.call_args.kwargs["base_url"], "http://127.0.0.1:9998/v1")
            self.assertEqual(llm.vision_model(), "vision-model")
            await llm.close_clients()

    async def test_missing_key_does_not_initialize_sdk(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "", "VISION_API_KEY": ""}), patch.object(llm, "AsyncOpenAI") as sdk:
            with self.assertRaises(ValueError):
                llm.get_client()
            with self.assertRaises(ValueError):
                llm.get_client(vision=True)
            sdk.assert_not_called()

    async def test_explicit_proxy_is_used_for_external_api(self):
        client = SimpleNamespace(close=AsyncMock())
        transport = SimpleNamespace()
        with patch.dict(os.environ, {"LLM_API_KEY": "test", "LLM_BASE_URL": "https://api.example.com/v1",
                                     "LLM_PROXY": "http://127.0.0.1:9997"}), \
             patch.dict(llm._clients, {}, clear=True), patch.object(llm.httpx, "AsyncClient", return_value=transport) as http, \
             patch.object(llm, "AsyncOpenAI", return_value=client) as sdk:
            llm.get_client()
            http.assert_called_once_with(proxy="http://127.0.0.1:9997", trust_env=False)
            self.assertIs(sdk.call_args.kwargs["http_client"], transport)
            await llm.close_clients()


class ImageHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_survives_database_reload(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp) / "images"), \
             patch.dict(os.environ, {"LLM_VISION_ENABLED": "true"}):
            identity = images.save_image(png())
            store = SessionStore(Path(temp) / "session.db")
            await store.save(Session("test", history=[{"role": "user", "content": "看图", "images": [identity]}]))
            restored = await SessionStore(store.path).load("test")
            self.assertNotIn(png(), store.path.read_bytes().decode(errors="ignore"))
            payload = llm.build_messages("persona", restored.history)
            self.assertTrue(payload[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    async def test_cache_expiry_and_path_restriction(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp)):
            identity = images.save_image(png())
            file = Path(temp) / f"{identity}.img"
            os.utime(file, (1, 1))
            self.assertIsNone(images.load_image(identity))
            self.assertIsNone(images.load_image("../secret"))
            images.cleanup_images()
            self.assertFalse(file.exists())

    async def test_direct_images_bypass_separate_vision_service(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp) / "images"), \
             patch.object(plugin, "store", SessionStore(Path(temp) / "session.db")), \
             patch.dict(os.environ, {"LLM_VISION_ENABLED": "true"}), \
             patch.object(plugin, "get_image_description", AsyncMock()) as vision, \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("看到了", None, None, False))) as chat, \
             patch.object(plugin, "send_reply", AsyncMock()):
            event = SimpleNamespace(group_id=None, get_user_id=lambda: "42",
                get_message=lambda: Message([MessageSegment.image("base64://" + png())]))
            await plugin.handle_message(SimpleNamespace(self_id="bot"), event)
            vision.assert_not_awaited()
            self.assertEqual(len(chat.call_args.args[0][0]["images"]), 1)

    async def test_recent_image_budget_and_expired_placeholder(self):
        history = [{"role": "user", "content": "old", "images": ["old"]},
                   {"role": "assistant", "content": "okay"},
                   {"role": "user", "content": "new", "images": ["new"]}]
        with patch.dict(os.environ, {"LLM_VISION_ENABLED": "true"}), patch.object(llm, "HISTORY_IMAGES", 1), \
             patch.object(llm, "load_image", return_value=None) as load:
            body = llm.build_messages("persona", history)
        load.assert_called_once_with("new")
        self.assertIn("历史图片已过期", body[-1]["content"][1]["text"])
        self.assertNotIn("images", body[-1])

    async def test_text_only_mode_does_not_load_old_images(self):
        with patch.dict(os.environ, {"LLM_VISION_ENABLED": "false"}), patch.object(llm, "load_image") as load:
            body = llm.build_messages("persona", [{"role": "user", "content": "hi", "images": ["old"]}])
        load.assert_not_called()
        self.assertIn("图片未附带", body[-1]["content"][1]["text"])


class APIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.status = 200
        self.finish = "stop"
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
        self.client = AsyncOpenAI(api_key="test-only", base_url=self.url, max_retries=0,
                                  http_client=httpx.AsyncClient(trust_env=False))
        self.client_patch = patch.object(llm, "get_client", return_value=self.client)
        self.client_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        await self.client.close()
        await self.runner.cleanup()

    async def handler(self, request):
        self.requests.append((dict(request.headers), await request.json()))
        if self.status != 200:
            return web.json_response({"error": {"message": "test failure"}}, status=self.status)
        return web.json_response({"id": "fake", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": self.finish,
                "message": {"role": "assistant", "content": "hello<draw: solo><sticker: happy>"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}})

    async def test_text_memory_and_usage_with_real_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RuntimeStore(Path(temp) / "runs.db")
            async with operation(store, "chat", "ling", "hi") as scope:
                reply = await llm.get_chat_response([{"role": "user", "content": "hi"}],
                    card=tavern_manager.cards["amelia"], memory_context={"memories": ["喜欢海边"], "summary": "穿白裙"})
            events = (await store.run_detail(scope.run_id))["events"]
            usage = json.loads(next(e["detail"] for e in events if e["stage"] == "usage"))
            self.assertEqual(usage["total_tokens"], 17)
        self.assertEqual(reply, ("hello", "solo", "happy", False))
        headers, payload = self.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer test-only")
        self.assertIn("Amelia", payload["messages"][0]["content"])
        self.assertIn("喜欢海边", payload["messages"][0]["content"])

    async def test_unified_multimodal_api_receives_png(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(images, "IMAGE_CACHE_DIR", Path(temp)), \
             patch.dict(os.environ, {"LLM_VISION_ENABLED": "true"}):
            identity = images.save_image(png())
            reply = await llm.get_chat_response([{"role": "user", "content": "看图", "images": [identity]}])
        self.assertEqual(reply[1], "solo")
        content = self.requests[0][1]["messages"][-1]["content"]
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    async def test_separate_vision_sends_configured_model(self):
        with patch.dict(os.environ, {"VISION_MODEL": "test-vision-model"}):
            await llm.get_image_description([png()], "看图")
        self.assertEqual(self.requests[0][1]["model"], "test-vision-model")

    async def test_separate_vision_usage_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RuntimeStore(Path(temp) / "runs.db")
            async with operation(store, "chat", "ling", "image") as scope:
                await llm.get_image_description([png()])
            detail = await store.run_detail(scope.run_id)
            usage = [json.loads(e["detail"]) for e in detail["events"] if e["stage"] == "usage"]
            self.assertEqual(usage[0]["total_tokens"], 17)

    async def test_permission_error_has_no_retry_and_no_actions(self):
        self.status = 403
        reply = await llm.get_chat_response([{"role": "user", "content": "hi"}])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(reply[1:], (None, None, False))

    async def test_incomplete_or_filtered_output_never_runs_actions(self):
        for reason in ("length", "content_filter", "tool_calls"):
            self.finish = reason
            reply = await llm.get_chat_response([{"role": "user", "content": "hi"}])
            self.assertEqual(reply[1:], (None, None, False))
