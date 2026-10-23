"""No real QQ sends: exercise persisted limits and user-message races."""
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_bot import plugin, Event, llm, delivery_module
from src.plugins.chat_and_draw.sessions import Session, SessionStore, session_key
from src.plugins.chat_and_draw.proactive import eligible, next_time, in_window


class ProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.tmp.name) / 'db.sqlite3')
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone(timedelta(hours=8)))
        self.session = Session('one', bot_id='999', user_id='123', proactive=True,
                               next_proactive=1, last_interaction=self.now.timestamp()-4*3600)
        await self.store.save(self.session)
        self.bot = SimpleNamespace(send_private_msg=AsyncMock())
        self.env = patch.dict(os.environ, PROACTIVE_MODE='windows', PROACTIVE_IDLE_HOURS='3', PROACTIVE_DAILY_LIMIT='2')
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    async def cycle(self, model=None):
        model = model or AsyncMock(return_value=('hello', None, None, False))
        with patch.object(plugin, 'store', self.store), patch.object(plugin, 'get_bot', return_value=self.bot), patch.object(plugin, 'get_chat_response', model):
            await plugin.run_proactive_once(self.now)
        return model

    async def test_recent_chat_does_not_call_model(self):
        self.session.last_interaction = self.now.timestamp()-60
        await self.store.save(self.session)
        model = await self.cycle()
        model.assert_not_awaited()

    async def test_unanswered_greeting_survives_restart(self):
        await self.cycle()
        session = await SessionStore(self.store.path).load('one')
        self.assertTrue(session.proactive_unanswered)
        session.next_proactive = 1
        await self.store.save(session)
        model = await self.cycle()
        model.assert_not_awaited()
        self.bot.send_private_msg.assert_awaited_once()

    async def test_daily_budget_and_next_day(self):
        self.session.proactive_day = self.now.date().isoformat()
        self.session.proactive_attempts = 2
        await self.store.save(self.session)
        (await self.cycle()).assert_not_awaited()
        self.assertTrue(eligible(self.session, self.now+timedelta(days=1)))

    async def test_new_human_message_during_generation_cancels_send(self):
        async def generate(*args, **kwargs):
            session = await self.store.load('one')
            session.touch()
            await self.store.save(session)
            return ('hello', None, None, False)
        await self.cycle(AsyncMock(side_effect=generate))
        self.bot.send_private_msg.assert_not_awaited()

    async def test_failure_is_silent_but_counts_budget(self):
        await self.cycle(AsyncMock(return_value=('', None, None, False)))
        self.bot.send_private_msg.assert_not_awaited()
        session = await self.store.load('one')
        self.assertEqual(session.proactive_attempts, 1)
        self.assertFalse(session.proactive_unanswered)

    def test_random_next_time_stays_in_local_window_after_idle(self):
        for _ in range(50):
            stamp = next_time(self.now, self.now.timestamp())
            self.assertGreaterEqual(stamp, self.now.timestamp()+3*3600)
            self.assertTrue(in_window(datetime.fromtimestamp(stamp, self.now.tzinfo)))

    async def test_service_notice_not_remembered_or_synthesized(self):
        bot = SimpleNamespace(self_id='999')
        result = delivery_module.DeliveryResult(text_sent=True)
        with patch.object(plugin, 'store', self.store), patch.object(plugin, 'get_chat_response', AsyncMock(return_value=(llm.ServiceNotice('service unavailable'), None, None, False))), patch.object(plugin, 'send_reply', AsyncMock(return_value=result)) as send, patch.object(plugin, 'prepare_event_memory', AsyncMock(return_value=[])):
            await plugin.handle_message(bot, Event('发条语音'))
        session = await self.store.load(session_key(bot, Event()))
        self.assertFalse(any(m.get('content') == 'service unavailable' for m in session.history))
        self.assertFalse(send.await_args.args[3])

    def test_old_service_notices_excluded_from_model_history(self):
        result = llm.build_messages('system', [{'role': 'assistant', 'content': '这次回复等得有点久，模型服务暂时没有响应，请稍后再试。'}, {'role': 'user', 'content': 'hi'}])
        self.assertEqual(len(result), 2)

    async def test_new_private_session_defaults_on(self):
        session = await self.store.load('new')
        self.assertTrue(session.proactive)
        session.touch()
        self.assertGreater(session.next_proactive, session.last_interaction)

    async def test_explicit_off_survives_reload_and_migration(self):
        self.session.proactive = False
        await self.store.save(self.session)
        await self.store.migrate_legacy()
        self.assertFalse((await self.store.load('one')).proactive)

    async def test_old_default_off_migrates_once_and_gets_schedule(self):
        import json
        with self.store.database.transaction() as conn:
            conn.execute('insert into sessions values (?,?)', ('legacy', json.dumps({'key':'legacy','private':True,'bot_id':'999','user_id':'123','proactive':False})))
        await self.store.migrate_legacy()
        session = await self.store.load('legacy')
        self.assertTrue(session.proactive)
        self.assertGreater(session.next_proactive, 0)
        session.proactive = False
        await self.store.save(session)
        await self.store.migrate_legacy()
        self.assertFalse((await self.store.load('legacy')).proactive)
