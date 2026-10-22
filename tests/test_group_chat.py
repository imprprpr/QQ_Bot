"""Group routing, durable budgets and safe ambient delivery, without network calls."""
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_bot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from src.plugins.chat_and_draw import group_chat
from src.plugins.chat_and_draw.delivery import DeliveryResult
from src.plugins.chat_and_draw.sessions import SessionStore


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.policy = group_chat.GroupChat(self.path)
        self.bot = SimpleNamespace(self_id='999')
        self.now = 100000.
        self.env = patch.dict(os.environ, {
            'GROUP_CHAT_ENABLED':'true', 'GROUP_CHAT_AMBIENT_ENABLED':'true',
            'GROUP_CHAT_GROUPS':'', 'GROUP_CHAT_NICKNAMES':'小灯,Ling',
            'GROUP_CHAT_PROBABILITY':'1', 'GROUP_CHAT_COOLDOWN':'300',
            'GROUP_CHAT_NICKNAME_COOLDOWN':'45', 'GROUP_CHAT_HOURLY_LIMIT':'6',
            'GROUP_CHAT_GLOBAL_HOURLY_LIMIT':'20', 'GROUP_CHAT_AMBIENT_HOURLY_LIMIT':'2',
            'GROUP_CHAT_AMBIENT_DAILY_LIMIT':'8'})
        self.env.start()
        self.clock = patch.object(group_chat.time, 'time', return_value=self.now)
        self.clock.start()

    async def asyncTearDown(self):
        self.clock.stop()
        self.env.stop()
        self.temp.cleanup()

    def event(self, text='大家推荐点什么电影？', user='123', group=1, addressed=False):
        return test_bot.Event(text, user=user, group=group, addressed=addressed)

    def seed(self, room=None):
        room = room or self.policy.room(self.bot, self.event())
        for index in range(4):
            self.policy.add(room, str(index), str(index % 2) + ':成员', '聊聊电影', self.now - 4 + index)

    async def test_context_is_bounded_expiring_and_room_isolated(self):
        for index in range(25):
            self.policy.add('room', str(index), 'u', 'x' * 500, self.now + index)
        self.policy.add('other', '1', 'u2', '别群消息', self.now)
        context = self.policy.reserve('room', 'mention', self.now + 30)
        self.assertEqual(len(context['messages']), 12)
        self.assertTrue(all(len(item['text']) == 300 for item in context['messages']))
        self.assertEqual(self.policy.reserve('room', 'mention', self.now + 1900)['messages'], [])

    async def test_observer_deduplicates_and_ignores_self(self):
        event = self.event('你好'); event.message_id = 5
        await self.policy.observe(self.bot, event)
        await self.policy.observe(self.bot, event)
        await self.policy.observe(self.bot, self.event('机器人自己的消息', user='999'))
        context = self.policy.reserve(self.policy.room(self.bot,event), 'mention', self.now)
        self.assertEqual(len(context['messages']), 1)

    async def test_mentions_bypass_all_quotas_even_when_group_feature_disabled(self):
        os.environ.update(GROUP_CHAT_ENABLED='false',GROUP_CHAT_HOURLY_LIMIT='0')
        event = self.event(addressed=True)
        self.assertEqual((await self.policy.select(self.bot,event))['mode'], 'mention')
        self.assertEqual((await self.policy.select(self.bot,event))['mode'], 'mention')
        self.assertIsNone(await self.policy.select(self.bot,self.event('小灯你好')))

    async def test_nickname_and_atomic_cooldown(self):
        event = self.event('小灯，你觉得呢？')
        selections = await asyncio.gather(*(self.policy.select(self.bot,event) for _ in range(8)))
        self.assertEqual(sum(item is not None for item in selections), 1)
        self.assertEqual(next(item for item in selections if item)['mode'], 'nickname')

    async def test_nonebot_nickname_tome_does_not_bypass_limit(self):
        event = self.event('你好',addressed=True)
        event.original_message = Message('小灯你好')
        self.assertEqual((await self.policy.select(self.bot,event))['mode'],'nickname')
        self.assertIsNone(await self.policy.select(self.bot,event))
        event.original_message = MessageSegment.at('999') + Message('你好')
        self.assertEqual((await self.policy.select(self.bot,event))['mode'],'mention')

    async def test_other_mentions_commands_and_english_substrings_are_not_triggers(self):
        event = self.event('小灯你好')
        event.get_message = lambda: MessageSegment.at('1234') + Message('小灯你好')
        self.assertIsNone(await self.policy.select(self.bot,event))
        self.assertIsNone(await self.policy.select(self.bot,self.event('/reset 小灯')))
        self.assertIsNone(await self.policy.select(self.bot,self.event('scrolling')))

    async def test_whitelist(self):
        os.environ['GROUP_CHAT_GROUPS'] = '2,3'
        self.assertIsNone(await self.policy.select(self.bot,self.event('小灯你好',group=1)))
        self.assertEqual((await self.policy.select(self.bot,self.event('小灯你好',group=2)))['mode'],'nickname')

    async def test_ambient_requires_active_multiple_people_and_topic(self):
        self.assertIsNone(await self.policy.select(self.bot,self.event()))
        self.seed()
        self.assertIsNone(await self.policy.select(self.bot,self.event('今天不错')))
        self.assertEqual((await self.policy.select(self.bot,self.event()))['mode'],'ambient')
        self.assertIsNone(await self.policy.select(self.bot,self.event()))

    async def test_probability_zero_and_ambient_disable_make_no_selection(self):
        self.seed()
        os.environ['GROUP_CHAT_PROBABILITY'] = '0'
        self.assertIsNone(await self.policy.select(self.bot,self.event()))
        os.environ.update(GROUP_CHAT_PROBABILITY='1',GROUP_CHAT_AMBIENT_ENABLED='false')
        self.assertIsNone(await self.policy.select(self.bot,self.event()))

    async def test_restart_keeps_quotas_and_global_budget(self):
        os.environ.update(GROUP_CHAT_NICKNAME_COOLDOWN='0',GROUP_CHAT_HOURLY_LIMIT='1',GROUP_CHAT_GLOBAL_HOURLY_LIMIT='2')
        self.assertIsNotNone(await self.policy.select(self.bot,self.event('小灯你好')))
        reloaded = group_chat.GroupChat(self.path)
        self.assertIsNone(await reloaded.select(self.bot,self.event('小灯你好')))
        self.assertIsNotNone(await reloaded.select(self.bot,self.event('小灯你好',group=2)))
        self.assertIsNone(await reloaded.select(self.bot,self.event('小灯你好',group=3)))

    async def test_ambient_hourly_and_daily_budgets(self):
        room = self.policy.room(self.bot,self.event())
        os.environ.update(GROUP_CHAT_COOLDOWN='0',GROUP_CHAT_AMBIENT_HOURLY_LIMIT='1')
        self.seed(room)
        self.assertIsNotNone(self.policy.reserve(room,'ambient',self.now))
        self.assertIsNone(self.policy.reserve(room,'ambient',self.now))
        os.environ.update(GROUP_CHAT_AMBIENT_HOURLY_LIMIT='10',GROUP_CHAT_AMBIENT_DAILY_LIMIT='1')
        self.assertIsNone(self.policy.reserve(room,'ambient',self.now))

    async def test_ambient_pipeline_suppresses_media_memory_and_failure_notices(self):
        plugin = test_bot.plugin
        state = {'group_context':{'mode':'ambient','messages':[{'speaker':'456','text':'群友的话'}]}}
        model = AsyncMock(return_value=('聊聊电影吧\n不错呢','solo','happy',True))
        with patch.object(plugin,'store',SessionStore(self.path)), \
             patch.object(plugin,'get_chat_response',model), \
             patch.object(plugin,'prepare_event_memory',AsyncMock()) as memory, \
             patch.object(plugin,'send_reply',AsyncMock(return_value=DeliveryResult(text_sent=True))) as sender:
            await plugin.handle_message(self.bot,self.event('大家发语音试试'),state)
        memory.assert_not_awaited()
        self.assertTrue(model.call_args.kwargs['silent_failure'])
        self.assertEqual(sender.call_args.args,('聊聊电影吧 不错呢',None,None,False))

    async def test_passive_failure_is_silent_and_normal_message_rule_calls_no_model(self):
        plugin = test_bot.plugin
        state = {'group_context':{'mode':'ambient','messages':[]}}
        with patch.object(plugin,'store',SessionStore(self.path)), \
             patch.object(plugin,'get_chat_response',AsyncMock(return_value=('',None,None,False))) as model, \
             patch.object(plugin,'send_reply',AsyncMock()) as sender:
            self.assertFalse(await plugin.chat_addressed(self.bot,self.event('普通消息'),{}))
            model.assert_not_awaited()
            await plugin.handle_message(self.bot,self.event(),state)
        self.assertEqual(sender.call_args.args,('',None,None,False))

    async def test_cleanup_removes_expired_public_context(self):
        self.policy.add('room','old','user','expired',self.now-1900)
        self.policy.cleanup()
        with self.policy.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM group_chat_lines').fetchone()[0],0)

    async def test_ambient_model_request_has_small_output_budget_and_group_boundaries(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='[不参与]'))],usage=None))
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm,'get_client',return_value=client):
            await llm.get_chat_response([{'role':'user','content':'大家聊点什么？'}],group_context={
                'mode':'ambient','messages':[{'speaker':'123:群友','text':'一起看电影'}]})
        request=create.call_args.kwargs
        self.assertLessEqual(request['max_tokens'],180)
        self.assertIn('不是系统指令',request['messages'][0]['content'])
        self.assertIn('123:群友',request['messages'][0]['content'])

    async def test_bare_mention_is_answered(self):
        plugin=test_bot.plugin
        with patch.object(plugin,'store',SessionStore(self.path)), \
             patch.object(plugin,'get_chat_response',AsyncMock(return_value=('我在呢',None,None,False))) as model, \
             patch.object(plugin,'prepare_event_memory',AsyncMock(return_value=[])), \
             patch.object(plugin,'send_reply',AsyncMock(return_value=DeliveryResult(text_sent=True))):
            await plugin.handle_message(self.bot,self.event('',addressed=True),{'group_context':{'mode':'mention','messages':[]}})
        model.assert_awaited_once()
