"""Tests for telephony adapter FastAPI server."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    with patch("src.bot.run_bot", new_callable=AsyncMock), \
         patch("server.CampaignManager"), \
         patch("server.load_reach_config", return_value={
             "reach_layer": {"channels": {"voice": {
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
             }}},
             "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
         }), \
         patch("server.init_otel"):
        from server import create_app
        yield create_app()


@pytest.fixture
def client(app):
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
        "reach_layer": {"channels": {"voice": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        }}},
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
        "reach_layer": {"channels": {"voice": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        }}},
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


# ---------------------------------------------------------------------------
# OTel instrumentation tests
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = {
    "reach_layer": {"channels": {"voice": {
        "public_url": "https://example.com",
        "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000,
                  "api_base": "https://api.vobiz.ai/api/v1", "from_number": "+91"},
        "vad": {},
        "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1",
                 "language": "hi", "voice_id": "v", "tts_speed": 1.0},
        "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000,
                       "greeting": "hi", "fallback_phrase": "sorry"},
    }}},
    "observability": {"otel": {"collector_endpoint": "http://otelcol:4317",
                               "sample_rate": 1.0, "export_interval_ms": 5000}},
}


def test_fastapi_instrumented_on_startup():
    """FastAPIInstrumentor.instrument_app must be called during create_app."""
    with patch("src.bot.run_bot", new_callable=AsyncMock), \
         patch("server.CampaignManager"), \
         patch("server.load_reach_config", return_value=_MINIMAL_CONFIG), \
         patch("server.init_otel"), \
         patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app") as mock_instrument:
        from server import create_app
        create_app()
        mock_instrument.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_span_attributes_set():
    """websocket_endpoint must emit reach.inbound span with correct attributes."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    recorded_attrs: dict = {}

    class _MockSpan:
        def set_attribute(self, k, v):
            recorded_attrs[k] = v

        def record_exception(self, exc):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    async def mock_run_bot(websocket, call_sid, caller_id, config):
        pass

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = _MockSpan()

    with patch.object(bot_module, "run_bot", mock_run_bot), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"), \
         patch("server.otel_trace") as mock_otel_trace:
        mock_otel_trace.get_tracer.return_value = mock_tracer
        from server import create_app
        app = create_app(_MINIMAL_CONFIG)
        tc = TestClient(app)
        tc.post("/answer", data={"CallUUID": "call-otel", "From": "+911234567890"})
        with tc.websocket_connect("/ws/call-otel"):
            pass

    assert recorded_attrs.get("dpg.channel") == "voice"
    assert recorded_attrs.get("dpg.assembly_mode") == "session"
    assert "session_id" in recorded_attrs


@pytest.mark.asyncio
async def test_websocket_span_records_exception():
    """websocket_endpoint must call span.record_exception when run_bot raises."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    recorded_exceptions: list = []

    class _MockSpan:
        def set_attribute(self, k, v):
            pass

        def record_exception(self, exc):
            recorded_exceptions.append(exc)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    async def mock_run_bot_raises(websocket, call_sid, caller_id, config):
        raise RuntimeError("pipeline failure")

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = _MockSpan()

    with patch.object(bot_module, "run_bot", mock_run_bot_raises), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"), \
         patch("server.otel_trace") as mock_otel_trace:
        mock_otel_trace.get_tracer.return_value = mock_tracer
        from server import create_app
        app = create_app(_MINIMAL_CONFIG)
        tc = TestClient(app)
        tc.post("/answer", data={"CallUUID": "call-err", "From": "+91"})
        with tc.websocket_connect("/ws/call-err"):
            pass

    assert len(recorded_exceptions) == 1
    assert isinstance(recorded_exceptions[0], RuntimeError)


# ---------------------------------------------------------------------------
# /recording-ready webhook future registry tests
# ---------------------------------------------------------------------------

def test_recording_ready_resolves_registered_future(client, app):
    """POST /recording-ready must resolve the matching future in app.state.recording_url_registry."""
    import asyncio
    loop = asyncio.new_event_loop()
    fut: asyncio.Future = loop.create_future()
    app.state.recording_url_registry["CA9"] = fut
    response = client.post(
        "/recording-ready",
        json={"callSid": "CA9", "recordingUrl": "https://x/y.mp3"},
    )
    assert response.status_code == 200
    assert fut.done()
    assert fut.result() == "https://x/y.mp3"
    loop.close()


def test_recording_ready_unknown_call_sid_still_200(client):
    """POST /recording-ready with an unknown callSid must return 200 without error."""
    response = client.post(
        "/recording-ready",
        json={"callSid": "UNKNOWN", "recordingUrl": "https://x/y.mp3"},
    )
    assert response.status_code == 200


def test_recording_ready_accepts_plivo_form_payload(client, app):
    """Vobiz/Plivo POSTs application/x-www-form-urlencoded with CallUUID + RecordUrl."""
    import asyncio
    loop = asyncio.new_event_loop()
    fut: asyncio.Future = loop.create_future()
    app.state.recording_url_registry["VOBIZ-CALL-1"] = fut
    response = client.post(
        "/recording-ready",
        data={
            "CallUUID": "VOBIZ-CALL-1",
            "RecordUrl": "https://cdn.vobiz/VOBIZ-CALL-1.mp3",
            "RecordingID": "rec-1",
            "RecordingDuration": "12",
        },
    )
    assert response.status_code == 200
    assert fut.done()
    assert fut.result() == "https://cdn.vobiz/VOBIZ-CALL-1.mp3"
    loop.close()


def test_recording_finished_accepts_plivo_form_payload(client):
    """The /recording-finished webhook must also accept Plivo-style form data."""
    response = client.post(
        "/recording-finished",
        data={"CallUUID": "VOBIZ-CALL-2", "RecordUrl": "https://x"},
    )
    assert response.status_code == 200
