"""Storage invariants: no hot-path DDL, serialized writes and safe legacy migration."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import test_bot
from src.plugins.chat_and_draw import database as database_module
from src.plugins.chat_and_draw.group_chat import GroupChat
from src.plugins.chat_and_draw.memory import EventMemoryStore
from src.plugins.chat_and_draw.runtime import RuntimeStore
from src.plugins.chat_and_draw.sessions import ROLE_FIELDS, Session, SessionStore


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.sessions = SessionStore(self.path)
        self.runtime = RuntimeStore(self.path)
        self.groups = GroupChat(self.path)
        self.memory = EventMemoryStore(self.path)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_all_repositories_share_file_coordinator_and_wal(self):
        self.assertIs(self.sessions.database,self.runtime.database)
        self.assertIs(self.groups.database,self.runtime.database)
        self.assertIs(self.memory.runtime.database,self.runtime.database)
        await self.sessions.save(Session('key'))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0],'wal')

    async def test_initialized_message_operations_never_run_ddl(self):
        self.sessions.initialize();self.runtime.initialize();self.memory.initialize()
        self.groups.cleanup()
        statements=[]
        original=sqlite3.connect
        def traced(*args,**kwargs):
            conn=original(*args,**kwargs)
            conn.set_trace_callback(statements.append)
            return conn
        with patch.object(database_module.sqlite3,'connect',side_effect=traced):
            for index in range(6):
                await self.sessions.save(Session(str(index)))
                await self.sessions.load(str(index))
                await RuntimeStore(self.path).start(str(index),'ling','hello',index)
                await EventMemoryStore(self.path).list(str(index),'ling')
                await asyncio.to_thread(GroupChat(self.path).add,'room',str(index),'u','hello',time.time())
        self.assertTrue(statements)
        self.assertFalse(any(sql.lstrip().upper().startswith(('CREATE ','ALTER ','DROP ')) for sql in statements))
        self.assertFalse(any('journal_mode' in sql.lower() for sql in statements))

    async def test_metadata_contains_no_duplicate_role_payload(self):
        await self.sessions.save(Session('key',character='ling',history=[{'role':'user','content':'hello'}],memories=['tea']))
        with sqlite3.connect(self.path) as conn:
            metadata=json.loads(conn.execute('SELECT data FROM sessions').fetchone()[0])
            role=json.loads(conn.execute('SELECT data FROM role_states').fetchone()[0])
        self.assertFalse(set(ROLE_FIELDS)&metadata.keys())
        self.assertEqual(role['memories'],['tea'])
        await self.sessions.finish_image('key','ling','new picture','sent')
        await self.sessions.update_memory('key','ling',['coffee'],'updated')
        loaded=await self.sessions.load('key')
        self.assertEqual(loaded.memories,['coffee'])
        self.assertEqual(loaded.last_image_prompt,'new picture')
        self.assertEqual(loaded.history[-1]['content'],'sent')

    async def test_legacy_copy_never_overwrites_newer_authoritative_role(self):
        await self.sessions.save(Session('key',character='ling',history=[{'role':'user','content':'new'}]))
        with sqlite3.connect(self.path) as conn:
            conn.execute('UPDATE sessions SET data=? WHERE key=?',(json.dumps({'key':'key','character':'ling',
                'history':[{'role':'user','content':'stale'}],'memories':['stale']}),'key'))
        await self.sessions.migrate_legacy()
        await self.sessions.migrate_legacy()
        self.assertEqual((await self.sessions.load('key')).history[0]['content'],'new')
        with sqlite3.connect(self.path) as conn:
            self.assertNotIn('history',json.loads(conn.execute('SELECT data FROM sessions').fetchone()[0]))

    async def test_save_failure_rolls_back_metadata_and_role_together(self):
        await self.sessions.save(Session('key',character='ling',history=[{'role':'user','content':'old'}]))
        original=self.sessions._save_conn
        def fail(conn,session):
            original(conn,session)
            raise RuntimeError('simulated failure before commit')
        with patch.object(self.sessions,'_save_conn',side_effect=fail):
            with self.assertRaises(RuntimeError):
                await self.sessions.save(Session('key',character='amelia',history=[{'role':'user','content':'new'}]))
        loaded=await self.sessions.load('key')
        self.assertEqual(loaded.character,'ling')
        self.assertEqual(loaded.history[0]['content'],'old')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM role_states').fetchone()[0],1)

    async def test_concurrent_read_modify_write_is_not_lost_between_store_instances(self):
        self.runtime.database.initialize('test-counter','CREATE TABLE counter(value INTEGER); INSERT INTO counter VALUES(0);')
        def increment(conn):
            value=conn.execute('SELECT value FROM counter').fetchone()[0]
            time.sleep(.001)
            conn.execute('UPDATE counter SET value=?',(value+1,))
        await asyncio.gather(*(RuntimeStore(self.path).call(increment) for _ in range(100)),
            *(self.sessions.save(Session('user'+str(index))) for index in range(30)),
            *(asyncio.to_thread(self.groups.add,'room',str(index),'u','text',time.time()) for index in range(30)))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT value FROM counter').fetchone()[0],100)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],30)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM role_states').fetchone()[0],30)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    async def test_application_reuses_group_service_and_rebinds_test_store(self):
        app=test_bot.plugin
        with patch.object(app,'store',self.sessions):
            self.assertIs(app.group_chat,app.group_chat)
            self.assertIs(app.group_chat.database,self.sessions.database)
            self.assertIs(app.runtime.database,self.sessions.database)
