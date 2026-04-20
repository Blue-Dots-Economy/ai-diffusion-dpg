# Reach Layer Upload Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two streaming-proxy endpoints to the Reach Layer web server that forward KB document upload requests to the Knowledge Engine and proxy job-status poll requests back, authenticated by static API keys.

**Architecture:** Two new routes on `reach_layer/web/server.py`: `POST /ingest/upload` streams the multipart body to KE without buffering (avoids 150 MB in-memory accumulation), `GET /ingest/job/{id}` proxies to KE's job-status endpoint. Both routes validate the `DEVKIT_TO_REACH_API_KEY` on ingress and forward `REACH_TO_KE_API_KEY` to KE. `verify_api_key()` is added to the existing `src/auth.py` module.

**Tech Stack:** Python 3.11, FastAPI, httpx (streaming), pytest, respx (HTTP mocking).

**Dependency:** Plan 1 (KE Upload Backend) must be deployed before end-to-end testing. These Reach Layer changes can be developed and unit-tested independently.

---

## File Map

| Path | Action | Responsibility |
|------|--------|---------------|
| `reach_layer/web/src/auth.py` | Modify | Add `verify_api_key(header, expected)` |
| `reach_layer/web/server.py` | Modify | Add `POST /ingest/upload` + `GET /ingest/job/{id}` |
| `reach_layer/web/config/reach_layer.yaml` | Modify | Add `ke_internal_url`, `cors.allowed_origins` |
| `reach_layer/web/tests/test_auth.py` | Modify | Add `verify_api_key` tests |
| `reach_layer/web/tests/test_server.py` | Modify | Add upload proxy endpoint tests |

---

## Task 1: Add verify_api_key to reach_layer auth.py

**Files:**
- Modify: `reach_layer/web/src/auth.py`
- Modify: `reach_layer/web/tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Append to `reach_layer/web/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# verify_api_key — static API key auth for ingest proxy endpoints
# ---------------------------------------------------------------------------

from fastapi import HTTPException
from src.auth import verify_api_key


class TestVerifyApiKeyNormal:
    def test_matching_key_does_not_raise(self):
        # Should return None without raising
        result = verify_api_key("my-secret-key", "my-secret-key")
        assert result is None


class TestVerifyApiKeyFailure:
    def test_wrong_key_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong", "right")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid API key"

    def test_missing_header_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, "expected")
        assert exc_info.value.status_code == 401

    def test_empty_header_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "expected")
        assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd reach_layer/web && uv run pytest tests/test_auth.py::TestVerifyApiKeyNormal tests/test_auth.py::TestVerifyApiKeyFailure -v
```

Expected: `ImportError: cannot import name 'verify_api_key' from 'src.auth'`

- [ ] **Step 3: Add verify_api_key to src/auth.py**

Append to the bottom of `reach_layer/web/src/auth.py` (after all existing code):

```python
# ---------------------------------------------------------------------------
# Static API key verification — ingest proxy endpoints
# ---------------------------------------------------------------------------

def verify_api_key(header: Optional[str], expected: str) -> None:
    """Verify that the X-API-Key header matches the expected static key.

    Used for Reach Layer ingest proxy endpoints to authenticate dev-kit requests.
    No JWT, no signing — simple string comparison.

    Args:
        header: Value of the X-API-Key header from the incoming request.
        expected: Expected API key read from env at startup.

    Raises:
        HTTPException: 401 if header is missing, empty, or does not match expected.
    """
    if not header or not expected or header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

Add `Optional` to the imports at the top of `src/auth.py` if not already imported:
```python
from typing import Optional
```

And `HTTPException` to the fastapi imports:
```python
from fastapi import HTTPException
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd reach_layer/web && uv run pytest tests/test_auth.py::TestVerifyApiKeyNormal tests/test_auth.py::TestVerifyApiKeyFailure -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/web/src/auth.py reach_layer/web/tests/test_auth.py
git commit -m "feat(reach): add verify_api_key static API key helper"
```

---

## Task 2: Update reach_layer.yaml with KE URL and CORS

**Files:**
- Modify: `reach_layer/web/config/reach_layer.yaml`

- [ ] **Step 1: Check the current reach_layer.yaml**

