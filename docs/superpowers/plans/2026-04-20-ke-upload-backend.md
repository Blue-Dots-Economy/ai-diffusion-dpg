# KE Upload Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runtime document upload API to the Knowledge Engine that accepts multipart batches, persists records to SQLite, processes ingestion via an async queue, and notifies the dev-kit via callback.

**Architecture:** A new `upload_router.py` registers `POST /upload` and `GET /upload/job/{id}` on the existing FastAPI app. A singleton async queue worker processes one job at a time against ChromaDB. All state is durable in SQLite (`ke_metadata.db` on the `/data/kb` PVC). Storage is abstracted behind `StorageBackend` ABC with Azure Blob and Local PVC implementations.

**Tech Stack:** Python 3.11, FastAPI, SQLite (stdlib), `azure-storage-blob>=12.0`, `python-multipart`, `python-docx`, ChromaDB, httpx (for callbacks), pytest-asyncio.

---

## Design Issue Resolutions (applied in this plan)

| Issue | Resolution |
|-------|-----------|
| `.docx` / `.html` in devkit.yaml but no handler | Add `_chunk_docx()` + `_chunk_html()` to `StaticKnowledgeBaseBlock`; add `python-docx>=1.1.0` to deps |
| `cloud_upload_ingest` needs dual-write | `_stage_file` calls `LocalPVCStorageBackend` first, then `AzureBlobStorageBackend` explicitly (bypasses factory) |
| `queue_position` not in SQLite | Calculated dynamically: count rows with `status='queued'` and `id ≤ current_row_id` |
| `_send_callback` bare `except Exception: pass` | Replace with `logger.debug("ke.callback_attempt_failed", ...)` |

---

## File Map

| Path | Action | Responsibility |
|------|--------|---------------|
| `knowledge_engine/pyproject.toml` | Modify | Add runtime + test deps |
| `knowledge_engine/src/auth.py` | **Create** | `verify_api_key(header, expected)` helper |
| `knowledge_engine/src/storage/base.py` | **Create** | `StorageBackend` ABC |
| `knowledge_engine/src/storage/local_pvc.py` | **Create** | `LocalPVCStorageBackend` |
| `knowledge_engine/src/storage/azure_blob.py` | **Create** | `AzureBlobStorageBackend` |
| `knowledge_engine/src/storage/__init__.py` | **Create** | `get_storage_backend()` factory |
| `knowledge_engine/src/db/__init__.py` | **Create** | Package marker |
| `knowledge_engine/src/db/ingestion_db.py` | **Create** | `IngestionDB` (SQLite) + `IngestionRecord` dataclass |
| `knowledge_engine/src/blocks/static_knowledge_base.py` | Modify | Add `ingest_single()`, `_chunk_docx()`, `_chunk_html()` |
| `knowledge_engine/src/upload_router.py` | **Create** | `POST /upload`, `GET /upload/job/{id}`, queue worker, callback sender |
| `knowledge_engine/main.py` | Modify | Register `upload_router`, start queue worker, init `IngestionDB` |
| `knowledge_engine/tests/test_ke_auth.py` | **Create** | Tests for `verify_api_key` |
| `knowledge_engine/tests/test_storage.py` | **Create** | Tests for all storage backends + factory |
| `knowledge_engine/tests/test_ingestion_db.py` | **Create** | Tests for `IngestionDB` CRUD |
| `knowledge_engine/tests/test_ingest_single.py` | **Create** | Tests for `ingest_single()` |
| `knowledge_engine/tests/test_upload_router.py` | **Create** | Tests for upload endpoints + queue worker |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `knowledge_engine/pyproject.toml`

- [ ] **Step 1: Add deps to pyproject.toml**

```toml
# In [project] dependencies list, add:
    "azure-storage-blob>=12.0",
    "python-multipart>=0.0.9",
    "python-docx>=1.1.0",
```

```toml
# In [project.optional-dependencies] dev list, add:
    "pytest-asyncio>=0.23",
    "respx>=0.21",
```

Full diff to pyproject.toml:
```toml
dependencies = [
    "chromadb>=0.4.0",
    "langchain-text-splitters>=0.2.0",
    "openai>=1.0.0",
    "pymupdf>=1.23.0",
    "pyyaml>=6.0",
    "httpx>=0.27.0",
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "python-dotenv>=1.0.0",
    "packaging>=23.0",
    "azure-storage-blob>=12.0",
    "python-multipart>=0.0.9",
    "python-docx>=1.1.0",
    "observability-layer",
    "opentelemetry-instrumentation-fastapi>=0.61b0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-mock>=3.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27.0",
    "respx>=0.21",
]
```

Also add asyncio mode config to `[tool.pytest.ini_options]`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
asyncio_mode = "auto"
```

- [ ] **Step 2: Install dependencies**

```bash
cd knowledge_engine && uv sync
```

Expected: all packages install without error.

- [ ] **Step 3: Commit**

```bash
git add knowledge_engine/pyproject.toml
git commit -m "chore(ke): add azure-storage-blob, python-multipart, python-docx, pytest-asyncio deps"
```

---

## Task 2: KE API Key Auth Helper

**Files:**
- Create: `knowledge_engine/src/auth.py`
- Create: `knowledge_engine/tests/test_ke_auth.py`

- [ ] **Step 1: Write the failing test**

Create `knowledge_engine/tests/test_ke_auth.py`:

```python
"""
knowledge_engine/tests/test_ke_auth.py

Tests for the API key verification helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.auth import verify_api_key


class TestVerifyApiKeyNormal:
    def test_matching_key_returns_none(self):
        # Should not raise
        result = verify_api_key("secret-key-123", "secret-key-123")
        assert result is None

    def test_any_valid_string_accepted(self):
        verify_api_key("abc", "abc")  # no exception


class TestVerifyApiKeyEdge:
    def test_empty_expected_key_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "")
        assert exc_info.value.status_code == 401

    def test_none_header_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, "expected")
        assert exc_info.value.status_code == 401


class TestVerifyApiKeyFailure:
    def test_wrong_key_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong-key", "right-key")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid API key"

    def test_empty_header_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "expected-key")
        assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd knowledge_engine && uv run pytest tests/test_ke_auth.py -v
```

Expected: `ImportError: cannot import name 'verify_api_key' from 'src.auth'`

- [ ] **Step 3: Implement src/auth.py**

```python
"""
knowledge_engine/src/auth.py

Static API key verification for Knowledge Engine service-to-service calls.

