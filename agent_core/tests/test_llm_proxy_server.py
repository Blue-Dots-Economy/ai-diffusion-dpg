"""
agent_core/tests/test_llm_proxy_server.py

Unit tests for llm_proxy_server.py (FastAPI LLM proxy).
All LLM wrapper calls are mocked — no real API calls are made.

Coverage:
Normal execution:
  - POST /internal/llm/call with text response returns correct LLMCallResponse
  - POST /internal/llm/call with tool_use response returns tool_calls list
  - POST /internal/llm/call passes model_override through to llm wrapper
  - POST /internal/llm/call passes system prompt through to llm wrapper
  - GET /health returns status=ok and active_model

Edge cases:
  - POST /internal/llm/call with empty messages returns HTTP 422
  - POST /internal/llm/call omitting tools defaults to empty list
  - POST /internal/llm/call omitting system defaults to empty string
  - create_app(None) raises ValueError

Failure scenarios:
  - LLM wrapper returns stop_reason=error → HTTP 200 with error in body, never HTTP 500
  - LLM wrapper returns content=None (tool_use turn) → HTTP 200 with null content
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.servers.llm_proxy_server import create_app
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_mock(response: LLMResponse) -> LLMWrapperBase:
    """Return a minimal LLMWrapperBase mock that returns the given response."""
    mock = MagicMock(spec=LLMWrapperBase)
    mock.call.return_value = response
    mock.get_active_model.return_value = "claude-test-model"
    return mock


def _text_response(
    content: str = "Hello back!",
    model: str = "claude-test-model",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        model_used=model,
        input_tokens=10,
        output_tokens=5,
    )


def _tool_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                tool_name="search_records",
                tool_use_id="tu_abc123",
                input_params={"query": "electrician Hubli"},
            )
        ],
        stop_reason="tool_use",
        model_used="claude-test-model",
        input_tokens=20,
        output_tokens=10,
    )


def _error_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[],
        stop_reason="error",
        model_used="claude-test-model",
        input_tokens=0,
        output_tokens=0,
    )


VALID_MESSAGES = [{"role": "user", "content": "kaam chahiye"}]


# ---------------------------------------------------------------------------
# create_app factory
# ---------------------------------------------------------------------------

def test_create_app_raises_on_none_llm():
    with pytest.raises(ValueError, match="llm must not be None"):
        create_app(None)


def test_create_app_returns_fastapi_app():
    from fastapi import FastAPI
    mock_llm = _make_llm_mock(_text_response())
    app = create_app(mock_llm)
    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# Normal execution — POST /internal/llm/call
# ---------------------------------------------------------------------------

def test_llm_call_returns_text_response():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post(
        "/internal/llm/call",
        json={"messages": VALID_MESSAGES},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Hello back!"
    assert body["stop_reason"] == "end_turn"
    assert body["tool_calls"] == []
    assert body["model_used"] == "claude-test-model"
    assert body["input_tokens"] == 10
    assert body["output_tokens"] == 5


def test_llm_call_returns_tool_use_response():
    mock_llm = _make_llm_mock(_tool_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post(
        "/internal/llm/call",
        json={
            "messages": VALID_MESSAGES,
            "tools": [{"name": "search_records", "description": "Search", "input_schema": {}}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"] is None
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["tool_name"] == "search_records"
    assert body["tool_calls"][0]["tool_use_id"] == "tu_abc123"
    assert body["tool_calls"][0]["input_params"] == {"query": "electrician Hubli"}


def test_llm_call_passes_model_override_to_wrapper():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post(
        "/internal/llm/call",
        json={
            "messages": VALID_MESSAGES,
            "model_override": "claude-haiku-4-5-20251001",
        },
    )

    mock_llm.call.assert_called_once()
    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("model_override") == "claude-haiku-4-5-20251001"


def test_llm_call_passes_system_prompt_to_wrapper():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post(
        "/internal/llm/call",
        json={
            "messages": VALID_MESSAGES,
            "system": "You are a domain agent.",
        },
    )

    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("system") == "You are a domain agent."


def test_llm_call_passes_tools_to_wrapper():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    tools = [{"name": "get_scheme", "description": "Fetch scheme", "input_schema": {}}]
    client.post(
        "/internal/llm/call",
        json={"messages": VALID_MESSAGES, "tools": tools},
    )

    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("tools") == tools


# ---------------------------------------------------------------------------
# Normal execution — GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok_and_active_model():
    mock_llm = _make_llm_mock(_text_response())
    mock_llm.get_active_model.return_value = "claude-sonnet-4-6"
    client = TestClient(create_app(mock_llm))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_model"] == "claude-sonnet-4-6"


def test_health_reflects_fallback_model_after_switch():
    mock_llm = _make_llm_mock(_text_response())
    mock_llm.get_active_model.return_value = "claude-haiku-4-5-20251001"
    client = TestClient(create_app(mock_llm))

    resp = client.get("/health")

    assert resp.json()["active_model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_llm_call_returns_422_on_empty_messages():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post(
        "/internal/llm/call",
        json={"messages": []},
    )

    assert resp.status_code == 422


def test_llm_call_empty_messages_does_not_reach_llm_wrapper():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post("/internal/llm/call", json={"messages": []})

    mock_llm.call.assert_not_called()


def test_llm_call_defaults_tools_to_empty_list():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("tools") == []


def test_llm_call_defaults_system_to_empty_string():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("system") == ""


def test_llm_call_defaults_model_override_to_none():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    _, kwargs = mock_llm.call.call_args
    assert kwargs.get("model_override") is None


def test_llm_call_multiple_tool_calls_in_response():
    multi_tool_response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(tool_name="tool_a", tool_use_id="tu_1", input_params={"x": 1}),
            ToolCall(tool_name="tool_b", tool_use_id="tu_2", input_params={"y": 2}),
        ],
        stop_reason="tool_use",
        model_used="claude-test-model",
        input_tokens=30,
        output_tokens=15,
    )
    mock_llm = _make_llm_mock(multi_tool_response)
    client = TestClient(create_app(mock_llm))

    resp = client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    assert resp.status_code == 200
    assert len(resp.json()["tool_calls"]) == 2


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------

def test_llm_error_returns_http_200_not_500():
    """LLM failures are expressed in the body (stop_reason=error), never as HTTP 500."""
    mock_llm = _make_llm_mock(_error_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    assert resp.status_code == 200
    assert resp.json()["stop_reason"] == "error"


def test_llm_error_response_has_null_content():
    mock_llm = _make_llm_mock(_error_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    assert resp.json()["content"] is None


def test_llm_error_response_has_zero_tokens():
    mock_llm = _make_llm_mock(_error_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post("/internal/llm/call", json={"messages": VALID_MESSAGES})

    body = resp.json()
    assert body["input_tokens"] == 0
    assert body["output_tokens"] == 0


def test_llm_call_invalid_json_body_returns_422():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post(
        "/internal/llm/call",
        content=b"not-valid-json",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_llm_call_missing_messages_field_returns_422():
    mock_llm = _make_llm_mock(_text_response())
    client = TestClient(create_app(mock_llm))

    resp = client.post(
        "/internal/llm/call",
        json={"tools": [], "system": ""},
    )

    assert resp.status_code == 422
