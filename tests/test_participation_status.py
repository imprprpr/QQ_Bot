"""Live status and decisions, using only disposable SQLite and mocked sends."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from fastapi import FastAPI
import test_bot
from src.plugins.chat_and_draw.participation_status import DecisionLog,snapshot
from src.plugins.chat_and_draw.group_chat import GroupChat
from src.plugins.chat_and_draw.bot_interaction import BotInteractions
from src.plugins.chat_and_draw.sessions import SessionStore
from src.plugins.chat_and_draw.llm import ServiceNotice
from src.plugins.chat_and_draw.delivery import DeliveryResult
from src.plugins.chat_and_draw.admin import register_admin


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'state.db'
        self.env=patch.dict(os.environ,BOT_PARTICIPATION_CONFIG_FILE='',BOT_INTERACTION_CONFIG_FILE='',
            GROUP_CHAT_ENABLED='true',GROUP_CHAT_AMBIENT_ENABLED='true',GROUP_CHAT_FOLLOWUP_ENABLED='true',
            GROUP_CHAT_GROUPS='',GROUP_CHAT_NICKNAME_COOLDOWN='8',GROUP_CHAT_FOLLOWUP_COOLDOWN='5',
            GROUP_CHAT_FOLLOWUP_WINDOW='300',GROUP_CHAT_FOLLOWUP_MAX_ROUNDS='12',
            GROUP_CHAT_AMBIENT_HOURLY_LIMIT='10',GROUP_CHAT_AMBIENT_DAILY_LIMIT='20',GROUP_CHAT_NICKNAME_HOURLY_LIMIT='20',GROUP_CHAT_NICKNAME_DAILY_LIMIT='60',
            BOT_INTERACTION_ENABLED='true',BOT_INTERACTION_TARGETS='456',BOT_INTERACTION_GROUPS='1',
            BOT_ADMIN_TOKEN='T'*40)
        self.env.start();self.groups=GroupChat(self.path);self.bots=BotInteractions(self.path)
        self.bot=SimpleNamespace(self_id='999');self.room=self.groups.room(self.bot,test_bot.Event(group=1))
        self.now=100000.

    async def asyncTearDown(self):
        self.env.stop();self.tmp.cleanup()

    async def test_live_quota_cooldown_and_reads_never_consume_budget(self):
        self.groups.reserve(self.room,'nickname',self.now,participant='123',external_id='77')
        data=snapshot(self.path,self.now+1);room=data['rooms'][0]
        self.assertEqual(room['quotas']['nickname']['daily']['used'],1)
        self.assertEqual(room['quotas']['nickname']['daily']['remaining'],59)
        self.assertEqual(room['members'][0]['nickname_until'],self.now+8)
        self.assertEqual(data['events'][0]['reason'],'generating')
        self.assertEqual(snapshot(self.path,self.now+2)['quotas']['nickname']['daily']['used'],1)
        self.groups.reserve(self.room,'nickname',self.now+2,participant='123')
        self.assertEqual(snapshot(self.path,self.now+3)['events'][0]['reason'],'cooldown')

    async def test_rolling_expiration_reduced_limit_and_zero_pause(self):
        with self.groups.connect() as conn:
            for t in [self.now-30,self.now-20,self.now-10]:conn.execute('INSERT INTO group_chat_calls VALUES(?,?,?)',(self.room,'nickname',t))
        with patch.dict(os.environ,GROUP_CHAT_NICKNAME_DAILY_LIMIT='2'):
            q=snapshot(self.path,self.now)['rooms'][0]['quotas']['nickname']['daily']
            self.assertEqual(q['retry_at'],self.now-20+86400)
            self.assertEqual(q['remaining'],0)
        with patch.dict(os.environ,GROUP_CHAT_NICKNAME_DAILY_LIMIT='0'):
            q=snapshot(self.path,self.now)['rooms'][0]['quotas']['nickname']['daily']
            self.assertTrue(q['paused']);self.assertEqual(q['retry_at'],0)
        self.assertEqual(snapshot(self.path,self.now+86400)['quotas']['nickname']['daily']['used'],0)

    async def test_decline_empty_and_failure_are_not_confused(self):
        app=test_bot.plugin
        for reply,expected in [('[不参与]','model_declined'),('', 'empty_result'),(ServiceNotice('',reason='model_timeout'),'model_timeout')]:
            context=self.groups.reserve(self.room,'ambient',self.now,True,participant='123')
            # Use a fresh time far beyond cooldown for the next reservation.
            self.now+=1000
            with patch.object(app,'store',SessionStore(self.path)),patch.object(app,'get_chat_response',AsyncMock(return_value=(reply,None,None,False))),patch.object(app,'send_reply',AsyncMock()) as send:
                await app.handle_message(self.bot,test_bot.Event('这个怎么用',group=1),{'group_context':context})
            send.assert_not_awaited()
            with self.groups.connect() as conn:
                row=conn.execute('SELECT reason FROM participation_decisions WHERE id=?',(context['decision_id'],)).fetchone()
            self.assertEqual(row[0],expected)

    async def test_bot_used_counts_rounds_and_outcome(self):
        context=self.bots.reserve('999','1','456',self.now,'reactive',addressed=True)
        DecisionLog(self.path).finish(context,'model_declined')
        data=snapshot(self.path,self.now+1);room=data['rooms'][0]
        self.assertEqual(room['quotas']['bots']['hourly']['used'],1)
        self.assertEqual(room['bot_state']['rounds'],1)
        self.assertEqual(data['events'][0]['reason'],'model_declined')

    async def test_api_auth_and_no_content_or_credentials(self):
        self.groups.add(self.room,'1','123:user','PRIVATE_TEXT_NOT_FOR_STATUS',self.now)
        self.groups.reserve(self.room,'nickname',self.now,participant='123')
        api=FastAPI();register_admin(api,lambda:SessionStore(self.path))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api,client=('127.0.0.1',123)),base_url='http://127.0.0.1') as client:
            self.assertEqual((await client.get('/manage/api/participation')).status_code,401)
            result=await client.get('/manage/api/participation',headers={'Authorization':'Bearer '+'T'*40})
            self.assertEqual(result.status_code,200)
            self.assertNotIn('PRIVATE_TEXT_NOT_FOR_STATUS',result.text);self.assertNotIn('T'*40,result.text)
            self.assertEqual((await client.get('/manage/api/participation',headers={'Authorization':'Bearer '+'T'*40,'Origin':'https://evil.example'})).status_code,403)

    async def test_silent_error_keeps_a_typed_reason(self):
        with patch.object(test_bot.llm,'active_profile',return_value=None),patch.object(test_bot.llm,'get_client',return_value=SimpleNamespace()),patch.object(test_bot.llm,'extra_parameters',return_value={}),patch.object(test_bot.llm,'complete',AsyncMock(side_effect=asyncio.TimeoutError())):
            reply,*_=await test_bot.llm.get_chat_response([{'role':'user','content':'hello'}],silent_failure=True)
        self.assertEqual(reply,'');self.assertIsInstance(reply,ServiceNotice)
        self.assertEqual(reply.reason,'model_timeout')

    async def test_decision_history_is_bounded_and_missing_history_is_not_invented(self):
        log=DecisionLog(self.path);log.initialize()
        with log.database.transaction() as conn:
            conn.executemany('INSERT INTO participation_decisions(room,participant,mode,reason,created,updated,retry_at,external_id) VALUES(?,?,?,?,?,?,?,?)',
                [(self.room,'123','ambient','not_sampled',self.now,self.now,0,'')]*3100)
            DecisionLog.write(conn,self.room,'123','ambient','inactive',self.now)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM participation_decisions').fetchone()[0],3000)
        data=snapshot(self.path,self.now+1)
        self.assertEqual(data['events'][0]['reason'],'inactive')
        self.assertIn('不能推断',data['history_started'])