Belongs to the Knowledge Engine block of the DPG framework.
Called by upload endpoints to authenticate requests from the Reach Layer.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def verify_api_key(header: Optional[str], expected: str) -> None:
    """Verify that the X-API-Key header matches the expected key.

    Args:
        header: Value of the X-API-Key header from the incoming request.
        expected: The expected API key read from env at startup.

    Raises:
        HTTPException: 401 if header is missing, empty, or does not match expected.
    """
    if not header or not expected or header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd knowledge_engine && uv run pytest tests/test_ke_auth.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add knowledge_engine/src/auth.py knowledge_engine/tests/test_ke_auth.py
git commit -m "feat(ke): add verify_api_key auth helper"
```

---

## Task 3: Storage Abstraction — ABC + Local PVC

**Files:**
- Create: `knowledge_engine/src/storage/base.py`
- Create: `knowledge_engine/src/storage/local_pvc.py`
- Create: `knowledge_engine/src/storage/__init__.py`
- Create: `knowledge_engine/tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge_engine/tests/test_storage.py`:

```python
"""
knowledge_engine/tests/test_storage.py

Tests for StorageBackend ABC, LocalPVCStorageBackend, AzureBlobStorageBackend,
and the get_storage_backend factory.

Azure calls are fully mocked — no real Azure credentials needed.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.storage.base import StorageBackend, StorageError
from src.storage.local_pvc import LocalPVCStorageBackend
from src.storage import get_storage_backend


# ---------------------------------------------------------------------------
# StorageBackend ABC
# ---------------------------------------------------------------------------

class TestStorageBackendABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            StorageBackend()  # type: ignore

    def test_concrete_without_all_methods_raises(self):
        class Partial(StorageBackend):
            def upload(self, content, filename):
                return ""
            # missing download and health_check

        with pytest.raises(TypeError):
            Partial()


# ---------------------------------------------------------------------------
# LocalPVCStorageBackend — normal
# ---------------------------------------------------------------------------

class TestLocalPVCNormal:
    def test_upload_writes_file(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        path = backend.upload(b"hello world", "test.txt")
        assert Path(path).read_bytes() == b"hello world"

    def test_upload_returns_absolute_path(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        path = backend.upload(b"data", "doc.pdf")
        assert path == str(tmp_path / "doc.pdf")

    def test_download_reads_file(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        (tmp_path / "readme.txt").write_bytes(b"content")
        data = backend.download(str(tmp_path / "readme.txt"))
        assert data == b"content"

    def test_health_check_returns_true(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        assert backend.health_check() is True


# ---------------------------------------------------------------------------
# LocalPVCStorageBackend — edge cases
# ---------------------------------------------------------------------------

class TestLocalPVCEdge:
    def test_upload_creates_missing_dir(self, tmp_path):
        subdir = tmp_path / "kb" / "docs"
        backend = LocalPVCStorageBackend(base_dir=str(subdir))
        backend.upload(b"x", "file.txt")  # should not raise
        assert (subdir / "file.txt").exists()

    def test_upload_empty_bytes(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        path = backend.upload(b"", "empty.txt")
        assert Path(path).read_bytes() == b""


# ---------------------------------------------------------------------------
# LocalPVCStorageBackend — failures
# ---------------------------------------------------------------------------

class TestLocalPVCFailure:
    def test_download_missing_file_raises_storage_error(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        with pytest.raises(StorageError):
            backend.download(str(tmp_path / "nonexistent.txt"))

    def test_upload_none_content_raises(self, tmp_path):
        backend = LocalPVCStorageBackend(base_dir=str(tmp_path))
        with pytest.raises((TypeError, ValueError)):
            backend.upload(None, "file.txt")  # type: ignore


# ---------------------------------------------------------------------------
# get_storage_backend factory
# ---------------------------------------------------------------------------

class TestGetStorageBackendFactory:
    def test_returns_local_when_no_azure_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT", raising=False)
        monkeypatch.delenv("AZURE_STORAGE_KEY", raising=False)
        monkeypatch.delenv("AZURE_CONTAINER_NAME", raising=False)
        monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
        backend = get_storage_backend()
        assert isinstance(backend, LocalPVCStorageBackend)

    def test_returns_azure_when_all_azure_env_set(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "acct")
        monkeypatch.setenv("AZURE_STORAGE_KEY", "key==")
        monkeypatch.setenv("AZURE_CONTAINER_NAME", "container")
        with patch("src.storage.AzureBlobStorageBackend") as MockAzure:
            MockAzure.return_value = MagicMock()
            backend = get_storage_backend()
            MockAzure.assert_called_once_with("acct", "key==", "container")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_storage.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.storage'`

- [ ] **Step 3: Implement storage/base.py**

```python
"""
knowledge_engine/src/storage/base.py

Abstract base class for KB document storage backends.

Belongs to the Knowledge Engine block of the DPG framework.
Concrete implementations: LocalPVCStorageBackend, AzureBlobStorageBackend.
Add AWS S3 or GCP GCS by subclassing — no changes to callers required.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class StorageError(Exception):
    """Raised when a storage backend operation fails after retries."""


class StorageBackend(ABC):
    """Abstract base for KB document storage backends."""

    @abstractmethod
    def upload(self, content: bytes, filename: str) -> str:
        """Upload content and return the storage path.

        Args:
            content: Raw file bytes.
            filename: Basename only — no path separators.

        Returns:
            Storage path (blob name or absolute local path).

        Raises:
            StorageError: On upload failure after retries.
        """

    @abstractmethod
    def download(self, path: str) -> bytes:
        """Download content from the given path.

        Args:
            path: Blob name or absolute local path.

        Returns:
            Raw file bytes.

        Raises:
            StorageError: If path does not exist or download fails.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the backend is reachable and writable."""
