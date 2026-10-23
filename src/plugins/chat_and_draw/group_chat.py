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

    def public_context(self, room, now):
        with self.connect() as conn:
            rows = conn.execute('SELECT speaker,text FROM group_chat_lines WHERE room=? AND created>=? ORDER BY id DESC LIMIT 12', (room, now-1800)).fetchall()[::-1]
            return [{'speaker': row[0], 'text': row[1]} for row in rows]

    def recent_activity(self, room, now):
        with self.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM group_chat_lines WHERE room=? AND created>=? AND speaker NOT LIKE 'bot:%' LIMIT 1", (room, now-900)).fetchone())

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
            result = await asyncio.to_thread(self.reserve, room, 'mention', time.time())
            result['current_speaker'] = str(event.get_user_id())
            return result
        if not self.allowed(event) or not text.strip() or text.lstrip().startswith(('/', '／')):
            return None
        # A message addressed to someone else is not an invitation to interrupt.
        if any(segment.type == 'at' for segment in message):
            return None
        if reply is not None and getattr(getattr(reply, 'sender', None), 'user_id', None) is not None:
            return None
        aliases = [unicodedata.normalize('NFKC', value.strip()) for value in
                   os.getenv('GROUP_CHAT_NICKNAMES', '小灯,玲').split(',') if value.strip()]
        named = any((re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', text, re.I)
                     if alias.isascii() else alias in text) for alias in aliases)
        now = time.time()
        followup = not named and await asyncio.to_thread(self.is_followup, room, event.get_user_id(), now)
        mode = 'nickname' if named else ('followup' if followup else 'ambient')
        question = bool(re.search(r'[?？]|怎么|为什么|为啥|如何|能不能|有没有|有谁|谁知道|求助|求推荐|怎么办|有人吗|什么建议|推荐一下|能帮忙', text))
        if mode in {'ambient', 'followup'}:
            if not feature_enabled('GROUP_CHAT_AMBIENT_ENABLED'):
                return None
            # Questions and short follow-ups are evaluated deliberately; ordinary
            # public conversation is sampled before any model call.
            if mode == 'ambient' and not question:
                if len(text.strip()) < 4 or random.random() >= min(1, number('GROUP_CHAT_PROBABILITY', .25)):
                    return None
        result = await asyncio.to_thread(self.reserve, room, mode, now, question)
        if result is not None:
            result['current_speaker'] = str(event.get_user_id())
        return result

    def is_followup(self, room, user, now):
        with self.connect() as conn:
            last = conn.execute("SELECT speaker,created FROM group_chat_lines WHERE room=? AND speaker LIKE 'bot:%' ORDER BY id DESC LIMIT 1", (room,)).fetchone()
            return bool(last and last[0].endswith(':to:' + str(user))
                        and now - last[1] <= number('GROUP_CHAT_FOLLOWUP_WINDOW', 180))

    def reserve(self, room, mode, now, question=False):
        """Reserve before generation; failed calls still count, including after restarts."""
        with self.connect() as conn:
            lines = conn.execute('SELECT speaker,text,created FROM group_chat_lines WHERE room=? AND created>=? '
                                 'ORDER BY id DESC LIMIT 12', (room, now - 1800)).fetchall()[::-1]
            if mode != 'mention':
                last = conn.execute('SELECT last_attempt FROM group_chat_limits WHERE room=?', (room,)).fetchone()
                cooldown = (number('GROUP_CHAT_COOLDOWN', 180) if mode == 'ambient' else
                            number('GROUP_CHAT_FOLLOWUP_COOLDOWN', 5) if mode == 'followup' else
                            number('GROUP_CHAT_NICKNAME_COOLDOWN', 45))
                if last and now - last[0] < cooldown:
                    return None
                hourly = conn.execute('SELECT COUNT(*) FROM group_chat_calls WHERE room=? AND created>?', (room, now - 3600)).fetchone()[0]
                global_hourly = conn.execute('SELECT COUNT(*) FROM group_chat_calls WHERE created>?', (now - 3600,)).fetchone()[0]
                if hourly >= number('GROUP_CHAT_HOURLY_LIMIT', 12) or global_hourly >= number('GROUP_CHAT_GLOBAL_HOURLY_LIMIT', 40):
                    return None
                if mode == 'ambient':
                    recent = [line for line in lines if line[2] >= now - 300 and not line[0].startswith('bot:')]
                    counts = conn.execute("SELECT SUM(created>?),COUNT(*) FROM group_chat_calls WHERE room=? AND mode='ambient' AND created>?",
                                          (now - 3600, room, now - 86400)).fetchone()
                    if ((not question and (len(recent) < 4 or len({line[0].split(':', 1)[0] for line in recent}) < 2))
                            or (counts[0] or 0) >= number('GROUP_CHAT_AMBIENT_HOURLY_LIMIT', 4)
                            or counts[1] >= number('GROUP_CHAT_AMBIENT_DAILY_LIMIT', 12)):
                        return None
                conn.execute('INSERT INTO group_chat_calls VALUES(?,?,?)', (room, mode, now))
            conn.execute('INSERT OR REPLACE INTO group_chat_limits VALUES(?,?)', (room, now))
            # Only public group messages enter this shared, expiring context.
            return {'mode': mode, 'messages': [{'speaker': row[0], 'text': row[1]} for row in lines]}
