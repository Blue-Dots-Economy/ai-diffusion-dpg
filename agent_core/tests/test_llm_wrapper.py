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

stream_call() coverage:
- Normal: streams text tokens
- Normal: tool use mid-stream raises ToolUseRequested
- Edge: empty messages raises ValueError
- Failure: retry on APITimeoutError, success on second attempt
- Failure: fallback model switch after primary exhaustion
- Failure: all retries exhausted yields nothing
- Failure: non-retryable APIError yields nothing
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from src.exceptions import ToolUseRequested
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


# ---------------------------------------------------------------------------
# stream_call() helpers
# ---------------------------------------------------------------------------


def _make_stream_context(tokens: list[str], stop_reason: str = "end_turn",
                         tool_blocks: list | None = None,
                         input_tokens: int = 10, output_tokens: int = 5):
    """Build a mock async context manager for messages.stream().

    Args:
        tokens: Text tokens to yield as content_block_delta events.
        stop_reason: The stop_reason on the final message.
        tool_blocks: Optional list of tool_use content blocks on the final message.
        input_tokens: Input token count on the final message usage.
        output_tokens: Output token count on the final message usage.
    """
    events = []
    for tok in tokens:
        event = MagicMock()
        event.type = "content_block_delta"
        event.delta = MagicMock()
        event.delta.text = tok
        events.append(event)

    final_message = MagicMock()
    final_message.stop_reason = stop_reason
    final_message.usage.input_tokens = input_tokens
    final_message.usage.output_tokens = output_tokens
    final_message.content = tool_blocks or []

    class _AsyncEventIter:
        """Async iterator over mock stream events."""
        def __init__(self, items):
            self._items = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration

    class _MockStream:
        """Mock stream object supporting async iteration and get_final_message."""
        def __init__(self, evts, final_msg):
            self._events = evts
            self._final_message = final_msg

        def __aiter__(self):
            return _AsyncEventIter(self._events)

        async def get_final_message(self):
            return self._final_message

    class _MockCtx:
        """Mock async context manager for messages.stream()."""
        def __init__(self, evts, final_msg):
            self._stream = _MockStream(evts, final_msg)

        async def __aenter__(self):
            return self._stream

        async def __aexit__(self, *args):
            return False

    return _MockCtx(events, final_message)


async def _collect_stream(gen):
    """Collect all tokens from an async generator into a list."""
    tokens = []
    async for token in gen:
        tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# stream_call() — Normal execution
# ---------------------------------------------------------------------------


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_yields_text_tokens(mock_anthropic_cls, mock_async_cls):
    """stream_call() yields text tokens from the LLM stream."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(["Hello", " world", "!"])
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Hello", " world", "!"]


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_raises_tool_use_requested(mock_anthropic_cls, mock_async_cls):
    """stream_call() raises ToolUseRequested when LLM returns tool_use stop reason."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "search"
    tool_block.id = "tu_999"
    tool_block.input = {"q": "test"}

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(
            ["I'll search"], stop_reason="tool_use", tool_blocks=[tool_block]
        )
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)

    with pytest.raises(ToolUseRequested) as exc_info:
        await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert len(exc_info.value.tool_calls) == 1
    assert exc_info.value.tool_calls[0].tool_name == "search"
    assert exc_info.value.tool_calls[0].tool_use_id == "tu_999"


