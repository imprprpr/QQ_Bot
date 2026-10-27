"""No real QQ sends: exercise persisted limits and user-message races."""
import os
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_bot import plugin, Event, llm, delivery_module
from src.plugins.chat_and_draw.sessions import Session, SessionStore, session_key
from src.plugins.chat_and_draw.proactive import eligible, next_time, in_window, choose_topic, opener_prompt, opener_text


class ProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.tmp.name) / 'db.sqlite3')
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone(timedelta(hours=8)))
        self.session = Session('one', bot_id='999', user_id='123', proactive=True,
                               next_proactive=1, last_interaction=self.now.timestamp()-4*3600)
        await self.store.save(self.session)
        self.bot = SimpleNamespace(send_private_msg=AsyncMock())
        self.env = patch.dict(os.environ, PROACTIVE_MODE='windows', PROACTIVE_IDLE_HOURS='3', PROACTIVE_DAILY_LIMIT='2', PROACTIVE_COOLDOWN_HOURS='6', PROACTIVE_GLOBAL_DAILY_LIMIT='6')
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

    async def test_topic_and_cooldown_survive_restart_even_after_failed_generation(self):
        await self.cycle(AsyncMock(return_value=('',None,None,False)))
        stored=await SessionStore(self.store.path).load('one')
        self.assertEqual(stored.proactive_last_attempt,self.now.timestamp())
        self.assertEqual(len(stored.proactive_topics),1)
        stored.last_interaction=self.now.timestamp()-4*3600
        self.assertFalse(eligible(stored,self.now+timedelta(hours=1)))
        self.assertTrue(eligible(stored,self.now+timedelta(hours=7)))

    async def test_user_reply_does_not_reset_proactive_cooldown(self):
        self.session.proactive_last_attempt=self.now.timestamp()
        later=self.now+timedelta(hours=1)
        with patch('src.plugins.chat_and_draw.sessions.time.time',return_value=later.timestamp()), patch('src.plugins.chat_and_draw.proactive.local_now',return_value=later):
            self.session.touch()
        self.assertGreaterEqual(self.session.next_proactive,self.now.timestamp()+6*3600)

    async def test_global_reservation_is_atomic_across_recipients(self):
        second=Session('two',bot_id='999',user_id='456',proactive=True,last_interaction=self.session.last_interaction,next_proactive=1)
        await self.store.save(second)
        reserved=await asyncio.gather(*(self.store.reserve_proactive(s,self.now.date().isoformat(),self.now.timestamp(),'interest',1) for s in [self.session,second]))
        self.assertEqual(sum(s is not None for s in reserved),1)
        states=[await self.store.load(key) for key in ['one','two']]
        self.assertEqual(sum(s.proactive_attempts for s in states),1)

    async def test_one_attempt_per_cycle_even_if_generated_text_is_skipped(self):
        await self.store.save(Session('two',bot_id='999',user_id='456',proactive=True,last_interaction=self.session.last_interaction,next_proactive=1))
        model=await self.cycle(AsyncMock(return_value=('[不打扰]',None,None,False)))
        self.assertEqual(model.await_count,1)
        self.bot.send_private_msg.assert_not_awaited()

    async def test_global_budget_blocks_model_call(self):
        with patch.dict(os.environ,PROACTIVE_GLOBAL_DAILY_LIMIT='0'):
            model=await self.cycle()
        model.assert_not_awaited()

    async def test_duplicate_and_operational_notice_never_sent(self):
        self.session.proactive_last_text='老大，我刚想到一个有趣的小问题。'
        await self.store.save(self.session)
        await self.cycle(AsyncMock(return_value=('老大！我刚想到一个有趣的小问题。',None,None,False)))
        self.bot.send_private_msg.assert_not_awaited()
        stored=await self.store.load('one')
        self.assertFalse(stored.proactive_unanswered)
        self.assertEqual(stored.proactive_attempts,1)

    async def test_notice_during_proactive_request_stays_silent(self):
        await self.cycle(AsyncMock(return_value=(llm.ServiceNotice('模型正在限流'),None,None,False)))
        self.bot.send_private_msg.assert_not_awaited()

    async def test_selected_topic_not_greeting_and_used_with_current_persona(self):
        model=await self.cycle()
        prompt=model.await_args.args[0][-1]['content']
        self.assertIn('主动搭话任务',prompt)
        self.assertIn('不劝睡',prompt)
        self.assertIn('当前角色',prompt)
        self.assertIn('最多70字',prompt)
        stored=await self.store.load('one')
        self.assertEqual(stored.proactive_last_text,'hello')

    def test_topic_rotation_avoids_recent_three(self):
        self.session.proactive_topics=['interest','opinion','excuse']
        self.assertEqual(choose_topic(self.session),'choice')
        self.assertIn('小选择',opener_prompt(self.session,'choice'))

    def test_validation_rejects_templates_tools_and_long_text_without_cutting(self):
        for text in ['[不打扰]','[不参与]','不打扰。','【不参与】','<voice>','[CQ:face,id=1]','https://example.com','很长的句子'*20]:
            self.assertEqual(opener_text(text),'')
        self.assertEqual(opener_text('**我有个小纠结。**'),'我有个小纠结。')

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


class ManagedProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await ProactiveTests.asyncSetUp(self)
        import json
        from src.plugins.chat_and_draw.proactive_config import settings
        self.file = Path(self.tmp.name) / 'policy.json'
        self.policy = {k:v for k,v in settings().items() if not k.startswith('_')}
        self.policy['windows'] = [[0,1440]]
        self.file.write_text(json.dumps(self.policy), encoding='utf-8')
        self.managed_env = patch.dict(os.environ, BOT_PROACTIVE_CONFIG_FILE=str(self.file))
        self.managed_env.start()

    async def asyncTearDown(self):
        self.managed_env.stop()
        await ProactiveTests.asyncTearDown(self)

    async def cycle(self, model=None):
        # Seed a matching revision so inherited eligibility tests remain due.
        from src.plugins.chat_and_draw.proactive_config import settings
        session = await self.store.load('one')
        session.proactive_schedule_revision = settings()['_revision']
        await self.store.save(session)
        return await ProactiveTests.cycle(self, model)

    async def test_hot_reload_and_fail_closed(self):
        import json
        from src.plugins.chat_and_draw.proactive_config import settings, validate
        before = settings()['_revision']
        self.file.write_text(json.dumps({**self.policy, 'enabled':False}), encoding='utf-8')
        self.assertFalse(settings()['enabled'])
        self.assertNotEqual(before, settings()['_revision'])
        self.file.write_text('{broken', encoding='utf-8')
        self.assertFalse(settings()['enabled'])
        self.file.unlink()
        self.assertFalse(settings()['enabled'])
        for change in ({'daily_limit':4},{'cooldown_hours':2},{'windows':[[0,60],[59,90]]},{'enabled':'true'},{'windows':[None]}):
            with self.assertRaises(ValueError):
                validate({**self.policy, **change})

    async def test_reschedule_preserves_roles_optout_and_quotas(self):
        from src.plugins.chat_and_draw.proactive_config import settings
        self.session.history = [{'role':'user','content':'preserve history'}]
        self.session.memories = ['preserve memory']
        self.session.proactive_attempts = 2
        self.session.proactive_unanswered = True
        await self.store.save(self.session)
        off = Session('off', proactive=False, next_proactive=0)
        await self.store.save(off)
        await self.store.reschedule_proactive(self.now, settings())
        loaded = await self.store.load('one')
        self.assertEqual(loaded.history, self.session.history)
        self.assertEqual(loaded.memories, self.session.memories)
        self.assertEqual(loaded.proactive_attempts,2)
        self.assertTrue(loaded.proactive_unanswered)
        self.assertGreater(loaded.next_proactive,self.now.timestamp())
        first=loaded.next_proactive
        await self.store.reschedule_proactive(self.now, settings())
        self.assertEqual((await self.store.load('one')).next_proactive,first)
        self.assertFalse((await self.store.load('off')).proactive)
        self.assertEqual((await self.store.load('off')).next_proactive,0)

    async def test_disable_during_generation_cancels_send(self):
        import json
        async def generate(*args,**kwargs):
            self.file.write_text(json.dumps({**self.policy,'enabled':False}),encoding='utf-8')
            return ('hello',None,None,False)
        await self.cycle(AsyncMock(side_effect=generate))
        self.bot.send_private_msg.assert_not_awaited()

    async def test_policy_change_reschedules_before_calling_model(self):
        model=AsyncMock()
        with patch.object(plugin,'store',self.store),patch.object(plugin,'get_chat_response',model):
            await plugin.run_proactive_once(self.now)
        model.assert_not_awaited()
        self.assertGreater((await self.store.load('one')).next_proactive,self.now.timestamp())
