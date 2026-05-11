"""End-to-end smoke: pipeline source + LocalFileStore via factory + ConsentEvent path."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.recordings.factory import build_recording_manager
from reach_layer_base import ConsentEvent


@pytest.mark.asyncio
async def test_pipeline_local_full_lifecycle(tmp_path: Path):
    cfg = {"reach_layer": {"channels": {"voice": {
        "vobiz": {"auth_id": "A", "auth_token": "T", "sample_rate": 8000},
        "recording": {
            "source": "pipeline", "consent_purpose": "recording",
            "webhook_timeout_s": 5.0, "fetch_timeout_s": 5.0, "min_duration_ms": 1,
            "caller_id_hash_salt": "s" * 32,
            "store": {"backend": "local",
                       "local": {"base_path": str(tmp_path)},
                       "s3": {"bucket": "", "prefix": "", "region": "", "kms_key_id": ""}},
        },
    }}}}
    manager = build_recording_manager(
        cfg, telephony=None, registry={}, call_sid="CA-E2E",
        session_id="sess", caller_id="+910000000000", vobiz_call_id="",
    )

    # Simulate the adapter's _on_consent_event call when the operator grants recording.
    evt = ConsentEvent(purpose="recording", granted=True, consent_granted_ts=1.0)
    assert evt.granted
    await manager.start(consent_granted_ts=evt.consent_granted_ts)

    # Feed audio frames through the tap processor.
    proc = manager.pipeline_processors[0]
    from pipecat.frames.frames import InputAudioRawFrame
    # Pipecat FrameProcessor.process_frame() refuses frames until _started
    # is True (normally set by the StartFrame the runner emits). Bypass the
    # runner machinery for this unit-level e2e.
    proc._started = True  # noqa: SLF001 — intentional test shortcut
    for _ in range(20):
        await proc.process_frame(
            InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1),
            direction=None,
        )

    await asyncio.sleep(0.05)
    await manager.stop()
    artifact = await manager.finalize()
    assert artifact is not None
    assert artifact.format == "wav"

    # File exists under tmp_path/YYYY/MM/DD/CA-E2E.wav
    matches = list(tmp_path.rglob("CA-E2E.wav"))
    assert len(matches) == 1
    sidecar = matches[0].with_suffix(".json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["call_sid"] == "CA-E2E"
    assert meta["source"] == "pipeline"
    assert meta["format"] == "wav"
    assert meta["sha256"]
