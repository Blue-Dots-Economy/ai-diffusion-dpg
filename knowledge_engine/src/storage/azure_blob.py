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

from src.storage.base import StorageBackend, StorageError

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


class AzureBlobStorageBackend(StorageBackend):
    """Upload and download KB documents from Azure Blob Storage."""

    def __init__(self, account_name: str, account_key: str, container_name: str) -> None:
        """Initialise the Azure Blob Storage backend.

        Args:
            account_name: Azure storage account name.
            account_key: Azure storage account key (base64-encoded).
            container_name: Target blob container name.
        """
        account_url = f"https://{account_name}.blob.core.windows.net"
        self._client = BlobServiceClient(account_url, credential=account_key)
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
                blob_client.upload_blob(content, overwrite=True)
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
            except Exception as e:
                if attempt == 0:
                    # Retry once on transient failures.
                    continue
                logger.error(
                    "azure_blob.upload_failed",
                    extra={
                        "operation": "azure_blob.upload",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                raise StorageError(f"Azure upload failed: {e}") from e
        return filename  # unreachable; satisfies type checker

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
        """Return True if the Azure container is reachable.

        Returns:
            True when the container properties can be fetched, False otherwise.
        """
        try:
            container_client = self._client.get_container_client(self._container)
            container_client.get_container_properties(timeout=5.0)
            return True
        except Exception:
            return False
