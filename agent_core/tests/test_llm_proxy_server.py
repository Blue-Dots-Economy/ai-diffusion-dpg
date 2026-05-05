"""
agent_core/tests/test_llm_proxy_server.py

Unit tests for llm_proxy_server.py (FastAPI LLM proxy).
All chat-provider calls are mocked — no real API calls are made.

Coverage:
Normal execution:
  - POST /internal/llm/call with text response returns ChatResponse
  - POST /internal/llm/call with tool_use response returns content with tool_use blocks
  - POST /internal/llm/call passes system prompt through
  - POST /internal/llm/call passes tools through
  - GET /health returns status=ok and active_model

Edge cases:
  - POST /internal/llm/call with empty messages returns HTTP 422
  - create_app(None) raises ValueError

Failure scenarios:
  - chat_provider returns stop_reason=error → HTTP 200 with error in body
  - Malformed JSON body returns HTTP 422
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import (
    ChatResponse,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from src.servers.llm_proxy_server import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider_mock(response: ChatResponse) -> ChatProviderBase:
    """Return a minimal ChatProviderBase mock that returns the given response."""
    mock = MagicMock(spec=ChatProviderBase)
    mock.call.return_value = response
    mock.get_active_model.return_value = "claude-test-model"
    return mock


def _text_response(
    text: str = "Hello back!",
    model: str = "claude-test-model",
) -> ChatResponse:
    return ChatResponse(
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
        model_used=model,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _tool_response() -> ChatResponse:
    return ChatResponse(
        content=[
            ToolUseBlock(
                tool_use_id="tu_abc123",
                tool_name="search_records",
                input={"query": "electrician Hubli"},
            ),
        ],
        stop_reason="tool_use",
        model_used="claude-test-model",
        usage=TokenUsage(input_tokens=20, output_tokens=10),
    )


def _error_response() -> ChatResponse:
    return ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-test-model",
        usage=TokenUsage(),
    )


# A minimal ChatRequest body: one user message with one text block.
VALID_BODY: dict = {
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "kaam chahiye"}]}
    ]
}


# ---------------------------------------------------------------------------
# create_app factory
# ---------------------------------------------------------------------------

def test_create_app_raises_on_none_chat_provider():
    with pytest.raises(ValueError, match="chat_provider must not be None"):
        create_app(None)


def test_create_app_returns_fastapi_app():
    from fastapi import FastAPI
    app = create_app(_make_provider_mock(_text_response()))
    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# Normal execution — POST /internal/llm/call
# ---------------------------------------------------------------------------

def test_llm_call_returns_text_response():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json=VALID_BODY)

    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_reason"] == "end_turn"
    assert body["model_used"] == "claude-test-model"
    assert body["content"][0]["type"] == "text"
    assert body["content"][0]["text"] == "Hello back!"
    assert body["usage"]["input_tokens"] == 10
    assert body["usage"]["output_tokens"] == 5


def test_llm_call_returns_tool_use_response():
    provider = _make_provider_mock(_tool_response())
    client = TestClient(create_app(provider))

    body_with_tool = {
        **VALID_BODY,
        "tools": [
            {"name": "search_records", "description": "Search", "input_schema": {}},
        ],
    }
    resp = client.post("/internal/llm/call", json=body_with_tool)

    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"][0]["type"] == "tool_use"
    assert body["content"][0]["tool_name"] == "search_records"
    assert body["content"][0]["tool_use_id"] == "tu_abc123"
    assert body["content"][0]["input"] == {"query": "electrician Hubli"}


def test_llm_call_passes_system_prompt_through():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    body = {
        **VALID_BODY,
        "system": {
            "blocks": [{"type": "text", "text": "You are a domain agent."}],
        },
    }
    client.post("/internal/llm/call", json=body)

    sent_request = provider.call.call_args.args[0]
    assert sent_request.system is not None
    assert sent_request.system.blocks[0].text == "You are a domain agent."


def test_llm_call_passes_tools_through():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    tools = [
        {"name": "get_scheme", "description": "Fetch scheme", "input_schema": {}},
    ]
    client.post("/internal/llm/call", json={**VALID_BODY, "tools": tools})

    sent_request = provider.call.call_args.args[0]
    assert len(sent_request.tools) == 1
    assert sent_request.tools[0].name == "get_scheme"


# ---------------------------------------------------------------------------
# Normal execution — GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok_and_active_model():
    provider = _make_provider_mock(_text_response())
    provider.get_active_model.return_value = "claude-sonnet-4-6"
    client = TestClient(create_app(provider))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_llm_call_returns_422_on_empty_messages():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json={"messages": []})

    assert resp.status_code == 422


def test_llm_call_empty_messages_does_not_reach_provider():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    client.post("/internal/llm/call", json={"messages": []})

    provider.call.assert_not_called()


def test_llm_call_defaults_tools_to_empty_list():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    client.post("/internal/llm/call", json=VALID_BODY)

    sent_request = provider.call.call_args.args[0]
    assert sent_request.tools == []


def test_llm_call_defaults_system_to_none():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    client.post("/internal/llm/call", json=VALID_BODY)

    sent_request = provider.call.call_args.args[0]
    assert sent_request.system is None


def test_llm_call_multiple_tool_calls_in_response():
    multi_tool_response = ChatResponse(
        content=[
            ToolUseBlock(tool_use_id="tu_1", tool_name="tool_a", input={"x": 1}),
            ToolUseBlock(tool_use_id="tu_2", tool_name="tool_b", input={"y": 2}),
        ],
        stop_reason="tool_use",
        model_used="claude-test-model",
        usage=TokenUsage(input_tokens=30, output_tokens=15),
    )
    provider = _make_provider_mock(multi_tool_response)
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json=VALID_BODY)

    assert resp.status_code == 200
    body = resp.json()
    assert sum(1 for b in body["content"] if b["type"] == "tool_use") == 2


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------

def test_provider_error_returns_http_200_not_500():
    """Provider failures are expressed in the body (stop_reason=error), not HTTP 500."""
    provider = _make_provider_mock(_error_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json=VALID_BODY)

    assert resp.status_code == 200
    assert resp.json()["stop_reason"] == "error"


def test_provider_error_response_has_empty_content():
    provider = _make_provider_mock(_error_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json=VALID_BODY)

    assert resp.json()["content"] == []


def test_provider_error_response_has_none_token_fields():
    provider = _make_provider_mock(_error_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json=VALID_BODY)

    body = resp.json()
    # TokenUsage defaults to all None when the provider didn't report
    assert body["usage"]["input_tokens"] is None
    assert body["usage"]["output_tokens"] is None


def test_llm_call_invalid_json_body_returns_422():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    resp = client.post(
        "/internal/llm/call",
        content=b"not-valid-json",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_llm_call_missing_messages_field_returns_422():
    provider = _make_provider_mock(_text_response())
    client = TestClient(create_app(provider))

    resp = client.post("/internal/llm/call", json={"tools": []})

    assert resp.status_code == 422
