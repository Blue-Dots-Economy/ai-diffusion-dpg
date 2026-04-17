# Voice OTel Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `reach_layer/voice/` OTel instrumentation to parity with `reach_layer/web/` — emit `reach.inbound` spans, FastAPI auto-instrumentation, HTTPx client instrumentation, and exception recording.

**Architecture:** Add three instrumentation layers to `server.py`: (1) FastAPI auto-instrumentation wires all HTTP endpoints automatically, (2) global HTTPx instrumentation propagates W3C `traceparent` headers into Vobiz/campaign outbound calls, (3) a manual `reach.inbound` span on `websocket_endpoint()` carries call identity attributes and records exceptions. All guards follow the existing web module pattern — try/except so observability never blocks startup.

**Tech Stack:** `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `opentelemetry-api` (all already in `pyproject.toml`), `pytest`, `unittest.mock`

**GitHub issue:** #131
**Branch:** `feat/voice-otel-instrumentation`

---

## File Map

| File | Change |
|---|---|
| `reach_layer/voice/server.py` | Add `otel_trace` import; `FastAPIInstrumentor`; `HTTPXClientInstrumentor`; `reach.inbound` span in `websocket_endpoint` |
| `reach_layer/voice/tests/test_server.py` | Add tests: FastAPIInstrumentor called; span attributes; exception recorded |

---

### Task 1: Add `reach.inbound` span to `websocket_endpoint`

**Files:**
- Modify: `reach_layer/voice/server.py`
- Test: `reach_layer/voice/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `reach_layer/voice/tests/test_server.py`:

```python
def test_fastapi_instrumented_on_startup():
    """FastAPIInstrumentor.instrument_app must be called during create_app."""
    with patch("src.bot.run_bot", new_callable=AsyncMock), \
         patch("server.CampaignManager"), \
         patch("server.load_reach_config", return_value={
             "telephony_adapter": {
                 "public_url": "https://example.app",
                 "vobiz": {"auth_id": "MA1", "auth_token": "t",
                           "api_base": "https://api.vobiz.ai/api/v1", "from_number": "+91"},
                 "raya": {"api_key": "k", "stt_wss_url": "wss://...",
                          "tts_base_url": "https://...", "language": "hi",
                          "voice_id": "v1", "tts_speed": 1.0},
                 "agent_core": {"base_url": "http://agent_core:8000", "timeout_ms": 5000,
                                "fallback_phrase": "sorry", "greeting": "Hello!"},
             },
             "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
         }), \
         patch("server.init_otel"), \
         patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app") as mock_instrument:
        from server import create_app
        create_app()
        mock_instrument.assert_called_once()


def test_websocket_span_attributes_set():
    """websocket_endpoint must emit reach.inbound span with correct attributes."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1",
                     "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000,
                           "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317",
                                   "sample_rate": 1.0, "export_interval_ms": 5000}},
    }

    recorded_attrs = {}

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

    with patch.object(bot_module, "run_bot", mock_run_bot), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"), \
         patch("opentelemetry.trace.Tracer.start_as_current_span", return_value=_MockSpan()):
        from server import create_app
        app = create_app(config)
        client = TestClient(app)
        client.post("/answer", data={"CallUUID": "call-otel", "From": "+911234567890"})
        with client.websocket_connect("/ws/call-otel"):
            pass

    assert recorded_attrs.get("dpg.channel") == "voice"
    assert recorded_attrs.get("dpg.assembly_mode") == "session"
    assert "session_id" in recorded_attrs


def test_websocket_span_records_exception():
    """websocket_endpoint must call span.record_exception when run_bot raises."""
    import src.bot as bot_module
    from starlette.testclient import TestClient

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1",
                     "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000,
                           "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317",
                                   "sample_rate": 1.0, "export_interval_ms": 5000}},
    }

    recorded_exceptions = []

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

    with patch.object(bot_module, "run_bot", mock_run_bot_raises), \
         patch("server.CampaignManager"), \
         patch("server.init_otel"), \
         patch("opentelemetry.trace.Tracer.start_as_current_span", return_value=_MockSpan()):
        from server import create_app
        app = create_app(config)
        client = TestClient(app)
        client.post("/answer", data={"CallUUID": "call-err", "From": "+91"})
        with client.websocket_connect("/ws/call-err"):
            pass

    assert len(recorded_exceptions) == 1
    assert isinstance(recorded_exceptions[0], RuntimeError)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd reach_layer/voice && uv run pytest tests/test_server.py::test_fastapi_instrumented_on_startup tests/test_server.py::test_websocket_span_attributes_set tests/test_server.py::test_websocket_span_records_exception -v
```

Expected: FAIL (no instrumentation yet)

- [ ] **Step 3: Implement — add OTel import and instrumentation to `server.py`**

At the top of `reach_layer/voice/server.py`, after the existing imports block, add:

```python
from opentelemetry import trace as otel_trace
```

After `app = FastAPI(...)` (line 144 area) in `create_app()`, add:

```python
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass  # Observability must not prevent startup
```

After `init_otel("telephony_adapter", config)` (line 113 area) in `create_app()`, add:

```python
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass  # Observability must not prevent startup
```

Replace the body of `websocket_endpoint()` with:

```python
    @app.websocket("/ws/{call_sid}")
    async def websocket_endpoint(websocket: WebSocket, call_sid: str) -> None:
        """Bidirectional audio stream for an active call."""
        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.websocket_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await websocket.accept()
        caller_id = _caller_id_map.pop(call_sid, "")
        with otel_trace.get_tracer(__name__).start_as_current_span("reach.inbound") as span:
            span.set_attribute("session_id", call_sid)
            span.set_attribute("dpg.channel", "voice")
            span.set_attribute("dpg.assembly_mode", "session")
            try:
                await bot.run_bot(websocket, call_sid, caller_id, _config)
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "server.ws_error",
                    extra={
                        "operation": "server.websocket_endpoint",
                        "status": "failure",
                        "call_sid": call_sid,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd reach_layer/voice && uv run pytest tests/test_server.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Run full test suite with coverage**

```bash
cd reach_layer/voice && uv run pytest --cov=src --cov=server --cov-report=term-missing -v
```

Expected: ≥70% coverage, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/voice/server.py reach_layer/voice/tests/test_server.py
git commit -m "feat(reach-layer/voice): add OTel traces, FastAPI+HTTPx instrumentation (#131)"
```
