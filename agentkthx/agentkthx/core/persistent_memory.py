"""
⚛️ AgentKthx — Persistent Memory

SQLite-backed conversation memory that survives across sessions.
Sessions can be saved, loaded, listed, and deleted.

The sliding window still applies to what gets sent to the model (via
Memory._prune_if_needed), but ALL messages are retained in the
database for full conversation history.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .memory import Memory, MemoryConfig, Message


_DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".agentkthx")
_DEFAULT_DB_NAME = "memory.db"


def _get_db_path(db_path: str | None = None) -> str:
    """Resolve the database path.

    SEC-07 (R07.05): the default ``~/.agentkthx/`` directory is now created
    with mode 0o700 (owner-only read/write/execute). Previously it inherited
    the umask, which on most systems is 0755 — readable by all local users.
    The SQLite DB file itself is chmod'd to 0o600 in ``_get_conn()`` after
    the connection is opened.
    """
    if db_path:
        return os.path.expanduser(db_path)
    # SEC-07: mode=0o700 so other local users can't read the conversation DB.
    # exist_ok=True so we don't fail if it already exists (the chmod below
    # will still tighten permissions on an existing dir).
    os.makedirs(_DEFAULT_DB_DIR, mode=0o700, exist_ok=True)
    # makedirs mode is masked by umask on some platforms — re-chmod to be sure.
    try:
        os.chmod(_DEFAULT_DB_DIR, 0o700)
    except OSError:
        pass
    return os.path.join(_DEFAULT_DB_DIR, _DEFAULT_DB_NAME)


def _init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            model        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            metadata     TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT NOT NULL,
            seq           INTEGER NOT NULL,
            role          TEXT NOT NULL,
            content       TEXT DEFAULT '',
            tool_calls    TEXT,
            tool_call_id  TEXT,
            name          TEXT,
            timestamp     TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            UNIQUE(session_id, seq)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session
        ON messages(session_id, seq)
    """)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")


