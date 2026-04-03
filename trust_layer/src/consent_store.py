"""
trust_layer/src/consent_store.py

ConsentStore — SQLite-backed persistence for DPDP Act consent records.

Stores when consent was granted per session. Used by TrustLayer.check_consent
to verify connector-level consent before write/identity tool execution.

Schema: consent_records(session_id TEXT PRIMARY KEY, granted_at TEXT NOT NULL)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ConsentStore:
    """SQLite-backed store for consent records.

    Args:
        db_path: Path to the SQLite database file. Defaults to ":memory:" for tests.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consent_records (
                session_id TEXT PRIMARY KEY,
                granted_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        logger.info(
            "consent_store.init",
            extra={"operation": "consent_store.init", "status": "success", "db_path": db_path},
        )

    def record_consent(self, session_id: str) -> None:
        """Persist that consent was granted for this session.

        Args:
            session_id: Session identifier for which consent was granted.

        Raises:
            ValueError: If session_id is None or empty.
        """
        if not session_id:
            raise ValueError("session_id must not be None or empty")
        granted_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO consent_records (session_id, granted_at) VALUES (?, ?)",
            (session_id, granted_at),
        )
        self._conn.commit()
        logger.info(
            "consent_store.record",
            extra={
                "operation": "consent_store.record_consent",
                "status": "success",
                "session_id": session_id,
            },
        )

    def has_consent(self, session_id: str) -> bool:
        """Check whether consent has been recorded for this session.

        Args:
            session_id: Session identifier to look up.

        Returns:
            True if a consent record exists, False otherwise.
        """
        if not session_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM consent_records WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None
