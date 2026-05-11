"""Tests for VobizRecordingSource — recording-list polling is the URL source of truth."""
from __future__ import annotations

import pytest
from aioresponses import aioresponses

from src.recordings.sources.vobiz_source import VobizRecordingSource


@pytest.fixture
def registry() -> dict:
    return {}


def _start_payload_minimal() -> dict:
    """Matches the actual Vobiz production response — no url field."""
    return {"api_id": "api-1", "message": "recording started"}


def _list_payload(call_uuid: str, url: str) -> dict:
    return {
        "api_id": "api-list-1",
        "meta": {"limit": 20, "next": None, "offset": 0, "previous": None, "total_count": 1},
        "objects": [
            {
                "call_uuid": call_uuid,
                "recording_id": "rec-1",
                "recording_url": url,
                "recording_format": "mp3",
                "recording_duration_ms": "12345",
            }
        ],
    }


def _src(
    registry: dict,
    webhook_timeout: float = 5.0,
    poll_interval: float = 0.05,
    poll_max: float = 5.0,
) -> VobizRecordingSource:
    return VobizRecordingSource(
        auth_id="A",
        auth_token="T",
        callback_url="http://x/recording-ready",
        webhook_timeout_s=webhook_timeout,
        fetch_timeout_s=5.0,
        registry=registry,
        poll_interval_s=poll_interval,
        poll_max_s=poll_max,
    )


@pytest.mark.asyncio
async def test_begin_accepts_minimal_response(registry):
    """The production Vobiz response has only api_id+message; begin() must accept it."""
    src = _src(registry)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert "CALL1" in registry


@pytest.mark.asyncio
async def test_end_finds_url_via_recording_list(registry):
    """When the webhook never fires, end() discovers the URL by polling /Recording/."""
    src = _src(registry, webhook_timeout=10.0, poll_max=5.0)
    list_url = "https://media.vobiz.ai/v1/Account/A/Recording/rec-1.mp3"
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get(
            "https://api.vobiz.ai/api/v1/Account/A/Recording/?call_uuid=CALL1&limit=5",
            status=200, payload=_list_payload("CALL1", list_url),
        )
        m.get(list_url, body=b"FAKEMP3", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        # Do NOT resolve the webhook future — poll wins.
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_prefers_webhook_when_it_arrives_first(registry):
    """Webhook is a fast-path: if it fires before the list poll succeeds, use it."""
    src = _src(registry, webhook_timeout=5.0, poll_interval=10.0, poll_max=10.0)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get("https://cdn.fast/x.mp3", body=b"FROM_WEBHOOK", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        registry["CALL1"].set_result("https://cdn.fast/x.mp3")
        payload = await src.end()
    assert payload.bytes_data == b"FROM_WEBHOOK"


@pytest.mark.asyncio
async def test_end_skips_list_entries_for_other_calls(registry):
    """Recording-list filter must match call_uuid; other entries are ignored."""
    src = _src(registry, webhook_timeout=10.0, poll_max=5.0)
    target_url = "https://media.vobiz.ai/v1/Account/A/Recording/target.mp3"
    payload = {
        "api_id": "x",
        "meta": {"limit": 20, "total_count": 2},
        "objects": [
            {"call_uuid": "OTHER", "recording_id": "r-other",
             "recording_url": "https://media.vobiz.ai/other.mp3"},
            {"call_uuid": "CALL1", "recording_id": "r-target",
             "recording_url": target_url},
        ],
    }
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get(
            "https://api.vobiz.ai/api/v1/Account/A/Recording/?call_uuid=CALL1&limit=5",
            status=200, payload=payload,
        )
        m.get(target_url, body=b"TARGET", status=200)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        result = await src.end()
    assert result.bytes_data == b"TARGET"


@pytest.mark.asyncio
async def test_end_raises_when_no_url_discoverable(registry):
    """If list never shows our call and webhook never fires, raise with clear log."""
    src = _src(registry, webhook_timeout=0.2, poll_interval=0.05, poll_max=0.2)
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload=_start_payload_minimal(),
        )
        m.delete("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=204)
        m.get(
            "https://api.vobiz.ai/api/v1/Account/A/Recording/?call_uuid=CALL1&limit=5",
            status=200, payload={"objects": []},
            repeat=True,
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        with pytest.raises(RuntimeError, match="not discoverable"):
            await src.end()


@pytest.mark.asyncio
async def test_end_raises_when_begin_not_called(registry):
    """end() must raise RuntimeError if begin() was never called."""
    src = _src(registry)
    with pytest.raises(RuntimeError, match="vobiz_call_id"):
        await src.end()


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


def test_pipeline_processors_is_empty_list(registry):
    """pipeline_processors must return an empty list for VobizRecordingSource."""
    src = _src(registry)
    assert src.pipeline_processors == []
