"""Regressions for the 2026-10 review: CQ injection, fail-safe configs, delivery bookkeeping."""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from nonebot.adapters.onebot.v11 import Message, MessageSegment

import test_upgrade
from test_upgrade import Event, plugin
from src.plugins.chat_and_draw import admin, config_errors, delivery, runtime
from src.plugins.chat_and_draw.group_chat import GroupChat
from src.plugins.chat_and_draw.participation_config import managed
from src.plugins.chat_and_draw.qq_text import contains_cq_code
from src.plugins.chat_and_draw.sessions import Session

CQ = "[CQ:image,file=http://127.0.0.1:6099/secret]"


def sent_payload(mock):
    return mock.await_args.args[0]


class CqInjectionTests(test_upgrade.UpgradeFixture):
    def test_detects_cq_codes_loosely(self):
        for text in (CQ, "[cq:at,qq=all]", "前缀 [ CQ :face,id=1]"):
            self.assertTrue(contains_cq_code(text), text)
        for text in ("我喜欢[方括号]", "CQ:没有括号", None):
            self.assertFalse(contains_cq_code(text), text)

    async def test_remember_rejects_cq_codes(self):
        with patch.object(plugin.cmd_remember, "send", AsyncMock()) as send:
            await plugin.handle_remember(self.bot, self.event, Message(MessageSegment.text(CQ)))
        self.assertEqual((await self.store.load(self.key)).memories, [])
        self.assertIn("消息码", sent_payload(send))

    async def test_memory_echo_is_a_text_segment_even_for_legacy_stored_cq(self):
        # Records saved before the fix may already contain CQ text; echoing must keep it literal.
        await self.store.save(Session(self.key, character="ling", memories=[CQ], summary="[CQ:at,qq=all]"))
        with patch.object(plugin.cmd_memory, "send", AsyncMock()) as send:
            await plugin.handle_memory(self.bot, self.event)
        payload = sent_payload(send)
        self.assertIsInstance(payload, MessageSegment)
        self.assertEqual(payload.type, "text")
        self.assertEqual([s.type for s in Message(payload)], ["text"])
        self.assertIn(CQ, payload.data["text"])


class AdminMemoryCqTests(test_upgrade.UpgradeFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.store.save(Session(self.key, character="ling", memories=["old"]))
        app = FastAPI()
        admin.register_admin(app, lambda: self.store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)),
                                        base_url="http://127.0.0.1")
        self.headers = {"Authorization": "Bearer " + admin.admin_token(self.store)}

    async def asyncTearDown(self):
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_memory_put_rejects_cq_codes(self):
        for body in ({"memories": [CQ]}, {"memories": ["ok"], "summary": CQ}):
            response = await self.client.put("/manage/api/memory", headers=self.headers,
                                             json={"session_key": self.key, "character": "ling", **body})
            self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.store.load(self.key)).memories, ["old"])

    async def test_overview_reports_unreadable_config(self):
        config_errors.report("participation", "/x/participation.json", ValueError("bad"))
        try:
            data = (await self.client.get("/manage/api/overview", headers=self.headers)).json()
            self.assertIn("participation", data["config_errors"])
        finally:
            config_errors.clear("participation")


class PersonaConfigOutageTests(test_upgrade.UpgradeFixture):
    async def test_missing_or_broken_persona_file_does_not_break_load_or_unbind(self):
        persona = "persona-12345678-1234-1234-1234-123456789012"
        bound = Session(self.key, bot_id="999", user_id="123", character=persona,
                        persona_binding_target=persona, persona_binding_previous="ling")
        await self.store.save(bound)
        broken = self.root / "personas.json"
        for state in ("missing", "broken"):
            if state == "broken":
                broken.write_text("{broken", encoding="utf-8")
            with self.subTest(state=state), patch.dict(os.environ, BOT_PERSONA_CONFIG_FILE=str(broken)):
                loaded = await self.store.load(self.key)
                self.assertEqual(loaded.character, persona)
                self.assertEqual(loaded.persona_binding_target, persona)
                self.assertIn("persona", config_errors.current())
                plain = await self.store.load(json.dumps(["999", "private", "", "456"]))
                self.assertNotEqual(plain.character, persona)
        config_errors.clear("persona")


class GroupFollowupCapTests(test_upgrade.UpgradeFixture):
    async def test_zero_followup_cap_blocks_without_crashing(self):
        env = dict(BOT_PARTICIPATION_CONFIG_FILE="", GROUP_CHAT_ENABLED="true", GROUP_CHAT_FOLLOWUP_ENABLED="true",
                   GROUP_CHAT_GROUPS="", GROUP_CHAT_FOLLOWUP_WINDOW="300", GROUP_CHAT_FOLLOWUP_MAX_ROUNDS="0",
                   GROUP_CHAT_FOLLOWUP_COOLDOWN="0")
        with patch.dict(os.environ, env):
            groups = GroupChat(self.root / "group.db")
            room = groups.room(SimpleNamespace(self_id="999"), Event(group=1))
            self.assertIsNone(groups.reserve(room, "followup", 100000.0, participant="123"))


class ManagedConfigVisibilityTests(test_upgrade.UpgradeFixture):
    def test_broken_participation_file_fails_closed_and_is_reported(self):
        path = self.root / "participation.json"
        path.write_text("{not json", encoding="utf-8")
        with patch.dict(os.environ, BOT_PARTICIPATION_CONFIG_FILE=str(path)):
            values = managed()
        self.assertFalse(values["GROUP_CHAT_ENABLED"])
        self.assertIn("participation", config_errors.current())
        config_errors.clear("participation")


class DeliveryBookkeepingTests(test_upgrade.UpgradeFixture):
    async def test_bookkeeping_failure_after_send_is_not_a_send_failure(self):
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": 5}))
        with patch.object(delivery, "record", AsyncMock(side_effect=RuntimeError("audit down"))):
            async with runtime.operation(self.runtime, self.key, "ling", "test", 1):
                self.assertTrue(await delivery.try_send(matcher, MessageSegment.text("hi"), "text-1"))
        matcher.send.assert_awaited_once()

    async def test_real_send_failure_still_returns_false(self):
        matcher = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("network")))
        self.assertFalse(await delivery.try_send(matcher, MessageSegment.text("hi"), "text-1"))
