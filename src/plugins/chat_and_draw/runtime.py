"""Durable run/message/task records. Context stays bound to the originating chat."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sqlite3
import time
import uuid
from nonebot import logger

from .config import ROOT

ARTIFACT_DIR = ROOT / "data" / "artifacts"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, session_key TEXT NOT NULL, character TEXT NOT NULL,
 started REAL NOT NULL, ended REAL, status TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS run_events (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS event_run ON run_events(run_id,id);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, session_key TEXT NOT NULL,
 character TEXT NOT NULL, direction TEXT NOT NULL, external_id TEXT,
 content TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS message_dedupe ON messages(session_key,direction,external_id)
 WHERE external_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, session_key TEXT NOT NULL,
 character TEXT NOT NULL, status TEXT NOT NULL, prompt_id TEXT,
 artifact TEXT, tags TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS task_run ON tasks(run_id);
"""


class RuntimeStore:
    def __init__(self, path):
        self.path = Path(path)

    async def call(self, fn):
        def work():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(SCHEMA)
                with conn:
                    return fn(conn)
            finally:
                conn.close()
        return await asyncio.to_thread(work)

    async def start(self, key, character, text, external_id=None):
        run_id = uuid.uuid4().hex
        def write(conn):
            if external_id is not None and conn.execute(
                "SELECT 1 FROM messages WHERE session_key=? AND direction='in' AND external_id=?",
                (key, str(external_id))).fetchone():
                return None
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,NULL,'running',NULL)",
                         (run_id, key, character, time.time()))
            try:
                conn.execute("INSERT INTO messages(run_id,session_key,character,direction,external_id,content,status,created) VALUES (?,?,?,'in',?,?,'received',?)",
                             (run_id, key, character, str(external_id) if external_id is not None else None, text[:50000], time.time()))
            except sqlite3.IntegrityError:
                conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
                return None
            return run_id
        return await self.call(write)

    async def event(self, run_id, stage, status, detail):
        await self.call(lambda c: c.execute("INSERT INTO run_events(run_id,stage,status,detail,created) VALUES (?,?,?,?,?)",
                                           (run_id, stage, status, json.dumps(detail, ensure_ascii=False), time.time())))

    async def outgoing(self, scope, kind, content, status, external_id=None):
        await self.call(lambda c: c.execute(
            "INSERT OR IGNORE INTO messages(run_id,session_key,character,direction,external_id,content,status,created) VALUES (?,?,?,'out',?,?,?,?)",
            (scope.run_id, scope.key, scope.character, str(external_id) if external_id is not None else None,
             content[:50000] if kind == "text" else f"[{kind}]", status, time.time())))

    async def finish(self, run_id, error=None):
        def write(conn):
            pending = conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND status IN ('queued','running')", (run_id,)).fetchone()
            failures = conn.execute("SELECT 1 FROM run_events WHERE run_id=? AND status='failed'", (run_id,)).fetchone()
            failures = failures or conn.execute(
                "SELECT 1 FROM tasks WHERE run_id=? AND status IN ('failed','delivery_failed','interrupted')", (run_id,)).fetchone()
            status = "waiting" if pending else "failed" if error else "partial" if failures else "completed"
            conn.execute("UPDATE runs SET status=?,ended=?,error=COALESCE(?,error) WHERE id=?",
                         (status, None if pending else time.time(), error, run_id))
            conn.execute("UPDATE messages SET status=? WHERE run_id=? AND direction='in'", (status, run_id))
        await self.call(write)

    async def create_task(self, scope, tags):
        identity = uuid.uuid4().hex
        await self.call(lambda c: c.execute("INSERT INTO tasks VALUES (?,?,?,?,'queued',NULL,NULL,?,?,?,NULL)",
                                           (identity, scope.run_id, scope.key, scope.character, tags[:3000], time.time(), time.time())))
        return identity

    async def task_update(self, identity, **values):
        values = {k: v for k, v in values.items() if k in {"status", "prompt_id", "artifact", "error"}}
        if not values:
            return
        values["updated"] = time.time()
        assignments = ",".join(k + "=?" for k in values)
        await self.call(lambda c: c.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*values.values(), identity)))

    async def task(self, identity):
        return await self.call(lambda c: dict(row) if (row := c.execute("SELECT * FROM tasks WHERE id=?", (identity,)).fetchone()) else None)

    async def list_rows(self, table, limit=50):
        if table not in {"runs", "tasks", "messages"}:
            raise ValueError("Unknown table")
        order = "started" if table == "runs" else "created"
        return await self.call(lambda c: [dict(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT ?", (min(max(limit, 1), 100),))])

    async def run_detail(self, identity):
        def read(conn):
            run = conn.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
            if not run:
                return None
            return {"run": dict(run), "events": [dict(row) for row in conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id", (identity,))],
                    "messages": [dict(row) for row in conn.execute("SELECT * FROM messages WHERE run_id=? ORDER BY id", (identity,))]}
        return await self.call(read)


@dataclass
class Scope:
    store: RuntimeStore
    run_id: str
    key: str
    character: str
    task_id: str | None = None


current_scope = ContextVar("qq_runtime_scope", default=None)


@asynccontextmanager
async def operation(store, key, character, text, external_id=None):
    run_id = await store.start(key, character, text, external_id)
    if run_id is None:
        yield None
        return
    scope = Scope(store, run_id, key, character)
    token = current_scope.set(scope)
    error = None
    try:
        yield scope
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        current_scope.reset(token)
        await store.finish(run_id, error)


async def record(stage, status, **detail):
    scope = current_scope.get()
    if scope:
        try:
            await scope.store.event(scope.run_id, stage, status, detail)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Runtime audit unavailable: {}", type(exc).__name__)


async def task_update(**values):
    scope = current_scope.get()
    if scope and scope.task_id:
        await scope.store.task_update(scope.task_id, **values)


async def save_artifact(data):
    scope = current_scope.get()
    if not scope or not scope.task_id:
        return None
    def save():
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        path = ARTIFACT_DIR / f"{scope.task_id}.png"
        path.write_bytes(data)
        return str(path.resolve())
    path = await asyncio.to_thread(save)
    await scope.store.task_update(scope.task_id, artifact=path)
    return path
