"""The bridge HTTP surface — routes, error envelope, both stream modes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.agent_core_client import AgentCoreError
from src.server import create_app

CONFIG = {
    "agent_core_url": "http://agent-core-test:8000",
    "channel": "bridge",
    "terminal_word": "Thank you",
    "timeout_s": 30.0,
}
PHONE = "919900112233"


@pytest.fixture
def client():
    return TestClient(create_app(CONFIG))


def _body(**over):
    base = {
        "model": "gpt-4.1-mini-2025-04-14",
        "metadata": {"caller_phone": PHONE},
        "messages": [{"role": "user", "content": "hello"}],
    }
    base.update(over)
    return base


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# --- non-streaming ---------------------------------------------------------

def test_non_streaming_returns_a_valid_completion(client):
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.return_value = {"response_text": "Hello there.", "model_used": "",
                          "session_id": PHONE, "error_type": None}
        r = client.post("/v1/chat/completions", json=_body(stream=False))
    assert r.status_code == 200
    ChatCompletion.model_validate(r.json())
    assert r.json()["choices"][0]["message"]["content"] == "Hello there."


def test_absent_stream_field_defaults_to_non_streaming(client):
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.return_value = {"response_text": "hi", "model_used": "", "error_type": None}
        r = client.post("/v1/chat/completions", json=_body())
    assert r.json()["object"] == "chat.completion"


# --- streaming -------------------------------------------------------------

def _fake_stream(events):
    async def _gen(self, payload):
        for e in events:
            yield e
    return _gen


def test_streaming_emits_valid_chunks_and_a_done_sentinel(client):
    events = [
        {"type": "signal", "stage": "nlu", "status": "start"},
        {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = [l for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    for line in lines[:-1]:
        ChatCompletionChunk.model_validate(json.loads(line[6:]))


def test_streaming_drops_signal_events(client):
    events = [
        {"type": "signal", "stage": "nlu", "status": "start"},
        {"type": "sentence", "text": "hi", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))
    assert "nlu" not in r.text


def test_streaming_reassembles_to_the_full_reply(client):
    events = [
        {"type": "sentence", "text": "I found 2 jobs.", "sentence_index": 0},
        {"type": "sentence", "text": " Which one?", "sentence_index": 1},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))
    text = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content", "")
        for l in r.text.split("\n\n")
        if l.startswith("data: ") and l != "data: [DONE]"
        and json.loads(l[6:])["choices"]
    )
    assert text == "I found 2 jobs. Which one?"


def test_streaming_speaks_the_terminal_word_on_session_end(client):
    events = [
        {"type": "sentence", "text": "Goodbye.", "sentence_index": 0},
        {"type": "done", "session_ended": True, "error_type": None},
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))
    assert "Thank you" in r.text


# --- errors (spec section 12) ---------------------------------------------

@pytest.mark.parametrize("body,param", [
    ({"model": "m", "metadata": {"caller_phone": PHONE}}, "messages"),
    ({"metadata": {"caller_phone": PHONE},
      "messages": [{"role": "user", "content": "x"}]}, "model"),
    ({"model": "m", "messages": [{"role": "user", "content": "x"}]},
     "metadata.caller_phone"),
])
def test_malformed_request_returns_400_with_param(client, body, param):
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["param"] == param
    assert set(err) == {"message", "type", "param", "code"}


def test_n_greater_than_one_returns_400(client):
    r = client.post("/v1/chat/completions", json=_body(n=3))
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "n"


@pytest.mark.parametrize("kind,status", [
    ("timeout", 502), ("connect", 502), ("http", 502), ("protocol", 502),
])
def test_agent_core_failure_returns_502(client, kind, status):
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.side_effect = AgentCoreError("down", kind)
        r = client.post("/v1/chat/completions", json=_body(stream=False))
    assert r.status_code == status
    assert r.json()["error"]["type"] == "api_error"


def test_turn_level_error_returns_502(client):
    """Agent Core signals turn failure with HTTP 200 and error_type set."""
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.return_value = {"response_text": "", "error_type": "internal_server_error",
                          "error_message": "boom"}
        r = client.post("/v1/chat/completions", json=_body(stream=False))
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "api_error"


def test_error_response_never_leaks_the_phone_number(client):
    r = client.post("/v1/chat/completions", json=_body(n=3))
    assert PHONE not in r.text


# --- disconnect handling (corrected — see task-7-report.md) ----------------
#
# The brief's `_stream` ends with `except Exception: await client.cancel_turn(...)`.
# That is unreachable dead code: Starlette closes the response generator on
# client disconnect by raising GeneratorExit at the suspended yield, and
# GeneratorExit (like asyncio.CancelledError) derives from BaseException, not
# Exception, so `except Exception` never catches it. Worse, awaiting inside a
# GeneratorExit handler raises `RuntimeError: async generator ignored
# GeneratorExit`. This test proves the corrected implementation — a
# try/finally with a fire-and-forget `asyncio.create_task` — actually fires
# the cancel when the client walks away mid-stream.

import asyncio

from src.server import _stream


def test_cancel_turn_fires_on_early_generator_close():
    """Closing the stream generator early must schedule a real turn cancel."""

    async def _run():
        events = [
            {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
            {"type": "sentence", "text": " More.", "sentence_index": 1},
            {"type": "done", "session_ended": False, "error_type": None},
        ]

        async def _slow_stream(payload):
            for e in events:
                yield e

        client = AsyncMock()
        client.stream_turn = _slow_stream
        client.cancel_turn = AsyncMock()

        gen = _stream(client, {"session_id": PHONE}, "gpt-4.1-mini-2025-04-14",
                      "Thank you", False, PHONE)

        # Drive it far enough to get at least one chunk out, then abandon it —
        # this is what Starlette does when the HTTP client disconnects.
        await gen.__anext__()
        await gen.aclose()

        # The cancel is scheduled via asyncio.create_task, not awaited, so the
        # event loop needs one tick to actually run it before we can assert.
        await asyncio.sleep(0)

        client.cancel_turn.assert_awaited_once_with(PHONE)

    asyncio.run(_run())


def test_cancel_turn_not_called_when_stream_finishes_normally():
    """A stream that runs to [DONE] must not cancel its own completed turn."""

    async def _run():
        events = [
            {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
            {"type": "done", "session_ended": False, "error_type": None},
        ]

        async def _fast_stream(payload):
            for e in events:
                yield e

        client = AsyncMock()
        client.stream_turn = _fast_stream
        client.cancel_turn = AsyncMock()

        gen = _stream(client, {"session_id": PHONE}, "gpt-4.1-mini-2025-04-14",
                      "Thank you", False, PHONE)

        chunks = [c async for c in gen]
        assert chunks[-1] == "data: [DONE]\n\n"

        await asyncio.sleep(0)
        client.cancel_turn.assert_not_awaited()

    asyncio.run(_run())
