"""Managed group policies: offline only, no real model or QQ requests."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import test_bot
from src.plugins.chat_and_draw.participation_config import FIELDS, managed, validate
from src.plugins.chat_and_draw.config import feature_enabled
from src.plugins.chat_and_draw.group_chat import GroupChat
from src.plugins.chat_and_draw.bot_interaction import BotInteractions
from src.plugins.chat_and_draw.sessions import SessionStore


class ParticipationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.file=Path(self.tmp.name)/'policy.json'
        self.db=Path(self.tmp.name)/'state.db'
        self.values={k:f['default'] for k,f in FIELDS.items()}
        self.env=patch.dict(os.environ,BOT_PARTICIPATION_CONFIG_FILE=str(self.file),
            BOT_INTERACTION_CONFIG_FILE='',BOT_INTERACTION_TARGETS='456',BOT_INTERACTION_GROUPS='1',GROUP_CHAT_GROUPS='')
        self.env.start()
        self.save()
        self.groups=GroupChat(self.db)
        self.bots=BotInteractions(self.db)

    async def asyncTearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self,**changes):
        self.values.update(changes)
        data={'schema_version':2,'revision':'fixture','values':self.values}
        self.file.write_text(json.dumps(data),encoding='utf-8')

    async def test_hot_flags_fail_closed_and_peer_quarantine(self):
        self.assertTrue(feature_enabled('GROUP_CHAT_ENABLED'))
        self.save(GROUP_CHAT_ENABLED=False,BOT_INTERACTION_ENABLED=False)
        self.assertFalse(feature_enabled('GROUP_CHAT_ENABLED'))
        self.assertFalse(self.bots.allowed('999','1','456'))
        self.assertTrue(self.bots.is_peer('456'))
        self.file.write_text('{broken',encoding='utf-8')
        self.assertFalse(feature_enabled('GROUP_CHAT_ENABLED'))
        self.assertFalse(feature_enabled('BOT_INTERACTION_ENABLED'))
        self.file.unlink()
        self.assertFalse(feature_enabled('GROUP_CHAT_ENABLED'))

    async def test_cooldown_and_daily_limits_hot_change_without_reset(self):
        self.assertIsNotNone(self.groups.reserve('room','nickname',100000))
        self.assertIsNone(self.groups.reserve('room','nickname',100010))
        self.save(GROUP_CHAT_NICKNAME_COOLDOWN=1,GROUP_CHAT_NICKNAME_DAILY_LIMIT=2)
        self.assertIsNotNone(self.groups.reserve('room','nickname',100010))
        self.save(GROUP_CHAT_PROBABILITY=1)
        self.assertIsNone(self.groups.reserve('room','nickname',100020))
        self.assertIsNotNone(self.groups.reserve('room','mention',100020))
        with self.groups.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM group_chat_calls').fetchone()[0],2)

    async def test_followup_rounds_and_global_daily_limit(self):
        self.save(GROUP_CHAT_FOLLOWUP_MAX_ROUNDS=1)
        self.assertIsNotNone(self.groups.reserve('room','followup',100000))
        self.assertIsNone(self.groups.reserve('room','followup',100010))
        self.save(GROUP_CHAT_FOLLOWUP_MAX_ROUNDS=2)
        self.assertIsNotNone(self.groups.reserve('room','followup',100010))
        self.save(GROUP_CHAT_FOLLOWUP_GLOBAL_DAILY_LIMIT=2)
        self.assertIsNone(self.groups.reserve('other','followup',100020))

    async def test_bot_rounds_disable_and_proactive_switch(self):
        self.save(BOT_INTERACTION_MAX_ROUNDS=1,BOT_INTERACTION_PROACTIVE_ENABLED=False)
        self.assertIsNone(self.bots.reserve('999','1','456',100000,'proactive'))
        first=self.bots.reserve('999','1','456',100000,'reactive',addressed=True)
        self.bots.finish(first,True,100000)
        self.assertIsNone(self.bots.reserve('999','1','456',100010,'reactive',addressed=True))
        self.save(BOT_INTERACTION_MAX_ROUNDS=2)
        # A finished one-turn conversation stays finished; saving cannot reopen it.
        self.assertIsNone(self.bots.reserve('999','1','456',100010,'reactive',addressed=True))
        self.save(BOT_INTERACTION_COOLDOWN=60)
        second=self.bots.reserve('999','1','456',100241,'reactive',addressed=True)
        self.assertIsNotNone(second)
        self.save(BOT_INTERACTION_ENABLED=False)
        self.assertFalse(self.bots.valid(second,100242))

    async def test_bot_daily_budget_is_retained_and_zero_blocks(self):
        self.save(BOT_INTERACTION_DAILY_LIMIT=1)
        first=self.bots.reserve('999','1','456',100000,'reactive',addressed=True)
        self.assertIsNotNone(first)
        self.bots.finish(first,True,100000)
        self.save(BOT_INTERACTION_REPLY_COOLDOWN=1)
        self.assertIsNone(self.bots.reserve('999','1','456',100010,'reactive',addressed=True))
        self.save(BOT_INTERACTION_DAILY_LIMIT=0)
        self.assertIsNone(self.bots.reserve('999','1','456',200000,'reactive',addressed=True))

    async def test_bare_mention_still_routes_when_group_disabled(self):
        self.save(GROUP_CHAT_ENABLED=False)
        event=test_bot.Event('你好',group=1,addressed=True)
        result=await self.groups.select(SimpleNamespace(self_id='999'),event)
        self.assertEqual(result['mode'],'mention')

    async def test_disable_during_group_generation_cancels_send(self):
        async def generate(*args,**kwargs):
            self.save(GROUP_CHAT_ENABLED=False)
            return ('来聊聊电影吧',None,None,False)
        app=test_bot.plugin
        with patch.object(app,'store',SessionStore(self.db)),patch.object(app,'get_chat_response',AsyncMock(side_effect=generate)),patch.object(app,'send_reply',AsyncMock()) as send:
            await app.handle_message(SimpleNamespace(self_id='999'),test_bot.Event('聊聊电影',group=1),{'group_context':{'mode':'ambient','messages':[]}})
        send.assert_not_awaited()

    async def test_schema_types_ranges_and_missing_fields(self):
        for change in ({'BOT_INTERACTION_MAX_ROUNDS':4},{'GROUP_CHAT_NICKNAME_DAILY_LIMIT':-1},{'GROUP_CHAT_ENABLED':'true'},{'GROUP_CHAT_PROBABILITY':2}):
            with self.assertRaises(ValueError):
                validate({'schema_version':2,'values':{**self.values,**change}})
        with self.assertRaises(ValueError):validate({'schema_version':2,'values':{}})
        self.assertEqual(set(managed()),set(FIELDS))

    async def test_pools_are_independent_and_keep_counts_after_restart(self):
        self.save(GROUP_CHAT_AMBIENT_DAILY_LIMIT=1,GROUP_CHAT_AMBIENT_GLOBAL_DAILY_LIMIT=1,
                  GROUP_CHAT_NICKNAME_DAILY_LIMIT=1,GROUP_CHAT_FOLLOWUP_DAILY_LIMIT=1)
        self.assertIsNotNone(self.groups.reserve('room','ambient',100000,True))
        self.assertIsNone(self.groups.reserve('other','ambient',100400,True))
        self.assertIsNotNone(self.groups.reserve('room','nickname',100001,participant='a'))
        self.assertIsNotNone(self.groups.reserve('room','followup',100010,participant='a'))
        reloaded=GroupChat(self.db)
        self.assertIsNone(reloaded.reserve('room','nickname',100100,participant='a'))
        self.assertIsNone(reloaded.reserve('room','followup',100100,participant='a'))
        self.assertIsNone(reloaded.reserve('other','ambient',100400,True))

    async def test_direct_cooldowns_and_rounds_are_per_member(self):
        self.save(GROUP_CHAT_FOLLOWUP_MAX_ROUNDS=1)
        self.assertIsNotNone(self.groups.reserve('room','followup',100000,participant='a'))
        self.assertIsNotNone(self.groups.reserve('room','followup',100001,participant='b'))
        self.assertIsNone(self.groups.reserve('room','followup',100010,participant='a'))
        self.groups.add('room',None,'bot:999:to:a','first',100000)
        self.groups.add('room',None,'bot:999:to:b','second',100001)
        self.assertTrue(self.groups.is_followup('room','a',100010))

    async def test_random_disabled_does_not_disable_ongoing_chat(self):
        self.save(GROUP_CHAT_AMBIENT_ENABLED=False)
        bot=SimpleNamespace(self_id='999')
        event=test_bot.Event('然后呢',group=1,user='123')
        room=self.groups.room(bot,event)
        self.groups.add(room,None,'bot:999:to:123','hello',100000)
        with patch('src.plugins.chat_and_draw.group_chat.time.time',return_value=100010):
            self.assertEqual((await self.groups.select(bot,event))['mode'],'followup')

    async def test_v1_migration_preserves_caps_bots_and_existing_attempts(self):
        from src.plugins.chat_and_draw.participation_config import LEGACY
        values={f['key']:f['default'] for f in LEGACY}
        values.update(GROUP_CHAT_GLOBAL_DAILY_LIMIT=24,BOT_INTERACTION_DAILY_LIMIT=6)
        self.file.write_text(json.dumps({'schema_version':1,'revision':'old','values':values}),encoding='utf-8')
        migrated=managed()
        self.assertEqual(migrated['GROUP_CHAT_AMBIENT_GLOBAL_DAILY_LIMIT'],24)
        self.assertEqual(migrated['BOT_INTERACTION_DAILY_LIMIT'],6)
        # Pre-upgrade counters stay in the same table and retain mode ownership.
        with self.groups.connect() as conn:
            conn.execute('INSERT INTO group_chat_calls VALUES(?,?,?)',('room','nickname',100000))
        migrated['GROUP_CHAT_NICKNAME_DAILY_LIMIT']=1
        self.file.write_text(json.dumps({'schema_version':2,'values':migrated}),encoding='utf-8')
        self.assertIsNone(self.groups.reserve('room','nickname',100010))
        self.assertEqual(validate({'schema_version':1,'revision':'old','values':values})['schema_version'],2)
