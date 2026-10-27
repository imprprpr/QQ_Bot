import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_bot
from src.plugins.chat_and_draw.conversation import brief_chat, sms_style_context
from src.plugins.chat_and_draw.tavern import tavern_manager


class PersonaStyleTests(unittest.IsolatedAsyncioTestCase):
    def test_smalltalk_and_explicit_requests_have_different_length_guidance(self):
        for text in ['我好累','在干嘛呀？','晚安','[用户发送了图片]','你是不是想我了？']:
            self.assertTrue(brief_chat(text),text)
            self.assertIn('最多90字',sms_style_context(text))
        for text in ['详细解释一下为什么会这样','帮我推荐三个适合的方案','写个Python程序','给我规划一个旅游行程','这个函数怎么修复','我想自杀','这是个比较复杂的问题。'*20]:
            self.assertFalse(brief_chat(text),text)
            self.assertIn('复杂度',sms_style_context(text))
        self.assertTrue(brief_chat('一句话解释原理'))

    def test_ling_card_keeps_media_identity_and_has_behavioral_examples(self):
        card=tavern_manager.cards['ling']
        prompt=tavern_manager.build_system_prompt(card)
        self.assertIn('22岁',card.description)
        self.assertIn('胜负欲',card.description+card.personality)
        self.assertIn('群聊',card.scenario)
        self.assertGreaterEqual(card.mes_example.count('<START>'),12)
        self.assertIn('不反复解释',card.system_prompt)
        self.assertIn('<voice>',prompt)
        self.assertIn('<draw:',prompt)
        self.assertIn('black hair',str(card.image_profile))
        self.assertIn('不擅自把所有人当恋人',card.scenario)

    async def test_brief_style_overrides_old_long_dialogue_without_removing_history(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='在挑歌，你来得正好。'))],usage=None))
        history=[{'role':'assistant','content':'一大段旧回复。'*80},{'role':'user','content':'在干嘛？'}]
        with patch.object(llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            result=await llm.get_chat_response(history,card=tavern_manager.cards['ling'])
        prompt=create.await_args.kwargs['messages'][0]['content']
        self.assertIn('最多90字',prompt)
        self.assertIn('本轮不要模仿',prompt)
        self.assertEqual(create.await_args.kwargs['messages'][1]['content'],history[0]['content'])
        self.assertEqual(create.await_count,1)
        self.assertEqual(result[0],'在挑歌，你来得正好。')

    async def test_professional_analysis_does_not_inherit_smalltalk_word_limit(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='需要看到实际代码。'))],usage=None))
        with patch.object(llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            await llm.get_chat_response([{'role':'user','content':'详细分析这段代码的并发问题'}])
        prompt=create.await_args.kwargs['messages'][0]['content']
        self.assertIn('专业代码解构',prompt)
        self.assertNotIn('最多90字',prompt)
