import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_bot
from src.plugins.chat_and_draw import model_provider as providers
from src.plugins.chat_and_draw.rate_limit import CooldownError, EndpointGate, endpoint_gate


class ManagedModelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_hot_switch_default_restore_and_no_legacy_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'models.json'
            profile={'id':'one','name':'fixture','base_url':'https://example.com/v1','api_key':'PRIVATE_FIXTURE','model':'chosen-model'}
            config={'schema_version':1,'active':'one','profiles':[profile]}
            path.write_text(json.dumps(config))
            with patch.dict(os.environ,{'BOT_MODEL_CONFIG_FILE':str(path)}):
                self.assertEqual(providers.active_profile(),profile)
                response={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]}
                with patch.object(providers,'request_json',AsyncMock(return_value=response)) as request:
                    result=await providers.ManagedClient(profile).create(model='old-model',messages=[],extra_body={'private_option':True})
                self.assertEqual(result.choices[0].message.content,'ok')
                payload=request.await_args.args[1]
                self.assertEqual(payload['model'],'chosen-model')
                self.assertNotIn('private_option',payload)
                config['active']=None
                path.write_text(json.dumps(config))
                self.assertIsNone(providers.active_profile())
                path.write_text('{BROKEN_PRIVATE_FIXTURE')
                with self.assertRaisesRegex(ValueError,'^INVALID_MANAGED_MODEL_CONFIG$'):
                    providers.active_profile()

    async def test_inflight_client_keeps_original_profile(self):
        first={'model':'first','base_url':'https://example.com/v1','api_key':'secret'}
        client=providers.ManagedClient(dict(first))
        first['model']='changed'
        self.assertEqual(client.profile['model'],'first')

    async def test_direct_reply_budget_grows_without_expanding_ambient_budget(self):
        client=providers.ManagedClient({'model':'fixture','base_url':'https://example.com/v1','api_key':'secret'})
        data={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]}
        with patch.object(providers,'request_json',AsyncMock(return_value=data)) as request:
            await client.create(model='fixture',messages=[],max_tokens=500)
            self.assertEqual(request.await_args.args[1]['max_tokens'],1024)
            await client.create(model='fixture',messages=[],max_tokens=180)
            self.assertEqual(request.await_args.args[1]['max_tokens'],180)

    async def test_truncated_reply_keeps_text_without_executing_partial_or_complete_media(self):
        llm=test_bot.llm
        for text in ['你好，这是生成的一部分<draw: incomplete', '你好<sticker: happy><voice>']:
            response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='length',message=SimpleNamespace(content=text))],usage=None)
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
            with patch.object(llm,'get_client',return_value=client):
                result=await llm.get_chat_response([{'role':'user','content':'你好'}])
            self.assertIn('你好',result[0])
            self.assertIn('未完整生成',result[0])
            self.assertNotIn('<',result[0])
            self.assertEqual(result[1:],(None,None,False))

    async def test_reasoning_only_or_code_truncation_never_exposes_incomplete_source(self):
        llm=test_bot.llm
        for user,text in [('你好',''),('分析这段代码','def run():')]:
            response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='length',message=SimpleNamespace(content=text))],usage=None)
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
            with patch.object(llm,'get_client',return_value=client):
                result=await llm.get_chat_response([{'role':'user','content':user}])
            self.assertIsInstance(result[0],llm.ServiceNotice)
            self.assertEqual(result[1:],(None,None,False))

    async def test_rate_notice_is_not_persona_history_and_has_no_media(self):
        llm=test_bot.llm
        create=AsyncMock(side_effect=CooldownError(30))
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm,'get_client',return_value=client):
            result=await llm.get_chat_response([{'role':'user','content':'你好'}])
            silent=await llm.get_chat_response([{'role':'user','content':'你好'}],silent_failure=True)
        self.assertIsInstance(result[0],llm.ServiceNotice)
        self.assertIn('限流',result[0])
        self.assertEqual(result[1:],(None,None,False))
        self.assertEqual(silent,('',None,None,False))

    def test_exponential_backoff_respects_retry_after_and_recovers(self):
        gate=EndpointGate()
        with patch('src.plugins.chat_and_draw.rate_limit.random.uniform',return_value=1):
            first=gate.penalize()
            second=gate.penalize()
            server=gate.penalize(120)
        self.assertGreater(first,7.9)
        self.assertGreater(second,15.9)
        self.assertGreater(server,119.9)
        gate.recovered()
        self.assertEqual((gate.strikes,gate.until),(0,0))

    def test_endpoint_identity_keeps_credentials_out_of_registry(self):
        self.assertIs(endpoint_gate('endpoint-secret'),endpoint_gate('endpoint-secret'))
        self.assertIsNot(endpoint_gate('endpoint-secret'),endpoint_gate('other-endpoint'))
