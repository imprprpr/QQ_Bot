import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import test_bot
from src.plugins.chat_and_draw.sessions import Session,SessionStore
from src.plugins.chat_and_draw.personas import read,card,TEXT_FIELDS
from src.plugins.chat_and_draw.proactive_status import snapshot

ID='persona-12345678-1234-1234-1234-123456789012'


class PersonaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.file=self.path/'personas.json';self.store=SessionStore(self.path/'state.db')
        self.profile={k:'' for k in TEXT_FIELDS};self.profile.update(id=ID,name='测试角色',personality='俏皮，爱音乐',base_card='ling')
        self.env=patch.dict(os.environ,BOT_PERSONA_CONFIG_FILE=str(self.file));self.env.start();self.write([])
        self.key=json.dumps(['999','private','','123'],separators=(',',':'))

    async def asyncTearDown(self):
        self.env.stop();self.tmp.cleanup()

    def write(self,bindings):
        self.file.write_text(json.dumps({'schema_version':1,'revision':'fixture','profiles':[self.profile],'bindings':bindings}),encoding='utf-8')

    async def test_bind_switch_restore_and_memory_isolation(self):
        original=Session(self.key,bot_id='999',user_id='123',character='ling',history=[{'role':'user','content':'old history'}],memories=['old memory'])
        await self.store.save(original)
        assignment=[{'bot_id':'*','user_id':'123','persona_id':ID}];self.write(assignment)
        assigned=await self.store.load(self.key)
        self.assertEqual(assigned.character,ID);self.assertEqual(assigned.history,[])
        self.assertEqual(assigned.persona_binding_previous,'ling')
        assigned.history=[{'role':'user','content':'custom history'}];assigned.memories=['custom memory'];await self.store.save(assigned)
        self.write([]);restored=await self.store.load(self.key)
        self.assertEqual(restored.character,'ling');self.assertEqual(restored.history,original.history);self.assertEqual(restored.memories,['old memory'])
        self.write(assignment);again=await self.store.load(self.key)
        self.assertEqual(again.memories,['custom memory']);self.assertEqual(again.history,assigned.history)

    async def test_user_scoped_assignment_applies_to_new_group_and_private_only_for_that_user(self):
        self.write([{'bot_id':'999','user_id':'123','persona_id':ID}])
        for key in [self.key,json.dumps(['999','group','777','123'])]:self.assertEqual((await self.store.load(key)).character,ID)
        self.assertNotEqual((await self.store.load(json.dumps(['999','private','','456']))).character,ID)
        self.assertNotEqual((await self.store.load(json.dumps(['888','private','','123']))).character,ID)

    async def test_hot_edit_preset_and_fail_closed_on_missing_card(self):
        self.assertEqual(card(ID).name,'测试角色')
        from src.plugins.chat_and_draw.tavern import tavern_manager
        self.assertTrue(any('测试角色' in c for c in tavern_manager.list_cards()))
        self.profile['name']='更新角色';self.write([])
        self.assertEqual(card(ID).name,'更新角色')
        self.file.write_text('{broken',encoding='utf-8')
        with self.assertRaises(ValueError):read()
        with self.assertRaises(ValueError):test_bot.plugin.current_card(Session('x',character=ID))

    async def test_style_hot_edit_reaches_model_without_switching_role_or_memory(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        self.write([{'bot_id':'*','user_id':'123','persona_id':ID}])
        session=await self.store.load(self.key)
        session.memories=['keep this'];await self.store.save(session)
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='在听歌'))],usage=None))
        for style,expected in [({'teasing':0,'refusal':0,'punctuation':'standard'},['调侃关闭','尽量回应','常规标点']),({'teasing':3,'refusal':2,'punctuation':'minimal'},['更爱逗人','更有主见','少句尾标点'])]:
            self.profile['reply_style']=style;self.write([{'bot_id':'*','user_id':'123','persona_id':ID}])
            current=await self.store.load(self.key)
            self.assertEqual(current.character,ID);self.assertEqual(current.memories,['keep this'])
            active=test_bot.plugin.current_card(current)
            with patch.object(test_bot.llm,'get_client',return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
                await test_bot.llm.get_chat_response([{'role':'user','content':'在干嘛？'}],card=active)
            outgoing=create.await_args.kwargs['messages'][0]['content'].split('【当前人设的聊天偏好】')[-1]
            for label in expected:self.assertIn(label,outgoing)
        self.assertEqual(create.await_count,2)

    async def test_old_presets_get_defaults_and_invalid_style_fails_closed(self):
        self.assertEqual(read()['profiles'][0]['reply_style'],{'teasing':1,'refusal':1,'punctuation':'natural'})
        for bad in [{'teasing':True},{'refusal':3},{'punctuation':'anything'},{'unknown':0}]:
            self.profile['reply_style']=bad;self.write([])
            with self.assertRaises(ValueError):read()


class ProactiveStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=SessionStore(Path(self.tmp.name)/'state.db')
        self.now=datetime(2026,10,27,12,tzinfo=timezone(timedelta(hours=8)))
        self.epoch=self.now.timestamp()
        self.env=patch.dict(os.environ,BOT_PROACTIVE_CONFIG_FILE='',PROACTIVE_MODE='windows',PROACTIVE_UTC_OFFSET='8',PROACTIVE_IDLE_HOURS='3',PROACTIVE_COOLDOWN_HOURS='6',PROACTIVE_DAILY_LIMIT='2',PROACTIVE_GLOBAL_DAILY_LIMIT='6')
        self.env.start()

    async def asyncTearDown(self):self.env.stop();self.tmp.cleanup()

    async def test_used_remaining_cooldown_and_prediction_do_not_reschedule(self):
        s=Session('one',bot_id='999',user_id='123',last_interaction=self.epoch-4*3600,next_proactive=self.epoch+3600,
            proactive_day=self.now.date().isoformat(),proactive_attempts=1,proactive_last_attempt=self.epoch-3600)
        await self.store.save(s);data=snapshot(self.store.path,self.epoch);row=data['sessions'][0]
        self.assertEqual((row['used'],row['remaining']),(1,1));self.assertEqual(data['remaining'],5)
        self.assertEqual(row['cooldown_until'],self.epoch+5*3600)
        self.assertGreaterEqual(row['expected_at'],row['cooldown_until'])
        self.assertEqual((await self.store.load('one')).next_proactive,s.next_proactive)

    async def test_unanswered_and_quota_blockers_are_distinct_and_no_false_eta(self):
        s=Session('one',bot_id='999',user_id='123',next_proactive=self.epoch+1,proactive_unanswered=True,
            proactive_day=self.now.date().isoformat(),proactive_attempts=2)
        await self.store.save(s);row=snapshot(self.store.path,self.epoch)['sessions'][0]
        self.assertIsNone(row['expected_at']);self.assertTrue(any('未回复' in r for r in row['reasons']))
        self.assertTrue(any('额度' in r for r in row['reasons']))
        tomorrow=snapshot(self.store.path,self.epoch+86400)['sessions'][0]
        self.assertEqual(tomorrow['used'],0);self.assertEqual(tomorrow['remaining'],2)

    async def test_result_updates_do_not_overwrite_memory_or_human_messages(self):
        s=Session('one',proactive_last_attempt=self.epoch,history=[{'role':'user','content':'keep'}],memories=['keep'])
        await self.store.save(s);await self.store.proactive_result('one',self.epoch,'model_declined')
        loaded=await self.store.load('one');self.assertEqual(loaded.history,s.history);self.assertEqual(loaded.memories,['keep'])
        self.assertEqual(loaded.proactive_last_result,'model_declined')
        await self.store.proactive_result('one',self.epoch-1,'sent')
        self.assertEqual((await self.store.load('one')).proactive_last_result,'model_declined')

    async def test_private_status_and_catalog_are_guarded_and_status_omits_chat_content(self):
        from fastapi import FastAPI
        import httpx
        from src.plugins.chat_and_draw.admin import register_admin
        await self.store.save(Session('one',history=[{'role':'user','content':'PRIVATE_TEXT'}],proactive_last_text='PRIVATE_TEXT'))
        api=FastAPI();register_admin(api,lambda:self.store)
        with patch.dict(os.environ,BOT_ADMIN_TOKEN='X'*40):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api,client=('127.0.0.1',123)),base_url='http://127.0.0.1') as client:
                for route in ['/manage/api/proactive','/manage/api/persona-catalog']:
                    self.assertEqual((await client.get(route)).status_code,401)
                    r=await client.get(route,headers={'Authorization':'Bearer '+'X'*40})
                    self.assertEqual(r.status_code,200)
                    self.assertNotIn('PRIVATE_TEXT',r.text);self.assertNotIn('X'*40,r.text)
                    self.assertEqual((await client.get(route,headers={'Authorization':'Bearer '+'X'*40,'Origin':'https://evil.example'})).status_code,403)

    async def test_cycle_distinguishes_provider_failure_from_model_choice_without_sending(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from src.plugins.chat_and_draw.llm import ServiceNotice
        bot=SimpleNamespace(send_private_msg=AsyncMock())
        for reply,expected in [(ServiceNotice('',reason='provider_rate_limited'),'provider_rate_limited'),('【不打扰】。','model_declined'),('','empty_result')]:
            await self.store.save(Session('one',bot_id='999',user_id='123',last_interaction=self.epoch-4*3600,next_proactive=1))
            with patch.object(test_bot.plugin,'store',self.store),patch.object(test_bot.plugin,'get_bot',return_value=bot),patch.object(test_bot.plugin,'get_chat_response',AsyncMock(return_value=(reply,None,None,False))):
                await test_bot.plugin.run_proactive_once(self.now)
            loaded=await self.store.load('one')
            self.assertEqual(loaded.proactive_last_result,expected);self.assertEqual(loaded.proactive_attempts,1)
        bot.send_private_msg.assert_not_awaited()