```

- [ ] **Step 4: Implement storage/local_pvc.py**

```python
"""
knowledge_engine/src/storage/local_pvc.py

Local filesystem storage backend for KB documents.

Writes documents to the /data/kb PVC mount (or a configurable base directory
for testing). Used for local_write_ingest mode and as the local staging step
for cloud_upload_ingest and cloud_fetch_ingest modes.

Belongs to the Knowledge Engine block of the DPG framework.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from src.storage.base import StorageBackend, StorageError

logger = logging.getLogger(__name__)

_DEFAULT_BASE_DIR = "/data/kb"


class LocalPVCStorageBackend(StorageBackend):
    """Write and read KB documents from the local PVC filesystem."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = Path(base_dir or os.environ.get("KB_DATA_DIR", _DEFAULT_BASE_DIR))

    def upload(self, content: bytes, filename: str) -> str:
        """Write bytes to base_dir/filename and return the absolute path.

        Args:
            content: Raw file bytes.
            filename: Basename only — no path separators.

        Returns:
            Absolute path to the written file.

        Raises:
            StorageError: If the write fails.
        """
        if content is None:
            raise ValueError("content must not be None")
        self._base_dir.mkdir(parents=True, exist_ok=True)
        dest = self._base_dir / filename
        try:
            dest.write_bytes(content)
            logger.info(
                "local_pvc.upload",
                extra={"operation": "local_pvc.upload", "status": "success", "path": str(dest)},
            )
            return str(dest)
        except OSError as e:
            logger.error(
                "local_pvc.upload_failed",
                extra={"operation": "local_pvc.upload", "status": "failure", "error": str(e)},
            )
            raise StorageError(f"LocalPVC upload failed: {e}") from e

    def download(self, path: str) -> bytes:
        """Read and return bytes from the given absolute path.

        Args:
            path: Absolute local path to the file.

        Returns:
            Raw file bytes.

        Raises:
            StorageError: If path does not exist or read fails.
        """
        p = Path(path)
        if not p.exists():
            raise StorageError(f"File not found: {path}")
        try:
            return p.read_bytes()
        except OSError as e:
            raise StorageError(f"LocalPVC download failed: {e}") from e

    def health_check(self) -> bool:
        """Return True if the base directory is accessible."""
        return self._base_dir.exists() or True  # always writable (mkdir on upload)
```

- [ ] **Step 5: Implement storage/__init__.py**

```python
"""
knowledge_engine/src/storage/__init__.py

Factory for storage backends. Returns Azure backend when all Azure env vars
are set; falls back to LocalPVCStorageBackend.
"""
from __future__ import annotations

import os

from src.storage.base import StorageBackend, StorageError
from src.storage.local_pvc import LocalPVCStorageBackend
from src.storage.azure_blob import AzureBlobStorageBackend

__all__ = ["get_storage_backend", "StorageBackend", "StorageError"]


def get_storage_backend() -> StorageBackend:
    """Return the appropriate storage backend based on environment variables.

    Returns Azure backend if AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY, and
    AZURE_CONTAINER_NAME are all set; otherwise returns LocalPVCStorageBackend.

    Returns:
        A concrete StorageBackend instance.
    """
    acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key = os.environ.get("AZURE_STORAGE_KEY")
    cont = os.environ.get("AZURE_CONTAINER_NAME")
    if acct and key and cont:
        return AzureBlobStorageBackend(acct, key, cont)
    return LocalPVCStorageBackend()
```

- [ ] **Step 6: Run tests to verify storage tests pass** (Azure tests still pending)

```bash
cd knowledge_engine && uv run pytest tests/test_storage.py::TestStorageBackendABC tests/test_storage.py::TestLocalPVCNormal tests/test_storage.py::TestLocalPVCEdge tests/test_storage.py::TestLocalPVCFailure tests/test_storage.py::TestGetStorageBackendFactory -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add knowledge_engine/src/storage/ knowledge_engine/tests/test_storage.py
git commit -m "feat(ke): add StorageBackend ABC, LocalPVCStorageBackend, and factory"
```

---

## Task 4: Azure Blob Storage Backend

**Files:**
- Create: `knowledge_engine/src/storage/azure_blob.py`

- [ ] **Step 1: Write failing tests** (add to existing `tests/test_storage.py`)

Append to `knowledge_engine/tests/test_storage.py`:

```python
# ---------------------------------------------------------------------------
# AzureBlobStorageBackend — mocked Azure SDK
# ---------------------------------------------------------------------------

