"""Explicit bot peers with durable, room-wide conversation and API budgets."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import time
import uuid

from .config import feature_enabled
from .database import database
from .group_chat import number


def ids(name):
    return {value.strip() for value in os.getenv(name, '').split(',')
            if value.strip().isascii() and value.strip().isdigit() and int(value.strip()) > 0}


class BotInteractions:
    def __init__(self, path):
        self.path = Path(path)
        self.database = database(path)

    def initialize(self):
        self.database.initialize('bot-interaction-v1', '''
        CREATE TABLE IF NOT EXISTS bot_interaction_rooms (
          room TEXT PRIMARY KEY, peer TEXT, rounds INTEGER, active_until REAL,
          cooldown_until REAL, last_attempt REAL, waiting INTEGER, ticket TEXT);
        CREATE TABLE IF NOT EXISTS bot_interaction_calls (
          room TEXT, peer TEXT, kind TEXT, created REAL, external_id TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS bot_interaction_dedupe
          ON bot_interaction_calls(room,external_id) WHERE external_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS bot_interaction_time ON bot_interaction_calls(created);
        ''')

    @contextmanager
    def connect(self):
        self.initialize()
        with self.database.transaction() as conn:
            yield conn

    @staticmethod
    def is_peer(user):
        return str(user) in ids('BOT_INTERACTION_TARGETS')

    @staticmethod
    def allowed(bot_id, group_id, peer):
        return (feature_enabled('BOT_INTERACTION_ENABLED')
                and str(group_id) in ids('BOT_INTERACTION_GROUPS')
                and BotInteractions.is_peer(peer) and str(peer) != str(bot_id))

    @staticmethod
    def room(bot_id, group_id):
        return json.dumps([str(bot_id), str(group_id)], separators=(',', ':'))

    @staticmethod
    def max_rounds():
        return int(min(3, number('BOT_INTERACTION_MAX_ROUNDS', 2, 1)))

    async def select(self, bot, event):
        peer = str(event.get_user_id())
        if not self.allowed(bot.self_id, event.group_id, peer):
            return None
        message = getattr(event, 'original_message', None)
        message = message if message is not None else event.get_message()
        text = message.extract_plain_text().strip()
        if text.startswith(('/', '／')):
            return None
        ats = [str(seg.data.get('qq')) for seg in message if seg.type == 'at']
        reply = getattr(event, 'reply', None)
        reply_user = getattr(getattr(reply, 'sender', None), 'user_id', None)
        if any(value != str(bot.self_id) for value in ats) or (reply_user is not None and str(reply_user) != str(bot.self_id)):
            return None
        addressed = str(bot.self_id) in ats or str(reply_user) == str(bot.self_id) or event.is_tome()
        identity = str(getattr(event, 'message_id', '')) or None
        return await asyncio.to_thread(self.reserve, bot.self_id, event.group_id, peer,
                                       time.time(), 'reactive', identity, addressed)

    def reserve(self, bot_id, group_id, peer, now, kind, identity=None, addressed=False):
        if not self.allowed(bot_id, group_id, peer):
            return None
        room = self.room(bot_id, group_id)
        with self.connect() as conn:
            conn.execute('DELETE FROM bot_interaction_calls WHERE created<?', (now-86400,))
            conn.execute('DELETE FROM bot_interaction_rooms WHERE cooldown_until<? AND active_until<?', (now-86400, now-86400))
            if identity is not None and conn.execute('SELECT 1 FROM bot_interaction_calls WHERE room=? AND external_id=?', (room, identity)).fetchone():
                return None
            state = conn.execute('SELECT * FROM bot_interaction_rooms WHERE room=?', (room,)).fetchone()
            active = bool(state and now < state['active_until'])
            if active:
                if (kind != 'reactive' or state['peer'] != str(peer) or not state['waiting']
                        or state['rounds'] >= self.max_rounds()
                        or now-state['last_attempt'] < number('BOT_INTERACTION_REPLY_COOLDOWN', 5, 1)):
                    return None
                rounds, until, cooldown = state['rounds']+1, state['active_until'], state['cooldown_until']
            else:
                if state and now < state['cooldown_until']:
                    return None
                if kind == 'reactive' and not addressed and random.random() >= min(1, number('BOT_INTERACTION_REPLY_PROBABILITY', .3)):
                    return None
                rounds = 1
                until = now + min(600, number('BOT_INTERACTION_SESSION_SECONDS', 180, 30))
                cooldown = until + number('BOT_INTERACTION_COOLDOWN', 600, 60)
            hourly = conn.execute('SELECT COUNT(*) FROM bot_interaction_calls WHERE room=? AND created>?', (room, now-3600)).fetchone()[0]
            daily = conn.execute('SELECT COUNT(*) FROM bot_interaction_calls WHERE created>?', (now-86400,)).fetchone()[0]
            if hourly >= number('BOT_INTERACTION_HOURLY_LIMIT', 6) or daily >= number('BOT_INTERACTION_DAILY_LIMIT', 12):
                return None
            if kind == 'proactive':
                count = conn.execute("SELECT COUNT(*) FROM bot_interaction_calls WHERE kind='proactive' AND created>?", (now-86400,)).fetchone()[0]
                if count >= number('BOT_INTERACTION_PROACTIVE_DAILY_LIMIT', 3):
                    return None
            ticket = uuid.uuid4().hex
            conn.execute('INSERT INTO bot_interaction_calls VALUES(?,?,?,?,?)', (room, str(peer), kind, now, identity))
            conn.execute('INSERT OR REPLACE INTO bot_interaction_rooms VALUES(?,?,?,?,?,?,?,?)',
                         (room, str(peer), rounds, until, cooldown, now, 0, ticket))
            return {'mode': 'bot-interaction', 'target': str(peer), 'group_id': str(group_id),
                    'bot_id': str(bot_id), 'room': room, 'ticket': ticket, 'kind': kind,
                    'round': rounds, 'current_speaker': str(peer)}

    def valid(self, context, now):
        if not self.allowed(context['bot_id'], context['group_id'], context['target']):
            return False
        with self.connect() as conn:
            row = conn.execute('SELECT ticket,active_until FROM bot_interaction_rooms WHERE room=?', (context['room'],)).fetchone()
            return bool(row and row['ticket'] == context['ticket'] and now < row['active_until'])

    def finish(self, context, sent, now):
        with self.connect() as conn:
            conn.execute('UPDATE bot_interaction_rooms SET waiting=? WHERE room=? AND ticket=?',
                         (int(sent and context['round'] < self.max_rounds()), context['room'], context['ticket']))

    @staticmethod
    def proactive_window(now):
        offset = timezone(timedelta(hours=float(os.getenv('BOT_INTERACTION_UTC_OFFSET', '8'))))
        return 8 <= datetime.fromtimestamp(now, offset).hour < 22