# ---------------------------------------------------------------------------
# stream_call() — Edge cases
# ---------------------------------------------------------------------------


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_raises_on_empty_messages(mock_anthropic_cls, mock_async_cls):
    """stream_call() raises ValueError for empty messages."""
    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    with pytest.raises(ValueError, match="messages must not be empty"):
        await _collect_stream(wrapper.stream_call(messages=[], system=SYSTEM))


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_without_tools(mock_anthropic_cls, mock_async_cls):
    """stream_call() works without tools parameter."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(["Hi"])
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES))

    assert tokens == ["Hi"]
    # Verify tools was not passed to the stream call
    call_kwargs = mock_async_client.messages.stream.call_args.kwargs
    assert "tools" not in call_kwargs


# ---------------------------------------------------------------------------
# stream_call() — Retry and fallback
# ---------------------------------------------------------------------------


@patch("src.llm_wrapper.claude_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_retries_on_timeout_then_succeeds(mock_anthropic_cls, mock_async_cls, mock_sleep):
    """stream_call() retries on APITimeoutError and succeeds on second attempt."""
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        side_effect=[
            MagicMock(__aenter__=AsyncMock(side_effect=anthropic_module.APITimeoutError(request=MagicMock())),
                      __aexit__=AsyncMock(return_value=False)),
            _make_stream_context(["Retry", " worked"]),
        ]
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Retry", " worked"]
    assert mock_async_client.messages.stream.call_count == 2


@patch("src.llm_wrapper.claude_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_switches_to_fallback_after_exhaustion(mock_anthropic_cls, mock_async_cls, mock_sleep):
    """stream_call() switches to fallback model after primary retries are exhausted."""
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    timeout_ctx = MagicMock(
        __aenter__=AsyncMock(side_effect=anthropic_module.APITimeoutError(request=MagicMock())),
        __aexit__=AsyncMock(return_value=False),
    )

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    # Primary fails twice (max_attempts=2), then fallback succeeds
    mock_async_client.messages.stream = MagicMock(
        side_effect=[
            timeout_ctx,
            MagicMock(__aenter__=AsyncMock(side_effect=anthropic_module.APITimeoutError(request=MagicMock())),
                      __aexit__=AsyncMock(return_value=False)),
            _make_stream_context(["Fallback", " ok"]),
        ]
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == ["Fallback", " ok"]
    assert wrapper.get_active_model() == "claude-fallback"


@patch("src.llm_wrapper.claude_wrapper.asyncio.sleep", new_callable=AsyncMock)
@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_yields_nothing_when_all_attempts_fail(mock_anthropic_cls, mock_async_cls, mock_sleep):
    """stream_call() yields nothing when all retry attempts on both models are exhausted."""
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    def _timeout_ctx():
        return MagicMock(
            __aenter__=AsyncMock(side_effect=anthropic_module.APITimeoutError(request=MagicMock())),
            __aexit__=AsyncMock(return_value=False),
        )

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    # All 4 attempts fail (2 primary + 2 fallback)
    mock_async_client.messages.stream = MagicMock(
        side_effect=[_timeout_ctx(), _timeout_ctx(), _timeout_ctx(), _timeout_ctx()]
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == []


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_non_retryable_api_error_yields_nothing(mock_anthropic_cls, mock_async_cls):
    """stream_call() yields nothing on non-retryable APIError (no retry)."""
    import anthropic as anthropic_module

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(
                side_effect=anthropic_module.APIError(
                    message="bad request", request=MagicMock(), body={}
                )
            ),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    tokens = await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    assert tokens == []
    # Only 1 attempt — non-retryable
    assert mock_async_client.messages.stream.call_count == 1


# ---------------------------------------------------------------------------
# GH-151: prompt caching, cache tokens, stream span instrumentation
# ---------------------------------------------------------------------------


def test_wrap_system_for_caching_returns_short_string_unchanged():
    short = "You are a helpful assistant."
    assert ClaudeLLMWrapper._wrap_system_for_caching(short) == short


def test_wrap_system_for_caching_wraps_long_string_as_cache_block():
    long = "x" * 5000  # well above _CACHE_MIN_CHARS
    wrapped = ClaudeLLMWrapper._wrap_system_for_caching(long)
    assert isinstance(wrapped, list)
    assert wrapped[0]["type"] == "text"
    assert wrapped[0]["cache_control"] == {"type": "ephemeral"}
    assert wrapped[0]["text"] == long


def test_wrap_system_for_caching_passes_through_empty_and_none():
    assert ClaudeLLMWrapper._wrap_system_for_caching("") == ""
    assert ClaudeLLMWrapper._wrap_system_for_caching(None) is None


def test_wrap_system_for_caching_passes_through_preformed_list():
    blocks = [{"type": "text", "text": "pre-formed"}]
    assert ClaudeLLMWrapper._wrap_system_for_caching(blocks) is blocks


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_forwards_cache_control_for_long_system_prompt(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_text_response()

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    long_system = "You are a helpful assistant. " * 200  # >3000 chars
    wrapper.call(messages=MESSAGES, tools=[], system=long_system)

    kwargs = mock_client.messages.create.call_args.kwargs
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_leaves_short_system_prompt_as_string(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_text_response()

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    kwargs = mock_client.messages.create.call_args.kwargs
    assert isinstance(kwargs["system"], str)
    assert kwargs["system"] == SYSTEM


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_parses_cache_tokens_from_response(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    raw = _mock_text_response()
    raw.usage.cache_read_input_tokens = 850
    raw.usage.cache_creation_input_tokens = 0
    mock_client.messages.create.return_value = raw

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    assert response.cache_read_input_tokens == 850
    assert response.cache_creation_input_tokens == 0


@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
def test_call_zero_cache_tokens_when_fields_absent(mock_anthropic_cls):
    """Old SDK versions without cache fields must not break us."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    raw = _mock_text_response()
    # The auto-MagicMock returns non-int sentinels for unknown attrs; _safe_int
    # must coerce to 0 to satisfy the dataclass's int type contract.
    mock_client.messages.create.return_value = raw

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    response = wrapper.call(messages=MESSAGES, tools=[], system=SYSTEM)

    # No cache attrs set on the mock's usage → should default to 0.
    assert isinstance(response.cache_read_input_tokens, int)
    assert isinstance(response.cache_creation_input_tokens, int)


