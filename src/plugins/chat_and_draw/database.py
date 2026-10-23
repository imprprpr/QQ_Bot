"""One per-file coordinator for migrations and short SQLite transactions.

Network/model calls must never execute while holding a database transaction.
Connections remain thread-local to each operation, not shared across workers.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from weakref import WeakValueDictionary

_registry = WeakValueDictionary()
_registry_lock = threading.Lock()


def database(path):
    key = str(Path(path).resolve())
    with _registry_lock:
        instance = _registry.get(key)
        if instance is None:
            instance = Database(Path(key))
            _registry[key] = instance
        return instance


class Database:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._ready = set()

    def initialize(self, name, schema):
        with self._lock:
            if name in self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=15)
            try:
                conn.execute('PRAGMA busy_timeout=15000')
                conn.execute('PRAGMA journal_mode=WAL')
                conn.executescript('BEGIN IMMEDIATE;\n' + schema + '\nCOMMIT;')
            finally:
                conn.close()
            self._ready.add(name)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')
        return conn

    @contextmanager
    def transaction(self):
        # Serialize all in-process writers, including read-modify-write operations.
        with self._lock:
            conn = self.connect()
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    yield conn
            finally:
                conn.close()
