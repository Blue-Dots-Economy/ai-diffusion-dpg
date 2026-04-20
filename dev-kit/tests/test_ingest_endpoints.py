"""
dev-kit/tests/test_ingest_endpoints.py

Tests for dev-kit ingest backend endpoints:
  POST /api/ingest/submit
  GET  /api/ingest/job/{job_id}
  POST /api/ingest/callback
"""
from __future__ import annotations

import json
import os
import pytest
import respx
import httpx as _httpx
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Must be set before importing the app module which raises EnvironmentError if missing.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    import dev_kit.agent.app as _app_module
    monkeypatch.setenv("DEVKIT_TO_REACH_API_KEY", "devkit-test-key")
    monkeypatch.setenv("KE_TO_DEVKIT_API_KEY", "ke-callback-key")
    monkeypatch.setenv("REACH_LAYER_URL", "http://reach-test:8005")
    monkeypatch.setattr(_app_module, "_DEVKIT_TO_REACH_API_KEY", "devkit-test-key")
    monkeypatch.setattr(_app_module, "_KE_TO_DEVKIT_API_KEY", "ke-callback-key")
    monkeypatch.setattr(_app_module, "_REACH_LAYER_URL", "http://reach-test:8005")


@pytest.fixture
def client():
    from dev_kit.agent.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/ingest/submit — normal
# ---------------------------------------------------------------------------

class TestIngestSubmitNormal:
    @respx.mock
    def test_submit_forwards_to_reach_layer(self, client):
        ke_response = {
            "batch_id": "b1",
            "jobs": [{"filename": "guide.pdf", "job_id": "j1"}]
        }
        respx.post("http://reach-test:8005/ingest/upload").mock(
            return_value=_httpx.Response(200, json=ke_response)
        )

        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "guide.pdf", "mode": "local_write_ingest"}])},
            files=[("files", ("guide.pdf", b"pdf content", "application/octet-stream"))],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["batch_id"] == "b1"
        assert body["jobs"][0]["job_id"] == "j1"

    @respx.mock
    def test_submit_returns_503_on_connect_error(self, client):
        respx.post("http://reach-test:8005/ingest/upload").mock(
            side_effect=_httpx.ConnectError("connection refused")
        )
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "doc.pdf", "mode": "local_write_ingest"}])},
            files=[("files", ("doc.pdf", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 503

    @respx.mock
    def test_submit_injects_user_id(self, client):
        """Verify user_id from devkit.yaml is injected into the metadata."""
        captured_body = {}

        def capture_request(request):
            captured_body["content"] = request.content
            return _httpx.Response(200, json={"batch_id": "b2", "jobs": []})

        respx.post("http://reach-test:8005/ingest/upload").mock(side_effect=capture_request)

        client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "doc.txt", "mode": "local_write_ingest"}])},
            files=[("files", ("doc.txt", b"content", "text/plain"))],
        )
        # user_id should appear somewhere in the multipart body
        assert b"devkit-operator" in captured_body.get("content", b"")


# ---------------------------------------------------------------------------
# POST /api/ingest/submit — auth and validation
# ---------------------------------------------------------------------------

class TestIngestSubmitValidation:
    def test_missing_metadata_returns_422(self, client):
        response = client.post(
            "/api/ingest/submit",
            files=[("files", ("doc.pdf", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 422

    def test_file_too_large_returns_413(self, client):
        """File exceeding max_file_size_mb (30 MB) should be rejected."""
        big_content = b"x" * (31 * 1024 * 1024)  # 31 MB
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "big.pdf", "mode": "local_write_ingest"}])},
            files=[("files", ("big.pdf", big_content, "application/octet-stream"))],
        )
        assert response.status_code == 413

    def test_unsupported_extension_rejected(self, client):
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "virus.exe", "mode": "local_write_ingest"}])},
            files=[("files", ("virus.exe", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 422

    def test_too_many_files_rejected(self, client):
        """More than max_files_per_upload (5) should be rejected."""
        entries = [{"filename": f"doc{i}.pdf", "mode": "local_write_ingest"} for i in range(6)]
        files = [(f"files", (f"doc{i}.pdf", b"x", "application/octet-stream")) for i in range(6)]
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps(entries)},
            files=files,
        )
        assert response.status_code == 422

    def test_path_traversal_rejected(self, client):
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "../etc/passwd", "mode": "local_write_ingest"}])},
            files=[("files", ("../etc/passwd", b"x", "text/plain"))],
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/ingest/job/{job_id}
# ---------------------------------------------------------------------------

class TestIngestJobStatus:
    @respx.mock
    def test_returns_job_status_from_ke(self, client):
        job_data = {"job_id": "j1", "status": "ingested", "chunks_added": 47}
        respx.get("http://reach-test:8005/ingest/job/j1").mock(
            return_value=_httpx.Response(200, json=job_data)
        )
        response = client.get("/api/ingest/job/j1")
        assert response.status_code == 200
        assert response.json()["status"] == "ingested"

    @respx.mock
    def test_ke_404_propagated(self, client):
        respx.get("http://reach-test:8005/ingest/job/unknown").mock(
            return_value=_httpx.Response(404, json={"detail": "Not found"})
        )
        response = client.get("/api/ingest/job/unknown")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/ingest/job/{job_id} — edge cases
# ---------------------------------------------------------------------------

class TestIngestJobStatusEdge:
    def test_invalid_job_id_returns_422(self, client):
        # Special characters like '!' are rejected by the alphanumeric regex guard
        response = client.get("/api/ingest/job/invalid!job@id")
        assert response.status_code == 422

    @respx.mock
    def test_connect_error_returns_503(self, client):
        respx.get("http://reach-test:8005/ingest/job/j1").mock(
            side_effect=_httpx.ConnectError("refused")
        )
        response = client.get("/api/ingest/job/j1")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/ingest/callback
# ---------------------------------------------------------------------------

class TestIngestCallback:
    def test_valid_callback_accepted(self, client, tmp_path, monkeypatch):
        """Valid callback with correct API key is accepted and best-effort persisted."""
        monkeypatch.setenv("PROJECTS_DIR", str(tmp_path))
        # Create a project structure
        project_dir = tmp_path / "test-project" / "_meta"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text('{"slug": "test"}')

        response = client.post(
            "/api/ingest/callback",
            json={
                "job_id": "j1",
                "status": "ingested",
                "chunks_added": 47,
                "error": None
            },
            headers={"X-API-Key": "ke-callback-key"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_wrong_api_key_returns_401(self, client):
        response = client.post(
            "/api/ingest/callback",
            json={"job_id": "j1", "status": "ingested", "chunks_added": 10},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_missing_api_key_returns_401(self, client):
        response = client.post(
            "/api/ingest/callback",
            json={"job_id": "j1", "status": "ingested"},
        )
        assert response.status_code == 401
