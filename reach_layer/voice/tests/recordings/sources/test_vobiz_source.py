"""Tests for VobizRecordingSource — REST start (no URL needed), DELETE stop, webhook URL."""
from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses

from src.recordings.sources.vobiz_source import VobizRecordingSource


@pytest.fixture
def registry() -> dict:
    return {}


def _start_payload_minimal() -> dict:
    """Matches the actual Vobiz production response — no url field."""
    return {"api_id": "api-1", "message": "recording started"}


def _start_payload_with_url(url: str = "https://cdn.vobiz/CALL1.mp3") -> dict:
    """Future-proofing: docs imply this shape; we accept it if it ever appears."""
    return {
        "api_id": "api-1",
        "message": "recording started",
        "recording_id": "rec-1",
        "url": url,
    }


def _src(registry: dict, webhook_timeout: float = 5.0) -> VobizRecordingSource:
    return VobizRecordingSource(
        auth_id="A",
        auth_token="T",
        callback_url="http://x/recording-ready",
        webhook_timeout_s=webhook_timeout,
        fetch_timeout_s=5.0,
        registry=registry,
    )


@pytest.mark.asyncio
async def test_begin_succeeds_without_url_in_response(registry):
    """The production Vobiz response has only api_id+message; begin() must accept it."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert "CALL1" in registry
    assert src._recording_url == ""  # noqa: SLF001


@pytest.mark.asyncio
async def test_begin_captures_url_if_provided(registry):
    """If Vobiz ever returns a url synchronously, capture it for end()."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_with_url(),
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert src._recording_url == "https://cdn.vobiz/CALL1.mp3"  # noqa: SLF001


@pytest.mark.asyncio
async def test_begin_raises_on_bad_status(registry):
    """Non-2xx responses on Record/ start must raise."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=500, payload={},
        )
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await src.begin(call_sid="CA1", vobiz_call_id="CALL1")


@pytest.mark.asyncio
async def test_end_deletes_and_uses_webhook_url(registry):
    """end() DELETEs the recording and uses the webhook-delivered URL."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get("https://cdn.vobiz/CALL1.mp3", body=b"FAKEMP3", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        registry["CALL1"].set_result("https://cdn.vobiz/CALL1.mp3")
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_prefers_start_response_url_over_webhook(registry):
    """If begin() captured a URL, end() uses it even if the webhook fires."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_with_url("https://primary/x.mp3"),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get("https://primary/x.mp3", body=b"FROM_START", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        # Webhook resolves with a different URL — must be ignored.
        registry["CALL1"].set_result("https://webhook/y.mp3")
        payload = await src.end()
    assert payload.bytes_data == b"FROM_START"


@pytest.mark.asyncio
async def test_end_times_out_when_no_url_available(registry):
    """If neither start nor webhook gave a URL within webhook_timeout_s, end() raises."""
    src = _src(registry, webhook_timeout=0.05)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        with pytest.raises(asyncio.TimeoutError):
            await src.end()


@pytest.mark.asyncio
async def test_end_raises_when_webhook_url_is_empty(registry):
    """If the webhook resolved with an empty string (alias miss), end() raises clearly."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        registry["CALL1"].set_result("")
        with pytest.raises(RuntimeError, match="empty"):
            await src.end()


@pytest.mark.asyncio
async def test_end_raises_when_begin_not_called(registry):
    """end() must raise RuntimeError if begin() was never called."""
    src = _src(registry)
    with pytest.raises(RuntimeError, match="vobiz_call_id"):
        await src.end()


def test_pipeline_processors_is_empty_list(registry):
    """pipeline_processors must return an empty list for VobizRecordingSource."""
    src = _src(registry)
    assert src.pipeline_processors == []