def test_safe_int_handles_none_and_garbage():
    from src.llm_wrapper.claude_wrapper import _safe_int

    assert _safe_int(42) == 42
    assert _safe_int(None) == 0
    assert _safe_int("bad") == 0
    assert _safe_int(MagicMock()) == 0
    assert _safe_int(0) == 0


# ---------------------------------------------------------------------------
# stream_call() — max_tokens plumbing (GH-194)
# ---------------------------------------------------------------------------


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_uses_default_max_tokens_when_unset(mock_anthropic_cls, mock_async_cls):
    """stream_call() falls back to the wrapper default (4096) when max_tokens is not provided."""
    mock_anthropic_cls.return_value = MagicMock()
    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(["hi"])
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    await _collect_stream(wrapper.stream_call(messages=MESSAGES, system=SYSTEM))

    # Inspect kwargs passed to the underlying Anthropic streaming call.
    call_kwargs = mock_async_client.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == 4096


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_forwards_max_tokens_override(mock_anthropic_cls, mock_async_cls):
    """stream_call() forwards an explicit max_tokens value to the Anthropic client."""
    mock_anthropic_cls.return_value = MagicMock()
    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(["hi"])
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    await _collect_stream(
        wrapper.stream_call(messages=MESSAGES, system=SYSTEM, max_tokens=200)
    )

    call_kwargs = mock_async_client.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == 200


@patch("src.llm_wrapper.claude_wrapper.anthropic.AsyncAnthropic")
@patch("src.llm_wrapper.claude_wrapper.anthropic.Anthropic")
async def test_stream_call_treats_none_max_tokens_as_default(mock_anthropic_cls, mock_async_cls):
    """Passing max_tokens=None explicitly yields the default 4096 cap."""
    mock_anthropic_cls.return_value = MagicMock()
    mock_async_client = MagicMock()
    mock_async_cls.return_value = mock_async_client
    mock_async_client.messages.stream = MagicMock(
        return_value=_make_stream_context(["hi"])
    )

    wrapper = ClaudeLLMWrapper(VALID_CONFIG)
    await _collect_stream(
        wrapper.stream_call(messages=MESSAGES, system=SYSTEM, max_tokens=None)
    )

    call_kwargs = mock_async_client.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == 4096
