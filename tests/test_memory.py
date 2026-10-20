"""Evidence, correction, day rollover and role isolation; no paid model requests."""
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx
from nonebot.adapters.onebot.v11 import Message

from test_bot import Event, plugin, llm, tavern_manager
from src.plugins.chat_and_draw import admin, memory
from src.plugins.chat_and_draw.conversation import dialogue_context
from src.plugins.chat_and_draw.sessions import Session, SessionStore, session_key


def proposal(quote, **extra):
    return {"action": "add", "kind": "event", "topic": "面试", "quote": quote, **extra}


class EventMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = SessionStore(Path(self.temp.name) / "sessions.db")
        self.store = memory.EventMemoryStore(self.sessions.path)
        self.bot = SimpleNamespace(self_id="999")
        self.event = Event("我明天下午面试，有点紧张")
        self.key = session_key(self.bot, self.event)
        self.now = datetime(2026, 10, 20, 15).astimezone().timestamp()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def add(self, text="我明天下午面试，有点紧张", key=None, character="ling", now=None):
        changes = await self.store.apply(key or self.key, character, "source-run", text, [proposal(text)], now=self.now if now is None else now)
        return changes[0]["id"]

    async def test_evidence_and_relative_date_survive_restart_and_next_day_recall(self):
        identity = await self.add()
        reopened = memory.EventMemoryStore(self.sessions.path)
        records = await reopened.recall(self.key, "ling", "我回来了", now=self.now + 86400)
        self.assertEqual(records[0]["id"], identity)
        self.assertEqual(records[0]["event_date"], "2026-10-21")
        self.assertEqual(records[0]["status"], "active")  # Date passing never guesses an outcome.
        self.assertEqual(records[0]["source_run_id"], "source-run")
        self.assertIn("2026-10-20", records[0]["recorded_at"])

    async def test_hallucinated_quote_invalid_shape_and_duplicate_are_not_written(self):
        source = "我明天下午面试"
        changes = await self.store.apply(self.key, "ling", "run", source,
            [proposal("我已经拿到了录用通知"), proposal(source, kind=[]),
             proposal(source, action={}), proposal(source)], now=self.now)
        self.assertEqual(len(changes), 1)
        await self.store.apply(self.key, "ling", "run2", source, [proposal(source)], now=self.now)
        self.assertEqual(len(await self.store.list(self.key, "ling")), 1)

    async def test_role_user_and_group_are_isolated(self):
        await self.add()
        for key, character in [(self.key, "amelia"), ("other-user", "ling"), ("other-group", "ling")]:
            self.assertEqual(await self.store.recall(key, character, "还记得面试吗", now=self.now), [])

    async def test_correction_supersedes_old_revision_without_old_date_in_active_statement(self):
        identity = await self.add()
        text = "我说错了，面试不是明天，是后天"
        await self.store.apply(self.key, "ling", "correction-run", text,
                               [proposal(text, action="replace", target_id=identity)], now=self.now)
        items = await self.store.list(self.key, "ling")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["event_date"], "2026-10-22")
        self.assertEqual(items[0]["source_run_id"], "correction-run")
        self.assertEqual(items[0]["supersedes_id"], identity)
        self.assertNotIn("紧张", items[0]["statement"])

    async def test_foreign_record_cannot_be_replaced_completed_or_forgotten(self):
        identity = await self.add(key="foreign")
        for action, text in [("replace", "我说错了，是后天面试"), ("complete", "我的面试完成了"), ("forget", "忘记我的面试安排")]:
            changes = await self.store.apply(self.key, "ling", "run", text, [proposal(text, action=action, target_id=identity)], now=self.now)
            self.assertEqual(changes, [])
        self.assertEqual(len(await self.store.list("foreign", "ling")), 1)

    async def test_replacement_requires_user_correction_signal(self):
        identity = await self.add()
        text = "我在喝咖啡看漫画"
        changes = await self.store.apply(self.key, "ling", "run", text, [proposal(text, action="replace", target_id=identity)], now=self.now)
        self.assertEqual(changes, [])
        self.assertEqual((await self.store.list(self.key, "ling"))[0]["id"], identity)

    async def test_completed_event_requires_explicit_progress_and_preserves_evidence(self):
        identity = await self.add()
        text = "我的面试结束了，还在等结果"
        await self.store.apply(self.key, "ling", "finish-run", text, [proposal(text, action="complete", target_id=identity)], now=self.now + 86400)
        item = (await self.store.list(self.key, "ling"))[0]
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["quote"], text)
        self.assertEqual(item["source_run_id"], "finish-run")

    async def test_forget_prevents_recall_but_preserves_other_roles(self):
        identity = await self.add()
        await self.add(character="amelia")
        await self.store.forget(self.key, "ling", identity)
        self.assertEqual(await self.store.recall(self.key, "ling", "面试", now=self.now), [])
        self.assertEqual(len(await self.store.list(self.key, "amelia")), 1)

    async def test_do_not_store_new_memory_from_forget_request(self):
        text = "忘记我明天面试这件事"
        self.assertEqual(await self.store.apply(self.key, "ling", "run", text, [proposal(text)]), [])

    async def test_old_irrelevant_events_are_not_injected(self):
        await self.add(now=self.now - 60 * 86400)
        self.assertEqual(await self.store.recall(self.key, "ling", "这部漫画画得真好", now=self.now), [])

    async def test_visible_record_count_is_bounded(self):
        for i in range(26):
            items = [proposal(f"我计划参加第{i*4+j}场读书分享会", topic="读书") for j in range(4)]
            await self.store.apply(self.key, "ling", "run", "；".join(p["quote"] for p in items), items, now=self.now + i)
        self.assertEqual(len(await self.store.list(self.key, "ling")), 100)

    async def test_handler_extracts_once_for_duplicate_message_and_passes_memory(self):
        self.event.message_id = 1234
        extracted = AsyncMock(return_value=[proposal(self.event.text)])
        with patch.dict(os.environ, {"EVENT_MEMORY_ENABLED": "true"}), \
             patch.object(memory, "extract", extracted), patch.object(plugin, "store", self.sessions), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("明天加油", None, None, False))) as chat, \
             patch.object(plugin.chat_handler, "send", AsyncMock()):
            await plugin.handle_message(self.bot, self.event)
            await plugin.handle_message(self.bot, self.event)
        extracted.assert_awaited_once()
        chat.assert_awaited_once()
        self.assertIn("events", chat.call_args.kwargs["memory_context"])

    async def test_short_ack_and_disabled_memory_make_no_extraction_call(self):
        session = Session(self.key, character="ling")
        with patch.object(memory, "extract", AsyncMock()) as extract:
            with patch.dict(os.environ, {"EVENT_MEMORY_ENABLED": "true"}):
                await memory.prepare(self.sessions.path, session, "嗯", "run")
                await memory.prepare(self.sessions.path, session, "今天好累", "run")
            with patch.dict(os.environ, {"EVENT_MEMORY_ENABLED": "false"}):
                await memory.prepare(self.sessions.path, session, self.event.text, "run")
        extract.assert_not_awaited()

    async def test_memory_service_error_does_not_block_chat_or_record_unproven_events(self):
        with patch.dict(os.environ, {"EVENT_MEMORY_ENABLED": "true"}), \
             patch.object(memory, "extract", AsyncMock(side_effect=RuntimeError("fake memory service failure"))), \
             patch.object(plugin, "store", self.sessions), \
             patch.object(plugin, "get_chat_response", AsyncMock(return_value=("先休息一会儿", None, None, False))) as chat, \
             patch.object(plugin.chat_handler, "send", AsyncMock()) as send:
            await plugin.handle_message(self.bot, self.event)
        chat.assert_awaited_once()
        send.assert_awaited_once()
        self.assertEqual(await self.store.list(self.key, "ling"), [])

    async def test_reset_clears_only_selected_role_events(self):
        await self.sessions.save(Session(self.key, character="ling"))
        await self.add()
        await self.add(character="amelia")
        with patch.object(plugin, "store", self.sessions), patch.object(plugin.cmd_reset, "send", AsyncMock()):
            await plugin.handle_reset(self.bot, self.event)
        self.assertEqual(await self.store.list(self.key, "ling"), [])
        self.assertEqual(len(await self.store.list(self.key, "amelia")), 1)

    async def test_command_lists_source_and_forgets_by_stable_id(self):
        identity = await self.add()
        with patch.object(plugin, "store", self.sessions), \
             patch.object(plugin.cmd_events, "send", AsyncMock()) as show, \
             patch.object(plugin.cmd_forget_event, "send", AsyncMock()):
            await plugin.handle_events(self.bot, self.event)
            self.assertIn(identity[:8], show.call_args.args[0])
            self.assertIn("source-r", show.call_args.args[0])
            await plugin.handle_forget_event(self.bot, self.event, Message(identity[:8]))
        self.assertEqual(await self.store.list(self.key, "ling"), [])

    async def test_admin_event_endpoints_require_auth_and_scope_deletion(self):
        identity = await self.add()
        app = FastAPI()
        admin.register_admin(app, lambda: self.sessions)
        headers = {"Authorization": "Bearer " + admin.admin_token(self.sessions)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42)), base_url="http://127.0.0.1") as client:
            params = {"session_key": self.key, "character": "ling"}
            self.assertEqual((await client.get("/manage/api/events", params=params)).status_code, 401)
            response = await client.get("/manage/api/events", params=params, headers=headers)
            self.assertEqual(response.json()[0]["id"], identity)
            self.assertEqual((await client.post("/manage/api/events/forget", headers=headers, json={**params, "character": "amelia", "id": identity})).status_code, 404)
            self.assertEqual((await client.post("/manage/api/events/forget", headers=headers, json={**params, "id": identity})).status_code, 200)
        self.assertEqual(await self.store.list(self.key, "ling"), [])


class ExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_json_only_proposes_memory_does_not_execute_tools(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"items": [proposal("我明天要去面试")]})))], usage=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm, "get_client", return_value=client):
            result = await memory.extract("我明天要去面试", [])
        self.assertEqual(result[0]["quote"], "我明天要去面试")
        self.assertEqual(create.await_count, 1)
        self.assertNotIn("tools", create.call_args.kwargs)

    async def test_invalid_or_truncated_json_is_ignored(self):
        for text, finish in [("not json", "stop"), ('{"items": {}}', "stop"), ('{"items": []}', "length")]:
            create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))], usage=None))
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with patch.object(llm, "get_client", return_value=client):
                self.assertEqual(await memory.extract("我明天要去面试", []), [])

    async def test_extractor_timeout_is_bounded(self):
        async def blocked(**kwargs):
            await asyncio.sleep(30)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=blocked)))
        with patch.object(llm, "get_client", return_value=client), patch.dict(os.environ, {"EVENT_MEMORY_TIMEOUT": "1"}):
            self.assertEqual(await asyncio.wait_for(memory.extract("我明天要去面试", []), 3), [])


class NaturalDialogueTests(unittest.TestCase):
    def test_large_memory_context_is_bounded_valid_json_without_mutating_saved_data(self):
        context = {"memories": ["偏" * 300 for _ in range(30)], "summary": "摘" * 4000,
                   "events": [{"statement": "事" * 300, "source_run_id": "x" * 32} for _ in range(6)]}
        packed = llm.memory_payload(context)
        self.assertLessEqual(len(packed), 14000)
        decoded = json.loads(packed)
        self.assertEqual(len(decoded["events"]), 6)
        self.assertEqual(len(context["summary"]), 4000)

    def test_goodnight_closes_but_explicit_request_takes_precedence(self):
        self.assertIn("不追问", dialogue_context("我先睡了，晚安"))
        self.assertNotIn("用户正在收尾", dialogue_context("晚安，能不能发张自拍？"))

    def test_acknowledgement_can_end_naturally(self):
        self.assertIn("允许话题停在这里", dialogue_context("嗯，好".replace("，", "")))

    def test_character_prompts_keep_distinct_persona_and_no_forced_new_topic(self):
        for card in (tavern_manager.cards["ling"], tavern_manager.cards["amelia"]):
            prompt = tavern_manager.build_system_prompt(card)
            self.assertIn(card.name, prompt)
            self.assertNotIn("必须主动依据当前人设抛出新话题", prompt)
            self.assertIn("用户说晚安", prompt)

    def test_relative_dates_and_corrections_do_not_invent_weekday_dates(self):
        now = datetime(2026, 12, 31, 15).astimezone().timestamp()
        self.assertEqual(memory.event_day("我明天面试", now), "2027-01-01")
        self.assertEqual(memory.event_day("不是后天，是明天面试", now), "2027-01-01")
        self.assertEqual(memory.event_day("改成2027年1月5日面试", now), "2027-01-05")
        self.assertIsNone(memory.event_day("我周六要面试", now))
        self.assertIsNone(memory.event_day("我明天和后天都要面试", now))
