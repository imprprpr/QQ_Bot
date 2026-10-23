"""Bot-to-bot safety and delivery regressions; all QQ/model calls are mocked."""
import asyncio
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_bot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from src.plugins.chat_and_draw.bot_interaction import BotInteractions
from src.plugins.chat_and_draw.sessions import SessionStore


class BotInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'state.db'
        self.policy = BotInteractions(self.path)
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.env = patch.dict(os.environ, {
            'BOT_INTERACTION_ENABLED':'true', 'BOT_INTERACTION_TARGETS':'456,789',
            'BOT_INTERACTION_GROUPS':'1', 'BOT_INTERACTION_MAX_ROUNDS':'2',
            'BOT_INTERACTION_COOLDOWN':'600', 'BOT_INTERACTION_SESSION_SECONDS':'180',
            'BOT_INTERACTION_REPLY_COOLDOWN':'5', 'BOT_INTERACTION_REPLY_PROBABILITY':'1',
            'BOT_INTERACTION_HOURLY_LIMIT':'6', 'BOT_INTERACTION_DAILY_LIMIT':'12',
            'BOT_INTERACTION_PROACTIVE_DAILY_LIMIT':'3', 'BOT_INTERACTION_PROACTIVE_PROBABILITY':'1',
            'BOT_INTERACTION_UTC_OFFSET':'8'})
        self.env.start()
        self.clock = patch('time.time', return_value=self.now)
        self.clock.start()
        self.bot = SimpleNamespace(self_id='999', send_group_msg=AsyncMock(return_value={'message_id':12}))
        self.app = test_bot.plugin
        self.store_patch = patch.object(self.app, 'store', SessionStore(self.path))
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop()
        self.clock.stop()
        self.env.stop()
        self.tmp.cleanup()

    def reserve(self, now=None, peer='456', kind='reactive', identity=None):
        return self.policy.reserve('999','1',peer,self.now if now is None else now,kind,identity,True)

    def event(self, text='哼，你又来了', group=1, user='456'):
        event = test_bot.Event(text, user=user, group=group, addressed=True)
        event.message_id = 17
        event.original_message = MessageSegment.at('999') + Message(text)
        return event

    async def test_empty_groups_or_targets_disable_everything_even_mentions(self):
        for key in ['BOT_INTERACTION_GROUPS','BOT_INTERACTION_TARGETS']:
            with patch.dict(os.environ,{key:''}):
                self.assertIsNone(await self.policy.select(self.bot,self.event()))

    async def test_disabled_or_unapproved_group_has_no_mention_fallback(self):
        with patch.dict(os.environ,BOT_INTERACTION_ENABLED='false'):
            self.assertFalse(await self.app.chat_addressed(self.bot,self.event(),{}))
        self.assertFalse(await self.app.chat_addressed(self.bot,self.event(group=2),{}))
        self.assertFalse(await self.app.addressed(self.event('/reset')))

    async def test_two_turn_limit_cannot_be_bypassed_by_at_or_restart(self):
        first=self.reserve(identity='1')
        self.policy.finish(first,True,self.now)
        second=self.reserve(self.now+10,identity='2')
        self.assertEqual(second['round'],2)
        self.policy.finish(second,True,self.now+10)
        reloaded=BotInteractions(self.path)
        self.assertIsNone(reloaded.reserve('999','1','456',self.now+20,'reactive','3',True))
        self.assertIsNone(reloaded.reserve('999','1','456',self.now+200,'reactive','4',True))
        self.assertIsNotNone(reloaded.reserve('999','1','456',self.now+781,'reactive','5',True))

    async def test_room_is_exclusive_and_reservation_is_atomic(self):
        results=await asyncio.gather(*(asyncio.to_thread(self.reserve) for _ in range(8)))
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertIsNone(self.reserve(peer='789'))

    async def test_duplicates_failed_send_and_inflight_do_not_start_loops(self):
        first=self.reserve(identity='same')
        self.assertIsNone(self.reserve(self.now+10,identity='other'))
        self.policy.finish(first,False,self.now)
        self.assertIsNone(self.reserve(self.now+20,identity='another'))
        self.assertIsNone(self.reserve(self.now+1000,identity='same'))

    async def test_reply_addressed_to_others_and_commands_are_ignored(self):
        event=self.event()
        event.original_message=MessageSegment.at('789')+Message('你好')
        self.assertIsNone(await self.policy.select(self.bot,event))
        self.assertIsNone(await self.policy.select(self.bot,self.event('/draw cat')))

    async def test_daily_and_hourly_api_budgets_survive_restarts(self):
        first=self.reserve()
        self.policy.finish(first,True,self.now)
        with patch.dict(os.environ,BOT_INTERACTION_DAILY_LIMIT='1'):
            self.assertIsNone(BotInteractions(self.path).reserve('999','1','456',self.now+1000,'reactive','2',True))
        with patch.dict(os.environ,BOT_INTERACTION_HOURLY_LIMIT='1'):
            self.assertIsNone(self.reserve(self.now+1000,identity='3'))

    async def test_proactive_limit_is_global_and_counts_attempts(self):
        with patch.dict(os.environ,BOT_INTERACTION_PROACTIVE_DAILY_LIMIT='1'):
            self.assertIsNotNone(self.reserve(kind='proactive'))
            self.assertIsNone(self.reserve(self.now+1000,peer='789',kind='proactive'))

    async def test_real_at_target_and_no_media_or_private_history(self):
        context=self.reserve()
        model=AsyncMock(return_value=('哼，算你还有点本事。','draw','happy',True))
        with patch.object(self.app,'get_chat_response',model), patch.object(self.app,'prepare_event_memory',AsyncMock()) as memory:
            await self.app.handle_bot_interaction(self.bot,context,'对方发言',17)
        memory.assert_not_awaited()
        self.bot.send_group_msg.assert_awaited_once()
        message=self.bot.send_group_msg.await_args.kwargs['message']
        self.assertEqual(message[0].type,'at')
        self.assertEqual(message[0].data['qq'],'456')
        self.assertEqual([seg.type for seg in message],['at','text'])
        self.assertTrue(model.await_args.kwargs['silent_failure'])
        self.assertEqual(model.await_args.kwargs['group_context']['mode'],'bot-interaction')
        self.app.store.initialize()
        with self.app.store.database.transaction() as conn:
            self.assertEqual(conn.execute('select count(*) from sessions').fetchone()[0],0)

    async def test_decline_or_ambiguous_send_never_retries(self):
        context=self.reserve()
        self.bot.send_group_msg.side_effect=TimeoutError()
        with patch.object(self.app,'get_chat_response',AsyncMock(return_value=('哼',None,None,False))):
            await self.app.handle_bot_interaction(self.bot,context)
        self.bot.send_group_msg.assert_awaited_once()
        self.assertIsNone(self.reserve(self.now+20))

    async def test_stale_or_disabled_configuration_cancels_pending_send(self):
        context=self.reserve()
        async def generate(*args,**kwargs):
            os.environ['BOT_INTERACTION_ENABLED']='false'
            return ('哼',None,None,False)
        with patch.object(self.app,'get_chat_response',AsyncMock(side_effect=generate)):
            await self.app.handle_bot_interaction(self.bot,context)
        self.bot.send_group_msg.assert_not_awaited()

    async def test_proactive_requires_recent_group_activity_and_daytime(self):
        model=AsyncMock(return_value=('哼，来比比谁更会接梗？',None,None,False))
        with patch.object(self.app,'get_bots',return_value={'999':self.bot}), patch.object(self.app,'get_chat_response',model):
            await self.app.run_bot_interaction_once(self.now)
            model.assert_not_awaited()
            room=self.policy.room('999','1')
            self.app.group_chat.add(room,'1','123:人类','聊聊游戏',self.now)
            await self.app.run_bot_interaction_once(self.now)
            self.bot.send_group_msg.assert_awaited_once()
        self.assertFalse(self.policy.proactive_window(self.now-12*3600))

    async def test_tsundere_prompt_and_small_output_budget(self):
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='哼'))],usage=None))
        with patch.object(test_bot.llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            await test_bot.llm.get_chat_response([{'role':'user','content':'你好'}], group_context={'mode':'bot-interaction','messages':[]}, silent_failure=True)
        self.assertIn('傲娇斗嘴',create.await_args.kwargs['messages'][0]['content'])
        self.assertLessEqual(create.await_args.kwargs['max_tokens'],180)

    async def test_declined_generation_sends_no_at_and_consumes_turn(self):
        context=self.reserve()
        with patch.object(self.app,'get_chat_response',AsyncMock(return_value=('[不参与]',None,None,False))):
            await self.app.handle_bot_interaction(self.bot,context)
        self.bot.send_group_msg.assert_not_awaited()
        self.assertIsNone(self.reserve(self.now+10))

    async def test_blank_configuration_never_calls_model_or_qq(self):
        with patch.dict(os.environ,BOT_INTERACTION_TARGETS=''), patch.object(self.app,'get_bots',return_value={'999':self.bot}), patch.object(self.app,'get_chat_response',AsyncMock()) as model:
            await self.app.run_bot_interaction_once(self.now)
        model.assert_not_awaited()
        self.bot.send_group_msg.assert_not_awaited()
