"""RecordingManagerBase — ABC for per-call recording lifecycle.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

RecordingState = Literal["idle", "recording", "stopped", "finalized", "failed"]


@dataclass
class RecordingPayload:
    """Carries either in-memory audio bytes or a URL the store must fetch."""

    bytes_data: Optional[bytes] = None
    fetch_url: Optional[str] = None


@dataclass
class RecordingArtifact:
    """Audit metadata + payload reference for a single recorded call."""

    call_sid: str
    session_id: str
    caller_id_hash: str
    start_ts: float
    end_ts: float
    duration_ms: int
    consent_granted_ts: float
    source: Literal["vobiz", "pipeline"]
    format: Literal["mp3", "wav"]
    sha256: str
    payload: RecordingPayload
    extra: dict = field(default_factory=dict)


class RecordingManagerBase(ABC):
    """Per-call recording lifecycle: idle → recording → stopped → finalized."""

    @abstractmethod
    async def start(self, *, consent_granted_ts: float) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def finalize(self) -> Optional[RecordingArtifact]: ...

    @property
    @abstractmethod
    def state(self) -> RecordingState: ...

    @property
    @abstractmethod
    def pipeline_processors(self) -> list:
        """Pipecat processors to splice into the call pipeline; [] for vobiz/null."""

    @property
    @abstractmethod
    def caller_id_hash(self) -> str:
        """16-hex SHA256 of (salt + caller_id). Empty string when disabled."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Source identifier — 'vobiz' | 'pipeline' | 'disabled'."""
