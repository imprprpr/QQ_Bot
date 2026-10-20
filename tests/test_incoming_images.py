"""Incoming sticker formats, message resolution and incomplete vision regressions."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from test_bot import plugin, llm, media
from src.plugins.chat_and_draw import incoming_images as incoming, image_history
from src.plugins.chat_and_draw.runtime import RuntimeStore, operation
from src.plugins.chat_and_draw.sessions import SessionStore, session_key


def gif(animated=True):
    output = io.BytesIO()
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "green", "blue")]
    frames[0].save(output, "GIF", save_all=animated, append_images=frames[1:] if animated else [], duration=100, loop=0)
    return output.getvalue()


class NormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_animated_gif_is_api_safe_png_with_multiple_frames(self):
        data = incoming.normalize_image(gif())
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "PNG")
            colors = {color for _, color in image.convert("RGB").getcolors()}
            self.assertIn((255, 0, 0), colors)
            self.assertIn((0, 128, 0), colors)
            self.assertIn((0, 0, 255), colors)

    async def test_single_frame_gif_and_cache_are_supported(self):
        data = incoming.normalize_image(gif(False))
        with tempfile.TemporaryDirectory() as temp, patch.object(image_history, "IMAGE_CACHE_DIR", Path(temp)):
            identity = image_history.save_image(base64.b64encode(data).decode())
            self.assertEqual(image_history.load_image(identity)["mimeType"], "image/png")

    async def test_base64_download_normalizes_gif(self):
        result = await media.get_image_base64("base64://" + base64.b64encode(gif()).decode())
        with Image.open(io.BytesIO(base64.b64decode(result))) as image:
            self.assertEqual(image.format, "PNG")

    async def test_static_png_is_not_reencoded(self):
        output = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(output, "PNG")
        self.assertEqual(incoming.normalize_image(output.getvalue()), output.getvalue())

    async def test_invalid_and_oversize_inputs_are_rejected(self):
        self.assertIsNone(await media.get_image_base64("base64://" + base64.b64encode(b"not an image").decode()))
        with patch.object(incoming, "MAX_PIXELS", 4):
            with self.assertRaises(ValueError):
                incoming.normalize_image(gif())


class ResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_onebot_cache_resolution_when_event_file_is_unreadable(self):
        download = AsyncMock(side_effect=[None, "png-data"])
        bot = SimpleNamespace(get_image=AsyncMock(return_value={"url": "http://127.0.0.1/cached.png"}))
        result = await incoming.read_image(bot, {"file": "opaque-file-id"}, download)
        self.assertEqual(result, "png-data")
        bot.get_image.assert_awaited_once_with(file="opaque-file-id")

    async def test_market_sticker_id_has_validated_cdn_fallback(self):
        identity = "ab" * 16
        download = AsyncMock(return_value="png-data")
        self.assertEqual(await incoming.read_image(SimpleNamespace(), {"emoji_id": identity}, download), "png-data")
        self.assertEqual(download.call_args.args[0], f"https://gxh.vip.qq.com/club/item/parcel/item/ab/{identity}/raw300.gif")
        self.assertIsNone(incoming.sticker_url({"emoji_id": "../../secret"}))

    async def test_onebot_base64_is_accepted_without_second_download_url(self):
        download = AsyncMock(side_effect=[None, "png-data"])
        bot = SimpleNamespace(get_image=AsyncMock(return_value={"base64": "YWJj"}))
        self.assertEqual(await incoming.read_image(bot, {"file": "opaque-id"}, download), "png-data")
        self.assertEqual(download.call_args.args[0], "base64://YWJj")

    async def test_duplicate_source_is_not_downloaded_twice(self):
        download = AsyncMock(return_value=None)
        bot = SimpleNamespace(get_image=AsyncMock(return_value={"url": "same", "file": "same"}))
        self.assertIsNone(await incoming.read_image(bot, {"file": "same", "url": "same"}, download))
        download.assert_awaited_once_with("same")

    async def test_unexpected_decoder_failure_does_not_abort_conversation(self):
        download = AsyncMock(side_effect=EOFError("bad frame"))
        self.assertIsNone(await incoming.read_image(SimpleNamespace(), {"url": "https://example.test/bad.gif"}, download))

    async def test_mface_message_reaches_vision_and_keeps_summary(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(plugin, "store", SessionStore(Path(temp) / "sessions.db")), \
             patch.dict(os.environ, {"LLM_VISION_ENABLED": "false"}), \
             patch.object(plugin, "get_image_base64", AsyncMock(return_value="png-data")) as download, \
             patch.object(plugin, "get_image_description", AsyncMock(return_value="白发人物在摇头，文字是对不起做不到")) as vision, \
             patch.object(plugin, "prepare_event_memory", AsyncMock(return_value=[])) as memory, \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("好，不勉强你", None, None, False))) as chat, \
             patch.object(plugin, "send_reply", AsyncMock()):
            event = SimpleNamespace(group_id=None, get_user_id=lambda: "42", get_message=lambda: Message([
                MessageSegment("mface", {"url": "https://example.test/sticker.gif", "summary": "对不起做不到"})]))
            await plugin.handle_message(SimpleNamespace(self_id="bot"), event)
        vision.assert_awaited_once()
        self.assertIn("对不起做不到", chat.call_args.args[0][0]["content"])
        self.assertEqual(memory.call_args.args[2], "")  # Protocol summaries are not user-stated memory evidence.
        download.assert_awaited_once_with("https://example.test/sticker.gif")

    async def test_failed_vision_does_not_recall_unrelated_old_events(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(plugin, "store", SessionStore(Path(temp) / "sessions.db")), \
             patch.dict(os.environ, {"LLM_VISION_ENABLED": "false"}), \
             patch.object(plugin, "get_image_base64", AsyncMock(return_value=None)), \
             patch.object(plugin, "prepare_event_memory", AsyncMock()) as recall, \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("我这边没识别成功", None, None, False))) as chat, \
             patch.object(plugin, "send_reply", AsyncMock()) as send:
            event = SimpleNamespace(group_id=None, get_user_id=lambda: "42", get_message=lambda: Message([MessageSegment.image("https://example.test/a.gif")]))
            await plugin.handle_message(SimpleNamespace(self_id="bot"), event)
        recall.assert_not_awaited()
        chat.assert_not_awaited()
        self.assertIn("我这边没识别成功", send.call_args.args[0])
        self.assertIsNone(send.call_args.args[1])


class VisionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_gif_is_sent_as_png_with_configurable_output_budget(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="a sticker"))], usage=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm, "get_client", return_value=client), patch.dict(os.environ, {"VISION_MAX_TOKENS": "2048"}):
            self.assertEqual(await llm.get_image_description([base64.b64encode(gif()).decode()]), "a sticker")
        self.assertEqual(create.call_args.kwargs["max_tokens"], 2048)
        self.assertTrue(create.call_args.kwargs["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    async def test_truncation_is_failed_with_reason_and_never_reported_completed(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="partial"))], usage=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as temp, patch.object(llm, "get_client", return_value=client):
            runtime = RuntimeStore(Path(temp) / "runs.db")
            async with operation(runtime, "chat", "ling", "image") as scope:
                result = await llm.get_image_description([base64.b64encode(gif()).decode()])
            events = (await runtime.run_detail(scope.run_id))["events"]
        self.assertTrue(result.startswith("[视觉识别失败"))
        self.assertFalse(any(e["stage"] == "vision" and e["status"] == "completed" for e in events))
        self.assertTrue(any(json.loads(e["detail"]).get("finish_reason") == "length" for e in events))

    async def test_empty_vision_output_is_failure_not_completed(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=None))], usage=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm, "get_client", return_value=client):
            self.assertTrue((await llm.get_image_description([base64.b64encode(gif()).decode()])).startswith("[视觉识别失败"))
