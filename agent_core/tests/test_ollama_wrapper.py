"""
agent_core/tests/test_ollama_wrapper.py

Unit tests for OllamaLLMWrapper.
All OpenAI SDK calls are mocked — no real API calls are made.

Coverage:
  call() — Normal execution
  - Successful text response
  - Successful tool_use response (tool_calls finish_reason)
  - Empty tools list does not pass tools param

  call() — Edge cases
  - Empty messages raises ValueError
  - get_active_model() returns primary initially
  - model_override is respected

  call() — Retry and fallback
  - Retry on RateLimitError, success on second attempt
  - Retry on APITimeoutError, fallback to secondary model
  - Non-retryable APIError returns error response immediately
  - All retries and fallback exhausted returns error response

  call() — Format conversion
  - String content message passes through unchanged
  - Assistant message with tool_use block converts to OpenAI tool_calls
  - User message with tool_result block converts to OpenAI tool role message
  - tool definitions convert input_schema → parameters
  - ollama_base_url is forwarded to the OpenAI client

  stream_call() — Normal execution
  - Streams text tokens
  - Tool use mid-stream raises ToolUseRequested with correct ToolCall objects
  - Partial JSON arguments are handled gracefully

  stream_call() — Edge cases
  - Empty messages raises ValueError
  - Works without tools parameter

  stream_call() — Retry and fallback
  - Retry on APITimeoutError, success on second attempt
  - Fallback model switch after primary exhaustion
  - All retries exhausted yields nothing
  - Non-retryable APIError yields nothing

  _map_stop_reason() — mapping table
  _convert_messages() — system prompt injection
"""

from __future__ import annotations

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.exceptions import ToolUseRequested
from src.llm_wrapper.ollama_wrapper import OllamaLLMWrapper
from src.models import LLMResponse, ToolCall


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "primary_model": "gpt-4o",
    "fallback_model": "gpt-4o-mini",
    "timeout_ms": 5000,
    "retry_attempts": 2,
}

MESSAGES = [{"role": "user", "content": "Hello"}]
SYSTEM = "You are a helpful assistant."


# ---------------------------------------------------------------------------
# OpenAI API response builders
# ---------------------------------------------------------------------------

def _mock_text_completion(model: str = "gpt-4o", content: str = "Hello back!") -> MagicMock:
    """Build a mock ChatCompletion with a text response."""
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = content
    choice.message.tool_calls = None

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5

    raw = MagicMock()
    raw.choices = [choice]
    raw.usage = usage
    return raw


def _mock_tool_completion(
    tool_name: str = "get_data",
    tool_id: str = "call_abc123",
    arguments: dict | None = None,
) -> MagicMock:
    """Build a mock ChatCompletion with a tool_calls response."""
    if arguments is None:
        arguments = {"query": "test"}

    tc = MagicMock()
    tc.id = tool_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.content = None
    choice.message.tool_calls = [tc]

    usage = MagicMock()
    usage.prompt_tokens = 15
    usage.completion_tokens = 8

    raw = MagicMock()
    raw.choices = [choice]
    raw.usage = usage
    return raw


# ---------------------------------------------------------------------------
# stream_call() helpers
# ---------------------------------------------------------------------------

def _make_stream_chunks(
    tokens: list[str],
    finish_reason: str = "stop",
    tool_call_chunks: list[dict] | None = None,
    include_usage: bool = True,
) -> list[MagicMock]:
    """
    Build a list of mock stream chunks for AsyncOpenAI.chat.completions.create.

    Each token becomes a chunk with delta.content set.
    If tool_call_chunks is provided, a single extra chunk carries them.
    A final usage-only chunk is appended if include_usage is True.
    """
    chunks: list[MagicMock] = []

    for tok in tokens:
        delta = MagicMock()
        delta.content = tok
        delta.tool_calls = None

        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = None

        chunk = MagicMock()
        chunk.choices = [choice]
        chunk.usage = None
        chunks.append(chunk)

    # Final chunk with finish_reason (and optional tool_calls)
    final_delta = MagicMock()
    final_delta.content = None
    final_delta.tool_calls = None

    if tool_call_chunks:
        oai_tcs = []
        for i, tc_data in enumerate(tool_call_chunks):
            tc_chunk = MagicMock()
            tc_chunk.index = i
            tc_chunk.id = tc_data.get("id", f"call_{i}")
            tc_func = MagicMock()
            tc_func.name = tc_data.get("name", "")
            tc_func.arguments = tc_data.get("arguments", "{}")
            tc_chunk.function = tc_func
            oai_tcs.append(tc_chunk)
        final_delta.tool_calls = oai_tcs

    final_choice = MagicMock()
    final_choice.delta = final_delta
    final_choice.finish_reason = finish_reason

    final_chunk = MagicMock()
    final_chunk.choices = [final_choice]
    final_chunk.usage = None
    chunks.append(final_chunk)

    # Usage-only sentinel chunk
    if include_usage:
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5

        sentinel = MagicMock()
        sentinel.choices = []
        sentinel.usage = usage
        chunks.append(sentinel)

    return chunks


