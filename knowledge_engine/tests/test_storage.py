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
