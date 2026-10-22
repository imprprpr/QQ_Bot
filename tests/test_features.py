"""Disabled cloud tools must never contact absent generation services."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_bot import llm, plugin, media, comfy
from src.plugins.chat_and_draw import jobs
from src.plugins.chat_and_draw.drawing import drawing_context


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_tags_are_removed_but_stickers_remain(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false", "TTS_ENABLED": "false"}):
            self.assertEqual(llm.parse_reply("hi<draw: solo><voice><sticker: happy>"),
                             ("hi", None, "happy", False))

    async def test_disabled_drawing_does_not_advertise_context(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false"}):
            prompt = drawing_context("SECRET_OLD_IMAGE_PROMPT")
        self.assertIn("未启用", prompt)
        self.assertNotIn("SECRET_OLD_IMAGE_PROMPT", prompt)

    async def test_draw_command_returns_without_queue_or_database(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false"}), \
             patch.object(plugin.cmd_draw, "send", AsyncMock()) as send, \
             patch.object(plugin, "enqueue_draw", AsyncMock()) as queue:
            await plugin.handle_draw(None, None, None)
        send.assert_awaited_once()
        queue.assert_not_awaited()

    async def test_delivery_blocks_explicit_voice_and_drawing(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false", "TTS_ENABLED": "false"}), \
             patch.object(plugin, "get_tts_audio", AsyncMock()) as tts, \
             patch.object(plugin, "generate_image", AsyncMock()) as image, \
             patch.object(plugin, "try_send", AsyncMock(return_value=True)) as send:
            deferred = AsyncMock()
            result = await plugin.send_reply("hello", "solo", None, True, defer_draw=deferred)
        tts.assert_not_awaited()
        image.assert_not_awaited()
        deferred.assert_not_awaited()
        self.assertTrue(result.text_sent)
        self.assertFalse(result.image_requested)
        self.assertEqual(send.call_args.args[1].type, "text")

    async def test_direct_generation_is_disabled_without_http(self):
        with patch.dict(os.environ, {"DRAW_ENABLED": "false"}), \
             patch.object(comfy, "_generate", AsyncMock()) as generate:
            self.assertIsNone(await comfy.generate_image("solo"))
            self.assertFalse(await jobs.enqueue_draw(None, None, None, "solo"))
        generate.assert_not_awaited()

    async def test_direct_tts_is_disabled_without_http(self):
        with patch.dict(os.environ, {"TTS_ENABLED": "false"}), \
             patch.object(media.aiohttp, "ClientSession") as client:
            self.assertIsNone(await media.get_tts_audio("hello"))
        client.assert_not_called()
