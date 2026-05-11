"""Tests for the concrete RecordingManager state machine."""
from __future__ import annotations

import pytest

from src.recordings.manager import RecordingManager
from src.recordings.manager_base import RecordingArtifact, RecordingPayload


class _StubSource:
    def __init__(self, payload: RecordingPayload) -> None:
        self.payload = payload
        self.began = False
        self.ended = False
    @property
    def pipeline_processors(self): return []
    async def begin(self, *, call_sid, vobiz_call_id):
        self.began = True
    async def end(self) -> RecordingPayload:
        self.ended = True
        return self.payload


class _StubStore:
    def __init__(self) -> None:
        self.last: RecordingArtifact | None = None
    async def put(self, art: RecordingArtifact) -> str:
        self.last = art
        return "file:///tmp/x.wav"


def _mgr(payload=RecordingPayload(bytes_data=b"x" * 320), min_ms=0) -> tuple:
    src, store = _StubSource(payload), _StubStore()
    m = RecordingManager(
        source=src, store=store, call_sid="CA1", session_id="s",
        caller_id_hash="h", source_name="pipeline", fmt="wav",
        sample_rate=8000, min_duration_ms=min_ms, vobiz_call_id="",
    )
    return m, src, store


@pytest.mark.asyncio
async def test_lifecycle_idle_recording_stopped_finalized():
    m, src, store = _mgr()
    assert m.state == "idle"
    await m.start(consent_granted_ts=1.0)
    assert m.state == "recording"
    assert src.began
    await m.stop()
    assert m.state == "stopped"
    art = await m.finalize()
    assert m.state == "finalized"
    assert art is not None
    assert store.last is art


@pytest.mark.asyncio
async def test_finalize_without_start_returns_none():
    m, _, store = _mgr()
    art = await m.finalize()
    assert art is None
    assert m.state == "idle"
    assert store.last is None


@pytest.mark.asyncio
async def test_zero_bytes_short_circuits_with_empty():
    m, _, store = _mgr(payload=RecordingPayload(bytes_data=b""))
    await m.start(consent_granted_ts=1.0)
    await m.stop()
    art = await m.finalize()
    assert art is None
    assert m.state == "finalized"
    assert store.last is None


@pytest.mark.asyncio
async def test_short_duration_short_circuits_with_empty():
    """duration_ms below min_duration_ms must skip storage as empty."""
    src, store = _StubSource(RecordingPayload(bytes_data=b"x" * 320)), _StubStore()
    m = RecordingManager(
        source=src, store=store, call_sid="CA1", session_id="s",
        caller_id_hash="h", source_name="pipeline", fmt="wav",
        sample_rate=8000, min_duration_ms=10_000, vobiz_call_id="",
    )
    await m.start(consent_granted_ts=1.0)
    await m.stop()
    art = await m.finalize()
    assert art is None
    assert m.state == "finalized"
    assert store.last is None


@pytest.mark.asyncio
async def test_failed_source_marks_state_failed():
    class _BadSource(_StubSource):
        async def end(self):
            raise RuntimeError("boom")
    src = _BadSource(RecordingPayload(bytes_data=b"x"))
    m = RecordingManager(
        source=src, store=_StubStore(), call_sid="CA1", session_id="s",
        caller_id_hash="h", source_name="pipeline", fmt="wav",
        sample_rate=8000, min_duration_ms=10, vobiz_call_id="",
    )
    await m.start(consent_granted_ts=1.0)
    await m.stop()
    art = await m.finalize()
    assert art is None
    assert m.state == "failed"


@pytest.mark.asyncio
async def test_pipeline_processors_passthrough():
    """Manager exposes the source's pipeline_processors list."""
    m, src, _ = _mgr()
    # Pipeline source has 1 processor (RecordingTapProcessor), but our stub returns [].
    # Use stubbed list to verify passthrough.
    assert m.pipeline_processors == src.pipeline_processors == []


@pytest.mark.asyncio
async def test_recording_manager_exposes_metadata_properties():
    m, _, _ = _mgr()
    assert m.caller_id_hash == "h"  # matches the _mgr() helper's caller_id_hash="h"
    assert m.source_name == "pipeline"
