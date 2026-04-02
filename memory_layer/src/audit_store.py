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

from audit_store_base import AuditStoreBase

logger = logging.getLogger(__name__)


class SQLiteAuditStore(AuditStoreBase):
    """
    Thread-safe SQLite store for session and turn auditing.
    
    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "audit.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_available: bool = True
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
                            end_reason TEXT,
                            consent_given TEXT
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
            self._db_available = False
            logger.critical(
                "sqlite_audit_store.init_error",
                extra={
                    "operation": "audit_store.init",
                    "status": "failure",
                    "error": str(e),
                    "path": self._db_path,
                },
            )

    def record_session_event(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: Optional[str] = None,
        consent_given: Optional[str] = None,
    ) -> None:
        """
        Record a session lifecycle event (start, end, escalate).

        Args:
            session_id:    Session identifier.
            user_id:       User identifier.
            action:        'start', 'end', or 'escalate'.
            reason:        Optional reason for the action.
            consent_given: DPDP consent state — 'true', 'false', or None (pending).
                           At session start this is typically None.
                           At session end/escalate this reflects the user's final
                           storage preference (user_storage_mode from Redis).
        """
        if not self._db_available:
            logger.warning(
                "sqlite_audit_store.record_session_skipped",
                extra={
                    "operation": "audit_store.record_session",
                    "status": "skipped",
                    "session_id": session_id,
                    "error": "audit store unavailable due to init failure",
                },
            )
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                with self._get_connection() as conn:
                    if action == "start":
                        conn.execute(
                            """
                            INSERT INTO session_audit
                                (session_id, user_id, created_at, status, consent_given)
                            VALUES (?, ?, ?, 'active', ?)
                            ON CONFLICT(session_id) DO UPDATE SET
                                status = 'active',
                                closed_at = NULL,
                                end_reason = NULL,
                                consent_given = COALESCE(
                                    excluded.consent_given,
                                    session_audit.consent_given
                                )
                            """,
                            (session_id, user_id, now, consent_given),
                        )
                    elif action in ("end", "escalate"):
                        status = "ended" if action == "end" else "escalated"
                        cursor = conn.execute(
                            """
                            UPDATE session_audit
                            SET closed_at = ?,
                                status = ?,
                                end_reason = ?,
                                consent_given = COALESCE(?, consent_given)
                            WHERE session_id = ?
                            """,
                            (now, status, reason, consent_given, session_id),
                        )
                        if cursor.rowcount == 0:
                            # No start row existed — insert a terminal record so the event is not lost.
                            logger.warning(
                                "sqlite_audit_store.session_end_without_start",
                                extra={
                                    "operation": "audit_store.record_session",
                                    "status": "skipped",
                                    "session_id": session_id,
                                    "action": action,
                                    "error": "session_audit row missing at end/escalate — inserting terminal record",
                                },
                            )
                            conn.execute(
                                """
                                INSERT INTO session_audit
                                    (session_id, user_id, created_at, closed_at, status, end_reason, consent_given)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (session_id, user_id, now, now, status, reason, consent_given),
                            )
                    else:
                        logger.warning(
                            "sqlite_audit_store.record_session_unknown_action",
                            extra={
                                "operation": "audit_store.record_session",
                                "status": "skipped",
                                "session_id": session_id,
                                "action": action,
                                "error": f"unrecognised action: {action!r}",
                            },
                        )
                        return
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
        if not self._db_available:
            logger.warning(
                "sqlite_audit_store.record_turn_skipped",
                extra={
                    "operation": "audit_store.record_turn",
                    "status": "skipped",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "error": "audit store unavailable due to init failure",
                },
            )
            return
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

    def update_consent(self, session_id: str, consent_given: str) -> None:
        """
        Persist the user's consent decision for an active session.

        Called mid-session when user_storage_mode is written to Redis, so the
        audit record is durable even if the session ends abruptly.

        Args:
            session_id:    Session identifier.
            consent_given: 'true' if user accepted storage, 'false' if declined.
        """
        if not self._db_available:
            logger.warning(
                "sqlite_audit_store.update_consent_skipped",
                extra={
                    "operation": "audit_store.update_consent",
                    "status": "skipped",
                    "session_id": session_id,
                    "error": "audit store unavailable due to init failure",
                },
            )
            return
        try:
            with self._lock:
                with self._get_connection() as conn:
                    conn.execute(
                        "UPDATE session_audit SET consent_given = ? WHERE session_id = ?",
                        (consent_given, session_id),
                    )
                    conn.commit()
            logger.info(
                "sqlite_audit_store.update_consent",
                extra={
                    "operation": "audit_store.update_consent",
                    "status": "success",
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.error(
                "sqlite_audit_store.update_consent_error",
                extra={
                    "operation": "audit_store.update_consent",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def get_history(self, session_id: str) -> list[dict]:
        """Retrieve full chat history for a session, sorted by timestamp."""
        try:
            with self._lock:
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
