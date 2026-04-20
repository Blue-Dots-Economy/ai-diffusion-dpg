"""
knowledge_engine/tests/test_ingestion_db.py

Tests for IngestionDB SQLite-backed ingestion record store.
Uses tmp_path for isolated DB files — no shared state between tests.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from datetime import datetime, timezone

from src.db.ingestion_db import IngestionDB, IngestionRecord


def _make_record(job_id: str = "job-1", batch_id: str = "batch-1") -> IngestionRecord:
    return IngestionRecord(
        job_id=job_id,
        batch_id=batch_id,
        filename="test.pdf",
        file_size_bytes=1024,
        source_type="local",
        cloud_path=None,
        mode="local_write_ingest",
        status="queued",
        user_id="devkit-operator",
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Normal
# ---------------------------------------------------------------------------

class TestIngestionDBNormal:
    def test_insert_and_retrieve(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        rec = _make_record()
        db.insert_batch([rec])
        result = db.get_record("job-1")
        assert result is not None
        assert result.job_id == "job-1"
        assert result.status == "queued"

    def test_insert_batch_multiple(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1", "b1"), _make_record("j2", "b1")])
        assert db.get_record("j1") is not None
        assert db.get_record("j2") is not None

    def test_update_status_to_ingesting(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1")])
        db.update_status("j1", "ingesting")
        assert db.get_record("j1").status == "ingesting"

    def test_update_status_ingested_with_chunks(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1")])
        db.update_status("j1", "ingested", chunks_added=47)
        rec = db.get_record("j1")
        assert rec.status == "ingested"
        assert rec.chunks_added == 47

    def test_update_status_failed_with_error(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1")])
        db.update_status("j1", "failed", error="ChromaDB write failed")
        rec = db.get_record("j1")
        assert rec.status == "failed"
        assert rec.error == "ChromaDB write failed"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestIngestionDBEdge:
    def test_get_nonexistent_returns_none(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        assert db.get_record("nonexistent") is None

    def test_insert_empty_batch_is_noop(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([])  # should not raise

    def test_queue_position_is_calculated(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1", "b1"), _make_record("j2", "b1")])
        assert db.get_record("j1").queue_position == 1
        assert db.get_record("j2").queue_position == 2


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------

class TestIngestionDBFailure:
    def test_rollback_batch_removes_rows(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1", "b1"), _make_record("j2", "b1")])
        db.rollback_batch("b1")
        assert db.get_record("j1") is None
        assert db.get_record("j2") is None

    def test_duplicate_job_id_raises(self, tmp_path):
        db = IngestionDB(tmp_path / "ke.db")
        db.insert_batch([_make_record("j1")])
        with pytest.raises(Exception):  # SQLite UNIQUE constraint
            db.insert_batch([_make_record("j1")])
