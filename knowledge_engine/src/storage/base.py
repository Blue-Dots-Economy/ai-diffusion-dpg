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