```bash
cat reach_layer/web/config/reach_layer.yaml
```

- [ ] **Step 2: Add ke_internal_url and CORS config**

Add the following to `reach_layer/web/config/reach_layer.yaml` (at the top level, before or after existing keys):

```yaml
# Internal Kubernetes service URL for the Knowledge Engine.
# Used by the upload proxy to forward document ingestion requests.
# Override via KE_INTERNAL_URL env var at deploy time.
ke_internal_url: "http://knowledge-engine.dpg.svc.cluster.local:8001"

# CORS origins allowed for the dev-kit upload flow.
# Must include the dev-kit VM's public URL.
cors:
  allowed_origins:
    - "https://devkit.your-vm.example.com"
    - "http://localhost:5173"
```

- [ ] **Step 3: Verify reach_layer.yaml is valid YAML**

```bash
cd reach_layer/web && python -c "import yaml; yaml.safe_load(open('config/reach_layer.yaml'))"
```

Expected: no output (valid YAML).

- [ ] **Step 4: Commit**

```bash
git add reach_layer/web/config/reach_layer.yaml
git commit -m "feat(reach): add ke_internal_url and cors config for upload proxy"
```

---

## Task 3: Add upload proxy endpoints to server.py

**Files:**
- Modify: `reach_layer/web/server.py`
- Modify: `reach_layer/web/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Append to `reach_layer/web/tests/test_server.py` (inside the existing test module):

```python
# ---------------------------------------------------------------------------
# Fixtures for upload proxy tests — need env vars set
# ---------------------------------------------------------------------------

import os
import respx
import httpx as _httpx


@pytest.fixture
def upload_client(config, web_reach):
    """TestClient with upload proxy env vars set."""
    os.environ["DEVKIT_TO_REACH_API_KEY"] = "devkit-key-test"
    os.environ["REACH_TO_KE_API_KEY"] = "ke-key-test"
    os.environ["KE_INTERNAL_URL"] = "http://ke-test:8001"

    from server import create_app
    test_app = create_app(web_reach, config)
    client = TestClient(test_app)
    yield client

    os.environ.pop("DEVKIT_TO_REACH_API_KEY", None)
    os.environ.pop("REACH_TO_KE_API_KEY", None)
    os.environ.pop("KE_INTERNAL_URL", None)


# ---------------------------------------------------------------------------
# POST /ingest/upload
# ---------------------------------------------------------------------------

class TestIngestUploadProxy:
    @respx.mock
    def test_proxies_to_ke_and_returns_response(self, upload_client):
        ke_response = {"batch_id": "b1", "jobs": [{"filename": "doc.pdf", "job_id": "j1"}]}
        respx.post("http://ke-test:8001/upload").mock(
            return_value=_httpx.Response(200, json=ke_response)
        )

        response = upload_client.post(
            "/ingest/upload",
            content=b"--boundary\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n[]\r\n--boundary--",
            headers={
                "X-API-Key": "devkit-key-test",
                "Content-Type": "multipart/form-data; boundary=boundary",
            },
        )
        assert response.status_code == 200
        assert response.json()["batch_id"] == "b1"

    def test_missing_api_key_returns_401(self, upload_client):
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 401

    def test_wrong_api_key_returns_401(self, upload_client):
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"X-API-Key": "wrong-key", "Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 401

    @respx.mock
    def test_ke_error_propagated(self, upload_client):
        respx.post("http://ke-test:8001/upload").mock(
            return_value=_httpx.Response(429, json={"detail": "Queue full"})
        )
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"X-API-Key": "devkit-key-test", "Content-Type": "multipart/form-data; boundary=b"},
        )
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# GET /ingest/job/{job_id}
# ---------------------------------------------------------------------------

