"""Upgrade regressions use disposable databases, fake bots and local ASGI only."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from PIL import Image
from nonebot.adapters.onebot.v11 import Message

from test_bot import Event, plugin, llm
from src.plugins.chat_and_draw import admin, jobs, runtime, stickers
from src.plugins.chat_and_draw.sessions import Session, SessionStore, session_key
from src.plugins.chat_and_draw.sticker_library import StickerLibrary


def png(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, "PNG")
    return buffer.getvalue()


class UpgradeFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "sessions.db")
        self.runtime = runtime.RuntimeStore(self.store.path)
        self.bot = SimpleNamespace(self_id="999", send=AsyncMock(return_value={"message_id": 71}))
        self.event = Event()
        self.key = session_key(self.bot, self.event)
        self.patches = [patch.object(plugin, "store", self.store),
                        patch.object(runtime, "ARTIFACT_DIR", self.root / "artifacts"),
                        patch.object(admin, "ARTIFACT_DIR", self.root / "artifacts")]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await jobs.stop_jobs()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()


class UpgradeTests(UpgradeFixture):
    async def test_legacy_migration_preserves_history_and_is_idempotent(self):
        self.store.path.parent.mkdir(exist_ok=True)
        data = {"key": self.key, "character": "ling", "history": [{"role": "user", "content": "旧记录"}]}
        with sqlite3.connect(self.store.path) as conn:
            conn.execute("CREATE TABLE sessions(key TEXT PRIMARY KEY,data TEXT NOT NULL)")
            conn.execute("INSERT INTO sessions VALUES (?,?)", (self.key, json.dumps(data)))
        await self.store.migrate_legacy()
        await self.store.migrate_legacy()
        restored = await self.store.load(self.key)
        self.assertEqual(restored.history, data["history"])
        with sqlite3.connect(self.store.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM role_states").fetchone()[0], 1)

    async def test_switch_round_trip_restores_all_role_fields_after_restart(self):
        session = Session(self.key, character="ling", memories=["喜欢海边"], summary="初次相遇",
                          last_image_prompt="beach", history=[{"role": "user", "content": "你好玲"}])
        await self.store.save(session)
        other, restored = await self.store.switch_character(session, "amelia", "hello")
        self.assertFalse(restored)
        self.assertEqual(other.memories, [])
        other.memories = ["喜欢咖啡"]
        await self.store.save(other)
        original, restored = await self.store.switch_character(other, "ling")
        self.assertTrue(restored)
        original = await SessionStore(self.store.path).load(self.key)
        self.assertEqual(original.memories, ["喜欢海边"])
        self.assertEqual(original.summary, "初次相遇")
        self.assertEqual(original.last_image_prompt, "beach")
        self.assertEqual(original.history[0]["content"], "你好玲")
        same, _ = await self.store.switch_character(original, "ling")
        self.assertEqual(same.history, original.history)

    async def test_reset_only_clears_current_role_and_keeps_other_role(self):
        session = Session(self.key, character="ling", memories=["old"], summary="old")
        await self.store.save(session)
        session, _ = await self.store.switch_character(session, "amelia")
        session.memories = ["other"]
        await self.store.save(session)
        with patch.object(plugin.cmd_reset, "send", AsyncMock()):
            await plugin.handle_reset(self.bot, self.event)
        cleared = await self.store.load(self.key)
        self.assertEqual(cleared.memories, [])
        self.assertEqual(cleared.summary, "")
        original, _ = await self.store.switch_character(cleared, "ling")
        self.assertEqual(original.memories, ["old"])

    async def test_remember_deduplicates_and_forget_removes(self):
        with patch.object(plugin.cmd_remember, "send", AsyncMock()), patch.object(plugin.cmd_forget, "send", AsyncMock()):
            await plugin.handle_remember(self.bot, self.event, Message("喜欢白裙"))
            await plugin.handle_remember(self.bot, self.event, Message("喜欢白裙"))
            self.assertEqual((await self.store.load(self.key)).memories, ["喜欢白裙"])
            await plugin.handle_forget(self.bot, self.event, Message("1"))
            self.assertEqual((await self.store.load(self.key)).memories, [])

    async def test_memory_is_passed_to_model_and_duplicate_event_is_ignored(self):
        await self.store.save(Session(self.key, character="ling", memories=["喜欢海边"], summary="上次去过海边"))
        self.event.message_id = 777
        with patch.object(plugin, "get_chat_response", AsyncMock(return_value=("你好", None, None, False))) as model, \
             patch.object(plugin.chat_handler, "send", AsyncMock(return_value={"message_id": 123})):
            await plugin.handle_message(self.bot, self.event)
            await plugin.handle_message(self.bot, self.event)
        model.assert_awaited_once()
        self.assertEqual(model.call_args.kwargs["memory_context"]["memories"], ["喜欢海边"])
        rows = await self.runtime.list_rows("messages")
        self.assertEqual(sum(row["direction"] == "in" for row in rows), 1)
        self.assertEqual(sum(row["direction"] == "out" for row in rows), 1)

    async def test_draw_returns_before_gpu_and_switch_keeps_original_role_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(tags):
            entered.set()
            await release.wait()
            return png()
        with patch.object(jobs, "generate_image", generate):
            await asyncio.wait_for(plugin.handle_draw(self.bot, self.event, Message("beach")), 3)
            await asyncio.wait_for(entered.wait(), 3)
            session = await self.store.load(self.key)
            session, _ = await self.store.switch_character(session, "amelia")
            self.assertFalse(self.bot.send.called)
            pending = list(jobs._jobs)
            release.set()
            await asyncio.wait_for(asyncio.gather(*pending), 3)
        active = await self.store.load(self.key)
        self.assertEqual(active.character, "amelia")
        self.assertIsNone(active.last_image_prompt)
        old, _ = await self.store.switch_character(active, "ling")
        self.assertEqual(old.last_image_prompt, "beach")
        self.bot.send.assert_awaited_once()
        self.assertIs(self.bot.send.call_args.args[0], self.event)
        task = (await self.runtime.list_rows("tasks"))[0]
        self.assertEqual(task["status"], "delivered")
        self.assertEqual(Path(task["artifact"]).read_bytes(), png())

    async def test_failed_image_send_keeps_artifact_and_task_for_manual_recovery(self):
        async def send(event, message):
            if getattr(message, "type", "") == "image":
                raise RuntimeError("fake send failure")
        self.bot.send.side_effect = send
        with patch.object(jobs, "generate_image", AsyncMock(return_value=png())):
            await plugin.handle_draw(self.bot, self.event, Message("solo"))
            await asyncio.gather(*list(jobs._jobs))
        task = (await self.runtime.list_rows("tasks"))[0]
        self.assertEqual(task["status"], "delivery_failed")
        self.assertTrue(Path(task["artifact"]).is_file())
        self.assertEqual((await self.runtime.list_rows("runs"))[0]["status"], "partial")

    async def test_restart_interrupts_stale_tasks_without_requeue(self):
        async with runtime.operation(self.runtime, self.key, "ling", "draw") as scope:
            identity = await self.runtime.create_task(scope, "solo")
            await self.runtime.task_update(identity, prompt_id="remote-123", status="running")
        await jobs.recover_jobs(self.runtime)
        task = await self.runtime.task(identity)
        self.assertEqual(task["status"], "interrupted")
        self.assertEqual(task["prompt_id"], "remote-123")
        self.assertEqual(len(jobs._jobs), 0)

    async def test_failed_task_marks_run_partial_even_without_service_events(self):
        async with runtime.operation(self.runtime, self.key, "ling", "draw") as scope:
            identity = await self.runtime.create_task(scope, "solo")
            await self.runtime.task_update(identity, status="failed", error="GENERATION_FAILED")
        self.assertEqual((await self.runtime.run_detail(scope.run_id))["run"]["status"], "partial")

    async def test_queue_limit_rejects_extra_task(self):
        gate = asyncio.Event()
        async def generate(tags):
            await gate.wait()
            return None
        await self.store.save(Session(self.key, character="ling"))
        with patch.dict("os.environ", {"DRAW_QUEUE_LIMIT": "1"}), patch.object(jobs, "generate_image", generate):
            async with runtime.operation(self.runtime, self.key, "ling", "draw"):
                self.assertTrue(await jobs.enqueue_draw(self.store, self.bot, self.event, "one"))
                self.assertFalse(await jobs.enqueue_draw(self.store, self.bot, self.event, "two"))
        self.assertEqual(len(await self.runtime.list_rows("tasks")), 1)

    async def test_queue_limit_includes_concurrent_database_reservations(self):
        gate = asyncio.Event()
        async def generate(tags):
            await gate.wait()
            return None
        async def submit(index):
            async with runtime.operation(self.runtime, self.key + str(index), "ling", "draw"):
                return await jobs.enqueue_draw(self.store, self.bot, self.event, str(index))
        with patch.dict("os.environ", {"DRAW_QUEUE_LIMIT": "1"}), patch.object(jobs, "generate_image", generate):
            results = await asyncio.gather(*(submit(i) for i in range(5)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(await self.runtime.list_rows("tasks")), 1)


class AdminTests(UpgradeFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.store.save(Session(self.key, character="ling", memories=["old"]))
        self.library = StickerLibrary(self.root / "stickers")
        self.library_patch = patch.object(admin, "library", self.library)
        self.library_patch.start()
        app = FastAPI()
        admin.register_admin(app, lambda: self.store)
        self.app = app
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)), base_url="http://127.0.0.1")
        self.headers = {"Authorization": "Bearer " + admin.admin_token(self.store)}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.library_patch.stop()
        await super().asyncTearDown()

    async def test_api_rejects_unauthenticated_and_cross_origin_requests(self):
        self.assertEqual((await self.client.get("/manage/api/overview")).status_code, 401)
        self.assertEqual((await self.client.get("/manage/api/overview", headers={**self.headers, "Origin": "http://evil.example"})).status_code, 403)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("192.0.2.1", 5000)), base_url="http://127.0.0.1") as remote:
            self.assertEqual((await remote.get("/manage/api/overview", headers=self.headers)).status_code, 403)
        self.assertEqual((await self.client.get("/manage/api/overview", headers=self.headers)).status_code, 200)
        self.assertEqual((await self.client.get("/manage", headers={"Host": "evil.example"})).status_code, 403)

    async def test_management_page_and_memory_edit_survive_reload(self):
        page = await self.client.get("/manage")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Content-Security-Policy", page.headers)
        edited = await self.client.put("/manage/api/memory", headers=self.headers,
                                       json={"session_key": self.key, "character": "ling", "memories": ["new"], "summary": "brief"})
        self.assertEqual(edited.status_code, 200)
        loaded = await self.store.load(self.key)
        self.assertEqual((loaded.memories, loaded.summary), (["new"], "brief"))
        bad = await self.client.put("/manage/api/memory", headers=self.headers,
                                    json={"session_key": self.key, "character": "ling", "memories": ["x" * 301]})
        self.assertEqual(bad.status_code, 400)

    async def test_usage_and_message_detail_are_retrievable(self):
        async with runtime.operation(self.runtime, self.key, "ling", "test", 99) as scope:
            await runtime.record("usage", "completed", input_tokens=10, output_tokens=5, total_tokens=15)
        response = await self.client.get("/manage/api/overview", headers=self.headers)
        self.assertEqual(response.json()["usage"]["total_tokens"], 15)
        detail = await self.client.get("/manage/api/runs/" + scope.run_id, headers=self.headers)
        self.assertEqual(detail.json()["messages"][0]["content"], "test")

    async def test_resend_uses_saved_image_original_destination_without_generation(self):
        async with runtime.operation(self.runtime, self.key, "ling", "draw") as scope:
            identity = await self.runtime.create_task(scope, "solo")
        path = self.root / "artifacts" / (identity + ".png")
        path.parent.mkdir()
        path.write_bytes(png())
        await self.runtime.task_update(identity, artifact=str(path), status="delivery_failed")
        bot = SimpleNamespace(send_private_msg=AsyncMock(return_value={"message_id": 4}))
        with patch.object(admin, "get_bot", return_value=bot), patch.object(jobs, "generate_image", AsyncMock()) as generate:
            response = await self.client.post(f"/manage/api/tasks/{identity}/resend", headers=self.headers)
        self.assertEqual(response.json(), {"ok": True})
        generate.assert_not_awaited()
        self.assertEqual(bot.send_private_msg.call_args.kwargs["user_id"], 123)
        self.assertEqual((await self.runtime.task(identity))["status"], "delivered")
        image = await self.client.get(f"/manage/api/tasks/{identity}/image", headers=self.headers)
        self.assertEqual(image.content, png())

    async def test_artifact_path_outside_archive_is_refused(self):
        async with runtime.operation(self.runtime, self.key, "ling", "draw") as scope:
            identity = await self.runtime.create_task(scope, "solo")
        path = self.root / "outside.png"
        path.write_bytes(png())
        await self.runtime.task_update(identity, artifact=str(path))
        self.assertEqual((await self.client.get(f"/manage/api/tasks/{identity}/image", headers=self.headers)).status_code, 404)

    async def test_sticker_upload_download_delete_and_reject_invalid_input(self):
        response = await self.client.post("/manage/api/stickers", headers=self.headers,
                                          json={"base64": base64.b64encode(png()).decode(), "name": "开心", "emotions": ["happy"]})
        self.assertEqual(response.status_code, 200)
        identity = response.json()["id"]
        image = await self.client.get(f"/manage/api/stickers/{identity}/image", headers=self.headers)
        self.assertEqual(image.content, png())
        invalid = await self.client.post("/manage/api/stickers", headers=self.headers, json={"base64": "bad"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual((await self.client.delete(f"/manage/api/stickers/{identity}", headers=self.headers)).status_code, 200)
        self.assertEqual(self.library.list(), [])


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = StickerLibrary(Path(self.temp.name))

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_role_scenario_avoid_and_selection_persistence(self):
        self.library.add(png("red"), {"emotions": ["happy"], "characters": ["ling"], "situations": ["海边"], "avoid": ["难过"]})
        self.library.add(png("blue"), {"emotions": ["happy"]})
        self.assertEqual(self.library.select("happy", "ling", "去海边"), png("red"))
        self.assertEqual(self.library.select("happy", "amelia", "去海边"), png("blue"))
        self.assertEqual(self.library.select("happy", "ling", "难过"), png("blue"))
        self.assertEqual(StickerLibrary(self.library.root).list()[0]["selected_count"], 1)

    async def test_equal_candidates_do_not_repeat_immediately(self):
        for color in ("red", "blue", "green"):
            self.library.add(png(color), {"emotions": ["happy"]})
        previous = self.library.select("happy")
        for _ in range(5):
            selected = self.library.select("happy")
            self.assertNotEqual(previous, selected)
            previous = selected

    async def test_local_precedes_external_even_when_external_disabled(self):
        self.library.add(png(), {"emotions": ["happy"]})
        with patch("src.plugins.chat_and_draw.sticker_library.library", self.library), patch.dict("os.environ", {"STICKER_ENABLED": "false"}):
            self.assertEqual(await stickers.get_sticker("happy", character="ling"), png())

    async def test_invalid_files_categories_and_path_traversal_rejected(self):
        for data, metadata in [(b"not an image", {"emotions": ["happy"]}), (png(), {"emotions": ["not-an-emotion"]}), (b"x" * (5 * 1024 * 1024 + 1), {"emotions": ["happy"]})]:
            with self.assertRaises((ValueError, OSError)):
                self.library.add(data, metadata)
        with self.assertRaises(ValueError):
            self.library.asset("../../secret")