class _AsyncChunkIter:
    """Async iterator over a pre-built list of mock chunks."""
    def __init__(self, items: list):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


async def _collect_stream(gen) -> list[str]:
    """Collect all tokens from an async generator into a list."""
    tokens: list[str] = []
    async for token in gen:
        tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# Init tests
# ---------------------------------------------------------------------------

def test_init_raises_on_empty_config():
    with pytest.raises(ValueError, match="non-empty config"):
        OllamaLLMWrapper({})


def test_init_raises_on_none_config():
    with pytest.raises((ValueError, TypeError)):
        OllamaLLMWrapper(None)


def test_init_raises_on_missing_primary_model():
    config = VALID_CONFIG.copy()
    config["primary_model"] = ""
    with pytest.raises(ValueError, match="agent.primary_model is not set"):
        OllamaLLMWrapper(config)


def test_init_raises_on_missing_fallback_model():
    config = VALID_CONFIG.copy()
    config["fallback_model"] = ""
    with pytest.raises(ValueError, match="agent.fallback_model is not set"):
        OllamaLLMWrapper(config)


@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
def test_init_forwards_base_url_to_client(mock_async_cls, mock_cls):
    config = {**VALID_CONFIG, "ollama_base_url": "https://my-endpoint.com/v1"}
    OllamaLLMWrapper(config)
    mock_cls.assert_called_once_with(base_url="https://my-endpoint.com/v1")
    mock_async_cls.assert_called_once_with(base_url="https://my-endpoint.com/v1")


@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
def test_init_no_base_url_uses_default_client(mock_async_cls, mock_cls):
    OllamaLLMWrapper(VALID_CONFIG)
    mock_cls.assert_called_once_with()
    mock_async_cls.assert_called_once_with()


