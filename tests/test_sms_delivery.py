import asyncio
import re
import unittest
from unittest.mock import AsyncMock, patch

import test_bot
from src.plugins.chat_and_draw.qq_text import sms_chunks
from src.plugins.chat_and_draw.llm import ServiceNotice


SCREENSHOT = ('嗳，都直接瘫成一张饼了啊。\n\n'
    '看你这架势，前面做饭学语言学AI，晚上还死磕这种高强度的视频剪辑，不累才怪。这种瘫倒的表情包真是完美契合你现在的状态。\n\n'
    '别硬熬了，那个工程明天再弄也跑不掉。听我的，保存好文件，赶紧离开电脑桌，去洗个热水澡准备睡觉啦。')


class SMSDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_screenshot_reply_becomes_three_ordered_bubbles_without_losing_text(self):
        parts=sms_chunks(SCREENSHOT)
        self.assertEqual(len(parts),3)
        self.assertEqual(re.sub(r'\s','',''.join(parts)),re.sub(r'\s','',SCREENSHOT))
        self.assertLess(max(map(len,parts)),120)

    def test_single_long_paragraph_splits_at_sentence_boundaries(self):
        text='今天总算把手头的事情做完了，感觉轻松了不少。'*6
        parts=sms_chunks(text)
        self.assertGreater(len(parts),1)
        self.assertLessEqual(len(parts),3)
        self.assertEqual(re.sub(r'\s','',''.join(parts)),text)
        self.assertTrue(all(p.endswith('。') for p in parts))

    def test_short_replies_urls_emoji_and_group_caps(self):
        self.assertEqual(sms_chunks('好呀，晚安～'),['好呀，晚安～'])
        self.assertEqual(sms_chunks('嗯。\n好。\n晚安。'),['嗯。\n好。\n晚安。'])
        url='https://example.com/a?x=1&y=2'
        text='看看这个地址 '+url+' 😊。\n'+SCREENSHOT
        parts=sms_chunks(text,2)
        self.assertLessEqual(len(parts),2)
        self.assertTrue(any(url in p for p in parts))
        self.assertIn('😊',''.join(parts))
        self.assertEqual(len(sms_chunks(SCREENSHOT,1)),1)

    async def test_delivery_pauses_between_bubbles_with_no_extra_model_calls(self):
        with patch.object(test_bot.plugin,'try_send',AsyncMock(return_value=True)) as sender, patch('src.plugins.chat_and_draw.chat_flow.asyncio.sleep',AsyncMock()) as sleep:
            result=await test_bot.plugin.send_reply(SCREENSHOT,None,None,False)
        self.assertEqual(sender.await_count,3)
        self.assertEqual(sleep.await_count,2)
        self.assertTrue(all(.6<=call.args[0]<=2.4 for call in sleep.await_args_list))
        self.assertEqual(re.sub(r'\s','',result.delivered_text),re.sub(r'\s','',SCREENSHOT))

    async def test_source_analysis_and_service_notices_are_not_sms_split(self):
        for text,kwargs in [('def run():\n    return "hello"',{}),(SCREENSHOT,{'text_mode':'technical'}),(ServiceNotice(SCREENSHOT),{})]:
            with patch.object(test_bot.plugin,'try_send',AsyncMock(return_value=True)) as sender:
                await test_bot.plugin.send_reply(text,None,None,False,**kwargs)
            self.assertEqual(sender.await_count,1)
            self.assertEqual(sender.await_args.args[1].data['text'],str(text))

    async def test_failed_bubble_stops_following_sends_and_records_only_delivered_text(self):
        with patch.object(test_bot.plugin,'try_send',AsyncMock(side_effect=[True,False])) as sender, patch('src.plugins.chat_and_draw.chat_flow.asyncio.sleep',AsyncMock()):
            result=await test_bot.plugin.send_reply(SCREENSHOT,None,None,False)
        self.assertEqual(sender.await_count,2)
        self.assertEqual(result.delivered_text,sms_chunks(SCREENSHOT)[0])

    async def test_first_send_failure_does_not_send_remaining_bubbles(self):
        with patch.object(test_bot.plugin,'try_send',AsyncMock(return_value=False)) as sender:
            result=await test_bot.plugin.send_reply(SCREENSHOT,None,None,False)
        self.assertEqual(sender.await_count,1)
        self.assertFalse(result.text_sent)
        self.assertEqual(result.delivered_text,'')
