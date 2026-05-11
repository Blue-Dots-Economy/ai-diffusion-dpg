"""Tests for VobizRecordingSource — start captures URL synchronously, stop is DELETE."""
from __future__ import annotations

import pytest
from aioresponses import aioresponses

from src.recordings.sources.vobiz_source import VobizRecordingSource


@pytest.fixture
def registry() -> dict:
    return {}


def _start_payload(url: str = "https://cdn.vobiz/CALL1.mp3") -> dict:
    return {
        "api_id": "api-1",
        "message": "recording started",
        "recording_id": "rec-1",
        "url": url,
    }


@pytest.mark.asyncio
async def test_begin_posts_record_start_and_captures_url(registry):
    """begin() POSTs Record/ with documented body and stores the response URL."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload(),
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert "CALL1" in registry
    assert src._recording_url == "https://cdn.vobiz/CALL1.mp3"  # noqa: SLF001


@pytest.mark.asyncio
async def test_begin_raises_when_response_missing_url(registry):
    """begin() must fail loudly when the Vobiz response carries no url field."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=200, payload={"api_id": "x", "message": "started"},
        )
        with pytest.raises(RuntimeError, match="url"):
            await src.begin(call_sid="CA1", vobiz_call_id="CALL1")


@pytest.mark.asyncio
async def test_begin_raises_on_bad_status(registry):
    """Non-2xx responses on Record/ start must raise."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=500, payload={},
        )
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await src.begin(call_sid="CA1", vobiz_call_id="CALL1")


@pytest.mark.asyncio
async def test_end_deletes_record_and_fetches_url(registry):
    """end() DELETEs the recording, awaits readiness, then GETs the start-response URL."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get("https://cdn.vobiz/CALL1.mp3", body=b"FAKEMP3", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        # Webhook resolves the readiness future (URL value is ignored).
        registry["CALL1"].set_result("https://ignored-by-impl")
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_proceeds_when_webhook_never_arrives(registry):
    """A missing readiness webhook must NOT block — the start-response URL is authoritative."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=0.05, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get("https://cdn.vobiz/CALL1.mp3", body=b"FAKEMP3", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        # Do not resolve the registry future; webhook_timeout_s expires.
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_raises_when_begin_not_called(registry):
    """end() must raise RuntimeError if begin() was never called."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with pytest.raises(RuntimeError, match="begin"):
        await src.end()


def test_pipeline_processors_is_empty_list(registry):
    """pipeline_processors must return an empty list for VobizRecordingSource."""
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    assert src.pipeline_processors == []