class PersistentMemory(Memory):
    """
    SQLite-backed conversation memory.

    Behaves exactly like Memory (same sliding window, same API) but
    every message is also persisted to a SQLite database. The full
    conversation history is retained in the DB even when the in-memory
    sliding window prunes older messages.

    Usage::

        # Create or resume a session
        mem = PersistentMemory(session_id="my-session")
        mem.add("user", "Hello!")
        mem.add("assistant", "Hi there!")
        # ... agent loop ...

        # Later, in a new process:
        mem = PersistentMemory(session_id="my-session")
        mem.load()  # restore from DB
        messages = mem.get_messages()  # full history available

    Class methods for session management::

        PersistentMemory.list_sessions()
        PersistentMemory.delete_session("my-session")
    """

    def __init__(
        self,
        session_id: str | None = None,
        db_path: str | None = None,
        config: MemoryConfig | None = None,
        auto_save: bool = True,
    ):
        """
        Initialize persistent memory.

        Parameters
        ----------
        session_id : str, optional
            Session to resume. If None, a new UUID is generated.
            Call load() after init to restore messages from DB.
        db_path : str, optional
            Path to the SQLite database file. Defaults to
            ~/.agentkthx/memory.db.
        config : MemoryConfig, optional
            In-memory sliding window config (same as Memory).
        auto_save : bool
            If True, every add() also writes to SQLite.
            Set False for bulk operations, then call save() manually.
        """
        super().__init__(config)

        self._db_path = _get_db_path(db_path)
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._auto_save = auto_save
        self._db: sqlite3.Connection | None = None
        # ROB-03 (R07.05): write-lock to prevent sqlite3.OperationalError
        # "database is locked" when multiple threads share a PersistentMemory
        # instance (e.g. Orchestrator parallel mode). SQLite serializes writes
        # via file locking, but concurrent execute() calls from multiple
        # threads can still trip the busy_timeout. This Lock ensures only
        # one thread writes at a time. Reads are lock-free (SQLite handles
        # concurrent reads natively).
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Database connection (lazy)                                      #
    # ------------------------------------------------------------------ #

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy-open the database connection.

        SEC-07 (R07.05): after opening the connection, the DB file is
        chmod'd to 0o600 (owner-only read/write). Previously it inherited
        the umask, which on most systems is 0644 — readable by all local
        users. The DB stores the full conversation history including any
        secrets the user typed (API keys, tokens, passwords pasted into
        chat).
        """
        if self._db is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._db = sqlite3.connect(self._db_path, check_same_thread=False)
            _init_db(self._db)
            # SEC-07: tighten file permissions to owner-only. This runs
            # after _init_db so the file exists. On filesystems that don't
            # support chmod (Windows), this is a no-op.
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                pass
        return self._db

    def close(self) -> None:
        """Close the database connection."""
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    # ------------------------------------------------------------------ #
    #  Override add methods to persist to SQLite                         #
    # ------------------------------------------------------------------ #

    def add(self, role: str, content: str, **kwargs) -> None:
        """Add a message and persist to SQLite (if auto_save)."""
        super().add(role, content, **kwargs)
        if self._auto_save:
            self._write_message(role, content, **kwargs)
            self._touch_session()

    def add_tool_call(self, role: str, content: str, tool_calls: list[dict]) -> None:
        """Add a tool call message and persist."""
        super().add_tool_call(role, content, tool_calls)
        if self._auto_save:
            self._write_message(role, content, tool_calls=tool_calls)
            self._touch_session()

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result message and persist."""
        super().add_tool_result(tool_call_id, name, content)
        if self._auto_save:
            self._write_message("tool", content, tool_call_id=tool_call_id, name=name)
            self._touch_session()

    def clear(self) -> None:
        """Clear in-memory messages and delete messages from DB.

        ROB-03 (R07.05): wraps the DELETE in ``self._write_lock``.
        """
        super().clear()
        if self._session_id:
            with self._write_lock:
                try:
                    conn = self._get_conn()
                    conn.execute(
                        "DELETE FROM messages WHERE session_id = ?",
                        (self._session_id,),
                    )
                    conn.commit()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    #  Persistence: save, load, delete                                   #
    # ------------------------------------------------------------------ #

    def save(self) -> str:
        """
        Save all in-memory messages to the database.

        This is idempotent: messages already in the DB (by session_id + seq)
        are skipped, so calling save() multiple times from different
        PersistentMemory instances with the same session_id is safe.

        ROB-03 (R07.05): wraps all writes in ``self._write_lock``.

        Returns the session_id.
        """
        with self._write_lock:
            conn = self._get_conn()

            # Ensure session row exists
            conn.execute(
                """INSERT INTO sessions (session_id, created_at, updated_at, message_count)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (
                    self._session_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    len(self._messages),
                ),
            )

            # Get existing seq numbers for this session
            existing = set()
            for row in conn.execute(
                "SELECT seq FROM messages WHERE session_id = ?", (self._session_id,)
            ):
                existing.add(row[0])

            # Insert only messages not already in DB
            seq = 0
            for msg in self._messages:
                seq += 1
                if seq in existing:
                    continue
                tool_calls_json = json.dumps(msg.tool_calls) if msg.tool_calls else None
                conn.execute(
                    """INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, name, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self._session_id,
                        seq,
                        msg.role,
                        msg.content,
                        tool_calls_json,
                        msg.tool_call_id,
                        msg.name,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

            # Also persist system prompt if present
            if self._system_prompt:
                conn.execute(
                    """INSERT INTO messages (session_id, seq, role, content, timestamp)
                       VALUES (?, 0, ?, ?, ?)
                       ON CONFLICT(session_id, seq) DO UPDATE SET content = excluded.content""",
                    (
                        self._session_id,
                        "system",
                        self._system_prompt,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

            conn.commit()
            return self._session_id

    def load(self) -> int:
        """
        Load messages from the database into memory.

        Returns the number of messages loaded.
        """
        conn = self._get_conn()

        rows = conn.execute(
            "SELECT seq, role, content, tool_calls, tool_call_id, name FROM messages WHERE session_id = ? ORDER BY seq",
            (self._session_id,),
        ).fetchall()

        if not rows:
            return 0

        self._messages = []
        self._system_prompt = None

        for row in rows:
            seq, role, content, tool_calls_json, tool_call_id, name = row
            tool_calls = json.loads(tool_calls_json) if tool_calls_json else None

            if role == "system":
                self._system_prompt = content
            else:
                kwargs = {}
                if tool_calls is not None:
                    kwargs["tool_calls"] = tool_calls
                if tool_call_id:
                    kwargs["tool_call_id"] = tool_call_id
                if name:
                    kwargs["name"] = name
                self._messages.append(Message(role=role, content=content, **kwargs))

        self._prune_if_needed()
        return len(self._messages)

    @staticmethod
    def list_sessions(db_path: str | None = None) -> list[dict]:
        """
        List all saved sessions.

        Returns a list of dicts with session metadata.
        """
        path = _get_db_path(db_path)
        if not os.path.exists(path):
            return []

        conn = sqlite3.connect(path)
        try:
            _init_db(conn)
            rows = conn.execute(
                "SELECT session_id, model, created_at, updated_at, message_count, metadata FROM sessions ORDER BY updated_at DESC",
            ).fetchall()

            sessions = []
            for row in rows:
                meta = {}
                try:
                    meta = json.loads(row[5]) if row[5] else {}
                except Exception:
                    pass
                sessions.append({
                    "session_id": row[0],
                    "model": row[1],
                    "created_at": row[2],
                    "updated_at": row[3],
                    "message_count": row[4],
                    "metadata": meta,
                })
            return sessions
        finally:
            conn.close()

    @staticmethod
    def delete_session(session_id: str, db_path: str | None = None) -> bool:
        """
        Delete a session and all its messages.

        Returns True if deleted, False if not found.
        """
        path = _get_db_path(db_path)
        if not os.path.exists(path):
            return False

        conn = sqlite3.connect(path)
        try:
            _init_db(conn)
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    #  Properties                                                      #
    # ------------------------------------------------------------------ #

    @property
    def session_id(self) -> str:
        """The current session identifier."""
        return self._session_id

    @property
    def db_path(self) -> str:
        """Path to the SQLite database."""
        return self._db_path

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                 #
    # ------------------------------------------------------------------ #

    def _write_message(self, role: str, content: str, **kwargs) -> None:
        """Write a single message to SQLite.

        ROB-03 (R07.05): wraps all writes in ``self._write_lock`` to prevent
        ``sqlite3.OperationalError: database is locked`` when multiple
        threads share a PersistentMemory instance.
        """
        with self._write_lock:
            conn = self._get_conn()

            # Ensure session row exists
            conn.execute(
                """INSERT INTO sessions (session_id, created_at, updated_at, message_count)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (
                    self._session_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    len(self._messages),
                ),
            )

            # Get next seq number
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?",
                (self._session_id,),
            ).fetchone()
            seq = (row[0] if row else 0) + 1

            tool_calls_json = json.dumps(kwargs.get("tool_calls")) if kwargs.get("tool_calls") else None

            conn.execute(
                """INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, name, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._session_id,
                    seq,
                    role,
                    content,
                    tool_calls_json,
                    kwargs.get("tool_call_id"),
                    kwargs.get("name"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def _touch_session(self) -> None:
        """Update the session's updated_at timestamp.

        ROB-03 (R07.05): wraps the write in ``self._write_lock``.
        """
        with self._write_lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    "UPDATE sessions SET updated_at = ?, message_count = ? WHERE session_id = ?",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        len(self._messages),
                        self._session_id,
                    ),
                )
                conn.commit()
            except Exception:
                pass

    def __repr__(self) -> str:
        return f"PersistentMemory(session={self._session_id!r}, messages={len(self._messages)}, db={self._db_path!r})"
