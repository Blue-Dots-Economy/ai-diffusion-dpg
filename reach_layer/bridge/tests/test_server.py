"""The bridge HTTP surface — routes, error envelope, both stream modes."""

from __future__ import annotations

import asyncio
import json
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

        gen = _stream(client, {"session_id": PHONE}, MODEL,
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

        gen = _stream(client, {"session_id": PHONE}, MODEL,
                      "Thank you", False, PHONE)

        chunks = [c async for c in gen]
        assert chunks[-1] == "data: [DONE]\n\n"

        await asyncio.sleep(0)
        client.cancel_turn.assert_not_awaited()

    asyncio.run(_run())


# --- fix round 1 (2026-09-22) -----------------------------------------------
#
# Review found the `finished` flag was set one yield too late: `yield
# SSE_DONE` followed by `finished = True`. An OpenAI client is free to close
# its connection the instant it reads "[DONE]", which races the still-
# suspended `yield SSE_DONE` — GeneratorExit can land at that yield before
# the following statement ever runs, so `finished` is still False and the
# `finally` fires a stray `cancel_turn` against a turn that already
# completed (which could land on the caller's *next* turn). The fix moves
# the flag write to the point where the turn is provably complete: as soon
# as the terminal `done` event is *read*, before any further chunks are
# emitted.


def test_no_cancel_fires_when_client_closes_right_after_reading_done():
    """A consumer that reads [DONE] and immediately closes must not cancel.

    This reproduces the exact race the fix addresses: pull events one at a
    time via `__anext__()` — never letting the generator run past the
    `yield SSE_DONE` statement on its own — then call `aclose()` the moment
    `[DONE]` is the value in hand, exactly as an SSE client disconnecting
    right on schedule would. `GeneratorExit` is thrown into that suspended
    `yield`, so any statement written *after* it (as in the pre-fix code)
    never executes. Only a flag set *before* the final yield survives this.
    """

    async def _run():
        events = [
            {"type": "sentence", "text": "Goodbye.", "sentence_index": 0},
            {"type": "done", "session_ended": False, "error_type": None},
        ]

        async def _fast_stream(payload):
            for e in events:
                yield e

        client = AsyncMock()
        client.stream_turn = _fast_stream
        client.cancel_turn = AsyncMock()

        gen = _stream(client, {"session_id": PHONE}, MODEL,
                      "Thank you", False, PHONE)

        chunk = None
        while chunk != "data: [DONE]\n\n":
            chunk = await gen.__anext__()
        # `gen` is now suspended exactly at `yield SSE_DONE`, having not yet
        # resumed past it. Closing here is what a real client's disconnect
        # looks like from the generator's point of view.
        await gen.aclose()

        await asyncio.sleep(0)
        client.cancel_turn.assert_not_awaited()

    asyncio.run(_run())


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


def test_streaming_without_a_done_event_closes_cleanly_and_cancels(client):
    """A stream that ends with no terminal `done` event must not truncate.

    Agent Core closing the SSE body cleanly with no DoneEvent is not an
    AgentCoreError, so the `except AgentCoreError` branch never runs — the
    only way to still close the stream properly is the post-loop fallback.
    Because no DoneEvent was ever seen, the turn did not genuinely finish,
    so the cancel must still fire.
    """
    events = [
        {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
        # No terminal `done` event — the upstream SSE body just ends.
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)), \
         patch("src.server.AgentCoreClient.cancel_turn",
               new_callable=AsyncMock) as cancel:
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
    cancel.assert_awaited_once_with(PHONE)


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


# --- fix round 2 (2026-09-22) -----------------------------------------------
#
# Review found `except AgentCoreError` set `finished = True`, reusing the
# comment from the `done` branch ("genuine and terminal here too"). That
# reasoning does not hold: a `done` event proves Agent Core finished the
# turn, but an AgentCoreError of kind `timeout` or `protocol` means only
# that *this bridge* gave up waiting — the turn can still be running
# upstream and can still commit a write (`save_profile`, `apply_job`)
# after this generator stops listening. Setting `finished = True` there
# suppressed the `finally` cancel for exactly the case it exists to cover.
# This test is deliberately mid-stream (not the very first event) so it
# also proves the cancel fires after partial output, not just on an
# immediate failure.


def test_agent_core_timeout_mid_stream_still_cancels(client):
    """A mid-stream AgentCoreError(kind='timeout') must still cancel the turn.

    Restoring the deleted `finished = True` line in the `except
    AgentCoreError` branch must make this test fail — that is what makes it
    discriminating rather than incidentally green.
    """

    def _fake_stream_then_timeout(events):
        async def _gen(self, payload):
            for e in events:
                yield e
            raise AgentCoreError("Agent Core timed out", "timeout")
        return _gen

    with patch("src.server.AgentCoreClient.stream_turn",
               _fake_stream_then_timeout(
                   [{"type": "sentence", "text": "hi", "sentence_index": 0}])), \
         patch("src.server.AgentCoreClient.cancel_turn",
               new_callable=AsyncMock) as cancel:
        r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.status_code == 200
    lines = [l for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    cancel.assert_awaited_once_with(PHONE)
