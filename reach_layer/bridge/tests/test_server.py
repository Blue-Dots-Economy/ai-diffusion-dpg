"""The bridge HTTP surface — routes, error envelope, both stream modes."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.agent_core_client import AgentCoreError
from src.server import _stream, create_app

CONFIG = {
    "agent_core_url": "http://agent-core-test:8000",
    "channel": "bridge",
    "terminal_word": "Thank you",
    "timeout_s": 30.0,
}
PHONE = "919900112233"
MODEL = "gpt-4.1-mini-2025-04-14"


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


def test_non_streaming_response_carries_present_null_logprobs(client):
    """The OpenAI contract requires `logprobs` present-and-null on a choice,
    not omitted (see src/openai_models.py's module docstring). This checks
    the actual HTTP response body, not just the builder's return value —
    `ChatCompletion.model_validate()` alone would not catch the field being
    dropped, since it is Optional with its own default in the openai SDK's
    pydantic model.
    """
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.return_value = {"response_text": "Hello there.", "model_used": "",
                          "session_id": PHONE, "error_type": None}
        r = client.post("/v1/chat/completions", json=_body(stream=False))
    choice = r.json()["choices"][0]
    assert "logprobs" in choice
    assert choice["logprobs"] is None


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


def test_client_has_no_cancel_path():
    """Spec §6: Agent Core stops the turn when the upstream stream closes."""
    from src.agent_core_client import AgentCoreClient
    assert not hasattr(AgentCoreClient, "cancel_turn")


def test_unhandled_exception_returns_500_envelope_without_leaking_detail():
    """A bare exception must not fall through to Starlette's plain-text 500.

    Uses its own TestClient with ``raise_server_exceptions=False``: the
    default (used by the ``client`` fixture) re-raises the original
    exception into the test process for debugging, which would make this
    test pass even if the app-level handler were missing entirely. Turning
    that off is what actually exercises the handler's HTTP response.
    """
    local_client = TestClient(create_app(CONFIG), raise_server_exceptions=False)
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.side_effect = RuntimeError(f"boom near caller {PHONE}")
        r = local_client.post("/v1/chat/completions", json=_body(stream=False))
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    err = body["error"]
    assert err["type"] == "api_error"
    assert set(err) == {"message", "type", "param", "code"}
    # Neither the phone number nor the raw exception text may leak.
    assert PHONE not in r.text
    assert "boom" not in r.text


def test_streaming_without_a_done_event_closes_cleanly(client):
    """A stream that ends with no terminal `done` event must not truncate.

    Agent Core closing the SSE body cleanly with no DoneEvent is not an
    AgentCoreError, so the `except AgentCoreError` branch never runs — the
    only way to still close the stream properly is the post-loop fallback.
    """
    events = [
        {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
        # No terminal `done` event — the upstream SSE body just ends.
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.status_code == 200
    lines = [l for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    payloads = [json.loads(l[6:]) for l in lines[:-1]]
    for payload in payloads:
        ChatCompletionChunk.model_validate(payload)
    # A finish_reason chunk must still be present — the stream is closed,
    # not truncated.
    assert any(
        p["choices"] and p["choices"][0].get("finish_reason") == "stop"
        for p in payloads
    )


def test_streaming_error_path_respects_include_usage(client):
    """A mid-stream AgentCoreError must still honour stream_options.include_usage.

    Also guards against silently dropping the finish_reason chunk: the
    error path emits every chunk `StreamTranslator.finish()` returns, not
    just the last one, so a usage-requesting client gets both the
    finish_reason chunk and the trailing usage chunk.
    """

    def _fake_stream_then_fail(events):
        async def _gen(self, payload):
            for e in events:
                yield e
            raise AgentCoreError("down", "connect")
        return _gen

    with patch("src.server.AgentCoreClient.stream_turn",
               _fake_stream_then_fail(
                   [{"type": "sentence", "text": "hi", "sentence_index": 0}])):
        r = client.post(
            "/v1/chat/completions",
            json=_body(stream=True, stream_options={"include_usage": True}),
        )

    assert r.status_code == 200
    lines = [l for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    payloads = [json.loads(l[6:]) for l in lines[:-1]]
    for payload in payloads:
        ChatCompletionChunk.model_validate(payload)
    assert any(
        p["choices"] and p["choices"][0].get("finish_reason") == "stop"
        for p in payloads
    )
    usage_chunks = [p for p in payloads if p.get("choices") == [] and "usage" in p]
    assert len(usage_chunks) == 1


def test_agent_core_timeout_mid_stream_closes_cleanly(client):
    """A mid-stream AgentCoreError(kind='timeout') closes with chunks and [DONE]."""

    def _fake_stream_then_timeout(events):
        async def _gen(self, payload):
            for e in events:
                yield e
            raise AgentCoreError("Agent Core timed out", "timeout")
        return _gen

    with patch("src.server.AgentCoreClient.stream_turn",
               _fake_stream_then_timeout(
                   [{"type": "sentence", "text": "hi", "sentence_index": 0}])):
        r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.status_code == 200
    lines = [l for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    payloads = [json.loads(l[6:]) for l in lines[:-1]]
    assert any(
        p["choices"] and p["choices"][0].get("finish_reason") == "stop"
        for p in payloads
    )


# --- per-turn timing log ---------------------------------------------------

CALL_ID = "0b748be7-ee2d-4fb5-8267-33aade8b1bfc"


def _timing_record(caplog):
    records = [r for r in caplog.records if r.getMessage() == "bridge.turn_timing"]
    assert len(records) == 1
    return records[0]


def _timed_body(**over):
    return _body(stream=True, metadata={"caller_phone": PHONE, "call_id": CALL_ID}, **over)


def test_turn_timing_logs_a_completed_turn(client, caplog):
    events = [
        {"type": "signal", "stage": "nlu", "status": "start", "turn_id": "t-1"},
        {"type": "sentence", "text": "I found 2 jobs.", "sentence_index": 0, "turn_id": "t-1"},
        {"type": "sentence", "text": "Which one?", "sentence_index": 1, "turn_id": "t-1"},
        {"type": "done", "session_ended": False, "error_type": None, "turn_id": "t-1"},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.status == "success"
    assert rec.outcome == "completed"
    assert rec.call_id == CALL_ID
    assert rec.turn_id == "t-1"
    assert rec.first_reply_kind == "sentence"
    assert rec.sentences == 2
    assert 0 <= rec.first_reply_ms <= rec.first_sentence_ms <= rec.latency_ms


def test_turn_timing_never_carries_the_phone_number(client, caplog):
    events = [
        {"type": "sentence", "text": "Hello.", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert PHONE not in json.dumps({k: str(v) for k, v in vars(rec).items()})


def test_turn_timing_marks_a_status_line_sent_before_the_answer(caplog):
    app = create_app({**CONFIG, "tool_status_phrases": {"fetch_jobs": "Let me check."}})
    events = [
        {"type": "signal", "stage": "tool_start", "tools": ["fetch_jobs"]},
        {"type": "sentence", "text": "I found 2 jobs.", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        TestClient(app).post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.first_reply_kind == "tool_status"
    assert rec.sentences == 1
    assert rec.first_reply_ms <= rec.first_sentence_ms


def test_turn_timing_without_any_reply_has_no_reply_times(client, caplog):
    events = [{"type": "done", "session_ended": False, "error_type": None}]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.outcome == "completed"
    assert rec.sentences == 0
    for absent in ("first_reply_ms", "first_reply_kind", "first_sentence_ms"):
        assert not hasattr(rec, absent)


def test_turn_timing_without_a_call_id_omits_it(client, caplog):
    events = [
        {"type": "sentence", "text": "Hello.", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_body(stream=True))

    rec = _timing_record(caplog)
    assert rec.outcome == "completed"
    assert not hasattr(rec, "call_id")


def test_turn_timing_marks_a_done_event_carrying_an_error_as_failed(client, caplog):
    events = [{"type": "done", "session_ended": False, "error_type": "llm_error"}]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.status == "failure"
    assert rec.outcome == "failed"


def test_turn_timing_marks_an_agent_core_failure_as_failed(client, caplog):
    async def _gen(self, payload):
        yield {"type": "sentence", "text": "hi", "sentence_index": 0}
        raise AgentCoreError("down", "connect")

    with caplog.at_level("INFO"), patch("src.server.AgentCoreClient.stream_turn", _gen):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.outcome == "failed"
    assert rec.sentences == 1


def test_turn_timing_marks_a_stream_without_done_as_incomplete(client, caplog):
    events = [{"type": "sentence", "text": "Hello.", "sentence_index": 0}]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.status == "failure"
    assert rec.outcome == "ended_without_done"


class _SlowClient:
    """Agent Core stand-in whose stream sends one sentence, then stalls."""

    async def stream_turn(self, payload):
        yield {"type": "sentence", "text": "Hello.", "sentence_index": 0, "turn_id": "t-9"}
        await asyncio.sleep(60)


def _open_stream(client=None):
    return _stream(client or _SlowClient(), {}, MODEL, "", False,
                   call_id=CALL_ID, received=time.monotonic())


async def test_turn_timing_logs_a_stream_closed_by_the_client(caplog):
    gen = _open_stream()
    with caplog.at_level("INFO"):
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    rec = _timing_record(caplog)
    assert rec.status == "success"
    assert rec.outcome == "disconnected_after_reply"
    assert rec.turn_id == "t-9"
    assert rec.sentences == 1


async def test_turn_timing_logs_a_stream_cancelled_mid_turn(caplog):
    gen = _open_stream()

    async def _drain():
        async for _ in gen:
            pass

    with caplog.at_level("INFO"):
        task = asyncio.create_task(_drain())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    rec = _timing_record(caplog)
    assert rec.outcome == "disconnected_after_reply"


class _SilentClient:
    """Agent Core stand-in that sends nothing before stalling."""

    async def stream_turn(self, payload):
        await asyncio.sleep(60)
        yield {}


async def test_turn_timing_marks_a_drop_before_any_reply_as_a_failure(caplog):
    gen = _open_stream(_SilentClient())

    async def _drain():
        async for _ in gen:
            pass

    with caplog.at_level("INFO"):
        task = asyncio.create_task(_drain())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    rec = _timing_record(caplog)
    assert rec.status == "failure"
    assert rec.outcome == "disconnected_before_reply"
    assert not hasattr(rec, "first_reply_ms")


def test_turn_timing_ignores_an_empty_sentence(client, caplog):
    events = [
        {"type": "sentence", "text": "", "sentence_index": 0},
        {"type": "sentence", "text": "Hello.", "sentence_index": 1},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.sentences == 1
    assert rec.first_reply_kind == "sentence"


def test_turn_timing_counts_a_closing_word_spoken_alone(client, caplog):
    events = [{"type": "done", "session_ended": True, "error_type": None}]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        client.post("/v1/chat/completions", json=_timed_body())

    rec = _timing_record(caplog)
    assert rec.first_reply_kind == "closing"
    assert rec.sentences == 0
    assert not hasattr(rec, "first_sentence_ms")


def test_failure_lines_leave_the_time_to_the_timing_record(client, caplog):
    async def _gen(self, payload):
        yield {"type": "sentence", "text": "hi", "sentence_index": 0}
        raise AgentCoreError("down", "connect")

    with caplog.at_level("INFO"), patch("src.server.AgentCoreClient.stream_turn", _gen):
        client.post("/v1/chat/completions", json=_timed_body())

    failed = [r for r in caplog.records if r.getMessage() == "bridge.stream_failed"]
    assert len(failed) == 1
    assert not hasattr(failed[0], "latency_ms")
    assert hasattr(_timing_record(caplog), "latency_ms")


async def test_stream_dropped_while_sending_is_logged_before_the_response_returns(caplog):
    """A disconnect that lands during a write must still close the stream at once."""
    from src.server import _ClosingStreamingResponse

    written = asyncio.Event()
    sent_after_close = []

    async def receive():
        await written.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message.get("type") == "http.response.body" and message.get("body"):
            written.set()
            await asyncio.sleep(60)
        sent_after_close.append(message)

    response = _ClosingStreamingResponse(_open_stream(), media_type="text/event-stream")
    scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
    with caplog.at_level("INFO"):
        await asyncio.wait_for(response(scope, receive, send), timeout=5)
        rec = _timing_record(caplog)

    assert rec.outcome.startswith("disconnected")


def test_turn_timing_survives_a_null_sentence_text(client, caplog):
    events = [
        {"type": "sentence", "text": None, "sentence_index": 0},
        {"type": "sentence", "text": "Hello.", "sentence_index": 1},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with caplog.at_level("INFO"), \
            patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_timed_body())

    assert r.status_code == 200
    rec = _timing_record(caplog)
    assert rec.outcome == "completed"
    assert rec.sentences == 1


class _ToolThenStallClient:
    """Agent Core stand-in that starts a tool, then never answers."""

    async def stream_turn(self, payload):
        yield {"type": "signal", "stage": "tool_start", "tools": ["fetch_jobs"]}
        await asyncio.sleep(60)


async def test_turn_timing_a_drop_after_only_the_status_line_is_before_the_answer(caplog):
    gen = _stream(_ToolThenStallClient(), {}, MODEL, "", False, None, {"fetch_jobs": "One minute."},
                  call_id=CALL_ID, received=time.monotonic())
    with caplog.at_level("INFO"):
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    rec = _timing_record(caplog)
    assert rec.first_reply_kind == "tool_status"
    assert rec.outcome == "disconnected_before_reply"
    assert rec.status == "failure"


class _AnswerThenDoneClient:
    async def stream_turn(self, payload):
        yield {"type": "sentence", "text": "Hello.", "sentence_index": 0}
        yield {"type": "done", "session_ended": False, "error_type": None}


async def test_turn_timing_keeps_completed_when_the_drop_lands_while_closing(caplog):
    gen = _open_stream(_AnswerThenDoneClient())
    with caplog.at_level("INFO"):
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    assert _timing_record(caplog).outcome == "completed"
