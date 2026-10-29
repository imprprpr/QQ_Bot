"""Offline regressions: no real model calls, QQ messages or GPU jobs."""
import asyncio
import base64
import importlib
import io
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from openai import AsyncOpenAI

import nonebot
from aiohttp import web
from PIL import Image
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.exception import ActionFailed

# All model requests below are mocked or served by loopback HTTP fixtures.
os.environ["EVENT_MEMORY_ENABLED"] = "false"  # New tests explicitly mock the extractor when enabling it.
nonebot.init(log_level="INFO")
plugin = importlib.import_module("src.plugins.chat_and_draw").app
# Offline tests must never select an operator's private deployment adapter.
os.environ["LLM_CLIENT_FACTORY"] = ""
os.environ["BOT_INTERACTION_CONFIG_FILE"] = ""  # Offline fixtures never read deployment peer lists.
# Never read the operator's saved model profiles (data/models.json) during tests.
os.environ["BOT_MODEL_CONFIG_FILE"] = os.path.join(tempfile.gettempdir(), "animabot-tests-no-models-" + str(os.getpid()) + ".json")
os.environ["BOT_PROACTIVE_CONFIG_FILE"] = ""
os.environ["BOT_PARTICIPATION_CONFIG_FILE"] = ""
os.environ["BOT_PERSONA_CONFIG_FILE"] = ""
os.environ["DRAW_ENABLED"] = "true"
os.environ["TTS_ENABLED"] = "true"
os.environ["TTS_BACKEND"] = "gpt-sovits"
os.environ["TTS_DELIVERY"] = "file"
os.environ["TTS_HEADERS_FACTORY"] = ""
os.environ["DRAW_BACKEND"] = "comfyui"
os.environ["DRAW_CLIENT_FACTORY"] = ""
llm = importlib.import_module("src.plugins.chat_and_draw.llm")
media = importlib.import_module("src.plugins.chat_and_draw.media")
comfy = importlib.import_module("src.plugins.chat_and_draw.comfyui")
delivery_module = importlib.import_module("src.plugins.chat_and_draw.delivery")
from src.plugins.chat_and_draw.sessions import Session, SessionStore, session_key, trim_history
from src.plugins.chat_and_draw.tavern import TavernCard, tavern_manager


