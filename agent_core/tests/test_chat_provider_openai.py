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
        "prompt_cache": False,   # OpenAI cap is False; matching here is a no-op.
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
        assert caps.supports_prompt_cache is False
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
        assert p._features["prompt_cache"] is False  # capability is False

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

    def test_output_format_with_invalid_json_marks_error(self):
        p = _make_provider()
        of = OutputFormat(schema={})
        raw = _mk_openai_completion(text='{"not valid')
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output is None
        assert resp.stop_reason == "error"
