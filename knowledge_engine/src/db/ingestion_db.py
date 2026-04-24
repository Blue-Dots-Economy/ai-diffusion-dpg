"""
knowledge_engine/src/db/ingestion_db.py

SQLite-backed store for KB document ingestion records.

Provides durable job state that survives pod restarts. The SQLite file lives
on the /data/kb PVC alongside KB documents, so it persists as long as the PVC
does.

Belongs to the Knowledge Engine block of the DPG framework.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ingestion_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL UNIQUE,
    batch_id        TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_size_bytes INTEGER,
    source_type     TEXT NOT NULL,
    cloud_path      TEXT,
    mode            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    chunks_added    INTEGER,
    error           TEXT,
    user_id         TEXT NOT NULL,
    uploaded_at     TEXT NOT NULL,
    ingested_at     TEXT,
    expires_at      TEXT,
    doc_type        TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_batch_id ON ingestion_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_filename  ON ingestion_records(filename);
CREATE INDEX IF NOT EXISTS idx_user_id   ON ingestion_records(user_id);
"""


@dataclass
class IngestionRecord:
    """Represents one file in a batch upload."""

    job_id: str
    batch_id: str
    filename: str
    source_type: str  # "local" | "cloud"
    mode: str         # "local_write_ingest" | "cloud_upload_ingest" | "cloud_fetch_ingest"
    status: str
    user_id: str
    uploaded_at: str
    file_size_bytes: Optional[int] = None
    cloud_path: Optional[str] = None
    chunks_added: Optional[int] = None
    error: Optional[str] = None
    ingested_at: Optional[str] = None
    expires_at: Optional[str] = None
    doc_type: Optional[str] = None
    enabled: int = 1
    # Computed at read time — not a DB column
    queue_position: Optional[int] = field(default=None, compare=False)


class IngestionDB:
    """SQLite-backed store for ingestion records.

    All methods are synchronous — called from the async queue worker using
    run_in_executor or directly (SQLite operations are fast enough at this scale).
    """

    def __init__(self, db_path: Path) -> None:
        """Initialise the database, creating the schema if it does not exist.

        Args:
            db_path: Filesystem path for the SQLite database file. Parent
                     directories are created automatically.
        """
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_CREATE_TABLE)
            # Migration: add doc_type column for existing databases.
            try:
                conn.execute("ALTER TABLE ingestion_records ADD COLUMN doc_type TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

    @contextmanager
    def _connect(self):
        """Open a SQLite connection with WAL mode, yields it, then closes."""
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def insert_batch(self, records: list[IngestionRecord]) -> None:
        """Insert all records in a single transaction.

        Args:
            records: List of records to insert. Empty list is a no-op.

        Raises:
            sqlite3.IntegrityError: If any job_id already exists (UNIQUE constraint).
        """
        if not records:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO ingestion_records
                    (job_id, batch_id, filename, file_size_bytes, source_type,
                     cloud_path, mode, status, user_id, uploaded_at, doc_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.job_id, r.batch_id, r.filename, r.file_size_bytes,
                        r.source_type, r.cloud_path, r.mode, r.status,
                        r.user_id, r.uploaded_at, r.doc_type,
                    )
                    for r in records
                ],
            )
            conn.commit()
        logger.info(
            "ingestion_db.insert_batch",
            extra={"operation": "ingestion_db.insert_batch", "status": "success", "count": len(records)},
        )

    def rollback_batch(self, batch_id: str) -> None:
        """Delete all records for a batch_id.

        Args:
            batch_id: UUID of the batch to remove.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM ingestion_records WHERE batch_id = ?", (batch_id,))
            conn.commit()
        logger.info(
            "ingestion_db.rollback_batch",
            extra={"operation": "ingestion_db.rollback_batch", "status": "success", "batch_id": batch_id},
        )

    def update_status(self, job_id: str, status: str, **kwargs) -> None:
        """Update status and optional fields for a job.

        Args:
            job_id: UUID of the job to update.
            status: New status value.
            **kwargs: Optional fields to update: chunks_added (int), error (str),
                      ingested_at (str ISO 8601 UTC).
        """
        fields = {"status": status}
        if "chunks_added" in kwargs:
            fields["chunks_added"] = kwargs["chunks_added"]
        if "error" in kwargs:
            fields["error"] = kwargs["error"]
        if "ingested_at" in kwargs:
            fields["ingested_at"] = kwargs["ingested_at"]

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE ingestion_records SET {set_clause} WHERE job_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                logger.warning(
                    "ingestion_db.update_status_noop",
                    extra={"operation": "ingestion_db.update_status", "status": "skipped", "job_id": job_id},
                )
            conn.commit()
        logger.info(
            "ingestion_db.update_status",
            extra={"operation": "ingestion_db.update_status", "status": "success", "job_id": job_id, "new_status": status},
        )

    def get_record(self, job_id: str) -> Optional[IngestionRecord]:
        """Fetch a record by job_id and calculate queue_position.

        Args:
            job_id: UUID of the job.

        Returns:
            IngestionRecord with queue_position set if status is 'queued',
            otherwise queue_position is None. Returns None if job_id not found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ingestion_records WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None

            queue_position: Optional[int] = None
            if row["status"] == "queued":
                count_row = conn.execute(
                    """
                    SELECT COUNT(*) as pos FROM ingestion_records
                    WHERE status = 'queued' AND id <= (
                        SELECT id FROM ingestion_records WHERE job_id = ?
                    )
                    """,
                    (job_id,),
                ).fetchone()
                queue_position = count_row["pos"] if count_row else None

        return IngestionRecord(
            job_id=row["job_id"],
            batch_id=row["batch_id"],
            filename=row["filename"],
            file_size_bytes=row["file_size_bytes"],
            source_type=row["source_type"],
            cloud_path=row["cloud_path"],
            mode=row["mode"],
            status=row["status"],
            chunks_added=row["chunks_added"],
            error=row["error"],
            user_id=row["user_id"],
            uploaded_at=row["uploaded_at"],
            ingested_at=row["ingested_at"],
            expires_at=row["expires_at"],
            doc_type=row["doc_type"] if "doc_type" in row.keys() else None,
            enabled=row["enabled"],
            queue_position=queue_position,
        )
