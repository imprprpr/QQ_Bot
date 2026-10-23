"""Local group participation gates; observing a message never calls a model."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import re
import time
import unicodedata

from .config import feature_enabled
from .database import database


def number(name, default, minimum=0):
    return max(minimum, float(os.getenv(name, str(default))))


class GroupChat:
    def __init__(self, path):
        self.path = Path(path)
        self.database = database(path)

    @contextmanager
    def connect(self):
        self.database.initialize('group-chat-v1', '''
        CREATE TABLE IF NOT EXISTS group_chat_lines (
          id INTEGER PRIMARY KEY, room TEXT, message_id TEXT, speaker TEXT, text TEXT, created REAL,
          UNIQUE(room,message_id));
        CREATE INDEX IF NOT EXISTS group_chat_recent ON group_chat_lines(room,created);
        CREATE TABLE IF NOT EXISTS group_chat_limits (room TEXT PRIMARY KEY, last_attempt REAL);
        CREATE TABLE IF NOT EXISTS group_chat_calls (room TEXT, mode TEXT, created REAL);
        CREATE INDEX IF NOT EXISTS group_chat_call_time ON group_chat_calls(created);
        ''')
        with self.database.transaction() as conn:
            yield conn

    @staticmethod
    def room(bot, event):
        return json.dumps([str(bot.self_id), str(event.group_id)], separators=(',', ':'))

    @staticmethod
    def allowed(event):
        groups = {value.strip() for value in os.getenv('GROUP_CHAT_GROUPS', '').split(',') if value.strip()}
        return feature_enabled('GROUP_CHAT_ENABLED') and (not groups or str(event.group_id) in groups)

    async def observe(self, bot, event):
        if not self.allowed(event) or str(event.get_user_id()) == str(bot.self_id):
            return
        text = event.get_message().extract_plain_text().strip()
        if not text:
            return
        sender = getattr(event, 'sender', None)
        name = getattr(sender, 'card', None) or getattr(sender, 'nickname', None) or str(event.get_user_id())
        message_id = str(getattr(event, 'message_id', '')) or None
        await asyncio.to_thread(self.add, self.room(bot, event), message_id,
                                str(event.get_user_id()) + ':' + str(name)[:40], text, time.time())

    def add(self, room, identity, speaker, text, now):
        with self.connect() as conn:
            conn.execute('DELETE FROM group_chat_lines WHERE created<?', (now - 1800,))
            conn.execute('DELETE FROM group_chat_calls WHERE created<?', (now - 86400,))
            conn.execute('DELETE FROM group_chat_limits WHERE last_attempt<?', (now - 86400,))
            conn.execute('INSERT OR IGNORE INTO group_chat_lines(room,message_id,speaker,text,created) VALUES(?,?,?,?,?)',
                         (room, identity, speaker, text[:300], now))
            conn.execute('DELETE FROM group_chat_lines WHERE room=? AND id NOT IN '
                         '(SELECT id FROM group_chat_lines WHERE room=? ORDER BY id DESC LIMIT 20)', (room, room))
            # Bound disk use even if an installation joins many groups.
            conn.execute('DELETE FROM group_chat_lines WHERE id NOT IN '
                         '(SELECT id FROM group_chat_lines ORDER BY id DESC LIMIT 10000)')

    def cleanup(self):
        now = time.time()
        with self.connect() as conn:
            conn.execute('DELETE FROM group_chat_lines WHERE created<?', (now - 1800,))
            conn.execute('DELETE FROM group_chat_calls WHERE created<?', (now - 86400,))
            conn.execute('DELETE FROM group_chat_limits WHERE last_attempt<?', (now - 86400,))

    async def select(self, bot, event):
        if str(event.get_user_id()) == str(bot.self_id):
            return None
        room = self.room(bot, event)
        original = getattr(event, 'original_message', None)
        message = original if original is not None else event.get_message()
        text = unicodedata.normalize('NFKC', message.extract_plain_text())
        mentioned = any(segment.type == 'at' and str(segment.data.get('qq')) == str(bot.self_id)
                        for segment in message)
        reply = getattr(event, 'reply', None)
        replying = str(getattr(getattr(reply, 'sender', None), 'user_id', '')) == str(bot.self_id)
        if mentioned or replying or (original is None and event.is_tome()):
            return await asyncio.to_thread(self.reserve, room, 'mention', time.time())
        if not self.allowed(event) or not text.strip() or text.lstrip().startswith(('/', '／')):
            return None
        # A message addressed to someone else is not an invitation to interrupt.
        if any(segment.type == 'at' for segment in message):
            return None
        aliases = [unicodedata.normalize('NFKC', value.strip()) for value in
                   os.getenv('GROUP_CHAT_NICKNAMES', '小灯,玲').split(',') if value.strip()]
        named = any((re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', text, re.I)
                     if alias.isascii() else alias in text) for alias in aliases)
        mode = 'nickname' if named else 'ambient'
        if mode == 'ambient':
            if not feature_enabled('GROUP_CHAT_AMBIENT_ENABLED'):
                return None
            if not re.search(r'大家|你们|有没有人|有人知道|求推荐|求助|怎么办|好无聊|聊点|有人吗', text):
                return None
            if random.random() >= min(1, number('GROUP_CHAT_PROBABILITY', .12)):
                return None
        return await asyncio.to_thread(self.reserve, room, mode, time.time())

    def reserve(self, room, mode, now):
        """Reserve before generation; failed calls still count, including after restarts."""
        with self.connect() as conn:
            lines = conn.execute('SELECT speaker,text,created FROM group_chat_lines WHERE room=? AND created>=? '
                                 'ORDER BY id DESC LIMIT 12', (room, now - 1800)).fetchall()[::-1]
            if mode != 'mention':
                last = conn.execute('SELECT last_attempt FROM group_chat_limits WHERE room=?', (room,)).fetchone()
                cooldown = number('GROUP_CHAT_COOLDOWN', 300) if mode == 'ambient' else number('GROUP_CHAT_NICKNAME_COOLDOWN', 45)
                if last and now - last[0] < cooldown:
                    return None
                hourly = conn.execute('SELECT COUNT(*) FROM group_chat_calls WHERE room=? AND created>?', (room, now - 3600)).fetchone()[0]
                global_hourly = conn.execute('SELECT COUNT(*) FROM group_chat_calls WHERE created>?', (now - 3600,)).fetchone()[0]
                if hourly >= number('GROUP_CHAT_HOURLY_LIMIT', 6) or global_hourly >= number('GROUP_CHAT_GLOBAL_HOURLY_LIMIT', 20):
                    return None
                if mode == 'ambient':
                    recent = [line for line in lines if line[2] >= now - 300 and not line[0].startswith('bot:')]
                    counts = conn.execute("SELECT SUM(created>?),COUNT(*) FROM group_chat_calls WHERE room=? AND mode='ambient' AND created>?",
                                          (now - 3600, room, now - 86400)).fetchone()
                    if (len(recent) < 4 or len({line[0].split(':', 1)[0] for line in recent}) < 2
                            or (counts[0] or 0) >= number('GROUP_CHAT_AMBIENT_HOURLY_LIMIT', 2)
                            or counts[1] >= number('GROUP_CHAT_AMBIENT_DAILY_LIMIT', 8)):
                        return None
                conn.execute('INSERT INTO group_chat_calls VALUES(?,?,?)', (room, mode, now))
            conn.execute('INSERT OR REPLACE INTO group_chat_limits VALUES(?,?)', (room, now))
            # Only public group messages enter this shared, expiring context.
            return {'mode': mode, 'messages': [{'speaker': row[0], 'text': row[1]} for row in lines]}
