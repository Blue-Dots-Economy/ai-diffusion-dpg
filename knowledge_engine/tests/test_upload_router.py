"""
knowledge_engine/tests/test_upload_router.py

Tests for KE upload endpoints and queue worker.
Uses FastAPI TestClient. DB, queue, and storage are mocked.
"""
from __future__ import annotations

import asyncio
import io
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_record.return_value = MagicMock(
        job_id="job-1",
        status="queued",
        queue_position=1,
        chunks_added=None,
        error=None,
        filename="guide.pdf",
        ingested_at=None,
        uploaded_at="2026-04-20T10:00:00Z",
    )
    return db


@pytest.fixture
def app(mock_db):
    from src.upload_router import create_upload_router
    import asyncio
    queue = asyncio.Queue()
    router = create_upload_router(
        db=mock_db,
        ingest_queue=queue,
        reach_to_ke_api_key="test-reach-key",
        azure_configured=False,
        max_queue_size=20,
    )
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /upload — normal
# ---------------------------------------------------------------------------

class TestUploadBatchNormal:
    def test_upload_returns_batch_and_job_ids(self, client, mock_db):
        metadata = json.dumps([
            {"filename": "guide.pdf", "mode": "local_write_ingest"},
        ])
        files = [("files", ("guide.pdf", b"pdf content", "application/octet-stream"))]
        data = {"metadata": metadata}

        response = client.post(
            "/upload",
            data=data,
            files=files,
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "batch_id" in body
        assert len(body["jobs"]) == 1
        assert body["jobs"][0]["filename"] == "guide.pdf"
        assert "job_id" in body["jobs"][0]

    def test_cloud_fetch_no_file_needed(self, client, mock_db):
        metadata = json.dumps([
            {"filename": "remote.pdf", "mode": "cloud_fetch_ingest", "cloud_path": "docs/remote.pdf"},
        ])
        response = client.post(
            "/upload",
            data={"metadata": metadata},
            # no files= for cloud_fetch_ingest
            headers={"X-API-Key": "test-reach-key"},
        )
        # Should fail with 400 since azure not configured
        assert response.status_code == 400

    def test_db_insert_called(self, client, mock_db):
        metadata = json.dumps([{"filename": "doc.txt", "mode": "local_write_ingest"}])
        client.post(
            "/upload",
            data={"metadata": metadata},
            files=[("files", ("doc.txt", b"content", "text/plain"))],
            headers={"X-API-Key": "test-reach-key"},
        )
        mock_db.insert_batch.assert_called_once()


# ---------------------------------------------------------------------------
# POST /upload — auth
# ---------------------------------------------------------------------------

class TestUploadBatchAuth:
    def test_missing_api_key_returns_401(self, client):
        response = client.post("/upload", data={"metadata": "[]"})
        assert response.status_code == 401

    def test_wrong_api_key_returns_401(self, client):
        response = client.post(
            "/upload",
            data={"metadata": "[]"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /upload — validation
# ---------------------------------------------------------------------------

class TestUploadBatchValidation:
    def test_path_traversal_rejected(self, client):
        metadata = json.dumps([{"filename": "../etc/passwd", "mode": "local_write_ingest"}])
        response = client.post(
            "/upload",
            data={"metadata": metadata},
            files=[("files", ("../etc/passwd", b"x", "text/plain"))],
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 422

    def test_unsupported_extension_rejected(self, client):
        metadata = json.dumps([{"filename": "script.exe", "mode": "local_write_ingest"}])
        response = client.post(
            "/upload",
            data={"metadata": metadata},
            files=[("files", ("script.exe", b"x", "application/octet-stream"))],
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 422

    def test_missing_file_part_rejected(self, client):
        metadata = json.dumps([{"filename": "guide.pdf", "mode": "local_write_ingest"}])
        # No files part
        response = client.post(
            "/upload",
            data={"metadata": metadata},
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /upload/job/{job_id}
# ---------------------------------------------------------------------------

class TestGetJobStatus:
    def test_returns_job_status(self, client, mock_db):
        response = client.get(
            "/upload/job/job-1",
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == "job-1"
        assert body["status"] == "queued"
        assert body["queue_position"] == 1

    def test_unknown_job_returns_404(self, client, mock_db):
        mock_db.get_record.return_value = None
        response = client.get(
            "/upload/job/nonexistent",
            headers={"X-API-Key": "test-reach-key"},
        )
        assert response.status_code == 404

    def test_missing_api_key_returns_401(self, client):
        response = client.get("/upload/job/job-1")
        assert response.status_code == 401
