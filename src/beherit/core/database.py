"""SQLite Database Storage for Job History and Agent Activity."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from beherit.config import settings


class DatabaseManager:
    """Manages local SQLite database for background jobs, chat sessions, and audit trail."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (db_path or settings.BEHERIT_DB_PATH).resolve()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_slug TEXT NOT NULL,
                    status TEXT NOT NULL,
                    executed_at REAL NOT NULL,
                    duration_seconds REAL DEFAULT 0.0,
                    summary TEXT,
                    error_message TEXT,
                    report_path TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    agent_slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    agent_slug TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    thoughts TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );
                """
            )
            conn.commit()

    def create_chat_session(
        self,
        agent_slug: str,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Create or initialize a chat session."""
        sid = session_id or f"sess_{agent_slug}_{int(time.time() * 1000)}"
        stitle = title or "Nowa rozmowa"
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (id, agent_slug, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = ?
                """,
                (sid, agent_slug, stitle, now, now, now),
            )
            conn.commit()
        return sid

    def get_agent_sessions(self, agent_slug: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent chat sessions for an agent."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, agent_slug, title, created_at, updated_at
                FROM chat_sessions
                WHERE agent_slug = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (agent_slug, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_session_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Return ordered messages for a given session."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, session_id, agent_slug, role, content, thoughts, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
            messages = []
            for row in cursor.fetchall():
                m = dict(row)
                if m.get("thoughts"):
                    try:
                        m["thoughts"] = json.loads(m["thoughts"])
                    except Exception:
                        m["thoughts"] = [m["thoughts"]]
                else:
                    m["thoughts"] = []
                messages.append(m)
            return messages

    def add_chat_message(
        self,
        session_id: str,
        agent_slug: str,
        role: str,
        content: str,
        thoughts: Optional[List[str]] = None,
    ) -> int:
        """Add a single message to a chat session and touch session updated_at."""
        now = time.time()
        thoughts_json = json.dumps(thoughts) if thoughts else None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            cursor = conn.execute(
                """
                INSERT INTO chat_messages (session_id, agent_slug, role, content, thoughts, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, agent_slug, role, content, thoughts_json, now),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_chat_session(self, session_id: str) -> bool:
        """Delete a chat session and cascade messages."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def record_job(
        self,
        agent_slug: str,
        status: str,
        duration_seconds: float,
        summary: Optional[str] = None,
        error_message: Optional[str] = None,
        report_path: Optional[str] = None,
    ) -> int:
        """Record a completed job execution."""
        executed_at = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO job_history (agent_slug, status, executed_at, duration_seconds, summary, error_message, report_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_slug,
                    status,
                    executed_at,
                    duration_seconds,
                    summary,
                    error_message,
                    report_path,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_agent_history(self, agent_slug: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return history for a specific agent."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, agent_slug, status, executed_at, duration_seconds, summary, error_message, report_path
                FROM job_history
                WHERE agent_slug = ?
                ORDER BY executed_at DESC
                LIMIT ?
                """,
                (agent_slug, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return global recent history."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, agent_slug, status, executed_at, duration_seconds, summary, error_message, report_path
                FROM job_history
                ORDER BY executed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