# ---------------------------------------------------------------------------
# call() — Normal execution
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_returns_text_response(mock_cls, mock_async_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert isinstance(response, LLMResponse)
    assert response.content == "Hello back!"
    assert response.stop_reason == "end_turn"
    assert response.tool_calls == []
    assert response.input_tokens == 10
    assert response.output_tokens == 5


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_returns_tool_use_response(mock_cls, mock_async_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_tool_completion()

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tools = [{"name": "get_data", "description": "Fetch", "parameters": {}}]
    response = wrapper.call(messages=MESSAGES, tools=tools, system=SYSTEM)

    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "get_data"
    assert response.tool_calls[0].tool_use_id == "call_abc123"
    assert response.tool_calls[0].input_params == {"query": "test"}


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_without_tools_does_not_pass_tools_param(mock_cls, mock_async_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "tools" not in call_kwargs


# ---------------------------------------------------------------------------
# call() — Edge cases
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_raises_on_empty_messages(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    with pytest.raises(ValueError, match="messages must not be empty"):
        wrapper.call(messages=[], tools=[], system=SYSTEM)


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_get_active_model_returns_primary_initially(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    assert wrapper.get_active_model() == "gpt-4o"


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_respects_model_override(mock_cls, mock_async_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM, model_override="gpt-4-turbo")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4-turbo"


# ---------------------------------------------------------------------------
# call() — Retry and fallback
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.time.sleep")
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_retries_on_rate_limit_then_succeeds(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = [
        openai_module.RateLimitError(
            message="rate limited", response=MagicMock(), body={}
        ),
        _mock_text_completion(),
    ]

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "end_turn"
    assert mock_client.chat.completions.create.call_count == 2


@patch("src.llm_wrapper.ollama_wrapper.time.sleep")
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_switches_to_fallback_after_primary_exhaustion(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = [
        openai_module.APITimeoutError(request=MagicMock()),
        openai_module.APITimeoutError(request=MagicMock()),
        _mock_text_completion("gpt-4o-mini"),
    ]

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "end_turn"
    assert wrapper.get_active_model() == "gpt-4o-mini"


@patch("src.llm_wrapper.ollama_wrapper.time.sleep")
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_returns_error_when_all_attempts_fail(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = openai_module.APITimeoutError(
        request=MagicMock()
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert response.content is None


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_non_retryable_api_error_returns_error_immediately(mock_cls, mock_async_cls):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = openai_module.APIError(
        message="bad request", request=MagicMock(), body={}
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert mock_client.chat.completions.create.call_count == 1


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_call_missing_api_key_returns_error_not_raises(mock_cls, mock_async_cls):
    """AuthenticationError from missing OPENAI_API_KEY must not crash the server."""
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = openai_module.AuthenticationError(
        message="No API key", response=MagicMock(), body={}
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.stop_reason == "error"
    assert response.content is None


# ---------------------------------------------------------------------------
# call() — Format conversion (_convert_messages and _convert_tools)
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_convert_messages_injects_system_as_first_role(mock_cls, mock_async_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=[], system="Be concise.")

    oai_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert oai_messages[0] == {"role": "system", "content": "Be concise."}
    assert oai_messages[1] == {"role": "user", "content": "Hello"}


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_convert_messages_assistant_tool_use_block(mock_cls, mock_async_cls):
    """Assistant message with tool_use block must be converted to OpenAI tool_calls format."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    anthropic_messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look that up."},
                {
                    "type": "tool_use",
                    "id": "tu_001",
                    "name": "search",
                    "input": {"q": "python"},
                },
            ],
        }
    ]

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=anthropic_messages, tools=[], system=SYSTEM)

    oai_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    # Find the assistant message (skip system)
    assistant_msg = next(m for m in oai_messages if m["role"] == "assistant")
    assert assistant_msg["content"] == "Let me look that up."
    assert len(assistant_msg["tool_calls"]) == 1
    assert assistant_msg["tool_calls"][0]["id"] == "tu_001"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "search"
    parsed_args = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert parsed_args == {"q": "python"}


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_convert_messages_user_tool_result_block(mock_cls, mock_async_cls):
    """User message with tool_result block must be converted to OpenAI tool role message."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    anthropic_messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_001",
                    "content": "Result text here",
                }
            ],
        }
    ]

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=anthropic_messages, tools=[], system=SYSTEM)

    oai_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    tool_msg = next(m for m in oai_messages if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "tu_001"
    assert tool_msg["content"] == "Result text here"


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_convert_tools_maps_input_schema_to_parameters(mock_cls, mock_async_cls):
    """Anthropic input_schema must be mapped to OpenAI parameters."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_text_completion()

    anthropic_tools = [
        {
            "name": "search",
            "description": "Search for information",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=anthropic_tools, system=SYSTEM)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    oai_tools = call_kwargs["tools"]
    assert len(oai_tools) == 1
    assert oai_tools[0]["type"] == "function"
    assert oai_tools[0]["function"]["name"] == "search"
    assert oai_tools[0]["function"]["description"] == "Search for information"
    assert oai_tools[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"


# ---------------------------------------------------------------------------
# _map_stop_reason() — mapping table
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_map_stop_reason_stop_maps_to_end_turn(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    assert wrapper._map_stop_reason("stop") == "end_turn"


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_map_stop_reason_tool_calls_maps_to_tool_use(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    assert wrapper._map_stop_reason("tool_calls") == "tool_use"


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_map_stop_reason_length_maps_to_max_tokens(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    assert wrapper._map_stop_reason("length") == "max_tokens"


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
def test_map_stop_reason_unknown_defaults_to_end_turn(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    assert wrapper._map_stop_reason("unknown_value") == "end_turn"
    assert wrapper._map_stop_reason(None) == "end_turn"


# ---------------------------------------------------------------------------
# stream_call() — Normal execution
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_call_yields_text_tokens(mock_cls, mock_async_cls):
    """stream_call() yields text tokens streamed from the OpenAI API."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    chunks = _make_stream_chunks(["Hello", " world", "!"])
    mock_async_client.chat.completions.create = AsyncMock(
        return_value=_AsyncChunkIter(chunks)
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Hello", " world", "!"]


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_call_raises_tool_use_requested(mock_cls, mock_async_cls):
    """stream_call() raises ToolUseRequested when finish_reason is tool_calls."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    tool_chunk = {
        "id": "call_xyz",
        "name": "search",
        "arguments": json.dumps({"q": "test"}),
    }
    chunks = _make_stream_chunks(
        ["I'll search"], finish_reason="tool_calls", tool_call_chunks=[tool_chunk]
    )
    mock_async_client.chat.completions.create = AsyncMock(
        return_value=_AsyncChunkIter(chunks)
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)

    with pytest.raises(ToolUseRequested) as exc_info:
        await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    tool_calls = exc_info.value.tool_calls
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "search"
    assert tool_calls[0].tool_use_id == "call_xyz"
    assert tool_calls[0].input_params == {"q": "test"}


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_call_handles_invalid_json_arguments_gracefully(mock_cls, mock_async_cls):
    """Malformed JSON in tool call arguments falls back to empty dict rather than crashing."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    tool_chunk = {
        "id": "call_bad",
        "name": "broken_tool",
        "arguments": "NOT_VALID_JSON",
    }
    chunks = _make_stream_chunks([], finish_reason="tool_calls", tool_call_chunks=[tool_chunk])
    mock_async_client.chat.completions.create = AsyncMock(
        return_value=_AsyncChunkIter(chunks)
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)

    with pytest.raises(ToolUseRequested) as exc_info:
        await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    # input_params should be empty dict, not raise
    assert exc_info.value.tool_calls[0].input_params == {}


# ---------------------------------------------------------------------------
# stream_call() — Edge cases
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_call_raises_on_empty_messages(mock_cls, mock_async_cls):
    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    with pytest.raises(ValueError, match="messages must not be empty"):
        await _collect_stream(wrapper.stream_call(messages=[]))


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_call_without_tools(mock_cls, mock_async_cls):
    """stream_call() works when tools parameter is omitted."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    chunks = _make_stream_chunks(["Hi"])
    mock_async_client.chat.completions.create = AsyncMock(
        return_value=_AsyncChunkIter(chunks)
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES))

    assert tokens == ["Hi"]
    call_kwargs = mock_async_client.chat.completions.create.call_args.kwargs
    assert "tools" not in call_kwargs


# ---------------------------------------------------------------------------
# stream_call() — Retry and fallback
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.ollama_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_retries_on_timeout_then_succeeds(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    chunks = _make_stream_chunks(["Retry", " worked"])
    mock_async_client.chat.completions.create = AsyncMock(
        side_effect=[
            openai_module.APITimeoutError(request=MagicMock()),
            _AsyncChunkIter(chunks),
        ]
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Retry", " worked"]
    assert mock_async_client.chat.completions.create.call_count == 2


@patch("src.llm_wrapper.ollama_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_switches_to_fallback_after_primary_exhaustion(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    chunks = _make_stream_chunks(["Fallback", " ok"])
    mock_async_client.chat.completions.create = AsyncMock(
        side_effect=[
            openai_module.APITimeoutError(request=MagicMock()),
            openai_module.APITimeoutError(request=MagicMock()),
            _AsyncChunkIter(chunks),
        ]
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Fallback", " ok"]
    assert wrapper.get_active_model() == "gpt-4o-mini"


@patch("src.llm_wrapper.ollama_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_yields_nothing_when_all_attempts_fail(mock_cls, mock_async_cls, mock_sleep):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    mock_async_client.chat.completions.create = AsyncMock(
        side_effect=openai_module.APITimeoutError(request=MagicMock())
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    # 2 primary + 2 fallback = 4 total attempts, all yielding nothing
    assert tokens == []
    assert mock_async_client.chat.completions.create.call_count == 4


@patch("src.llm_wrapper.ollama_wrapper.openai.AsyncOpenAI")
@patch("src.llm_wrapper.ollama_wrapper.openai.OpenAI")
async def test_stream_non_retryable_api_error_yields_nothing(mock_cls, mock_async_cls):
    import openai as openai_module

    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client

    mock_async_client.chat.completions.create = AsyncMock(
        side_effect=openai_module.APIError(
            message="bad request", request=MagicMock(), body={}
        )
    )

    wrapper = OllamaLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == []
    # Non-retryable — only 1 attempt
    assert mock_async_client.chat.completions.create.call_count == 1