class Event:
    def __init__(self, text="hello", user="123", group=None, addressed=False):
        self.text, self.user, self.group_id = text, user, group
        self.message_type = "private" if group is None else "group"
        self.addressed = addressed

    def get_user_id(self):
        return self.user

    def get_message(self):
        return Message(self.text)

    def get_type(self):
        return "message"

    def is_tome(self):
        return self.addressed


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "sessions.db")
        self.bot = SimpleNamespace(self_id="999")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_history_and_character_survive_reopen(self):
        session = Session("one", character="amelia", history=[{"role": "user", "content": "remember"}])
        await self.store.save(session)
        restored = await SessionStore(self.store.path).load("one")
        self.assertEqual(restored.character, "amelia")
        self.assertEqual(restored.history, session.history)

    async def test_session_keys_separate_bot_group_and_private(self):
        keys = {session_key(self.bot, Event()), session_key(self.bot, Event(group=1)),
                session_key(self.bot, Event(group=2)), session_key(self.bot, Event(user="456")),
                session_key(SimpleNamespace(self_id="888"), Event())}
        self.assertEqual(len(keys), 5)

    async def test_concurrent_messages_are_serialized(self):
        async def append(index):
            async with self.store.lock("same"):
                session = await self.store.load("same")
                await asyncio.sleep(0.001)
                session.history.append({"role": "user", "content": str(index)})
                await self.store.save(session)
        await asyncio.gather(*(append(i) for i in range(10)))
        session = await self.store.load("same")
        self.assertEqual([m["content"] for m in session.history], [str(i) for i in range(10)])

    async def test_chat_persists_and_passes_selected_character(self):
        key = session_key(self.bot, Event())
        await self.store.save(Session(key, character="amelia"))
        with patch.object(plugin, "store", self.store), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("hi", None, None, False))) as model, \
             patch.object(plugin, "send_reply", AsyncMock()):
            await plugin.handle_message(self.bot, Event())
        self.assertEqual(model.call_args.kwargs["card"].name, "Amelia")
        restored = await self.store.load(key)
        self.assertEqual([m["role"] for m in restored.history], ["user", "assistant"])
        self.assertGreater(restored.last_interaction, 0)

    async def test_switch_only_changes_current_session(self):
        first, second = Event(user="123"), Event(user="456")
        k1, k2 = session_key(self.bot, first), session_key(self.bot, second)
        await self.store.save(Session(k1, character="ling", last_image_prompt="old beach scene"))
        await self.store.save(Session(k2, character="ling", history=[{"role": "user", "content": "keep"}]))
        with patch.object(plugin, "store", self.store), patch.object(plugin.cmd_switch, "send", AsyncMock()):
            await plugin.handle_switch(self.bot, first, Message("amelia"))
        self.assertEqual((await self.store.load(k1)).character, "amelia")
        self.assertIsNone((await self.store.load(k1)).last_image_prompt)
        other = await self.store.load(k2)
        self.assertEqual(other.character, "ling")
        self.assertEqual(other.history[0]["content"], "keep")

    async def test_proactive_due_only_for_opted_in_private_session(self):
        for key, private, enabled in [("yes", True, True), ("off", True, False), ("group", False, True)]:
            await self.store.save(Session(key, private=private, proactive=enabled, next_proactive=1))
        self.assertEqual(await self.store.due(time.time()), ["yes"])

    async def test_proactive_sends_once_and_reschedules(self):
        session = Session("one", bot_id="999", user_id="123", proactive=True, next_proactive=1)
        await self.store.save(session)
        bot = SimpleNamespace(send_private_msg=AsyncMock())
        with patch.object(plugin, "store", self.store), patch.object(plugin, "get_bot", return_value=bot), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("hello", None, None, False))):
            await plugin.run_proactive_once(datetime(2026, 10, 19, 12))
            await plugin.run_proactive_once(datetime(2026, 10, 19, 12))
        bot.send_private_msg.assert_awaited_once_with(user_id=123, message="hello")
        self.assertGreater((await self.store.load("one")).next_proactive, datetime(2026, 10, 19, 12).timestamp())

    async def test_proactive_quiet_hours(self):
        with patch.object(plugin, "store") as store:
            await plugin.run_proactive_once(datetime(2026, 10, 19, 6, 29))
            store.due.assert_not_called()

    async def test_direct_draw_bypasses_model_and_queues_image(self):
        with patch.object(plugin, "store", self.store), \
             patch.object(plugin, "get_chat_response", AsyncMock()) as model, \
             patch.object(plugin, "enqueue_draw", AsyncMock(return_value=True)) as draw, \
             patch.object(plugin.cmd_draw, "send", AsyncMock()) as send:
            await plugin.handle_draw(self.bot, Event(), Message("1girl, casual clothes"))
        model.assert_not_awaited()
        draw.assert_awaited_once_with(self.store, self.bot, unittest.mock.ANY, "1girl, casual clothes")
        send.assert_not_awaited()

    async def test_direct_draw_has_no_progress_message(self):
        with patch.object(plugin, "store", self.store), \
             patch.object(plugin, "enqueue_draw", AsyncMock(return_value=True)) as draw, \
             patch.object(plugin.cmd_draw, "send", AsyncMock()) as send:
            await plugin.handle_draw(self.bot, Event(), Message("solo"))
        draw.assert_awaited_once_with(self.store, self.bot, unittest.mock.ANY, "solo")
        send.assert_not_awaited()

    async def test_failed_image_is_recorded_in_history(self):
        result = delivery_module.DeliveryResult(text_sent=True, image_requested=True,
                                                image_generated=True, image_sent=False)
        with patch.object(plugin, "store", self.store), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("给你照片", "solo", None, False))), \
             patch.object(plugin, "send_reply", AsyncMock(return_value=result)):
            await plugin.handle_message(self.bot, Event())
        history = (await self.store.load(session_key(self.bot, Event()))).history
        self.assertIn("QQ 发送失败", history[-1]["content"])

    async def test_last_generated_scene_survives_restart_and_is_used_for_followup(self):
        key = session_key(self.bot, Event())
        await self.store.save(Session(key, character="ling", last_image_prompt="black hair, white dress, beach, sunset"))
        result = delivery_module.DeliveryResult(text_sent=True, image_requested=True,
                                                image_generated=True, image_sent=True)
        with patch.object(plugin, "store", SessionStore(self.store.path)), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("换好了", "black hair, red dress, beach, sunset", None, False))) as model, \
             patch.object(plugin, "send_reply", AsyncMock(return_value=result)):
            await plugin.handle_message(self.bot, Event("裙子改红色，其他不变"))
        self.assertEqual(model.call_args.kwargs["visual_context"], "black hair, white dress, beach, sunset")
        restored = await SessionStore(self.store.path).load(key)
        self.assertEqual(restored.last_image_prompt, "black hair, red dress, beach, sunset")

    async def test_failed_generation_preserves_previous_scene(self):
        key = session_key(self.bot, Event())
        await self.store.save(Session(key, last_image_prompt="white dress, beach"))
        result = delivery_module.DeliveryResult(text_sent=True, image_requested=True)
        with patch.object(plugin, "store", self.store), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("准备画", "red dress, beach", None, False))), \
             patch.object(plugin, "send_reply", AsyncMock(return_value=result)):
            await plugin.handle_message(self.bot, Event("换红裙"))
        self.assertEqual((await self.store.load(key)).last_image_prompt, "white dress, beach")

    async def test_reset_clears_previous_image_scene(self):
        key = session_key(self.bot, Event())
        await self.store.save(Session(key, character="ling", last_image_prompt="beach"))
        with patch.object(plugin, "store", self.store), patch.object(plugin.cmd_reset, "send", AsyncMock()):
            await plugin.handle_reset(self.bot, Event())
        self.assertIsNone((await self.store.load(key)).last_image_prompt)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store_patch = patch.object(plugin, "store", SessionStore(Path(self.temp.name) / "sessions.db"))
        self.store_patch.start()
        # Legacy delivery tests must never contact a public sticker provider.
        self.stickers = patch.object(plugin, "get_sticker", AsyncMock(return_value=None))
        self.stickers.start()

    async def asyncTearDown(self):
        self.stickers.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    async def test_drawing_sends_image_without_fixed_progress_notice(self):
        with patch.object(plugin, "generate_image", AsyncMock(return_value=b"png")), \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as sender:
            await plugin.send_reply("", "solo", None, False)
        sender.assert_awaited_once()
        self.assertEqual(sender.call_args.args[0].type, "image")

    async def test_contextual_reaction_is_sent_as_image(self):
        with patch.object(plugin, "get_sticker", AsyncMock(return_value=b"gif")) as reaction, \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as sender:
            result = await plugin.send_reply("", None, "害羞", False)
        reaction.assert_awaited_once_with("blush")
        self.assertEqual(sender.call_args.args[0].type, "image")
        self.assertTrue(result.sticker_sent)

    async def test_sticker_cooldown_skips_fetch_and_send(self):
        with patch.object(plugin, "get_sticker", AsyncMock()) as reaction, \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as sender:
            result = await plugin.send_reply("", None, "happy", False, allow_sticker=False)
        reaction.assert_not_awaited()
        sender.assert_not_awaited()
        self.assertFalse(result.sticker_sent)

    async def test_missing_sticker_does_not_prevent_generation_or_send_qq_face(self):
        with patch.object(plugin, "generate_image", AsyncMock(return_value=b"png")) as draw, \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as sender:
            result = await plugin.send_reply("hello", "solo", "happy", False)
        draw.assert_awaited_once_with("solo")
        self.assertTrue(result.image_sent)
        self.assertEqual(sender.call_args.args[0].type, "image")
        self.assertEqual([call.args[0].type for call in sender.call_args_list], ["text", "image"])

    async def test_missing_sticker_only_preserves_text(self):
        with patch.object(plugin.chat_handler, "send", AsyncMock()) as sender:
            result = await plugin.send_reply("hello", None, "happy", False)
        sender.assert_awaited_once()
        self.assertEqual(sender.call_args.args[0].type, "text")
        self.assertFalse(result.sticker_sent)

    async def test_sticker_send_failure_never_falls_back_to_qq_face(self):
        async def send(message):
            if message.type == "image":
                raise ActionFailed(retcode=1200, wording="image rejected")
        with patch.object(plugin, "get_sticker", AsyncMock(return_value=b"gif")), \
             patch.object(plugin.chat_handler, "send", AsyncMock(side_effect=send)) as sender:
            result = await plugin.send_reply("hello", None, "happy", False)
        self.assertEqual([call.args[0].type for call in sender.call_args_list], ["text", "image"])
        self.assertTrue(result.text_sent)
        self.assertFalse(result.sticker_sent)

    async def test_text_and_progress_failure_do_not_prevent_image_delivery(self):
        async def send(message):
            if getattr(message, "type", None) != "image":
                raise ActionFailed(retcode=1200, message="send rejected")
        with patch.object(plugin, "generate_image", AsyncMock(return_value=b"png")) as draw, \
             patch.object(plugin.chat_handler, "send", AsyncMock(side_effect=send)):
            result = await plugin.send_reply("hello", "solo", None, False)
        draw.assert_awaited_once()
        self.assertFalse(result.text_sent)
        self.assertTrue(result.image_sent)

    async def test_image_rejection_preserves_file_and_reports_failure(self):
        async def send(message):
            if getattr(message, "type", None) == "image":
                raise ActionFailed(retcode=1200, wording="image upload rejected")
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(delivery_module, "FAILED_IMAGE_DIR", Path(folder)), \
             patch.object(plugin, "generate_image", AsyncMock(return_value=b"test-image-bytes")), \
             patch.object(plugin.chat_handler, "send", AsyncMock(side_effect=send)) as sender:
            result = await plugin.send_reply("", "solo", None, False)
            self.assertTrue(result.image_generated)
            self.assertFalse(result.image_sent)
            self.assertEqual(Path(result.saved_path).read_bytes(), b"test-image-bytes")
            self.assertIn("QQ 发送失败", sender.call_args.args[0].data["text"])

    async def test_private_or_addressed_group_only(self):
        self.assertTrue(await plugin.addressed(Event()))
        self.assertFalse(await plugin.addressed(Event(group=1)))
        self.assertTrue(await plugin.addressed(Event(group=1, addressed=True)))

    async def test_voice_success_does_not_send_text(self):
        with patch.object(plugin, "get_tts_audio", AsyncMock(return_value="file:///test.wav")), \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as send:
            await plugin.send_reply("hello", None, None, True)
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[0].type, "record")

    async def test_voice_delivery_failure_falls_back_once(self):
        with patch.object(plugin, "get_tts_audio", AsyncMock(return_value="file:///test.wav")), \
             patch.object(plugin.chat_handler, "send", AsyncMock(side_effect=[RuntimeError("offline"), None])) as send:
            await plugin.send_reply("hello", None, None, True)
        self.assertEqual([c.args[0].type for c in send.call_args_list], ["record", "text"])

    async def test_tts_failure_falls_back_once(self):
        with patch.object(plugin, "get_tts_audio", AsyncMock(return_value=None)), \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as send:
            await plugin.send_reply("hello", None, None, True)
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[0].type, "text")

    async def test_combined_reply_sends_all_actions(self):
        with patch.object(plugin, "generate_image", AsyncMock(return_value=b"png")), \
             patch.object(plugin, "get_sticker", AsyncMock(return_value=b"gif")), \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as send:
            await plugin.send_reply("hello", "1girl", "害羞", False)
        self.assertEqual([c.args[0].type if hasattr(c.args[0], "type") else "text"
                          for c in send.call_args_list], ["text", "image", "image"])

    async def test_background_task_shutdown(self):
        await plugin.start_background_task()
        task = plugin._background_task
        await plugin.start_background_task()
        self.assertIs(plugin._background_task, task)
        await plugin.stop_background_task()
        self.assertTrue(task.cancelled())
        self.assertIsNone(plugin._background_task)


