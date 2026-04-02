"""
agent_core/tests/test_llm_wrapper.py

Unit tests for ClaudeLLMWrapper.
All Anthropic SDK calls are mocked — no real API calls are made.

Coverage:
- Normal: successful response with text content
- Normal: successful response with tool_use block
- Edge: empty tools list
- Failure: retry on RateLimitError, success on second attempt
- Failure: retry on APITimeoutError, fallback to secondary model
- Failure: non-retryable APIError returns error response immediately
- Failure: all retries and fallback exhausted returns error response
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

from src.llm_wrapper.claude_wrapper import ClaudeLLMWrapper
from src.models import LLMResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "primary_model": "claude-primary",
    "fallback_model": "claude-fallback",
    "timeout_ms": 5000,
    "retry_attempts": 2,
}

MESSAGES = [{"role": "user", "content": "Hello"}]
SYSTEM = "You are a helpful assistant."


def _mock_text_response(model: str = "claude-primary") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = "Hello back!"
    raw = MagicMock()
    raw.content = [block]
    raw.stop_reason = "end_turn"
    raw.usage.input_tokens = 10
    raw.usage.output_tokens = 5
    return raw


def _mock_tool_response() -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "get_data"
    block.id = "tu_123"
    block.input = {"query": "test"}
    raw = MagicMock()
    raw.content = [block]
    raw.stop_reason = "tool_use"
    raw.usage.input_tokens = 15
    raw.usage.output_tokens = 8
    return raw


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def test_init_raises_on_empty_config():
    with pytest.raises(ValueError, match="non-empty config"):
        ClaudeLLMWrapper({})


def test_init_raises_on_none_config():
    with pytest.raises((ValueError, TypeError)):
        ClaudeLLMWrapper(None)


def test_init_raises_on_empty_primary_model():
    config = VALID_CONFIG.copy()
    config["primary_model"] = ""
    with pytest.raises(ValueError, match="agent.primary_model is not set"):
        ClaudeLLMWrapper(config)


def test_init_raises_on_empty_fallback_model():
    config = VALID_CONFIG.copy()
    config["fallback_model"] = ""
    with pytest.raises(ValueError, match="agent.fallback_model is not set"):
        ClaudeLLMWrapper(config)


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_returns_text_response(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_text_response()

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert isinstance(response, LLMResponse)
    assert response.content == "Hello back!"
    assert response.stop_reason == "end_turn"
    assert response.tool_calls == []
    assert response.input_tokens == 10
    assert response.output_tokens == 5


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_returns_tool_use_response(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response()

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tools = [{"name": "get_data", "description": "Fetch data", "input_schema": {}}]
    response = wrapper.call(messages=MESSAGES, tools=tools, system=SYSTEM)

    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "get_data"
    assert response.tool_calls[0].tool_use_id == "tu_123"


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_without_tools_does_not_pass_tools_param(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_text_response()

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "tools" not in call_kwargs


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_raises_on_empty_messages(mock_anthropic_cls):
    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    with pytest.raises(ValueError, match="messages must not be empty"):
        wrapper.call(messages=[], tools=[], system=SYSTEM)


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_get_active_model_returns_primary_initially(mock_anthropic_cls):
    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    assert wrapper.get_active_model() == "claude-primary"


# ---------------------------------------------------------------------------
# Retry and fallback
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.claude_wrapper.time.sleep")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_retries_on_rate_limit_then_succeeds(mock_anthropic_cls, mock_sleep):
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = [
        anthropic_module.RateLimitError(
            message="rate limited", response=MagicMock(), body={}
        ),
        _mock_text_response(),
    ]

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "end_turn"
    assert mock_client.messages.create.call_count == 2


@patch("src.llm_wrapper.claude_wrapper.time.sleep")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_switches_to_fallback_after_primary_exhaustion(mock_anthropic_cls, mock_sleep):
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    # Primary fails all retries; fallback succeeds
    mock_client.messages.create.side_effect = [
        anthropic_module.APITimeoutError(request=MagicMock()),
        anthropic_module.APITimeoutError(request=MagicMock()),
        _mock_text_response("claude-fallback"),
    ]

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "end_turn"
    assert wrapper.get_active_model() == "claude-fallback"


@patch("src.llm_wrapper.claude_wrapper.time.sleep")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_returns_error_response_when_all_attempts_fail(mock_anthropic_cls, mock_sleep):
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = anthropic_module.APITimeoutError(
        request=MagicMock()
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert response.content is None


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_non_retryable_api_error_returns_error_immediately(mock_anthropic_cls):
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = anthropic_module.APIError(
        message="bad request", request=MagicMock(), body={}
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert mock_client.messages.create.call_count == 1


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_missing_api_key_returns_error_not_raises(mock_anthropic_cls):
    """TypeError from missing API key must return stop_reason=error, not crash the server."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = TypeError(
        "Could not resolve authentication method. Expected either api_key or auth_token"
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert response.content is None
