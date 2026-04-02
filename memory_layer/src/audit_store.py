"""
memory_layer/src/audit_store.py

SQLiteAuditStore — manages persistent chat history and session lifecycle auditing using SQLite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SQLiteAuditStore:
    """
    Thread-safe SQLite store for session and turn auditing.
    
    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "audit.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a SQLite connection with row factory set."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the database schema if it doesn't exist."""
        try:
            with self._lock:
                with self._get_connection() as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    
                    # session_audit: Tracks session lifecycle
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS session_audit (
                            session_id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            created_at TIMESTAMP NOT NULL,
                            closed_at TIMESTAMP,
                            status TEXT DEFAULT 'active',
                            end_reason TEXT
                        )
                    """)
                    
                    # turn_audit: Tracks turn-by-turn interactions
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS turn_audit (
                            turn_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL,
                            user_message TEXT,
                            system_message TEXT,
                            timestamp TIMESTAMP NOT NULL,
                            subagent_id TEXT,
                            intent TEXT,
                            model TEXT,
                            latency_ms INTEGER,
                            metadata TEXT,
                            FOREIGN KEY (session_id) REFERENCES session_audit (session_id)
                        )
                    """)
                    conn.commit()
            
            logger.info(
                "sqlite_audit_store.init",
                extra={
                    "operation": "audit_store.init",
                    "status": "success",
                    "path": self._db_path,
                },
            )
        except Exception as e:
            logger.error(
                "sqlite_audit_store.init_error",
                extra={
                    "operation": "audit_store.init",
                    "status": "failure",
                    "error": str(e),
                },
            )
            # We don't raise here to avoid crashing the whole service; 
            # audit is secondary to core functionality.

    def record_session_event(
        self, 
        session_id: str, 
        user_id: str, 
        action: str, 
        reason: Optional[str] = None
    ) -> None:
        """
        Record a session lifecycle event (start, end, escalate).
        
        Args:
            session_id: Session identifier.
            user_id:    User identifier.
            action:     'start', 'end', or 'escalate'.
            reason:     Optional reason for the action.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                with self._get_connection() as conn:
                    if action == "start":
                        conn.execute(
                            """
                            INSERT INTO session_audit (session_id, user_id, created_at, status)
                            VALUES (?, ?, ?, 'active')
                            ON CONFLICT(session_id) DO UPDATE SET 
                                status = 'active',
                                closed_at = NULL,
                                end_reason = NULL
                            """,
                            (session_id, user_id, now),
                        )
                    elif action in ("end", "escalate"):
                        status = "ended" if action == "end" else "escalated"
                        conn.execute(
                            """
                            UPDATE session_audit 
                            SET closed_at = ?, status = ?, end_reason = ?
                            WHERE session_id = ?
                            """,
                            (now, status, reason, session_id),
                        )
                    conn.commit()
            
            logger.info(
                "sqlite_audit_store.record_session",
                extra={
                    "operation": "audit_store.record_session",
                    "status": "success",
                    "session_id": session_id,
                    "action": action,
                },
            )
        except Exception as e:
            logger.error(
                "sqlite_audit_store.record_session_error",
                extra={
                    "operation": "audit_store.record_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": str(e),
                },
            )

    def record_turn_history(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_msg: str,
        system_msg: str,
        subagent_id: str = "",
        intent: str = "",
        model: str = "",
        latency_ms: int = 0,
        metadata: Optional[dict] = None
    ) -> None:
        """
        Record a single conversation turn.
        
        Note: Metadata is stored as a JSON string.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            meta_str = json.dumps(metadata) if metadata else None
            
            # Ensure the session exists in the audit table (e.g. if start event was missed)
            self.record_session_event(session_id, user_id, "start")

            with self._lock:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO turn_audit 
                        (turn_id, session_id, user_message, system_message, timestamp, 
                         subagent_id, intent, model, latency_ms, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (turn_id, session_id, user_msg, system_msg, now, 
                         subagent_id, intent, model, latency_ms, meta_str),
                    )
                    conn.commit()
            
            logger.info(
                "sqlite_audit_store.record_turn",
                extra={
                    "operation": "audit_store.record_turn",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn_id,
                },
            )
        except Exception as e:
            logger.error(
                "sqlite_audit_store.record_turn_error",
                extra={
                    "operation": "audit_store.record_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "error": str(e),
                },
            )

    def get_history(self, session_id: str) -> list[dict]:
        """Retrieve full chat history for a session, sorted by timestamp."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM turn_audit WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                "sqlite_audit_store.get_history_error",
                extra={"session_id": session_id, "error": str(e)},
            )
            return []
