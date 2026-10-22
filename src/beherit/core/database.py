"""SQLite Database Storage for Job History and Agent Activity."""

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from beherit.config import settings


class DatabaseManager:
    """Manages local SQLite database for background jobs and audit trail."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (db_path or settings.BEHERIT_DB_PATH).resolve()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
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
            conn.commit()

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
