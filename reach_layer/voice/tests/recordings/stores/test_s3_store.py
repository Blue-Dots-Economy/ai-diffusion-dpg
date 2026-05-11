"""Tests for S3Store using aiobotocore stubbed via monkeypatch."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.recordings.manager_base import RecordingArtifact, RecordingPayload
from src.recordings.stores.s3_store import S3Store


def _artifact() -> RecordingArtifact:
    return RecordingArtifact(
        call_sid="CA1", session_id="s", caller_id_hash="h",
        start_ts=1.0, end_ts=2.0, duration_ms=1000, consent_granted_ts=0.5,
        source="vobiz", format="mp3", sha256="",
        payload=RecordingPayload(bytes_data=b"DATA"),
    )


def _stub_session(client) -> tuple[MagicMock, MagicMock]:
    class _Ctx:
        async def __aenter__(self_inner):
            return client
        async def __aexit__(self_inner, *a):
            return False
    sess = MagicMock()
    sess.create_client = MagicMock(return_value=_Ctx())
    return sess, client


@pytest.mark.asyncio
async def test_s3_store_uploads_audio_and_sidecar(monkeypatch):
    client = MagicMock()
    client.put_object = AsyncMock(return_value={"ETag": '"x"'})
    sess, _ = _stub_session(client)
    monkeypatch.setattr("src.recordings.stores.s3_store._make_session", lambda: sess)

    store = S3Store(bucket="b", prefix="rec/", region="ap-south-1", kms_key_id="")
    uri = await store.put(_artifact())
    assert uri.startswith("s3://b/")
    assert client.put_object.call_count == 2  # audio + sidecar
    args = client.put_object.call_args_list[0].kwargs
    assert args["Bucket"] == "b"
    assert args["Key"].endswith("CA1.mp3")
    assert args["ServerSideEncryption"] == "AES256"


@pytest.mark.asyncio
async def test_s3_store_uses_kms_when_configured(monkeypatch):
    client = MagicMock()
    client.put_object = AsyncMock(return_value={})
    sess, _ = _stub_session(client)
    monkeypatch.setattr("src.recordings.stores.s3_store._make_session", lambda: sess)

    store = S3Store(bucket="b", prefix="rec/", region="ap-south-1", kms_key_id="kms-1")
    await store.put(_artifact())
    args = client.put_object.call_args_list[0].kwargs
    assert args["ServerSideEncryption"] == "aws:kms"
    assert args["SSEKMSKeyId"] == "kms-1"


def test_s3_store_requires_bucket():
    with pytest.raises(ValueError, match="bucket"):
        S3Store(bucket="", prefix="rec/", region="ap-south-1", kms_key_id="")


@pytest.mark.asyncio
async def test_s3_store_rejects_missing_payload(monkeypatch):
    client = MagicMock()
    client.put_object = AsyncMock(return_value={})
    sess, _ = _stub_session(client)
    monkeypatch.setattr("src.recordings.stores.s3_store._make_session", lambda: sess)
    art = RecordingArtifact(
        call_sid="CA1", session_id="s", caller_id_hash="h",
        start_ts=1.0, end_ts=2.0, duration_ms=0, consent_granted_ts=0.0,
        source="vobiz", format="mp3", sha256="",
        payload=RecordingPayload(fetch_url="https://x"),
    )
    store = S3Store(bucket="b", prefix="rec/", region="ap-south-1", kms_key_id="")
    with pytest.raises(ValueError, match="bytes_data"):
        await store.put(art)