class TestIngestJobProxy:
    @respx.mock
    def test_proxies_job_status_from_ke(self, upload_client):
        job_response = {"job_id": "j1", "status": "ingested", "chunks_added": 42}
        respx.get("http://ke-test:8001/upload/job/j1").mock(
            return_value=_httpx.Response(200, json=job_response)
        )

        response = upload_client.get(
            "/ingest/job/j1",
            headers={"X-API-Key": "devkit-key-test"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ingested"

    def test_missing_api_key_returns_401(self, upload_client):
        response = upload_client.get("/ingest/job/j1")
        assert response.status_code == 401

    @respx.mock
    def test_ke_404_propagated(self, upload_client):
        respx.get("http://ke-test:8001/upload/job/nonexistent").mock(
            return_value=_httpx.Response(404, json={"detail": "Not found"})
        )
        response = upload_client.get(
            "/ingest/job/nonexistent",
            headers={"X-API-Key": "devkit-key-test"},
        )
        assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd reach_layer/web && uv run pytest tests/test_server.py::TestIngestUploadProxy tests/test_server.py::TestIngestJobProxy -v
```

Expected: FAIL — routes not defined.

- [ ] **Step 3: Add upload proxy endpoints to server.py**

Locate the `create_app` function in `reach_layer/web/server.py`. Before the `return app` statement (or after the existing `@app.get("/user-history/{user_id}")` route), add:

```python
    # ------------------------------------------------------------------
    # Upload proxy — Reach Layer → KE (approved architecture exception)
    # Scope: POST /ingest/upload and GET /ingest/job/{id} only.
    # ------------------------------------------------------------------

    _DEVKIT_TO_REACH_API_KEY = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
    _REACH_TO_KE_API_KEY = os.environ.get("REACH_TO_KE_API_KEY", "")
    _KE_INTERNAL_URL = os.environ.get("KE_INTERNAL_URL") or config.get("ke_internal_url", "")

    from src.auth import verify_api_key as _verify_api_key

    @app.post("/ingest/upload")
    async def ingest_upload(
        request: Request,
        x_api_key: Optional[str] = None,
    ):
        """Stream multipart upload from dev-kit to KE without buffering.

        Validates dev-kit API key, then streams the full multipart body to KE.
        Streaming avoids accumulating up to 150 MB (5 files × 30 MB) in memory.
        """
        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, _DEVKIT_TO_REACH_API_KEY)

        if not _KE_INTERNAL_URL:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{_KE_INTERNAL_URL}/upload",
                    content=request.stream(),
                    headers={
                        "Content-Type": request.headers.get("Content-Type", ""),
                        "X-API-Key": _REACH_TO_KE_API_KEY,
                    },
                )
            logger.info(
                "reach.ingest_upload",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "success",
                    "ke_status": response.status_code,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.ingest_upload_ke_unreachable",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.ingest_upload_timeout",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e

    @app.get("/ingest/job/{job_id}")
    async def ingest_job_status(
        job_id: str,
        request: Request,
    ):
        """Proxy job status poll from dev-kit to KE.

        Validates dev-kit API key, then forwards the GET to KE's job status endpoint.
        Used as fallback when dev-kit polling detects a callback was missed.
        """
        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, _DEVKIT_TO_REACH_API_KEY)

        if not _KE_INTERNAL_URL:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{_KE_INTERNAL_URL}/upload/job/{job_id}",
                    headers={"X-API-Key": _REACH_TO_KE_API_KEY},
                )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            raise HTTPException(504, "Knowledge Engine timed out") from e
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd reach_layer/web && uv run pytest tests/test_server.py::TestIngestUploadProxy tests/test_server.py::TestIngestJobProxy -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run full Reach Layer test suite**

```bash
cd reach_layer/web && uv run pytest --cov=src -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/web/server.py reach_layer/web/tests/test_server.py
git commit -m "feat(reach): add /ingest/upload streaming proxy and /ingest/job/{id} proxy"
```

---

## Self-Review Checklist

- [x] `POST /ingest/upload` uses `request.stream()` (not `await request.body()`) — no 150 MB buffer
- [x] Both endpoints validate `DEVKIT_TO_REACH_API_KEY` before proxying
- [x] Both endpoints inject `REACH_TO_KE_API_KEY` when calling KE
- [x] `KE_INTERNAL_URL` is read from env, falling back to YAML config — not hardcoded
- [x] ConnectError → 503, TimeoutException → 504 (structured errors)
- [x] `verify_api_key` added to existing `src/auth.py` (no new file created)
- [x] CORS config added to `reach_layer.yaml` (not hardcoded in source)
- [x] Approved architecture exception is documented in a code comment