class TestAzureBlobNormal:
    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_upload_calls_sdk_upload(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_blob = MagicMock()
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_blob_client.return_value = mock_blob

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        path = backend.upload(b"data", "guide.pdf")

        mock_client.get_blob_client.assert_called_once_with(
            container="container", blob="guide.pdf"
        )
        mock_blob.upload_blob.assert_called_once_with(b"data", overwrite=True)
        assert path == "guide.pdf"

    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_download_calls_sdk_download(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_stream = MagicMock()
        mock_stream.readall.return_value = b"file bytes"
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_blob_client.return_value = mock_blob
        mock_blob.download_blob.return_value = mock_stream

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        data = backend.download("docs/guide.pdf")
        assert data == b"file bytes"


class TestAzureBlobFailure:
    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_upload_failure_raises_storage_error(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_blob = MagicMock()
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_blob_client.return_value = mock_blob
        mock_blob.upload_blob.side_effect = Exception("Azure error")

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        with pytest.raises(StorageError):
            backend.upload(b"data", "file.pdf")

    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_download_failure_raises_storage_error(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_blob = MagicMock()
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_blob_client.return_value = mock_blob
        mock_blob.download_blob.side_effect = Exception("Not found")

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        with pytest.raises(StorageError):
            backend.download("missing.pdf")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_storage.py::TestAzureBlobNormal tests/test_storage.py::TestAzureBlobFailure -v
```

Expected: `ModuleNotFoundError: No module named 'src.storage.azure_blob'`

- [ ] **Step 3: Implement storage/azure_blob.py**

```python
"""
knowledge_engine/src/storage/azure_blob.py

Azure Blob Storage backend for KB document upload and download.

Reads credentials from constructor arguments (provided from env vars at startup,
not per-request). Uses azure-storage-blob>=12.0 SDK.

Belongs to the Knowledge Engine block of the DPG framework.
"""
from __future__ import annotations

import logging
import time

from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import AzureError

from src.storage.base import StorageBackend, StorageError

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


class AzureBlobStorageBackend(StorageBackend):
    """Upload and download KB documents from Azure Blob Storage."""

    def __init__(self, account_name: str, account_key: str, container_name: str) -> None:
        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={account_name};"
            f"AccountKey={account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        self._client = BlobServiceClient.from_connection_string(conn_str)
        self._container = container_name

    def upload(self, content: bytes, filename: str) -> str:
        """Upload bytes to Azure Blob Storage and return the blob name.

        Args:
            content: Raw file bytes.
            filename: Basename used as the blob name.

        Returns:
            Blob name (filename) in the configured container.

        Raises:
            StorageError: If upload fails after one retry.
        """
        start = time.time()
        for attempt in range(2):
            try:
                blob_client = self._client.get_blob_client(
                    container=self._container, blob=filename
                )
                blob_client.upload_blob(content, overwrite=True, timeout=_TIMEOUT_S)
                logger.info(
                    "azure_blob.upload",
                    extra={
                        "operation": "azure_blob.upload",
                        "status": "success",
                        "blob": filename,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return filename
            except AzureError as e:
                if attempt == 1:
                    logger.error(
                        "azure_blob.upload_failed",
                        extra={
                            "operation": "azure_blob.upload",
                            "status": "failure",
                            "error": str(e),
                        },
                    )
                    raise StorageError(f"Azure upload failed: {e}") from e
            except Exception as e:
                logger.error(
                    "azure_blob.upload_failed",
                    extra={
                        "operation": "azure_blob.upload",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                raise StorageError(f"Azure upload failed: {e}") from e
        return filename  # unreachable, satisfies type checker

    def download(self, path: str) -> bytes:
        """Download a blob from Azure Blob Storage.

        Args:
            path: Blob name (path within the container).

        Returns:
            Raw file bytes.

        Raises:
            StorageError: If blob does not exist or download fails.
        """
        start = time.time()
        try:
            blob_client = self._client.get_blob_client(
                container=self._container, blob=path
            )
            stream = blob_client.download_blob(timeout=_TIMEOUT_S)
            data = stream.readall()
            logger.info(
                "azure_blob.download",
                extra={
                    "operation": "azure_blob.download",
                    "status": "success",
                    "blob": path,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return data
        except Exception as e:
            logger.error(
                "azure_blob.download_failed",
                extra={
                    "operation": "azure_blob.download",
                    "status": "failure",
                    "blob": path,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            raise StorageError(f"Azure download failed: {e}") from e

    def health_check(self) -> bool:
        """Return True if the Azure container is reachable."""
        try:
            container_client = self._client.get_container_client(self._container)
            container_client.get_container_properties(timeout=5.0)
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Run all storage tests**

```bash
cd knowledge_engine && uv run pytest tests/test_storage.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add knowledge_engine/src/storage/azure_blob.py knowledge_engine/tests/test_storage.py
git commit -m "feat(ke): add AzureBlobStorageBackend"
```

---

## Task 5: IngestionDB (SQLite)

**Files:**
- Create: `knowledge_engine/src/db/__init__.py`
- Create: `knowledge_engine/src/db/ingestion_db.py`
- Create: `knowledge_engine/tests/test_ingestion_db.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge_engine/tests/test_ingestion_db.py`:

```python
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
        rec = db.get_record("j1")
        assert rec.queue_position is not None
        assert rec.queue_position >= 1


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_ingestion_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.db'`

- [ ] **Step 3: Implement src/db/__init__.py (empty)**

```python
# knowledge_engine/src/db/__init__.py
```

- [ ] **Step 4: Implement src/db/ingestion_db.py**

```python
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
    enabled: int = 1
    # Computed at read time — not a DB column
    queue_position: Optional[int] = field(default=None, compare=False)


class IngestionDB:
    """SQLite-backed store for ingestion records.

    All methods are synchronous — called from the async queue worker using
    run_in_executor or directly (SQLite operations are fast enough at this scale).

    Future: if Redis is needed for speed, implement a parallel class with the
    same method signatures. The interface is narrow enough to replace without
    affecting callers.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL mode for concurrent reads."""
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

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
                     cloud_path, mode, status, user_id, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.job_id, r.batch_id, r.filename, r.file_size_bytes,
                        r.source_type, r.cloud_path, r.mode, r.status,
                        r.user_id, r.uploaded_at,
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
            conn.execute(
                f"UPDATE ingestion_records SET {set_clause} WHERE job_id = ?",
                values,
            )
            conn.commit()

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
            enabled=row["enabled"],
            queue_position=queue_position,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd knowledge_engine && uv run pytest tests/test_ingestion_db.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add knowledge_engine/src/db/ knowledge_engine/tests/test_ingestion_db.py
git commit -m "feat(ke): add IngestionDB SQLite store"
```

---

## Task 6: ingest_single() + DOCX/HTML handlers

**Files:**
- Modify: `knowledge_engine/src/blocks/static_knowledge_base.py`
- Create: `knowledge_engine/tests/test_ingest_single.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge_engine/tests/test_ingest_single.py`:

```python
"""
knowledge_engine/tests/test_ingest_single.py

Tests for StaticKnowledgeBaseBlock.ingest_single() and the new DOCX/HTML
chunking helpers. ChromaDB and embedding functions are fully mocked.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from src.blocks.static_knowledge_base import StaticKnowledgeBaseBlock


CONFIG = {
    "knowledge": {
        "blocks": {
            "static_knowledge_base": {
                "enabled": True,
                "collection_name": "test_collection",
                "chroma_persist_dir": "/tmp/test_chroma_ingest",
                "embedding_provider": "chroma_default",
            }
        }
    }
}


@pytest.fixture
def block():
    return StaticKnowledgeBaseBlock()


@pytest.fixture
def mock_collection():
    col = MagicMock()
    col.get.return_value = {"ids": ["old-chunk-1"]}
    return col


# ---------------------------------------------------------------------------
# Normal
# ---------------------------------------------------------------------------

class TestIngestSingleNormal:
    @patch.object(StaticKnowledgeBaseBlock, "_get_collection")
    @patch.object(StaticKnowledgeBaseBlock, "_load_and_chunk")
    @patch.object(StaticKnowledgeBaseBlock, "_add_chunks_to_collection")
    def test_returns_chunk_count(self, mock_add, mock_chunk, mock_get_col, block, mock_collection):
        mock_get_col.return_value = mock_collection
        mock_chunk.return_value = [{"text": "chunk1", "metadata": {}}, {"text": "chunk2", "metadata": {}}]

        count = block.ingest_single(CONFIG, Path("/data/kb/guide.pdf"))
        assert count == 2

    @patch.object(StaticKnowledgeBaseBlock, "_get_collection")
    @patch.object(StaticKnowledgeBaseBlock, "_load_and_chunk")
    @patch.object(StaticKnowledgeBaseBlock, "_add_chunks_to_collection")
    def test_deletes_old_chunks_before_adding(self, mock_add, mock_chunk, mock_get_col, block, mock_collection):
        mock_get_col.return_value = mock_collection
        mock_chunk.return_value = [{"text": "chunk1", "metadata": {}}]

        block.ingest_single(CONFIG, Path("/data/kb/guide.pdf"))

        # Should have queried for existing chunks and deleted them
        mock_collection.get.assert_called_once()
        mock_collection.delete.assert_called_once_with(ids=["old-chunk-1"])

    @patch.object(StaticKnowledgeBaseBlock, "_get_collection")
    @patch.object(StaticKnowledgeBaseBlock, "_load_and_chunk")
    @patch.object(StaticKnowledgeBaseBlock, "_add_chunks_to_collection")
    def test_no_delete_when_no_existing_chunks(self, mock_add, mock_chunk, mock_get_col, block):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_col.return_value = mock_collection
        mock_chunk.return_value = [{"text": "x", "metadata": {}}]

        block.ingest_single(CONFIG, Path("/data/kb/new.pdf"))
        mock_collection.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestIngestSingleEdge:
    @patch.object(StaticKnowledgeBaseBlock, "_get_collection")
    @patch.object(StaticKnowledgeBaseBlock, "_load_and_chunk")
    @patch.object(StaticKnowledgeBaseBlock, "_add_chunks_to_collection")
    def test_empty_file_returns_zero_chunks(self, mock_add, mock_chunk, mock_get_col, block, mock_collection):
        mock_get_col.return_value = mock_collection
        mock_chunk.return_value = []

        count = block.ingest_single(CONFIG, Path("/data/kb/empty.pdf"))
        assert count == 0
        mock_add.assert_not_called()

    def test_nonexistent_file_raises_value_error(self, block):
        with pytest.raises((ValueError, FileNotFoundError)):
            block.ingest_single(CONFIG, Path("/data/kb/nonexistent.pdf"))


# ---------------------------------------------------------------------------
# HTML + DOCX chunking helpers
# ---------------------------------------------------------------------------

class TestChunkHTML:
    def test_html_returns_text_chunks(self, block, tmp_path):
        html_file = tmp_path / "page.html"
        html_file.write_text("<html><body><p>Hello world from HTML.</p></body></html>")
        chunks = block._chunk_html(str(html_file), "general")
        assert len(chunks) >= 1
        assert "Hello world" in chunks[0]["text"]
        assert chunks[0]["metadata"]["doc_type"] == "general"

    def test_html_strips_tags(self, block, tmp_path):
        html_file = tmp_path / "page.html"
        html_file.write_text("<html><body><h1>Title</h1><p>Body text.</p></body></html>")
        chunks = block._chunk_html(str(html_file), "faq")
        combined = " ".join(c["text"] for c in chunks)
        assert "<h1>" not in combined
        assert "Title" in combined

    def test_empty_html_returns_empty(self, block, tmp_path):
        html_file = tmp_path / "empty.html"
        html_file.write_text("<html><body></body></html>")
        chunks = block._chunk_html(str(html_file), "general")
        assert chunks == []


class TestChunkDOCX:
    def test_docx_returns_chunks(self, block, tmp_path):
        from docx import Document
        doc = Document()
        doc.add_paragraph("This is paragraph one with enough text to form a chunk.")
        doc.add_paragraph("This is paragraph two with additional content.")
        docx_path = tmp_path / "test.docx"
        doc.save(str(docx_path))

        chunks = block._chunk_docx(str(docx_path), "policy")
        assert len(chunks) >= 1
        assert "paragraph one" in chunks[0]["text"]

    def test_empty_docx_returns_empty(self, block, tmp_path):
        from docx import Document
        doc = Document()
        docx_path = tmp_path / "empty.docx"
        doc.save(str(docx_path))

        chunks = block._chunk_docx(str(docx_path), "policy")
        assert chunks == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_ingest_single.py -v
```

Expected: `AttributeError: 'StaticKnowledgeBaseBlock' object has no attribute 'ingest_single'`

- [ ] **Step 3: Add ingest_single, _chunk_html, _chunk_docx to static_knowledge_base.py**

Add the following methods to the `StaticKnowledgeBaseBlock` class (after the existing `ingest()` method):

```python
def ingest_single(self, config: dict, file_path: Path) -> int:
    """Ingest a single document into the existing ChromaDB collection.

    Deletes all existing chunks for this filename before re-ingesting,
    ensuring no duplicate chunks on re-upload of the same file.
    Appends to the collection — does not affect other documents.

    Serialization is guaranteed by the queue worker (one job at a time).
    No asyncio.Lock is needed here.

    Args:
        config: Full KE YAML config dict.
        file_path: Absolute path to the document on /data/kb PVC (must exist).

    Returns:
        Number of chunks added.

    Raises:
        ValueError: If file_path does not exist.
        KnowledgeEngineError: If ChromaDB write fails.
    """
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path}")

    block_cfg = (
        config.get("knowledge", {})
        .get("blocks", {})
        .get("static_knowledge_base", {})
    )

    collection = self._get_collection(block_cfg)

    # Delete all existing chunks for this filename (deduplication on re-upload)
    existing = collection.get(
        where={"source": {"$eq": file_path.name}},
        include=[],
    )
    existing_ids = existing.get("ids", [])
    if existing_ids:
        collection.delete(ids=existing_ids)
        logger.info(
            "static_kb.ingest_single_dedup",
            extra={
                "operation": "static_kb.ingest_single",
                "status": "dedup",
                "filename": file_path.name,
                "deleted_chunks": len(existing_ids),
            },
        )

    # Load, chunk, and embed
    doc_type = "general"  # default doc_type for runtime-uploaded files
    chunks = self._load_and_chunk(str(file_path), doc_type)
    if not chunks:
        return 0

    self._add_chunks_to_collection(collection, chunks, doc_type)
    logger.info(
        "static_kb.ingest_single",
        extra={
            "operation": "static_kb.ingest_single",
            "status": "success",
            "filename": file_path.name,
            "chunks_added": len(chunks),
        },
    )
    return len(chunks)
```

Also extend `_load_and_chunk` to handle `.docx` and `.html`:

```python
# In _load_and_chunk, add to the elif chain:
elif ext == ".html":
    return self._chunk_html(path, doc_type)
elif ext == ".docx":
    return self._chunk_docx(path, doc_type)
```

Add the new private methods:

```python
def _chunk_html(self, path: str, doc_type: str) -> list[dict]:
    """Extract text from an HTML file and split into chunks.

    Uses stdlib html.parser to strip tags. No external dependencies.

    Args:
        path: Path to the .html file.
        doc_type: Document type tag stored in ChromaDB metadata.

    Returns:
        List of chunk dicts with ``text`` and ``metadata`` keys.
    """
    from html.parser import HTMLParser
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
            self._skip_tags = {"script", "style"}
            self._current_skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._current_skip += 1

        def handle_endtag(self, tag):
            if tag in self._skip_tags:
                self._current_skip = max(0, self._current_skip - 1)

        def handle_data(self, data):
            if self._current_skip == 0:
                stripped = data.strip()
                if stripped:
                    self._parts.append(stripped)

        def get_text(self) -> str:
            return " ".join(self._parts)

    with open(path, encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    extractor = _TextExtractor()
    extractor.feed(raw)
    text = extractor.get_text()

    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks_text = splitter.split_text(text)
    return [
        {
            "text": chunk,
            "metadata": {
                "doc_type": doc_type,
                "source": os.path.basename(path),
            },
        }
        for chunk in chunks_text
        if chunk.strip()
    ]

def _chunk_docx(self, path: str, doc_type: str) -> list[dict]:
    """Extract text from a .docx file and split into chunks.

    Uses python-docx. Extracts paragraph text only (not tables or headers
    in this iteration).

    Args:
        path: Path to the .docx file.
        doc_type: Document type tag stored in ChromaDB metadata.

    Returns:
        List of chunk dicts with ``text`` and ``metadata`` keys.
    """
    from docx import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    full_text = "\n\n".join(paragraphs)

    if not full_text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks_text = splitter.split_text(full_text)
    return [
        {
            "text": chunk,
            "metadata": {
                "doc_type": doc_type,
                "source": os.path.basename(path),
            },
        }
        for chunk in chunks_text
        if chunk.strip()
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd knowledge_engine && uv run pytest tests/test_ingest_single.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add knowledge_engine/src/blocks/static_knowledge_base.py knowledge_engine/tests/test_ingest_single.py
git commit -m "feat(ke): add ingest_single(), _chunk_html(), _chunk_docx() to StaticKnowledgeBaseBlock"
```

---

## Task 7: Upload Router (POST /upload + GET /upload/job + queue worker + callback)

**Files:**
- Create: `knowledge_engine/src/upload_router.py`
- Create: `knowledge_engine/tests/test_upload_router.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge_engine/tests/test_upload_router.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_upload_router.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.upload_router'`

- [ ] **Step 3: Implement src/upload_router.py**

```python
"""
knowledge_engine/src/upload_router.py

FastAPI router for KB document upload ingestion.

Exposes:
  POST /upload         — accept multipart batch, validate, enqueue jobs
  GET  /upload/job/{id} — return job status from SQLite

The async queue worker (started once at KE startup) processes one job at a
time to protect ChromaDB writes. All state is durable in SQLite.

Belongs to the Knowledge Engine block of the DPG framework.
Approved architecture exception: receives calls from Reach Layer (not Agent Core).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from src.auth import verify_api_key
from src.db.ingestion_db import IngestionDB, IngestionRecord
from src.storage.local_pvc import LocalPVCStorageBackend
from src.storage.azure_blob import AzureBlobStorageBackend

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".docx", ".html"}


class _IngestJob:
    """In-memory job passed through the asyncio queue."""

    __slots__ = ("job_id", "batch_id", "filename", "mode", "cloud_path", "file_bytes")

    def __init__(
        self,
        job_id: str,
        batch_id: str,
        filename: str,
        mode: str,
        cloud_path: Optional[str],
        file_bytes: Optional[bytes],
    ) -> None:
        self.job_id = job_id
        self.batch_id = batch_id
        self.filename = filename
        self.mode = mode
        self.cloud_path = cloud_path
        self.file_bytes = file_bytes


def create_upload_router(
    db: IngestionDB,
    ingest_queue: asyncio.Queue,
    reach_to_ke_api_key: str,
    azure_configured: bool,
    max_queue_size: int = 20,
    devkit_callback_url: Optional[str] = None,
    ke_to_devkit_api_key: Optional[str] = None,
    ke_config: Optional[dict] = None,
    static_kb_block=None,
) -> APIRouter:
    """Create and return the upload router.

    Args:
        db: Initialised IngestionDB instance.
        ingest_queue: Singleton asyncio.Queue shared with the queue worker.
        reach_to_ke_api_key: Expected value of X-API-Key from Reach Layer.
        azure_configured: True if AZURE_STORAGE_ACCOUNT/KEY/CONTAINER are all set.
        max_queue_size: Maximum combined queue+pending jobs before returning 429.
        devkit_callback_url: Dev-kit callback URL for job completion notification.
        ke_to_devkit_api_key: API key sent to dev-kit callback endpoint.
        ke_config: Full KE config dict passed to ingest_single.
        static_kb_block: StaticKnowledgeBaseBlock instance for ingest_single calls.

    Returns:
        Configured APIRouter.
    """
    router = APIRouter()

    # ------------------------------------------------------------------
    # POST /upload
    # ------------------------------------------------------------------

    @router.post("/upload")
    async def upload_batch(
        request: Request,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    ):
        """Accept a multipart document batch and enqueue ingestion jobs.

        Validates API key, sanitizes filenames, checks extensions, inserts all
        records atomically into SQLite, then enqueues all jobs.

        Returns batch_id and per-file job_id list.
        """
        verify_api_key(x_api_key, reach_to_ke_api_key)

        form = await request.form()

        raw_metadata = form.get("metadata")
        if not raw_metadata:
            raise HTTPException(422, "metadata field is required")

        try:
            metadata_entries = json.loads(raw_metadata)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"Invalid metadata JSON: {e}") from e

        user_id = form.get("user_id", "unknown")

        # Build a dict of filename → file bytes from the form
        file_parts: dict[str, bytes] = {}
        for field_name, value in form.multi_items():
            if field_name == "files" and hasattr(value, "filename") and hasattr(value, "read"):
                content = await value.read()
                file_parts[value.filename] = content

        # Validate all entries
        validated: list[tuple[str, str, Optional[str], Optional[bytes]]] = []
        for entry in metadata_entries:
            filename = entry.get("filename", "")
            mode = entry.get("mode", "")
            cloud_path = entry.get("cloud_path")

            # Path traversal check
            safe_name = Path(filename).name
            if safe_name != filename or "/" in filename or "\\" in filename:
                raise HTTPException(422, f"Invalid filename: {filename}")

            ext = Path(safe_name).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                raise HTTPException(422, f"Unsupported extension: {ext}")

            if mode in ("cloud_upload_ingest", "cloud_fetch_ingest") and not azure_configured:
                raise HTTPException(400, "Azure storage not configured on this deployment")

            if mode in ("cloud_upload_ingest", "local_write_ingest"):
                if safe_name not in file_parts:
                    raise HTTPException(422, f"No file part found for: {filename}")

            if mode == "cloud_fetch_ingest" and not cloud_path:
                raise HTTPException(422, f"cloud_path required for: {filename}")

            file_bytes = file_parts.get(safe_name)
            validated.append((safe_name, mode, cloud_path, file_bytes))

        # Queue capacity check
        if ingest_queue.qsize() + len(validated) > max_queue_size:
            raise HTTPException(429, "Queue full — try again later")

        # Atomic: insert all DB rows first, then enqueue
        batch_id = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()
        source_type_map = {
            "local_write_ingest": "local",
            "cloud_upload_ingest": "cloud",
            "cloud_fetch_ingest": "cloud",
        }

        db_rows: list[IngestionRecord] = []
        jobs: list[_IngestJob] = []

        for safe_name, mode, cloud_path, file_bytes in validated:
            job_id = str(uuid.uuid4())
            db_rows.append(IngestionRecord(
                job_id=job_id,
                batch_id=batch_id,
                filename=safe_name,
                file_size_bytes=len(file_bytes) if file_bytes else None,
                source_type=source_type_map.get(mode, "local"),
                cloud_path=cloud_path,
                mode=mode,
                status="queued",
                user_id=user_id,
                uploaded_at=now_utc,
            ))
            jobs.append(_IngestJob(job_id, batch_id, safe_name, mode, cloud_path, file_bytes))

        try:
            db.insert_batch(db_rows)
        except Exception as e:
            logger.error(
                "ke.upload.db_insert_failed",
                extra={"operation": "ke.upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(500, f"DB insert failed: {e}") from e

        try:
            for job in jobs:
                await ingest_queue.put(job)
        except Exception as e:
            db.rollback_batch(batch_id)
            logger.error(
                "ke.upload.enqueue_failed",
                extra={"operation": "ke.upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(500, f"Queue enqueue failed: {e}") from e

        logger.info(
            "ke.upload.batch_accepted",
            extra={
                "operation": "ke.upload",
                "status": "success",
                "batch_id": batch_id,
                "job_count": len(jobs),
            },
        )

        return {
            "batch_id": batch_id,
            "jobs": [{"filename": j.filename, "job_id": j.job_id} for j in jobs],
        }

    # ------------------------------------------------------------------
    # GET /upload/job/{job_id}
    # ------------------------------------------------------------------

    @router.get("/upload/job/{job_id}")
    async def get_job_status(
        job_id: str,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    ):
        """Return ingestion job status from SQLite.

        Reads directly from the DB — status survives pod restarts.
        queue_position is calculated dynamically for queued jobs.
        """
        verify_api_key(x_api_key, reach_to_ke_api_key)

        record = db.get_record(job_id)
        if record is None:
            raise HTTPException(404, f"Job not found: {job_id}")

        return {
            "job_id": record.job_id,
            "filename": record.filename,
            "status": record.status,
            "queue_position": record.queue_position,
            "chunks_added": record.chunks_added,
            "error": record.error,
            "uploaded_at": record.uploaded_at,
        }

    return router


# ------------------------------------------------------------------
# Queue worker (started once at KE startup)
# ------------------------------------------------------------------

async def run_queue_worker(
    db: IngestionDB,
    ingest_queue: asyncio.Queue,
    ke_config: dict,
    static_kb_block,
    devkit_callback_url: Optional[str],
    ke_to_devkit_api_key: Optional[str],
    kb_data_dir: str = "/data/kb",
) -> None:
    """Process upload jobs sequentially. One job at a time to protect ChromaDB writes.

    Args:
        db: IngestionDB instance for status updates.
        ingest_queue: Singleton asyncio.Queue containing _IngestJob items.
        ke_config: Full KE config dict passed to ingest_single.
        static_kb_block: StaticKnowledgeBaseBlock instance.
        devkit_callback_url: Dev-kit callback URL (optional).
        ke_to_devkit_api_key: API key for callback requests (optional).
        kb_data_dir: Base directory for staged files (PVC mount).
    """
    azure_acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    azure_key = os.environ.get("AZURE_STORAGE_KEY")
    azure_cont = os.environ.get("AZURE_CONTAINER_NAME")
    azure_configured = bool(azure_acct and azure_key and azure_cont)

    while True:
        job: _IngestJob = await ingest_queue.get()
        db.update_status(job.job_id, "ingesting")

        staged_path: Optional[Path] = None
        try:
            staged_path = await _stage_file(job, kb_data_dir, azure_acct, azure_key, azure_cont)
            chunks = static_kb_block.ingest_single(ke_config, staged_path)
            ingested_at = datetime.now(timezone.utc).isoformat()
            db.update_status(job.job_id, "ingested", chunks_added=chunks, ingested_at=ingested_at)
            await _send_callback(
                devkit_callback_url, ke_to_devkit_api_key, job.job_id, "ingested", chunks_added=chunks
            )
            logger.info(
                "ke.worker.job_complete",
                extra={
                    "operation": "ke.worker",
                    "status": "success",
                    "job_id": job.job_id,
                    "chunks_added": chunks,
                },
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(
                "ke.worker.job_failed",
                extra={
                    "operation": "ke.worker",
                    "status": "failure",
                    "job_id": job.job_id,
                    "error": error_msg,
                },
            )
            db.update_status(job.job_id, "failed", error=error_msg)
            await _send_callback(
                devkit_callback_url, ke_to_devkit_api_key, job.job_id, "failed", error=error_msg
            )
        finally:
            ingest_queue.task_done()


async def _stage_file(
    job: _IngestJob,
    kb_data_dir: str,
    azure_acct: Optional[str],
    azure_key: Optional[str],
    azure_cont: Optional[str],
) -> Path:
    """Write the file to the local PVC and optionally to Azure Blob.

    Mode behaviour:
      local_write_ingest:   write bytes to PVC
      cloud_upload_ingest:  write bytes to PVC AND upload to Azure Blob
      cloud_fetch_ingest:   download from Azure Blob → write to PVC

    Args:
        job: The in-memory job descriptor.
        kb_data_dir: Base directory for PVC file storage.
        azure_acct/key/cont: Azure credentials (None if not configured).

    Returns:
        Absolute Path to the staged file on the PVC.
    """
    local_backend = LocalPVCStorageBackend(base_dir=kb_data_dir)

    if job.mode == "local_write_ingest":
        dest = local_backend.upload(job.file_bytes, job.filename)
        return Path(dest)

    if job.mode == "cloud_upload_ingest":
        # Write to PVC first (source of truth for re-ingestion)
        dest = local_backend.upload(job.file_bytes, job.filename)
        # Also upload to Azure Blob
        azure = AzureBlobStorageBackend(azure_acct, azure_key, azure_cont)
        azure.upload(job.file_bytes, job.filename)
        return Path(dest)

    if job.mode == "cloud_fetch_ingest":
        # Download from Azure, write to PVC
        azure = AzureBlobStorageBackend(azure_acct, azure_key, azure_cont)
        content = azure.download(job.cloud_path)
        dest = local_backend.upload(content, job.filename)
        return Path(dest)

    raise ValueError(f"Unknown ingest mode: {job.mode}")


async def _send_callback(
    callback_url: Optional[str],
    api_key: Optional[str],
    job_id: str,
    status: str,
    **kwargs,
) -> None:
    """Notify dev-kit of job completion. Retries 3x with exponential backoff.

    Args:
        callback_url: Dev-kit callback endpoint (None disables callback).
        api_key: API key for X-API-Key header.
        job_id: UUID of the completed job.
        status: Terminal status ('ingested' or 'failed').
        **kwargs: Optional fields: chunks_added (int), error (str).
    """
    if not callback_url:
        return

    payload = {"job_id": job_id, "status": status, **kwargs}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{callback_url}/api/ingest/callback",
                    json=payload,
                    headers={"X-API-Key": api_key or ""},
                    timeout=10.0,
                )
                if r.status_code < 500:
                    return  # 2xx or 4xx — do not retry
        except Exception as e:
            logger.debug(
                "ke.callback_attempt_failed",
                extra={
                    "operation": "ke.callback",
                    "status": "failure",
                    "attempt": attempt + 1,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
        await asyncio.sleep(2 ** attempt)

    logger.warning(
        "ke.callback_failed_all_retries",
        extra={
            "operation": "ke.callback",
            "status": "failure",
            "job_id": job_id,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd knowledge_engine && uv run pytest tests/test_upload_router.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add knowledge_engine/src/upload_router.py knowledge_engine/tests/test_upload_router.py
git commit -m "feat(ke): add upload router, queue worker, and callback sender"
```

---

## Task 8: Wire upload router into main.py

**Files:**
- Modify: `knowledge_engine/main.py`

- [ ] **Step 1: Write failing test** (add to `tests/test_server.py`)

Add to existing `knowledge_engine/tests/test_server.py`:

```python
class TestUploadRouterRegistered:
    def test_upload_endpoint_exists(self):
        """Verify /upload endpoint is registered on the FastAPI app."""
        from main import app
        routes = [r.path for r in app.routes]
        assert "/upload" in routes

    def test_upload_job_endpoint_exists(self):
        from main import app
        routes = [r.path for r in app.routes]
        assert "/upload/job/{job_id}" in routes
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine && uv run pytest tests/test_server.py::TestUploadRouterRegistered -v
```

Expected: FAIL — routes not registered yet.

- [ ] **Step 3: Modify main.py**

Add to `knowledge_engine/main.py` after the existing imports:

```python
import asyncio as _asyncio
from src.db.ingestion_db import IngestionDB
from src.upload_router import create_upload_router, run_queue_worker
```

In `create_app()`, after `app.state.ke = ke`:

```python
    # ------------------------------------------------------------------
    # Upload router — KB document ingestion API
    # ------------------------------------------------------------------

    _REACH_TO_KE_API_KEY = os.environ.get("REACH_TO_KE_API_KEY", "")
    _AZURE_CONFIGURED = bool(
        os.environ.get("AZURE_STORAGE_ACCOUNT")
        and os.environ.get("AZURE_STORAGE_KEY")
        and os.environ.get("AZURE_CONTAINER_NAME")
    )
    _KB_DATA_DIR = os.environ.get("KB_DATA_DIR", "/data/kb")
    _DB_PATH = Path(_KB_DATA_DIR) / "ke_metadata.db"
    _DEVKIT_CALLBACK_URL = os.environ.get("KE_DEVKIT_CALLBACK_URL", "")
    _KE_TO_DEVKIT_API_KEY = os.environ.get("KE_TO_DEVKIT_API_KEY", "")
    _MAX_QUEUE_SIZE = 20

    ingest_db = IngestionDB(_DB_PATH)
    ingest_queue: _asyncio.Queue = _asyncio.Queue()

    upload_router = create_upload_router(
        db=ingest_db,
        ingest_queue=ingest_queue,
        reach_to_ke_api_key=_REACH_TO_KE_API_KEY,
        azure_configured=_AZURE_CONFIGURED,
        max_queue_size=_MAX_QUEUE_SIZE,
        devkit_callback_url=_DEVKIT_CALLBACK_URL or None,
        ke_to_devkit_api_key=_KE_TO_DEVKIT_API_KEY or None,
        ke_config=config,
        static_kb_block=ke._blocks[1] if len(ke._blocks) > 1 else None,
    )
    app.include_router(upload_router)
    app.state.ingest_db = ingest_db
    app.state.ingest_queue = ingest_queue

    @app.on_event("startup")
    async def _start_queue_worker():
        """Start the singleton async queue worker on app startup."""
        static_kb = ke._blocks[1] if len(ke._blocks) > 1 else None
        _asyncio.create_task(
            run_queue_worker(
                db=ingest_db,
                ingest_queue=ingest_queue,
                ke_config=config,
                static_kb_block=static_kb,
                devkit_callback_url=_DEVKIT_CALLBACK_URL or None,
                ke_to_devkit_api_key=_KE_TO_DEVKIT_API_KEY or None,
                kb_data_dir=_KB_DATA_DIR,
            )
        )
        logger.info(
            "ke.queue_worker.started",
            extra={"operation": "ke.startup", "status": "success"},
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd knowledge_engine && uv run pytest tests/test_server.py::TestUploadRouterRegistered -v
```

Expected: PASS.

- [ ] **Step 5: Run full KE test suite**

```bash
cd knowledge_engine && uv run pytest --cov=src --cov-report=term-missing
```

Expected: ≥70% coverage, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add knowledge_engine/main.py knowledge_engine/tests/test_server.py
git commit -m "feat(ke): wire upload router and queue worker into main.py"
```

---

## Self-Review Checklist

- [x] `POST /upload` validates API key, sanitizes filenames, checks extensions, handles queue capacity, inserts atomically, enqueues
- [x] `GET /upload/job/{id}` reads from SQLite (durable), calculates queue_position dynamically
- [x] Queue worker handles all 3 modes (`local_write_ingest`, `cloud_upload_ingest`, `cloud_fetch_ingest`)
- [x] `cloud_upload_ingest` dual-write: LocalPVC + Azure explicitly (design issue #2 resolved)
- [x] `_send_callback` retries 3x with backoff, logs debug on each failure (design issue #4 resolved)
- [x] `.docx` and `.html` handlers added (design issue #1 resolved)
- [x] `queue_position` calculated dynamically (design issue #3 resolved)
- [x] All three auth.py helpers (`src/auth.py`) follow the same interface
- [x] StorageBackend ABC enforces method signatures
- [x] SQLite WAL mode for concurrent reads
- [x] Temp file handling: only true temp files cleaned; PVC files kept permanently
- [x] Error handling: no bare `except: pass`; all errors logged + structured response
