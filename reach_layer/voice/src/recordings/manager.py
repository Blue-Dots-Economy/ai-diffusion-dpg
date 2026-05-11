"""Concrete RecordingManager(s). RecordingManager (the real implementation)
is added in a later task.
"""
from __future__ import annotations

from typing import Optional

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingState,
)


class NullRecordingManager(RecordingManagerBase):
    """No-op manager used when recording.source == 'disabled'."""

    async def start(self, *, consent_granted_ts: float) -> None:
        return

    async def stop(self) -> None:
        return

    async def finalize(self) -> Optional[RecordingArtifact]:
        return None

    @property
    def state(self) -> RecordingState:
        return "idle"

    @property
    def pipeline_processors(self) -> list:
        return []
