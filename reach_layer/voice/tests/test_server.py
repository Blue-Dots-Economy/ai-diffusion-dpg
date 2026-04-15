"""Tests for telephony adapter FastAPI server."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("src.bot.run_bot", new_callable=AsyncMock) as mock_bot, \
         patch("server.CampaignManager"), \
         patch("server.load_reach_config", return_value={
             "telephony_adapter": {
                 "port": 8006,
                 "public_url": "https://example.app",
                 "vobiz": {
                     "auth_id": "MA1", "auth_token": "t",
                     "api_base": "https://api.vobiz.ai/api/v1",
                     "from_number": "+91",
                 },
                 "raya": {
                     "api_key": "k", "stt_wss_url": "wss://...",
                     "tts_base_url": "https://...", "language": "hi",
                     "voice_id": "v1", "tts_speed": 1.0,
                 },
                 "agent_core": {
                     "base_url": "http://agent_core:8000",
                     "timeout_ms": 5000,
                     "fallback_phrase": "sorry",
                     "greeting": "Hello!",
                 },
             },
             "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
         }), \
         patch("server.init_otel"):
        from server import create_app
        app = create_app()
        yield TestClient(app)


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_answer_returns_xml_with_websocket_url(client):
    resp = client.post("/answer", data={"CallSid": "CA1", "From": "+91999"})
    assert resp.status_code == 200
    assert "wss://" in resp.text or "ws://" in resp.text
    assert "CA1" in resp.text
    assert resp.headers["content-type"].startswith("application/xml")


def test_campaign_endpoint_calls_manager(client):
    with patch("server._campaign_manager") as mock_mgr:
        mock_mgr.initiate_call = AsyncMock(return_value={"callSid": "CA_NEW"})
        resp = client.post("/campaign", json={"to_number": "+919999999999"})
    assert resp.status_code == 200
    assert resp.json()["callSid"] == "CA_NEW"


def test_campaign_empty_to_number_returns_422(client):
    resp = client.post("/campaign", json={"to_number": ""})
    assert resp.status_code in (400, 422)


def test_recording_finished_returns_200(client):
    resp = client.post(
        "/recording-finished", json={"callSid": "CA1", "recordingUrl": "https://..."}
    )
    assert resp.status_code == 200


def test_recording_ready_returns_200(client):
    resp = client.post(
        "/recording-ready", json={"callSid": "CA1", "recordingUrl": "https://..."}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_websocket_passes_caller_id_from_stored_form():
    """WebSocket endpoint must pass the caller_id stored during /answer to run_bot."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317", "sample_rate": 1.0, "export_interval_ms": 5000}},
    }

    captured = {}

    async def mock_run_bot(websocket, call_sid, caller_id, config):
        captured["caller_id"] = caller_id
        captured["call_sid"] = call_sid

    with patch.object(bot_module, "run_bot", mock_run_bot), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"):
        from server import create_app
        app = create_app(config)
        client = TestClient(app)

        # First register the caller_id via /answer
        client.post("/answer", data={"CallUUID": "call-xyz", "From": "+911111111111"})

        # Then open the WebSocket
        with client.websocket_connect("/ws/call-xyz"):
            pass

    assert captured.get("caller_id") == "+911111111111"
    assert captured.get("call_sid") == "call-xyz"


@pytest.mark.asyncio
async def test_websocket_caller_id_empty_when_from_missing():
    """When /answer has no From field, caller_id must be empty string (not None or crash)."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317", "sample_rate": 1.0, "export_interval_ms": 5000}},
    }

    captured = {}

    async def mock_run_bot(websocket, call_sid, caller_id, config):
        captured["caller_id"] = caller_id

    with patch.object(bot_module, "run_bot", mock_run_bot), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"):
        from server import create_app
        app = create_app(config)
        client = TestClient(app)

        # /answer without a From field (masked CLID, internal transfer, etc.)
        client.post("/answer", data={"CallUUID": "call-nofrom"})

        with client.websocket_connect("/ws/call-nofrom"):
            pass

    assert captured.get("caller_id") == ""