class PromptTests(unittest.TestCase):
    def test_list_worldbook(self):
        card = TavernCard({"character_book": [{"keys": ["HELLO"], "content": "world"}]})
        self.assertEqual(card.scan_lorebook("hello"), ["world"])

    def test_character_system_prompt_and_macro_are_applied(self):
        card = TavernCard({"name": "Alice", "system_prompt": "Special instruction for {{char}}"})
        self.assertIn("Special instruction for Alice", tavern_manager.build_system_prompt(card))

    def test_lookup_does_not_change_default(self):
        before = tavern_manager.active_card_name
        self.assertEqual(tavern_manager.find_card("amelia").name, "Amelia")
        self.assertEqual(tavern_manager.active_card_name, before)

    def test_case_insensitive_multiline_actions(self):
        self.assertEqual(llm.parse_reply("hello<DRAW: one,\ntwo><STICKER: Happy><VOICE>"),
                         ("hello", "one,\ntwo", "happy", True))

    def test_empty_model_output_is_rejected(self):
        with self.assertRaises(ValueError):
            llm.parse_reply(None)

    def test_history_is_bounded(self):
        history = trim_history([{"role": "user", "content": "x" * 20000}] * 40)
        self.assertLessEqual(len(history), 30)
        self.assertLessEqual(sum(len(m["content"]) for m in history), 16000)

    def test_speech_cleanup_preserves_english(self):
        self.assertEqual(media.clean_speech("*waves* hello world ww～"), " hello world ，".strip())


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "success"
        self.app = web.Application()
        self.app.router.add_post("/prompt", self.prompt)
        self.app.router.add_get("/history/{id}", self.history)
        self.app.router.add_get("/view", self.view)
        self.app.router.add_get("/tts", self.tts)
        self.app.router.add_post("/v1/chat/completions", self.completion)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def prompt(self, request):
        self.submitted = await request.json()
        if self.mode == "http_error":
            return web.json_response({"error": "invalid workflow"}, status=400)
        return web.json_response({"prompt_id": "test"})

    async def history(self, request):
        if self.mode == "pending":
            return web.json_response({})
        if self.mode == "failed":
            return web.json_response({"test": {"status": {"status_str": "error"}}})
        if self.mode == "no_image":
            return web.json_response({"test": {"status": {"completed": True}, "outputs": {}}})
        return web.json_response({"test": {"outputs": {"12": {"images": [{"filename": "a.png"}]}}}})

    async def view(self, request):
        return web.Response(body=b"fake-image", content_type="image/png")

    async def tts(self, request):
        if self.mode == "failed":
            return web.json_response({"error": "bad reference"})
        return web.Response(body=b"RIFF-test-audio", content_type="audio/wav")

    async def completion(self, request):
        self.model_request = await request.json()
        return web.json_response({
            "id": "test-chat", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant",
                         "content": "hi <draw: solo><sticker: happy><voice>"},
                         "finish_reason": "stop"}],
        })

    async def draw(self):
        with patch.object(comfy, "COMFYUI_SERVER", self.url), \
             patch.object(comfy, "DRAW_POLL_INTERVAL", 0.001), \
             patch.object(comfy, "DRAW_TIMEOUT", 0.2 if self.mode == "pending" else 5):
            return await comfy.generate_image("solo, test")

    async def test_comfy_http_round_trip(self):
        self.assertEqual(await self.draw(), b"fake-image")
        self.assertIn("solo, test", self.submitted["prompt"]["3"]["inputs"]["wildcard_text"])

    async def test_comfy_task_id_is_saved_from_http_response(self):
        from dataclasses import replace
        from src.plugins.chat_and_draw.runtime import RuntimeStore, current_scope, operation
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeStore(Path(temp) / "tasks.db")
            async with operation(runtime, "chat", "ling", "draw") as scope:
                identity = await runtime.create_task(scope, "solo")
                token = current_scope.set(replace(scope, task_id=identity))
                try:
                    self.assertEqual(await self.draw(), b"fake-image")
                finally:
                    current_scope.reset(token)
            task = await runtime.task(identity)
            self.assertEqual(task["prompt_id"], "test")
            events = (await runtime.run_detail(scope.run_id))["events"]
            self.assertTrue(any(e["stage"] == "comfyui" and e["status"] == "completed" for e in events))

    async def test_comfy_pending_has_deadline(self):
        self.mode = "pending"
        self.assertIsNone(await self.draw())

    async def test_comfy_failure_status(self):
        self.mode = "failed"
        self.assertIsNone(await self.draw())

    async def test_comfy_rejected_workflow(self):
        self.mode = "http_error"
        self.assertIsNone(await self.draw())

    async def test_comfy_completed_without_image(self):
        self.mode = "no_image"
        self.assertIsNone(await self.draw())

    async def test_tts_http_round_trip_and_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(media, "TTS_CACHE_DIR", Path(temp)), \
             patch.dict(os.environ, {"TTS_API_URL": self.url + "/tts"}):
            uri = await media.get_tts_audio("hello")
            self.assertTrue(uri.startswith("file:///"))
            self.assertEqual(next(Path(temp).glob("*.wav")).read_bytes(), b"RIFF-test-audio")

    async def test_tts_rejects_json_error(self):
        self.mode = "failed"
        with patch.dict(os.environ, {"TTS_API_URL": self.url + "/tts"}):
            self.assertIsNone(await media.get_tts_audio("hello"))

    async def test_image_size_limit(self):
        with patch.object(media, "MAX_IMAGE_BYTES", 3):
            self.assertIsNone(await media.get_image_base64(self.url + "/view"))

    async def test_vision_uses_actual_image_mime(self):
        data = io.BytesIO()
        Image.new("RGB", (2, 2)).save(data, format="PNG")
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="a picture"))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm, "get_client", return_value=client):
            self.assertEqual(await llm.get_image_description([base64.b64encode(data.getvalue()).decode()]), "a picture")
        image_url = create.call_args.kwargs["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;"))

    async def test_model_error_returns_safe_reply(self):
        with patch.object(llm, "get_client", side_effect=ValueError("missing key")):
            reply = await llm.get_chat_response([{"role": "user", "content": "hi"}])
        self.assertEqual(len(reply), 4)
        self.assertEqual(reply[1:], (None, None, False))

    async def test_real_sdk_http_request_and_response(self):
        async with AsyncOpenAI(api_key="test-only", base_url=self.url + "/v1", max_retries=0) as client:
            with patch.object(llm, "get_client", return_value=client), \
                 patch.dict(os.environ, {"LLM_EXTRA_BODY": '{"thinking":{"type":"disabled"}}'}):
                result = await llm.get_chat_response(
                    [{"role": "user", "content": "hello"}], card=tavern_manager.cards["amelia"])
        self.assertEqual(result, ("hi", "solo", "happy", True))
        self.assertEqual(self.model_request["thinking"], {"type": "disabled"})
        self.assertIn("Amelia", self.model_request["messages"][0]["content"])
        self.assertEqual(self.model_request["messages"][-1]["content"], "hello")

    async def test_model_total_timeout_and_silent_proactive_failure(self):
        async def stalled(**kwargs):
            await asyncio.sleep(10)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stalled)))
        with patch.object(llm, "get_client", return_value=client), patch.object(llm, "LLM_TIMEOUT", 0.01):
            self.assertEqual(await llm.get_chat_response([], silent_failure=True), ("", None, None, False))
            reply = await llm.get_chat_response([])
            self.assertIn("暂时没有响应", reply[0])
            self.assertIsNone(reply[1])

    async def test_missing_optional_vision_key_does_not_create_client(self):
        with patch.dict(os.environ, {"VISION_API_KEY": "", "LLM_API_KEY": ""}):
            with self.assertRaises(ValueError):
                llm.get_client(vision=True)


if __name__ == "__main__":
    unittest.main()
