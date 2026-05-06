"""Tests for OpenAIChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.openai_provider import OpenAIChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError
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


VALID_CONFIG = {
    "primary_model": "gpt-4o-2024-08-06",
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
    def test_capabilities(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(VALID_CONFIG)
        caps = p.capabilities
        assert isinstance(caps, Capabilities)
        assert caps.supports_tools is True
        assert caps.supports_streaming is True
        assert caps.supports_prompt_cache is True
        assert caps.supports_image_input is True
        assert caps.supports_audio_input is False
        assert caps.supports_structured_output is True
        assert caps.supports_force_tool_choice is True

    def test_features_defaults_match_capability(self):
        # Empty features dict → effective features come from capabilities.
        cfg = {**VALID_CONFIG, "features": {}}
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(cfg)
        assert p._features["streaming"] is True
        assert p._features["image_input"] is True
        assert p._features["prompt_cache"] is True  # capability is True

    def test_empty_config_raises(self):
        with pytest.raises(ProviderConfigError):
            OpenAIChatProvider({})

    def test_missing_primary_model_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("primary_model")
        with pytest.raises(ProviderConfigError, match="primary_model"):
            OpenAIChatProvider(cfg)

    def test_missing_timeout_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("timeout_ms")
        with pytest.raises(ProviderConfigError, match="timeout_ms"):
            OpenAIChatProvider(cfg)

    def test_get_active_model(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(VALID_CONFIG)
        assert p.get_active_model() == "gpt-4o-2024-08-06"


def _make_provider(features: dict | None = None) -> OpenAIChatProvider:
    cfg = dict(VALID_CONFIG)
    if features is not None:
        cfg["features"] = features
    with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
        return OpenAIChatProvider(cfg)


class TestToWire:
    def test_minimal_text_request(self):
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire == {
            "model": "gpt-4o-2024-08-06",
            "max_completion_tokens": 4096,
            "messages": [{"role": "user", "content": "hi"}],
            "timeout": 5.0,
        }

    def test_system_prompt_concatenated_at_head(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[
                TextBlock(text="You are helpful."),
                TextBlock(text="Be concise."),
            ]),
        )
        wire = p._to_wire(req)
        assert wire["messages"][0] == {
            "role": "system",
            "content": "You are helpful.\n\nBe concise.",
        }
        assert wire["messages"][1]["role"] == "user"

    def test_text_only_message_uses_string_content(self):
        # Single TextBlock → content is a string, not a list of parts.
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == "hi"

    def test_image_block_uses_content_parts(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="describe"), img])]
        )
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
        ]

    def test_image_base64_uses_data_url(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="base64", media_type="image/png", data="ABC=="))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC=="}},
        ]

    def test_tool_definition(self):
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
        assert wire["tools"] == [{
            "type": "function",
            "function": {
                "name": "get_x",
                "description": "get x",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }]

    def test_tool_choice_auto(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="auto",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == "auto"

    def test_tool_choice_any_maps_to_required(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="any",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == "required"

    def test_tool_choice_none_drops_tools(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="none",
        )
        wire = p._to_wire(req)
        assert "tools" not in wire and "tool_choice" not in wire

    def test_tool_choice_named(self):
        p = _make_provider()
        td = ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="my_tool",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {
            "type": "function",
            "function": {"name": "my_tool"},
        }

    def test_assistant_tool_use_and_tool_result_messages(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="look it up")]),
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking"),
                        ToolUseBlock(tool_use_id="call_abc", tool_name="lookup", input={"q": "x"}),
                    ],
                ),
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="call_abc", content="42")],
                ),
            ]
        )
        wire = p._to_wire(req)
        assert wire["messages"][1] == {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "x"}'},
            }],
        }
        assert wire["messages"][2] == {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "42",
        }

    def test_output_format_native_response_format(self):
        p = _make_provider()
        of = OutputFormat(
            schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="answer")])],
            output_format=of,
        )
        wire = p._to_wire(req)
        assert wire["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "out",
                "schema": of.schema,
                "strict": True,
            },
        }

    def test_max_tokens_passed_through(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            max_tokens=200,
        )
        wire = p._to_wire(req)
        assert wire["max_completion_tokens"] == 200


from unittest.mock import MagicMock


def _mk_openai_completion(
    text: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> MagicMock:
    """Build a MagicMock that mimics openai.ChatCompletion."""
    raw = MagicMock()
    msg = MagicMock()
    msg.content = text
    if tool_calls is not None:
        wire_calls = []
        for tc in tool_calls:
            wc = MagicMock()
            wc.id = tc["id"]
            wc.type = "function"
            wc.function = MagicMock()
            wc.function.name = tc["name"]
            wc.function.arguments = tc["arguments"]
            wire_calls.append(wc)
        msg.tool_calls = wire_calls
    else:
        msg.tool_calls = None

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    raw.choices = [choice]

    raw.usage.prompt_tokens = prompt_tokens
    raw.usage.completion_tokens = completion_tokens
    # Default: SDK / model that does not report cached_tokens. Tests that
    # exercise the cache path replace this with an explicit object.
    raw.usage.prompt_tokens_details = None
    return raw


class TestFromWire:
    def test_text_only(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="hello back", prompt_tokens=12, completion_tokens=4)
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "end_turn"
        assert resp.model_used == "gpt-4o-2024-08-06"
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert resp.content[0].text == "hello back"
        assert resp.parsed_output is None
        assert resp.usage.input_tokens == 12
        assert resp.usage.output_tokens == 4
        assert resp.usage.cache_read_tokens is None
        assert resp.usage.cache_creation_tokens is None

    def test_tool_calls(self):
        p = _make_provider()
        raw = _mk_openai_completion(
            text=None,
            tool_calls=[{"id": "call_1", "name": "lookup", "arguments": '{"q": "x"}'}],
            finish_reason="tool_calls",
        )
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "tool_use"
        assert len(resp.content) == 1
        assert resp.content[0].type == "tool_use"
        assert resp.content[0].tool_name == "lookup"
        assert resp.content[0].input == {"q": "x"}

    def test_text_plus_tool_call(self):
        p = _make_provider()
        raw = _mk_openai_completion(
            text="checking",
            tool_calls=[{"id": "call_1", "name": "lookup", "arguments": "{}"}],
            finish_reason="tool_calls",
        )
        resp = p._from_wire(raw, output_format=None)
        assert len(resp.content) == 2
        assert resp.content[0].type == "text"
        assert resp.content[1].type == "tool_use"

    def test_finish_reason_length(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="trunc", finish_reason="length")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "max_tokens"

    def test_finish_reason_content_filter(self):
        p = _make_provider()
        raw = _mk_openai_completion(text=None, finish_reason="content_filter")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "error"

    def test_output_format_parses_json(self):
        p = _make_provider()
        of = OutputFormat(schema={"type": "object", "properties": {"answer": {"type": "string"}}})
        raw = _mk_openai_completion(text='{"answer": "42"}')
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output == {"answer": "42"}
        assert resp.stop_reason == "end_turn"

    def test_cache_read_tokens_populated_from_prompt_tokens_details(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="ok", prompt_tokens=2000, completion_tokens=10)
        details = MagicMock()
        details.cached_tokens = 1536
        raw.usage.prompt_tokens_details = details

        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.input_tokens == 2000
        assert resp.usage.cache_read_tokens == 1536
        # OpenAI never reports a creation event.
        assert resp.usage.cache_creation_tokens is None

    def test_cache_read_tokens_zero_when_field_present_but_no_hit(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="ok")
        details = MagicMock()
        details.cached_tokens = 0
        raw.usage.prompt_tokens_details = details

        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.cache_read_tokens == 0  # reported, no hit
        assert resp.usage.cache_creation_tokens is None

    def test_cache_read_tokens_none_when_field_missing(self):
        # Older SDK / model that does not surface prompt_tokens_details.
        p = _make_provider()
        raw = _mk_openai_completion(text="ok")
        # _mk_openai_completion already sets prompt_tokens_details=None.
        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.cache_read_tokens is None
        assert resp.usage.cache_creation_tokens is None

    def test_cache_read_tokens_none_when_cached_tokens_missing(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="ok")
        details = MagicMock()
        details.cached_tokens = None  # field on object, but not populated
        raw.usage.prompt_tokens_details = details

        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.cache_read_tokens is None

    def test_output_format_with_invalid_json_marks_error(self):
        p = _make_provider()
        of = OutputFormat(schema={})
        raw = _mk_openai_completion(text='{"not valid')
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output is None
        assert resp.stop_reason == "error"


import openai as _openai
from src.chat_provider.base import UnsupportedFeatureError


class _FakeRateLimit(_openai.RateLimitError):
    def __init__(self):  # noqa: D401
        pass


class _FakeAPIError(_openai.APIError):
    def __init__(self):  # noqa: D401
        pass


class TestCall:
    def test_normal_text_response(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="hi back", prompt_tokens=10, completion_tokens=2)
        p._client.chat.completions.create = MagicMock(return_value=raw)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert resp.content[0].text == "hi back"
        p._client.chat.completions.create.assert_called_once()

    def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            p.call(req)

    def test_cache_hint_is_tolerated(self):
        # OpenAI now declares supports_prompt_cache=True. Per-block
        # cache_hint markers must be accepted by the validator and
        # must NOT alter the wire payload (caching is automatic on
        # OpenAI's side).
        p = _make_provider()
        raw = _mk_openai_completion(text="ok")
        p._client.chat.completions.create = MagicMock(return_value=raw)

        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text="x" * 3500, cache_hint="session")]),
        )
        resp = p.call(req)
        assert resp.stop_reason == "end_turn"

        kwargs = p._client.chat.completions.create.call_args.kwargs
        # No cache marker is emitted on the request — only system + user msgs.
        for m in kwargs["messages"]:
            assert "cache_control" not in m
            if isinstance(m["content"], list):
                for part in m["content"]:
                    assert "cache_control" not in part

    def test_retry_on_rate_limit_then_success(self):
        p = _make_provider()
        raw_ok = _mk_openai_completion(text="ok")
        p._client.chat.completions.create = MagicMock(side_effect=[_FakeRateLimit(), raw_ok])

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert p._client.chat.completions.create.call_count == 2

    def test_exhausted_retries_returns_error_response(self):
        p = _make_provider()
        p._client.chat.completions.create = MagicMock(side_effect=_FakeRateLimit())

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert resp.content == []
        assert p._client.chat.completions.create.call_count == 2

    def test_non_retryable_api_error_returns_error_response(self):
        p = _make_provider()
        p._client.chat.completions.create = MagicMock(side_effect=_FakeAPIError())

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert p._client.chat.completions.create.call_count == 1


import asyncio
from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested


class _FakeOpenAIDelta:
    def __init__(self, *, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeOpenAIToolCallDelta:
    def __init__(self, *, index: int, id: str | None = None, name: str | None = None,
                 arguments: str | None = None) -> None:
        self.index = index
        self.id = id
        self.type = "function" if (name or arguments) else None
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = arguments


class _FakeOpenAIChunk:
    def __init__(self, *, content: str | None = None, tool_calls: list | None = None,
                 finish_reason: str | None = None, usage: dict | None = None) -> None:
        choice = MagicMock()
        choice.delta = _FakeOpenAIDelta(content=content, tool_calls=tool_calls)
        choice.finish_reason = finish_reason
        self.choices = [choice]
        if usage is not None:
            u = MagicMock()
            u.prompt_tokens = usage.get("prompt_tokens", 0)
            u.completion_tokens = usage.get("completion_tokens", 0)
            cached = usage.get("cached_tokens")
            if cached is None:
                u.prompt_tokens_details = None
            else:
                details = MagicMock()
                details.cached_tokens = cached
                u.prompt_tokens_details = details
            self.usage = u
        else:
            self.usage = None


class _FakeAsyncStream:
    def __init__(self, chunks: list[_FakeOpenAIChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


def _install_async_stream(provider: OpenAIChatProvider, chunks: list) -> None:
    async def _create(*args, **kwargs):
        return _FakeAsyncStream(chunks)
    provider._async_client.chat.completions.create = _create


class TestStream:
    @pytest.mark.asyncio
    async def test_streams_text(self):
        p = _make_provider()
        chunks = [
            _FakeOpenAIChunk(content="hello "),
            _FakeOpenAIChunk(content="there"),
            _FakeOpenAIChunk(finish_reason="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
        ]
        _install_async_stream(p, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req):
            out.append(token)
        assert out == ["hello ", "there"]

    @pytest.mark.asyncio
    async def test_tool_use_raises_after_accumulation(self):
        p = _make_provider()
        # Tool call arrives in 3 partial chunks: id+name first, then args fragments.
        chunks = [
            _FakeOpenAIChunk(content="checking"),
            _FakeOpenAIChunk(tool_calls=[_FakeOpenAIToolCallDelta(index=0, id="call_1", name="lookup", arguments='{"q": ')]),
            _FakeOpenAIChunk(tool_calls=[_FakeOpenAIToolCallDelta(index=0, arguments='"x"}')]),
            _FakeOpenAIChunk(finish_reason="tool_calls", usage={"prompt_tokens": 5, "completion_tokens": 4}),
        ]
        _install_async_stream(p, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        with pytest.raises(ChatToolUseRequested) as ei:
            async for token in p.stream(req):
                out.append(token)
        assert out == ["checking"]
        assert ei.value.tool_calls[0].tool_name == "lookup"
        assert ei.value.tool_calls[0].input == {"q": "x"}

    @pytest.mark.asyncio
    async def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            async for _ in p.stream(req):
                pass

    @pytest.mark.asyncio
    async def test_stream_propagates_cached_tokens_to_metrics(self):
        # Streaming usage frame surfaces cached_tokens — verify it ends up
        # in the synth response that drives record_call_metrics().
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)

        p = _make_provider()
        chunks = [
            _FakeOpenAIChunk(content="hi"),
            _FakeOpenAIChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 1500, "completion_tokens": 4, "cached_tokens": 1200},
            ),
        ]
        _install_async_stream(p, chunks)

        with patch("src.chat_provider.openai_provider.record_call_metrics", side_effect=_capture):
            req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
            async for _ in p.stream(req):
                pass

        usage = captured["response"].usage
        assert usage.input_tokens == 1500
        assert usage.cache_read_tokens == 1200
        assert usage.cache_creation_tokens is None

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
        chunks = [
            _FakeOpenAIChunk(content="hel"),
            _FakeOpenAIChunk(content="lo"),
            _FakeOpenAIChunk(content=" "),
            _FakeOpenAIChunk(content="there"),
            _FakeOpenAIChunk(finish_reason="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
        ]
        _install_async_stream(p, chunks)

        abort = asyncio.Event()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req, abort_event=abort):
            out.append(token)
            if len(out) == 2:
                abort.set()
        assert out == ["hel", "lo"]
