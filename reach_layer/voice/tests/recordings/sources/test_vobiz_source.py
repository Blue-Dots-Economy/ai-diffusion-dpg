"""Tests for VobizRecordingSource — REST start/stop + webhook future."""
from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses

from src.recordings.sources.vobiz_source import VobizRecordingSource


@pytest.fixture
def registry() -> dict:
    return {}


@pytest.mark.asyncio
async def test_begin_posts_record_start(registry):
    """begin() must POST to the Vobiz Record endpoint and register a future."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload={"ok": True},
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert "CALL1" in registry  # future registered


@pytest.mark.asyncio
async def test_end_posts_stop_and_awaits_future_then_fetches(registry):
    """end() must POST stop, wait for the webhook future, and return MP3 bytes."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=202, payload={})
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/Stop/", status=204)
        m.get("https://cdn.vobiz/CALL1.mp3", body=b"FAKEMP3", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        registry["CALL1"].set_result("https://cdn.vobiz/CALL1.mp3")
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_times_out_when_webhook_never_arrives(registry):
    """end() must raise asyncio.TimeoutError when the webhook future is never resolved."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=0.1, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=202)
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/Stop/", status=204)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        with pytest.raises(asyncio.TimeoutError):
            await src.end()


@pytest.mark.asyncio
async def test_end_raises_when_begin_not_called(registry):
    """end() must raise RuntimeError if begin() was never called."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call//Record/Stop/", status=204)
        with pytest.raises(RuntimeError):
            await src.end()


def test_pipeline_processors_is_empty_list(registry):
    """pipeline_processors must return an empty list for VobizRecordingSource."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    assert src.pipeline_processors == []
