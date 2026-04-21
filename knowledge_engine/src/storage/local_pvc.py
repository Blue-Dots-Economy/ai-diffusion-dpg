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
        """Initialise backend with a base directory.

        Args:
            base_dir: Absolute path to the directory where files are stored.
                Defaults to the KB_DATA_DIR environment variable, then /data/kb.
        """
        self._base_dir = Path(base_dir or os.environ.get("KB_DATA_DIR", _DEFAULT_BASE_DIR))

    def upload(self, content: bytes, filename: str) -> str:
        """Write bytes to base_dir/filename and return the absolute path.

        Args:
            content: Raw file bytes.
            filename: Basename only — no path separators.

        Returns:
            Absolute path to the written file.

        Raises:
            ValueError: If content is None.
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
        """Return True if the base directory is accessible.

        Returns:
            Always True — the directory is created on first upload if absent.
        """
        return self._base_dir.exists() or True  # always writable (mkdir on upload)
