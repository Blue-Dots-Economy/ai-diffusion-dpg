"""OpenAI object builders, validated against the vendor's own models.

These tests assert compliance with the published contract rather than with our
own expectations: every object is fed through the openai SDK's pydantic models,
so a drift in field names or required members fails here rather than at a
client.
"""

from __future__ import annotations

from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.openai_models import (
    ZERO_USAGE,
    build_chunk,
    build_completion,
    build_error,
    new_completion_id,
)

CID = "chatcmpl-0123456789abcdef01234567"
CREATED = 1790000000
MODEL = "gpt-4.1-mini-2025-04-14"


def test_new_completion_id_shape():
    cid = new_completion_id()
    assert cid.startswith("chatcmpl-")
    assert len(cid) == len("chatcmpl-") + 24
    assert new_completion_id() != new_completion_id()


def test_opening_chunk_validates():
    chunk = build_chunk(CID, CREATED, MODEL, delta={"role": "assistant", "content": ""})
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["index"] == 0
    assert chunk["choices"][0]["finish_reason"] is None


def test_content_chunk_validates():
    chunk = build_chunk(CID, CREATED, MODEL, delta={"content": "Hello there."})
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["choices"][0]["delta"]["content"] == "Hello there."


def test_terminal_chunk_carries_finish_reason():
    chunk = build_chunk(CID, CREATED, MODEL, delta={}, finish_reason="stop")
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["choices"][0]["finish_reason"] == "stop"


def test_usage_chunk_has_empty_choices():
    """The usage chunk carries no choices — clients skip it for content."""
    chunk = build_chunk(CID, CREATED, MODEL, usage=ZERO_USAGE, empty_choices=True)
    ChatCompletionChunk.model_validate(chunk)
    assert chunk["choices"] == []
    assert chunk["usage"]["total_tokens"] == 0


def test_all_chunks_share_id_created_model():
    a = build_chunk(CID, CREATED, MODEL, delta={"content": "one"})
    b = build_chunk(CID, CREATED, MODEL, delta={"content": "two"})
    for key in ("id", "created", "model"):
        assert a[key] == b[key]


def test_completion_validates_and_has_all_required_choice_members():
    obj = build_completion(CID, CREATED, MODEL, "Hello there.")
    ChatCompletion.model_validate(obj)
    assert obj["object"] == "chat.completion"
    choice = obj["choices"][0]
    for member in ("index", "message", "finish_reason", "logprobs"):
        assert member in choice
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Hello there."
    assert choice["logprobs"] is None


def test_error_envelope_always_carries_all_four_members():
    err = build_error("bad", "invalid_request_error", param="metadata.caller_phone")
    assert set(err["error"]) == {"message", "type", "param", "code"}
    assert err["error"]["param"] == "metadata.caller_phone"
    assert err["error"]["code"] is None


def test_error_envelope_nulls_absent_members():
    err = build_error("boom", "api_error")
    assert err["error"]["param"] is None
    assert err["error"]["code"] is None
