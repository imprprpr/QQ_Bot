import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_bot
from src.plugins.chat_and_draw.code_analysis import needs_code_analysis
from src.plugins.chat_and_draw.qq_text import qq_plain_text, qq_chunks
from src.plugins.chat_and_draw.sessions import SessionStore
from src.plugins.chat_and_draw.delivery import DeliveryResult


class CodeAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def test_activation_and_followup_without_unrelated_topic_leakage(self):
        for text in ['分析一下这段代码', '这个项目有什么问题', '解释这个函数的逻辑', 'https://github.com/example/repository', 'def hello():\n    return 1']:
            self.assertTrue(needs_code_analysis([{'role':'user','content':text}]),text)
        self.assertTrue(needs_code_analysis([{'role':'user','content':'分析这段代码'}, {'role':'user','content':'继续'}]))
        for text in ['中午吃什么', '写代码写累了，陪我聊聊', '晚安']:
            self.assertFalse(needs_code_analysis([{'role':'user','content':'分析这段代码'}, {'role':'user','content':text}]),text)

    def test_continuations_follow_latest_topic_instead_of_reviving_old_review(self):
        history=[{'role':'user','content':value} for value in ['分析这段代码','继续','继续','继续','继续']]
        self.assertTrue(needs_code_analysis(history))
        history += [{'role':'user','content':'午饭吃什么'}, {'role':'user','content':'继续'}]
        self.assertFalse(needs_code_analysis(history))

    def test_markdown_to_plain_text(self):
        raw='### 代码分析\n---\n- **明确问题**：看 `worker.py`\n> 参考 [来源](https://example.com)\n| 项 | 说明 |\n| --- | --- |\n| A | 内容 |'
        text=qq_plain_text(raw)
        self.assertNotIn('###',text)
        self.assertNotIn('**',text)
        self.assertNotIn('`',text)
        self.assertNotIn('|',text)
        self.assertIn('来源（https://example.com）',text)
        self.assertIn('A；内容',text)

    def test_code_survives_rendering_twice_and_chunking(self):
        code='# 注释\ndef __init__(self):\n    value = 2 ** 3\n    return "**literal**"'
        text=qq_plain_text('```python\n'+code+'\n```')
        self.assertEqual(text,code)
        self.assertEqual(qq_plain_text(text),code)
        self.assertEqual(qq_chunks(text),[code])
        self.assertEqual(qq_plain_text('调用 `__init__`，x = 2 ** 3'), '调用 __init__，x = 2 ** 3')

    async def test_analysis_prompt_truthfulness_budget_and_no_media_tools(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='### 分析\n**发现**：只看到了这段代码。'))],usage=None))
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm,'get_client',return_value=client):
            result=await llm.get_chat_response([{'role':'user','content':'分析这段代码 def run(): pass'}])
        prompt=create.await_args.kwargs['messages'][0]['content']
        self.assertIn('专业代码解构',prompt)
        self.assertIn('没有仓库抓取或代码执行工具',prompt)
        self.assertIn('触发',prompt)
        self.assertGreaterEqual(create.await_args.kwargs['max_tokens'],1400)
        self.assertLessEqual(create.await_args.kwargs['temperature'],.3)
        self.assertEqual(result,('分析\n发现：只看到了这段代码。',None,None,False))

    async def test_everyday_chat_has_plain_text_guidance_without_review_prompt(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='**早安**'))],usage=None))
        with patch.object(llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            result=await llm.get_chat_response([{'role':'user','content':'早安'}])
        self.assertIn('QQ输出格式',create.await_args.kwargs['messages'][0]['content'])
        self.assertNotIn('本轮专业代码解构',create.await_args.kwargs['messages'][0]['content'])
        self.assertEqual(result[0],'早安')

    async def test_analysis_delivery_does_not_call_memory_or_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SessionStore(Path(folder)/'state.sqlite3')
            model=AsyncMock(return_value=('def run():\n    return 1','image','happy',True))
            with patch.object(test_bot.plugin,'store',store), patch.object(test_bot.plugin,'get_chat_response',model), patch.object(test_bot.plugin,'prepare_event_memory',AsyncMock()) as memory, patch.object(test_bot.plugin,'send_reply',AsyncMock(return_value=DeliveryResult(text_sent=True))) as sender:
                await test_bot.plugin.handle_message(SimpleNamespace(self_id='999'),test_bot.Event('用语音解释这段代码'))
            memory.assert_not_awaited()
            self.assertEqual(sender.await_args.args,('def run():\n    return 1',None,None,False))

    async def test_delivery_keeps_source_indentation_in_one_qq_message(self):
        raw='```python\ndef run():\n    return "**literal**"\n```'
        with patch.object(test_bot.plugin,'try_send',AsyncMock(return_value=True)) as send:
            await test_bot.plugin.send_reply(raw,None,None,False)
        self.assertEqual(send.await_count,1)
        self.assertEqual(send.await_args.args[1].data['text'],'def run():\n    return "**literal**"')

    async def test_empty_analysis_response_uses_service_notice(self):
        llm=test_bot.llm
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=''))],usage=None))
        with patch.object(llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            result=await llm.get_chat_response([{'role':'user','content':'分析这段代码'}])
        self.assertIsInstance(result[0],llm.ServiceNotice)
