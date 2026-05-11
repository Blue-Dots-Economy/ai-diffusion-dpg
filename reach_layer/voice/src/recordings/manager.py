"""Concrete RecordingManager(s).

NullRecordingManager is the no-op for disabled recording.
RecordingManager is the real state-machine implementation.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingState,
)
from src.recordings.sources.source_base import RecordingSourceBase
from src.recordings.stores.store_base import RecordingStoreBase

logger = logging.getLogger(__name__)


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

    @property
    def caller_id_hash(self) -> str:
        return ""

    @property
    def source_name(self) -> str:
        return "disabled"


class RecordingManager(RecordingManagerBase):
    """Concrete recording manager with an idle→recording→stopped→finalized|failed state machine.

    Delegates audio capture to a RecordingSourceBase and persistence to a
    RecordingStoreBase. Recordings shorter than min_duration_ms or with zero
    bytes are finalized without storing an artifact. All source/store failures
    are logged, the state is set to 'failed', and None is returned — the call
    is never affected.

    Args:
        source: Audio capture back-end (pipeline tap or Vobiz webhook).
        store: Persistence back-end (local file or S3).
        call_sid: Telephony call identifier.
        session_id: Agent Core session identifier.
        caller_id_hash: Hashed caller identifier for audit logs (no raw PII).
        source_name: Human-readable source label ("vobiz" | "pipeline").
        fmt: Audio format string ("mp3" | "wav").
        sample_rate: Sample rate in Hz.
        min_duration_ms: Recordings shorter than this are not stored.
        vobiz_call_id: Vobiz-side call ID (empty string when using pipeline source).
    """

    def __init__(
        self,
        *,
        source: RecordingSourceBase,
        store: RecordingStoreBase,
        call_sid: str,
        session_id: str,
        caller_id_hash: str,
        source_name: str,
        fmt: str,
        sample_rate: int,
        min_duration_ms: int,
        vobiz_call_id: str,
    ) -> None:
        self._source = source
        self._store = store
        self._call_sid = call_sid
        self._session_id = session_id
        self._caller_id_hash = caller_id_hash
        self._source_name = source_name
        self._fmt = fmt
        self._sample_rate = sample_rate
        self._min_duration_ms = min_duration_ms
        self._vobiz_call_id = vobiz_call_id
        self._state: RecordingState = "idle"
        self._consent_granted_ts: float = 0.0
        self._start_ts: float = 0.0
        self._end_ts: float = 0.0
        self._extra: dict = {}

    @property
    def state(self) -> RecordingState:
        """Return the current state machine state."""
        return self._state

    @property
    def pipeline_processors(self) -> list:
        """Return the source's pipeline processor list for pipecat wiring."""
        return self._source.pipeline_processors

    @property
    def caller_id_hash(self) -> str:
        """Return the caller ID hash for audit logs."""
        return self._caller_id_hash

    @property
    def source_name(self) -> str:
        """Return the recording source identifier."""
        return self._source_name

    def attach_trace_id(self, trace_id: str) -> None:
        """Attach an OTel trace ID to be included in the stored artifact's extra dict.

        Args:
            trace_id: OpenTelemetry trace identifier string.
        """
        self._extra["trace_id"] = trace_id

    async def start(self, *, consent_granted_ts: float) -> None:
        """Transition from idle to recording and begin audio capture.

        No-ops if the manager is not in the idle state.

        Args:
            consent_granted_ts: Unix timestamp when caller consent was granted.
        """
        if self._state != "idle":
            return
        try:
            await self._source.begin(
                call_sid=self._call_sid, vobiz_call_id=self._vobiz_call_id
            )
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.start_failed",
                extra={
                    "operation": "recording_manager.start",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        self._consent_granted_ts = consent_granted_ts
        self._start_ts = time.time()
        self._state = "recording"

    async def stop(self) -> None:
        """Transition from recording to stopped, recording the end timestamp.

        No-ops if the manager is not in the recording state.
        """
        if self._state not in ("recording",):
            return
        self._end_ts = time.time()
        self._state = "stopped"

    async def finalize(self) -> Optional[RecordingArtifact]:
        """Drain the source, validate duration, persist the artifact, and transition to finalized.

        Returns None (without storing) if:
        - The manager was never started (state == idle).
        - The manager is already in a failed state.
        - The source returns zero bytes.
        - The recording duration is below min_duration_ms.

        Returns:
            The stored RecordingArtifact, or None if recording was skipped or failed.
        """
        if self._state == "idle":
            return None
        if self._state == "failed":
            return None
        try:
            payload = await self._source.end()
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.finalize_failed",
                extra={
                    "operation": "recording_manager.finalize",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None
        if self._end_ts == 0.0:
            self._end_ts = time.time()
        duration_ms = max(0, int((self._end_ts - self._start_ts) * 1000))
        size = len(payload.bytes_data or b"")
        skip = duration_ms < self._min_duration_ms or size == 0
        if skip:
            self._state = "finalized"
            logger.info(
                "recording_manager.empty",
                extra={
                    "operation": "recording_manager.finalize",
                    "status": "skipped",
                    "call_sid": self._call_sid,
                    "duration_ms": duration_ms,
                    "bytes": size,
                    "reason": "below_min_duration" if size > 0 else "zero_bytes",
                },
            )
            return None
        artifact = RecordingArtifact(
            call_sid=self._call_sid,
            session_id=self._session_id,
            caller_id_hash=self._caller_id_hash,
            start_ts=self._start_ts,
            end_ts=self._end_ts,
            duration_ms=duration_ms,
            consent_granted_ts=self._consent_granted_ts,
            source=self._source_name,  # type: ignore[arg-type]
            format=self._fmt,  # type: ignore[arg-type]
            sha256="",
            payload=payload,
            extra=dict(self._extra),
        )
        try:
            await self._store.put(artifact)
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.store_failed",
                extra={
                    "operation": "recording_manager.finalize",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None
        self._state = "finalized"
        return artifact
