"""Agent Core events -> OpenAI responses (spec 8, 9).

Every emitted object is validated with the openai SDK's own models.
"""

from __future__ import annotations

import json

from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.translate import SSE_DONE, StreamTranslator, sse, to_completion

MODEL = "gpt-4.1-mini-2025-04-14"


def _done(**over):
    base = {
        "type": "done", "was_escalated": False, "was_tool_used": False,
        "model_used": "", "latency_ms": 1882, "turn_id": "t1",
        "turn_status": "completed", "session_ended": False,
        "error_type": None, "error_message": None,
    }
    base.update(over)
    return base


# --- streaming -------------------------------------------------------------

def test_opening_chunk_declares_the_assistant_role():
    t = StreamTranslator(MODEL)
    chunk = t.opening()
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["choices"][0]["delta"]["role"] == "assistant"


def test_sentence_becomes_a_content_delta():
    t = StreamTranslator(MODEL)
    chunk = t.sentence("Hello there.")
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["choices"][0]["delta"]["content"] == "Hello there."
    assert chunk["choices"][0]["finish_reason"] is None


def test_every_chunk_of_one_response_shares_id_and_created():
    t = StreamTranslator(MODEL)
    chunks = [t.opening(), t.sentence("a"), t.sentence("b")]
    assert len({c["id"] for c in chunks}) == 1
    assert len({c["created"] for c in chunks}) == 1


def test_two_translators_produce_different_ids():
    assert StreamTranslator(MODEL).opening()["id"] != StreamTranslator(MODEL).opening()["id"]


def test_model_is_echoed_from_the_request():
    """Agent Core returns model_used as an empty string, so we echo the
    client's requested model rather than propagating a blank."""
    t = StreamTranslator(MODEL)
    assert t.sentence("x")["model"] == MODEL


def test_finish_emits_terminal_chunk_then_done_sentinel():
    t = StreamTranslator(MODEL)
    out = t.finish(_done(), include_usage=False)
    for chunk in out:
        ChatCompletionChunk.model_validate(chunk)
    assert out[-1]["choices"][0]["finish_reason"] == "stop"
    assert out[-1]["choices"][0]["delta"] == {}


def test_finish_adds_a_usage_chunk_when_requested():
    t = StreamTranslator(MODEL)
    out = t.finish(_done(), include_usage=True)
    usage_chunks = [c for c in out if c.get("usage") is not None]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["choices"] == []
    ChatCompletionChunk.model_validate(usage_chunks[0])


def test_session_ended_appends_the_terminal_word():
    """Spec 11.5 — the shim speaks the closing word; the client ends the call."""
    t = StreamTranslator(MODEL, terminal_word="Thank you")
    out = t.finish(_done(session_ended=True), include_usage=False)
    spoken = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in out if c["choices"]
    )
    assert "Thank you" in spoken


def test_terminal_word_is_not_appended_on_an_ordinary_turn():
    t = StreamTranslator(MODEL, terminal_word="Thank you")
    out = t.finish(_done(session_ended=False), include_usage=False)
    spoken = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in out if c["choices"]
    )
    assert "Thank you" not in spoken


def test_empty_terminal_word_appends_nothing():
    t = StreamTranslator(MODEL, terminal_word="")
    out = t.finish(_done(session_ended=True), include_usage=False)
    assert all(c["choices"][0]["delta"] == {} for c in out if c["choices"])


def test_sse_framing_is_data_json_blank_line():
    frame = sse({"a": 1})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame[6:].strip()) == {"a": 1}


def test_done_sentinel_is_the_literal_openai_terminator():
    assert SSE_DONE == "data: [DONE]\n\n"


# --- non-streaming ---------------------------------------------------------

def test_completion_carries_the_response_text():
    obj = to_completion(
        {"response_text": "Hello there.", "model_used": "", "session_id": "9199"},
        MODEL,
    )
    ChatCompletion.model_validate(obj)
    assert obj["choices"][0]["message"]["content"] == "Hello there."
    assert obj["model"] == MODEL


def test_completion_does_not_leak_the_session_id():
    """session_id is the caller's phone number — PII, and not part of the
    contract."""
    obj = to_completion(
        {"response_text": "hi", "model_used": "", "session_id": "919900112233"},
        MODEL,
    )
    assert "919900112233" not in json.dumps(obj)
