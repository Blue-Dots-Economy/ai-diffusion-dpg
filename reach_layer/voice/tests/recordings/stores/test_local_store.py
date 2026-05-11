"""Tests for LocalFileStore."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.recordings.manager_base import RecordingArtifact, RecordingPayload
from src.recordings.stores.local_store import LocalFileStore


def _artifact(payload: RecordingPayload, source="pipeline", fmt="wav") -> RecordingArtifact:
    return RecordingArtifact(
        call_sid="CA1", session_id="s", caller_id_hash="h",
        start_ts=1.0, end_ts=2.0, duration_ms=1000, consent_granted_ts=0.5,
        source=source, format=fmt, sha256="", payload=payload,
    )


@pytest.mark.asyncio
async def test_local_store_writes_audio_and_sidecar(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    payload = RecordingPayload(bytes_data=b"AUDIO")
    art = _artifact(payload)
    uri = await store.put(art)
    assert uri.startswith("file://")
    audio_path = Path(uri.removeprefix("file://"))
    assert audio_path.read_bytes() == b"AUDIO"
    sidecar = audio_path.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    assert meta["call_sid"] == "CA1"
    assert meta["sha256"] == hashlib.sha256(b"AUDIO").hexdigest()
    assert meta["recording_uri"] == uri


@pytest.mark.asyncio
async def test_local_store_path_uses_yyyy_mm_dd(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    art = _artifact(RecordingPayload(bytes_data=b"x"))
    uri = await store.put(art)
    rel = uri.removeprefix("file://").replace(str(tmp_path) + "/", "")
    parts = rel.split("/")
    assert len(parts) == 4  # YYYY/MM/DD/CA1.wav
    assert parts[-1] == "CA1.wav"


@pytest.mark.asyncio
async def test_local_store_rejects_missing_payload(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    payload = RecordingPayload(fetch_url="https://x")
    art = _artifact(payload)
    with pytest.raises(ValueError, match="bytes_data"):
        await store.put(art)


@pytest.mark.asyncio
async def test_local_store_computes_sha256_on_artifact(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    data = b"hello recording"
    art = _artifact(RecordingPayload(bytes_data=data))
    assert art.sha256 == ""  # blank before put
    await store.put(art)
    assert art.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
async def test_local_store_sidecar_schema_version(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    art = _artifact(RecordingPayload(bytes_data=b"data"))
    uri = await store.put(art)
    audio_path = Path(uri.removeprefix("file://"))
    meta = json.loads(audio_path.with_suffix(".json").read_text())
    assert meta["schema_version"] == "1.0"
    assert meta["store_backend"] == "local"


@pytest.mark.asyncio
async def test_local_store_mp3_format(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    art = _artifact(RecordingPayload(bytes_data=b"mp3data"), fmt="mp3")
    uri = await store.put(art)
    assert uri.endswith(".mp3")
    audio_path = Path(uri.removeprefix("file://"))
    assert audio_path.suffix == ".mp3"
