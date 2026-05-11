"""RecordingStoreBase — ABC for the audit storage backend.

Belongs to the Reach Layer / Voice channel in the DPG framework.
Every storage backend (local disk, GCS, S3, …) must inherit from this
class and implement ``put``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.recordings.manager_base import RecordingArtifact


class RecordingStoreBase(ABC):
    """Abstract base class for recording storage backends."""

    @abstractmethod
    async def put(self, artifact: RecordingArtifact) -> str:
        """Persist artifact (audio + sidecar manifest). Returns recording URI.

        Args:
            artifact: Fully populated ``RecordingArtifact`` including payload.

        Returns:
            A URI string (e.g. ``file://…``, ``gs://…``) pointing to the
            stored audio file.

        Raises:
            ValueError: If the payload is missing or incompatible with the
                backend.
        """
