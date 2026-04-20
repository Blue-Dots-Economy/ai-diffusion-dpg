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
        mock_blob.upload_blob.assert_called_once_with(b"data", overwrite=True, timeout=30.0)
        assert path == "guide.pdf"

    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_health_check_returns_true_when_reachable(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_container = MagicMock()
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_container_client.return_value = mock_container

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        result = backend.health_check()
        assert result is True

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

    @patch("src.storage.azure_blob.BlobServiceClient")
    def test_health_check_returns_false_on_error(self, MockBlobServiceClient):
        mock_client = MagicMock()
        mock_container = MagicMock()
        MockBlobServiceClient.return_value = mock_client
        mock_client.get_container_client.return_value = mock_container
        mock_container.get_container_properties.side_effect = Exception("unreachable")

        from src.storage.azure_blob import AzureBlobStorageBackend
        backend = AzureBlobStorageBackend("acct", "key==", "container")
        result = backend.health_check()
        assert result is False
