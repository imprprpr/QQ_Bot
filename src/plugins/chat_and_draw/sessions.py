"""SQLite sessions, isolated by bot, conversation and participant.

Use one bot process per database. Locks serialize each conversation within that
process; blocking SQLite operations run in worker threads.
"""
import asyncio
import json
import random
import time
import os
from copy import deepcopy
from dataclasses import dataclass, field, fields
from pathlib import Path
from weakref import WeakValueDictionary
from .config import HISTORY_CHAR_LIMIT, HISTORY_LIMIT, MAX_IMAGES
from .database import database
SCHEMA = '\nCREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, data TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS role_states (session_key TEXT NOT NULL, character TEXT NOT NULL,\n data TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(session_key,character));\n'
ROLE_FIELDS = ('history', 'last_image_prompt', 'memories', 'summary')

def session_key(bot, event) -> str:
    group = getattr(event, 'group_id', None)
    return json.dumps([str(bot.self_id), 'group' if group is not None else 'private', str(group) if group is not None else '', str(event.get_user_id())], ensure_ascii=False, separators=(',', ':'))

def trim_history(history: list[dict]) -> list[dict]:
    result = []
    remaining = HISTORY_CHAR_LIMIT
    for item in reversed(history[-HISTORY_LIMIT:]):
        if remaining <= 0:
            break
        content = str(item.get('content', ''))[-remaining:]
        trimmed = {'role': item['role'], 'content': content}
        if item.get('images') and item['role'] == 'user':
            trimmed['images'] = list(item['images'])[:MAX_IMAGES]
        result.append(trimmed)
        remaining -= len(content)
    result.reverse()
    return result

@dataclass
class Session:
    key: str
    bot_id: str = ''
    user_id: str = ''
    private: bool = True
    character: str | None = None
    last_image_prompt: str | None = None
    last_sticker_time: float = 0
    history: list[dict] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    summary: str = ''
    proactive: bool = False
    last_interaction: float = 0
    next_proactive: float = 0

    def touch(self):
        self.last_interaction = time.time()
        self.next_proactive = self.last_interaction + random.uniform(3600, 7200)

class SessionStore:

    def __init__(self, path: Path):
        self.path = Path(path)
        self._locks = WeakValueDictionary()
        self.database = database(path)

    def lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _connect(self):
        self.initialize()
        return self.database.connect()

    def initialize(self):
        self.database.initialize('sessions-v1', SCHEMA)

    @staticmethod
    def role_id(value=None):
        from .tavern import tavern_manager
        value = value or os.getenv('DEFAULT_CHARACTER', 'ling')
        card = tavern_manager.cards.get(value)
        return Path(card.filepath).stem if card and card.filepath else value

    @staticmethod
    def _role_data(session):
        return {name: deepcopy(getattr(session, name)) for name in ROLE_FIELDS}

    @staticmethod
    def _metadata(session):
        return {item.name: getattr(session, item.name) for item in fields(session) if item.name not in ROLE_FIELDS}

    def _save_conn(self, conn, session):
        session.character = self.role_id(session.character)
        conn.execute('INSERT OR REPLACE INTO sessions(key,data) VALUES (?,?)', (session.key, json.dumps(self._metadata(session), ensure_ascii=False)))
        conn.execute('INSERT OR REPLACE INTO role_states VALUES (?,?,?,?)', (session.key, session.character, json.dumps(self._role_data(session), ensure_ascii=False), time.time()))

    async def load(self, key: str) -> Session:

        def read():
            self.initialize()
            with self.database.transaction() as conn:
                row = conn.execute('SELECT data FROM sessions WHERE key=?', (key,)).fetchone()
                session = Session(**json.loads(row[0])) if row else Session(key=key)
                session.character = self.role_id(session.character)
                role = conn.execute('SELECT data FROM role_states WHERE session_key=? AND character=?', (key, session.character)).fetchone()
                if role:
                    for (name, value) in json.loads(role[0]).items():
                        setattr(session, name, value)
                elif row:
                    conn.execute('INSERT OR IGNORE INTO role_states VALUES (?,?,?,?)', (key, session.character, json.dumps(self._role_data(session), ensure_ascii=False), time.time()))
                if row and any((name in json.loads(row[0]) for name in ROLE_FIELDS)):
                    conn.execute('UPDATE sessions SET data=? WHERE key=?', (json.dumps(self._metadata(session), ensure_ascii=False), key))
                return session
        return await asyncio.to_thread(read)

    async def save(self, session: Session):
        session.history = trim_history(session.history)
        snapshot = deepcopy(session)

        def write():
            self.initialize()
            with self.database.transaction() as conn:
                self._save_conn(conn, snapshot)
        await asyncio.to_thread(write)

    async def switch_character(self, session, character, greeting=''):
        """Persist old role and select/restore the new role in one transaction."""
        selected = self.role_id(character)
        snapshot = deepcopy(session)

        def switch():
            self.initialize()
            with self.database.transaction() as conn:
                self._save_conn(conn, snapshot)
                row = conn.execute('SELECT data FROM role_states WHERE session_key=? AND character=?', (snapshot.key, selected)).fetchone()
                values = json.loads(row[0]) if row else {'history': [{'role': 'assistant', 'content': greeting}] if greeting else [], 'last_image_prompt': None, 'memories': [], 'summary': ''}
                snapshot.character = selected
                for (name, value) in values.items():
                    setattr(snapshot, name, value)
                snapshot.touch()
                self._save_conn(conn, snapshot)
                return (snapshot, bool(row))
        return await asyncio.to_thread(switch)

    async def update_memory(self, key, character, memories, summary):
        """Call while holding the conversation lock; preserves inactive-role history."""
        selected = self.role_id(character)

        def write():
            self.initialize()
            with self.database.transaction() as conn:
                row = conn.execute('SELECT data FROM role_states WHERE session_key=? AND character=?', (key, selected)).fetchone()
                if not row:
                    raise ValueError('Role memory does not exist')
                data = json.loads(row[0])
                data.update(memories=memories, summary=summary)
                conn.execute('UPDATE role_states SET data=?,updated=? WHERE session_key=? AND character=?', (json.dumps(data, ensure_ascii=False), time.time(), key, selected))
        await asyncio.to_thread(write)

    async def finish_image(self, key, character, tags, status):

        def write():
            self.initialize()
            with self.database.transaction() as conn:
                row = conn.execute('SELECT data FROM role_states WHERE session_key=? AND character=?', (key, character)).fetchone()
                if not row:
                    return
                data = json.loads(row[0])
                if tags:
                    data['last_image_prompt'] = tags[:3000]
                data['history'] = trim_history(data.get('history', []) + [{'role': 'assistant', 'content': status}])
                conn.execute('UPDATE role_states SET data=?,updated=? WHERE session_key=? AND character=?', (json.dumps(data, ensure_ascii=False), time.time(), key, character))
        await asyncio.to_thread(write)

    async def due(self, now: float) -> list[str]:

        def read():
            self.initialize()
            with self.database.transaction() as conn:
                sessions = [Session(**json.loads(row[0])) for row in conn.execute('SELECT data FROM sessions')]
                return [s.key for s in sessions if s.private and s.proactive and (0 < s.next_proactive <= now)]
        return await asyncio.to_thread(read)

    async def migrate_legacy(self):

        def keys():
            self.initialize()
            with self.database.transaction() as conn:
                return [row[0] for row in conn.execute('SELECT key FROM sessions')]
        for key in await asyncio.to_thread(keys):
            await self.load(key)
