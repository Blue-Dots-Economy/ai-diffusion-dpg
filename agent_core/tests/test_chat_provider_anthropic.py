"""Tests for AnthropicChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.anthropic_provider import AnthropicChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError


VALID_CONFIG = {
    "primary_model": "claude-sonnet-4-5-20250514",
    "timeout_ms": 5000,
    "retry_attempts": 2,
    "retry_backoff_seconds": [0, 0.0, 0.0],
    "features": {
        "prompt_cache": True,
        "streaming": True,
        "image_input": True,
    },
}


class TestInit:
    def test_capabilities_are_declared(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        caps = p.capabilities
        assert isinstance(caps, Capabilities)
        assert caps.supports_tools is True
        assert caps.supports_prompt_cache is True
        assert caps.supports_image_input is True
        assert caps.supports_audio_input is False
        assert caps.supports_streaming is True
        assert caps.supports_structured_output is True
        assert caps.supports_force_tool_choice is True

    def test_features_disable_caching(self):
        cfg = {**VALID_CONFIG, "features": {**VALID_CONFIG["features"], "prompt_cache": False}}
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(cfg)
        # Capabilities are still True (intrinsic), but the effective
        # feature for this deployment is False — _validate_request reads
        # from self._features.
        assert p._features["prompt_cache"] is False
        assert p.capabilities.supports_prompt_cache is True

    def test_empty_config_raises(self):
        with pytest.raises(ProviderConfigError):
            AnthropicChatProvider({})

    def test_missing_primary_model_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("primary_model")
        with pytest.raises(ProviderConfigError, match="primary_model"):
            AnthropicChatProvider(cfg)

    def test_missing_timeout_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("timeout_ms")
        with pytest.raises(ProviderConfigError, match="timeout_ms"):
            AnthropicChatProvider(cfg)

    def test_get_active_model(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        assert p.get_active_model() == "claude-sonnet-4-5-20250514"


from src.chat_provider.types import (
    ChatRequest,
    ImageBlock,
    ImageSource,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


def _make_provider(features: dict | None = None) -> AnthropicChatProvider:
    cfg = dict(VALID_CONFIG)
    if features is not None:
        cfg["features"] = features
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        return AnthropicChatProvider(cfg)


class TestToWire:
    def test_minimal_text_request(self):
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire == {
            "model": "claude-sonnet-4-5-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "timeout": 5.0,
        }

    def test_system_prompt_with_cache_hint_long_enough(self):
        p = _make_provider()
        long_text = "x" * 3500   # over _CACHE_MIN_CHARS
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint="session")]),
        )
        wire = p._to_wire(req)
        assert wire["system"] == [
            {"type": "text", "text": long_text, "cache_control": {"type": "ephemeral"}}
        ]

    def test_system_prompt_short_skips_cache_marker(self):
        p = _make_provider()
        short = "you are helpful"
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=short, cache_hint="session")]),
        )
        wire = p._to_wire(req)
        assert wire["system"] == [{"type": "text", "text": short}]

    def test_system_prompt_caching_disabled_drops_marker(self):
        p = _make_provider(features={"prompt_cache": False, "streaming": True, "image_input": True})
        long_text = "x" * 3500
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint=None)]),
        )
        wire = p._to_wire(req)
        assert "cache_control" not in wire["system"][0]

    def test_tool_definition_passthrough(self):
        p = _make_provider()
        td = ToolDefinition(
            name="get_x",
            description="get x",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
        )
        wire = p._to_wire(req)
        assert wire["tools"] == [
            {
                "name": "get_x",
                "description": "get x",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]

    def test_tool_choice_auto(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="auto",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "auto"}

    def test_tool_choice_any(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="any",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "any"}

    def test_tool_choice_named(self):
        p = _make_provider()
        td = ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="my_tool",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "tool", "name": "my_tool"}

    def test_tool_choice_none_drops_tools(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="none",
        )
        wire = p._to_wire(req)
        assert "tools" not in wire and "tool_choice" not in wire

    def test_image_block_url(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"][0] == {
            "type": "image",
            "source": {"type": "url", "url": "https://x/y.png"},
        }

    def test_image_block_base64(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="base64", media_type="image/png", data="ABC=="))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"][0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "ABC=="},
        }

    def test_assistant_message_with_tool_use_and_tool_result(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="look it up")]),
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking"),
                        ToolUseBlock(tool_use_id="t_1", tool_name="lookup", input={"q": "x"}),
                    ],
                ),
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="t_1", content="42")],
                ),
            ]
        )
        wire = p._to_wire(req)
        assert wire["messages"][1]["content"] == [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "t_1", "name": "lookup", "input": {"q": "x"}},
        ]
        assert wire["messages"][2]["content"] == [
            {"type": "tool_result", "tool_use_id": "t_1", "content": "42", "is_error": False}
        ]

    def test_output_format_emulated_via_tool_coercion(self):
        p = _make_provider()
        of = OutputFormat(
            schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="answer")])],
            output_format=of,
        )
        wire = p._to_wire(req)
        # A synthetic tool is appended and tool_choice forces it.
        assert wire["tools"] == [
            {
                "name": "respond_with_json",
                "description": "Return the response as JSON conforming to the schema.",
                "input_schema": of.schema,
            }
        ]
        assert wire["tool_choice"] == {"type": "tool", "name": "respond_with_json"}

    def test_max_tokens_passed_through(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            max_tokens=200,
        )
        wire = p._to_wire(req)
        assert wire["max_tokens"] == 200


from unittest.mock import MagicMock


def _mk_anthropic_message(
    text: str | None = None,
    tool_use: dict | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_create: int = 0,
) -> MagicMock:
    """Build a MagicMock that mimics anthropic.types.Message."""
    raw = MagicMock()
    blocks: list = []
    if text is not None:
        b = MagicMock()
        b.type = "text"
        b.text = text
        blocks.append(b)
    if tool_use is not None:
        b = MagicMock()
        b.type = "tool_use"
        b.id = tool_use["id"]
        b.name = tool_use["name"]
        b.input = tool_use["input"]
        blocks.append(b)
    raw.content = blocks
    raw.stop_reason = stop_reason
    raw.usage.input_tokens = input_tokens
    raw.usage.output_tokens = output_tokens
    raw.usage.cache_read_input_tokens = cache_read
    raw.usage.cache_creation_input_tokens = cache_create
    return raw


class TestFromWire:
    def test_text_only(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="hello back", input_tokens=12, output_tokens=4)
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "end_turn"
        assert resp.model_used == "claude-sonnet-4-5-20250514"
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert resp.content[0].text == "hello back"
        assert resp.parsed_output is None
        assert resp.usage.input_tokens == 12
        assert resp.usage.output_tokens == 4
        assert resp.usage.cache_read_tokens == 0
        assert resp.usage.cache_creation_tokens == 0

    def test_tool_use(self):
        p = _make_provider()
        raw = _mk_anthropic_message(
            text="checking",
            tool_use={"id": "t_1", "name": "lookup", "input": {"q": "x"}},
            stop_reason="tool_use",
        )
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "tool_use"
        assert len(resp.content) == 2
        assert resp.content[1].type == "tool_use"
        assert resp.content[1].tool_name == "lookup"

    def test_output_format_unwraps_synthetic_tool_use(self):
        p = _make_provider()
        of = OutputFormat(schema={"type": "object", "properties": {"answer": {"type": "string"}}})
        raw = _mk_anthropic_message(
            tool_use={"id": "t_x", "name": "respond_with_json", "input": {"answer": "42"}},
            stop_reason="tool_use",
        )
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output == {"answer": "42"}
        # Stop reason is normalised back to end_turn — caller sees a clean response.
        assert resp.stop_reason == "end_turn"
        # Content carries a synthesised TextBlock with the JSON string.
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert '"answer"' in resp.content[0].text

    def test_cache_token_fields_use_safe_int(self):
        p = _make_provider()
        raw = _mk_anthropic_message()
        # Simulate a missing field (older SDK / mocked response without cache fields)
        del raw.usage.cache_read_input_tokens
        del raw.usage.cache_creation_input_tokens
        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.cache_read_tokens == 0
        assert resp.usage.cache_creation_tokens == 0

    def test_max_tokens_stop_reason_passthrough(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="truncated", stop_reason="max_tokens")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "max_tokens"


import anthropic as _anthropic
from src.chat_provider.base import UnsupportedFeatureError


class TestCall:
    def test_normal_text_response(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="hi back", input_tokens=10, output_tokens=2)
        p._client.messages.create = MagicMock(return_value=raw)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert resp.content[0].text == "hi back"
        p._client.messages.create.assert_called_once()

    def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            p.call(req)

    def test_unsupported_feature_raises(self):
        p = _make_provider(features={"prompt_cache": False, "streaming": True, "image_input": True})
        long_text = "x" * 3500
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint="session")]),
        )
        with pytest.raises(UnsupportedFeatureError):
            p.call(req)

    def test_retry_on_rate_limit_then_success(self):
        p = _make_provider()
        raw_ok = _mk_anthropic_message(text="ok")

        class _FakeRateLimit(_anthropic.RateLimitError):
            def __init__(self): pass

        p._client.messages.create = MagicMock(side_effect=[_FakeRateLimit(), raw_ok])

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert p._client.messages.create.call_count == 2

    def test_exhausted_retries_returns_error_response(self):
        p = _make_provider()

        class _FakeRateLimit(_anthropic.RateLimitError):
            def __init__(self): pass

        p._client.messages.create = MagicMock(side_effect=_FakeRateLimit)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert resp.content == []
        # retry_attempts in VALID_CONFIG is 2
        assert p._client.messages.create.call_count == 2

    def test_non_retryable_api_error_returns_error_response(self):
        p = _make_provider()

        class _FakeAPIError(_anthropic.APIError):
            def __init__(self): pass

        p._client.messages.create = MagicMock(side_effect=_FakeAPIError())

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert p._client.messages.create.call_count == 1   # not retried


import asyncio
from unittest.mock import MagicMock

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested


class _FakeStreamEvent:
    def __init__(self, type_: str, text: str | None = None) -> None:
        self.type = type_
        self.delta = MagicMock()
        if text is not None:
            self.delta.text = text


class _FakeStream:
    def __init__(
        self,
        text_deltas: list[str],
        final_message: MagicMock,
    ) -> None:
        self._events = [_FakeStreamEvent("content_block_delta", text=t) for t in text_deltas]
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


def _install_stream(provider: AnthropicChatProvider, stream: _FakeStream) -> None:
    """Replace messages.stream(...) on the async client with a callable returning the stream."""
    provider._async_client.messages.stream = MagicMock(return_value=stream)


class TestStream:
    @pytest.mark.asyncio
    async def test_streams_text(self):
        p = _make_provider()
        final = _mk_anthropic_message(text="hello there", stop_reason="end_turn")
        stream = _FakeStream(text_deltas=["hello ", "there"], final_message=final)
        _install_stream(p, stream)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req):
            out.append(token)
        assert out == ["hello ", "there"]

    @pytest.mark.asyncio
    async def test_tool_use_raises(self):
        p = _make_provider()
        final = _mk_anthropic_message(
            tool_use={"id": "t_1", "name": "lookup", "input": {"q": "x"}},
            stop_reason="tool_use",
        )
        stream = _FakeStream(text_deltas=["checking"], final_message=final)
        _install_stream(p, stream)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        with pytest.raises(ChatToolUseRequested) as ei:
            async for token in p.stream(req):
                out.append(token)
        assert out == ["checking"]
        assert ei.value.tool_calls[0].tool_name == "lookup"

    @pytest.mark.asyncio
    async def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            async for _ in p.stream(req):
                pass

    @pytest.mark.asyncio
    async def test_output_format_on_stream_raises(self):
        p = _make_provider()
        of = OutputFormat(schema={})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            output_format=of,
        )
        with pytest.raises(UnsupportedFeatureError, match="stream"):
            async for _ in p.stream(req):
                pass

    @pytest.mark.asyncio
    async def test_abort_event_short_circuits(self):
        p = _make_provider()
        final = _mk_anthropic_message(text="hello there")
        stream = _FakeStream(text_deltas=["hel", "lo", " ", "there"], final_message=final)
        _install_stream(p, stream)

        abort = asyncio.Event()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req, abort_event=abort):
            out.append(token)
            if len(out) == 2:
                abort.set()
        assert out == ["hel", "lo"]
