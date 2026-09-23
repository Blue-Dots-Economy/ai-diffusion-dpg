"""Agent Core HTTP client — normal, edge and failure paths."""

from __future__ import annotations

import httpx
import pytest

from src.agent_core_client import AgentCoreClient, AgentCoreError

BASE = "http://agent-core-test:8000"


def _client(handler) -> AgentCoreClient:
    c = AgentCoreClient(BASE, timeout_s=5.0)
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return c


async def test_process_turn_returns_the_parsed_body():
    def handler(request):
        assert request.url.path == "/process_turn"
        return httpx.Response(200, json={"response_text": "hi", "session_id": "9199"})

    out = await _client(handler).process_turn({"session_id": "9199"})
    assert out["response_text"] == "hi"


async def test_process_turn_timeout_raises_typed_error():
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(AgentCoreError) as exc:
        await _client(handler).process_turn({})
    assert exc.value.kind == "timeout"


async def test_process_turn_connect_error_raises_typed_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AgentCoreError) as exc:
        await _client(handler).process_turn({})
    assert exc.value.kind == "connect"


async def test_process_turn_http_error_raises_typed_error():
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(AgentCoreError) as exc:
        await _client(handler).process_turn({})
    assert exc.value.kind == "http"


@pytest.mark.parametrize(
    "exc_type",
    [httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError],
    ids=["ReadError", "RemoteProtocolError", "WriteError"],
)
async def test_process_turn_other_transport_errors_map_to_connect(exc_type):
    """httpx's transport-error tree is wider than Timeout/ConnectError — a turn
    is 4-6s, so a mid-response RemoteProtocolError is a realistic failure, not
    an exotic one. These must not leak as raw httpx exceptions."""
    def handler(request):
        raise exc_type("boom", request=request)

    with pytest.raises(AgentCoreError) as exc:
        await _client(handler).process_turn({})
    assert exc.value.kind == "connect"


async def test_stream_turn_yields_each_event_in_order():
    body = (
        'data: {"type": "signal", "stage": "nlu", "status": "start"}\n\n'
        'data: {"type": "sentence", "text": "Hello.", "sentence_index": 0}\n\n'
        'data: {"type": "done", "session_ended": false, "error_type": null}\n\n'
    )

    def handler(request):
        assert request.url.path == "/stream_turn"
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    events = [e async for e in _client(handler).stream_turn({})]
    assert [e["type"] for e in events] == ["signal", "sentence", "done"]
    assert events[1]["text"] == "Hello."


async def test_stream_turn_skips_malformed_event_lines():
    """A non-JSON data line must not abort a live call."""
    body = (
        'data: not json at all\n\n'
        'data: {"type": "sentence", "text": "ok", "sentence_index": 0}\n\n'
    )

    def handler(request):
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    events = [e async for e in _client(handler).stream_turn({})]
    assert [e["type"] for e in events] == ["sentence"]


async def test_stream_turn_ignores_blank_and_comment_lines():
    body = (
        "\n"
        ": keep-alive\n"
        'data: {"type": "done"}\n\n'
    )

    def handler(request):
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    events = [e async for e in _client(handler).stream_turn({})]
    assert [e["type"] for e in events] == ["done"]


@pytest.mark.parametrize(
    "exc_type",
    [httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError],
    ids=["ReadError", "RemoteProtocolError", "WriteError"],
)
async def test_stream_turn_other_transport_errors_map_to_connect(exc_type):
    """stream_turn is an async generator, so the exception surfaces on first
    iteration rather than at call time."""
    def handler(request):
        raise exc_type("boom", request=request)

    with pytest.raises(AgentCoreError) as exc:
        async for _ in _client(handler).stream_turn({}):
            pass
    assert exc.value.kind == "connect"


async def test_cancel_turn_calls_the_documented_endpoint():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "cancelled"})

    await _client(handler).cancel_turn("919900112233")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/sessions/919900112233/active_turn"


async def test_cancel_turn_never_raises():
    """Cancel is best-effort cleanup on a connection that is already gone;
    a failure here must not mask the original disconnect."""
    def handler(request):
        raise httpx.ConnectError("gone", request=request)

    await _client(handler).cancel_turn("919900112233")  # must not raise
