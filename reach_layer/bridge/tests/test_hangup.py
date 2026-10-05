"""Hanging up the call when Agent Core ends the session.

The client owns the call, so the shim cannot hang up itself. What it can do is
use the one out-of-band channel the OpenAI contract has — a ``tool_calls``
response — to invoke the hangup tool the client offered in ``tools``. VoicERA
offers ``end_conversation`` when ``automatic_call_ending`` and
``graceful_llm_call_ending`` are enabled on the agent; on receiving the call it
pushes ``EndWorkerFrame`` and the line drops after the goodbye is spoken.

Before this, ``DoneEvent.session_ended`` had no consumer: the goodbye was
spoken and the line stayed open until the platform's max-duration timeout.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.server import create_app
from src.translate import (
    StreamTranslator,
    is_tool_result_followup,
    offered_hangup_tool,
)

MODEL = "gpt-4.1-mini-2025-04-14"
PHONE = "919900112233"
HANGUP = "end_conversation"
CONFIG = {
    "agent_core_url": "http://agent-core-test:8000",
    "channel": "bridge",
    "terminal_word": "Thank you",
    "hangup_tool_name": HANGUP,
}
TOOLS = [
    {"type": "function", "function": {
        "name": HANGUP, "description": "End the conversation.",
        "parameters": {"type": "object", "properties": {}}}},
]


def _body(**over):
    base = {
        "model": MODEL,
        "metadata": {"caller_phone": PHONE},
        "messages": [{"role": "user", "content": "ठीक है धन्यवाद"}],
    }
    base.update(over)
    return base


def _fake_stream(events):
    async def _gen(self, payload):
        for e in events:
            yield e
    return _gen


def _chunks(text: str) -> list[dict]:
    return [json.loads(l[6:]) for l in text.split("\n\n")
            if l.startswith("data: ") and l != "data: [DONE]"]


GOODBYE = [
    {"type": "sentence", "text": "आपका दिन शुभ हो।", "sentence_index": 0},
    {"type": "done", "session_ended": True, "error_type": None},
]


# --- which tool, if any, the client offered --------------------------------

def test_offered_hangup_tool_found_in_tools():
    assert offered_hangup_tool(_body(tools=TOOLS), HANGUP) == HANGUP


def test_offered_hangup_tool_absent_when_no_tools():
    assert offered_hangup_tool(_body(), HANGUP) is None


def test_offered_hangup_tool_absent_when_only_other_tools_offered():
    other = [{"type": "function", "function": {"name": "switch_language"}}]
    assert offered_hangup_tool(_body(tools=other), HANGUP) is None


def test_offered_hangup_tool_disabled_by_empty_name():
    assert offered_hangup_tool(_body(tools=TOOLS), "") is None


# --- translator -------------------------------------------------------------

def test_finish_invokes_the_hangup_tool_on_session_end():
    t = StreamTranslator(MODEL, "Thank you", hangup_tool=HANGUP)
    out = t.finish({"session_ended": True}, include_usage=False)
    for c in out:
        ChatCompletionChunk.model_validate(c)
    calls = [c["choices"][0]["delta"]["tool_calls"] for c in out
             if c["choices"][0]["delta"].get("tool_calls")]
    assert len(calls) == 1
    call = calls[0][0]
    assert call["index"] == 0
    assert call["type"] == "function"
    assert call["id"].startswith("call_")
    assert call["function"] == {"name": HANGUP, "arguments": "{}"}
    assert out[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_finish_speaks_the_terminal_word_before_hanging_up():
    t = StreamTranslator(MODEL, "Thank you", hangup_tool=HANGUP)
    out = t.finish({"session_ended": True}, include_usage=False)
    kinds = ["content" if c["choices"][0]["delta"].get("content")
             else "tool" if c["choices"][0]["delta"].get("tool_calls")
             else "other" for c in out]
    assert kinds.index("content") < kinds.index("tool")


def test_finish_without_session_end_never_hangs_up():
    t = StreamTranslator(MODEL, "Thank you", hangup_tool=HANGUP)
    out = t.finish({"session_ended": False}, include_usage=False)
    assert not any(c["choices"][0]["delta"].get("tool_calls") for c in out)
    assert out[-1]["choices"][0]["finish_reason"] == "stop"


def test_finish_on_session_end_without_offered_tool_just_stops():
    """A client that did not offer the tool must not receive a call to it."""
    t = StreamTranslator(MODEL, "Thank you")
    out = t.finish({"session_ended": True}, include_usage=False)
    assert not any(c["choices"][0]["delta"].get("tool_calls") for c in out)
    assert out[-1]["choices"][0]["finish_reason"] == "stop"


# --- the follow-up request carrying the tool result ------------------------

def test_tool_result_followup_detected():
    body = _body(messages=[
        {"role": "user", "content": "ठीक है धन्यवाद"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": HANGUP, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "ended"}'},
    ])
    assert is_tool_result_followup(body) is True


def test_ordinary_user_turn_is_not_a_followup():
    assert is_tool_result_followup(_body()) is False


# --- server -----------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(create_app(CONFIG))


def test_stream_hangs_up_when_session_ends_and_tool_offered(client):
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(GOODBYE)):
        r = client.post("/v1/chat/completions", json=_body(stream=True, tools=TOOLS))
    chunks = _chunks(r.text)
    for c in chunks:
        ChatCompletionChunk.model_validate(c)
    names = [tc["function"]["name"] for c in chunks
             for tc in (c["choices"][0]["delta"].get("tool_calls") or [])]
    assert names == [HANGUP]
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert r.text.rstrip().endswith("data: [DONE]")


def test_stream_does_not_hang_up_when_tool_not_offered(client):
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(GOODBYE)):
        r = client.post("/v1/chat/completions", json=_body(stream=True))
    assert "tool_calls\": [" not in r.text
    assert _chunks(r.text)[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_does_not_hang_up_mid_conversation(client):
    events = [
        {"type": "sentence", "text": "कौन सा काम?", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        r = client.post("/v1/chat/completions", json=_body(stream=True, tools=TOOLS))
    assert "tool_calls\": [" not in r.text


_FOLLOWUP_MESSAGES = [
    {"role": "user", "content": "ठीक है धन्यवाद"},
    {"role": "assistant", "content": "आपका दिन शुभ हो।", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": HANGUP, "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "ended"}'},
]


def test_streamed_tool_result_followup_never_reaches_agent_core(client):
    """The client re-runs the model with the tool result. Forwarding it would
    replay the caller's goodbye as a fresh turn."""
    with patch("src.server.AgentCoreClient.stream_turn") as m:
        r = client.post("/v1/chat/completions",
                        json=_body(stream=True, tools=TOOLS, messages=_FOLLOWUP_MESSAGES))
    m.assert_not_called()
    assert r.status_code == 200
    chunks = _chunks(r.text)
    for c in chunks:
        ChatCompletionChunk.model_validate(c)
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks) == ""
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert r.text.rstrip().endswith("data: [DONE]")


