"""Contract tests for RecordingManagerBase + NullRecordingManager."""
from __future__ import annotations

import pytest

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingPayload,
)
from src.recordings.manager import NullRecordingManager


def test_recording_manager_base_is_abstract():
    with pytest.raises(TypeError):
        RecordingManagerBase()  # type: ignore[abstract]


def test_recording_payload_carries_either_bytes_or_url():
    p1 = RecordingPayload(bytes_data=b"x")
    assert p1.bytes_data == b"x"
    assert p1.fetch_url is None
    p2 = RecordingPayload(fetch_url="https://x")
    assert p2.fetch_url == "https://x"
    assert p2.bytes_data is None


def test_recording_artifact_is_a_dataclass_with_required_fields():
    art = RecordingArtifact(
        call_sid="CA1",
        session_id="s",
        caller_id_hash="h",
        start_ts=1.0,
        end_ts=2.0,
        duration_ms=1000,
        consent_granted_ts=0.5,
        source="vobiz",
        format="mp3",
        sha256="abc",
        payload=RecordingPayload(bytes_data=b"x"),
    )
    assert art.duration_ms == 1000


@pytest.mark.asyncio
async def test_null_recording_manager_idle_forever():
    m = NullRecordingManager()
    assert m.state == "idle"
    await m.start(consent_granted_ts=1.0)
    assert m.state == "idle"
    await m.stop()
    assert m.state == "idle"
    assert await m.finalize() is None
    assert m.pipeline_processors == []


@pytest.mark.asyncio
async def test_null_manager_metadata_defaults():
    m = NullRecordingManager()
    assert m.caller_id_hash == ""
    assert m.source_name == "disabled"
