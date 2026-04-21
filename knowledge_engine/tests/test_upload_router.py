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


# ---------------------------------------------------------------------------
# run_queue_worker — success and failure paths
# ---------------------------------------------------------------------------

class TestRunQueueWorker:
    @pytest.mark.asyncio
    async def test_worker_success_path(self, mock_db):
        """Worker processes a job: ingesting → ingested, with correct DB updates."""
        from src.upload_router import run_queue_worker, _IngestJob

        queue = asyncio.Queue()
        job = _IngestJob("j1", "b1", "guide.pdf", "local_write_ingest", None, b"bytes")
        await queue.put(job)

        mock_kb = MagicMock()
        mock_kb.ingest_single.return_value = 5

        with patch("src.upload_router.LocalPVCStorageBackend") as MockPVC:
            mock_pvc = MagicMock()
            mock_pvc.upload.return_value = "/data/kb/guide.pdf"
            MockPVC.return_value = mock_pvc

            # Run worker but stop after processing one item
            async def run_once():
                task = asyncio.create_task(
                    run_queue_worker(
                        db=mock_db,
                        ingest_queue=queue,
                        ke_config={},
                        static_kb_block=mock_kb,
                        devkit_callback_url=None,
                        ke_to_devkit_api_key=None,
                        kb_data_dir="/data/kb",
                    )
                )
                await queue.join()  # wait until task_done() is called
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_once()

        from unittest.mock import ANY
        mock_db.update_status.assert_any_call("j1", "ingesting")
        mock_db.update_status.assert_any_call("j1", "ingested", chunks_added=5, ingested_at=ANY)
        # Verify ingested was called (chunks_added=5 in the kwargs)
        calls = [str(c) for c in mock_db.update_status.call_args_list]
        assert any("ingested" in c and "chunks_added" in c for c in calls)

    @pytest.mark.asyncio
    async def test_worker_failure_path(self, mock_db):
        """Worker marks job failed when ingest_single raises."""
        from src.upload_router import run_queue_worker, _IngestJob

        queue = asyncio.Queue()
        job = _IngestJob("j2", "b1", "bad.pdf", "local_write_ingest", None, b"bytes")
        await queue.put(job)

        mock_kb = MagicMock()
        mock_kb.ingest_single.side_effect = RuntimeError("ChromaDB write failed")

        with patch("src.upload_router.LocalPVCStorageBackend") as MockPVC:
            mock_pvc = MagicMock()
            mock_pvc.upload.return_value = "/data/kb/bad.pdf"
            MockPVC.return_value = mock_pvc

            async def run_once():
                task = asyncio.create_task(
                    run_queue_worker(
                        db=mock_db,
                        ingest_queue=queue,
                        ke_config={},
                        static_kb_block=mock_kb,
                        devkit_callback_url=None,
                        ke_to_devkit_api_key=None,
                        kb_data_dir="/data/kb",
                    )
                )
                await queue.join()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_once()

        calls = [str(c) for c in mock_db.update_status.call_args_list]
        assert any("failed" in c for c in calls)


# ---------------------------------------------------------------------------
# _send_callback — no-url early exit and retry-exhaustion paths
# ---------------------------------------------------------------------------

class TestSendCallback:
    @pytest.mark.asyncio
    async def test_no_callback_url_is_noop(self):
        """_send_callback returns immediately when callback_url is None."""
        from src.upload_router import _send_callback

        with patch("httpx.AsyncClient") as MockClient:
            await _send_callback(None, None, "j1", "ingested")
            MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_sends_correct_payload(self):
        """_send_callback POSTs the correct payload to callback_url."""
        from src.upload_router import _send_callback
        import respx
        import httpx as _httpx

        with respx.mock:
            route = respx.post("http://devkit:5000/api/ingest/callback").mock(
                return_value=_httpx.Response(200)
            )
            await _send_callback(
                "http://devkit:5000", "my-key", "j1", "ingested", chunks_added=7
            )

        assert route.called
        payload = json.loads(route.calls[0].request.content)
        assert payload["job_id"] == "j1"
        assert payload["status"] == "ingested"
        assert payload["chunks_added"] == 7

    @pytest.mark.asyncio
    async def test_callback_retries_on_500(self):
        """_send_callback retries up to 3 times on 5xx response."""
        from src.upload_router import _send_callback
        import respx
        import httpx as _httpx

        with patch("asyncio.sleep"):  # skip actual sleep in test
            with respx.mock:
                route = respx.post("http://devkit:5000/api/ingest/callback").mock(
                    return_value=_httpx.Response(500)
                )
                await _send_callback("http://devkit:5000", "key", "j1", "failed")

        assert route.call_count == 3