def test_blocking_tool_result_followup_never_reaches_agent_core(client):
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        r = client.post("/v1/chat/completions",
                        json=_body(tools=TOOLS, messages=_FOLLOWUP_MESSAGES))
    m.assert_not_called()
    assert r.status_code == 200
    completion = ChatCompletion.model_validate(r.json())
    assert completion.choices[0].message.content == ""


# --- the official SDK accumulates the call ---------------------------------

def test_sdk_accumulates_the_hangup_call_from_the_stream():
    app_client = TestClient(create_app(CONFIG))
    transport = httpx.MockTransport(
        lambda request: app_client.request(
            request.method, str(request.url.path),
            content=request.content, headers=dict(request.headers)))
    sdk = OpenAI(api_key="unused", base_url="http://bridge/v1",
                 http_client=httpx.Client(transport=transport))
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(GOODBYE)):
        stream = sdk.chat.completions.create(
            model=MODEL, stream=True, tools=TOOLS,
            messages=[{"role": "user", "content": "ठीक है धन्यवाद"}],
            metadata={"caller_phone": PHONE},
        )
        chunks = list(stream)
    names = [tc.function.name for c in chunks if c.choices
             for tc in (c.choices[0].delta.tool_calls or [])]
    assert names == [HANGUP]
    assert chunks[-1].choices[0].finish_reason == "tool_calls"


# ---------------------------------------------------------------------------
# A session that ends with no hangup tool must not fail silently
# ---------------------------------------------------------------------------


def test_session_end_without_hangup_tool_warns(caplog):
    """The call stays open in this case; an operator needs a trace of why.

    Either the domain configured no hangup tool or the client did not offer
    the configured one. Both look identical from the caller's side — the bot
    says goodbye and the line never drops — and both were previously silent.
    """
    translator = StreamTranslator("blue-dots", hangup_tool=None)

    with caplog.at_level(logging.WARNING):
        chunks = translator.finish({"session_ended": True}, include_usage=False)

    assert any(r.message == "bridge.session_ended_without_hangup"
               for r in caplog.records)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_session_end_with_hangup_tool_does_not_warn(caplog):
    translator = StreamTranslator("blue-dots", hangup_tool="end_conversation")

    with caplog.at_level(logging.WARNING):
        chunks = translator.finish({"session_ended": True}, include_usage=False)

    assert not any(r.message == "bridge.session_ended_without_hangup"
                   for r in caplog.records)
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_unfinished_session_never_warns(caplog):
    """A normal turn ends with session_ended False and must stay quiet."""
    translator = StreamTranslator("blue-dots", hangup_tool=None)

    with caplog.at_level(logging.WARNING):
        translator.finish({"session_ended": False}, include_usage=False)

    assert not any(r.message == "bridge.session_ended_without_hangup"
                   for r in caplog.records)
