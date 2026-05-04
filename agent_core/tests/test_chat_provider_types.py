"""Tests for chat_provider.types — neutral Pydantic models."""

import pytest
from pydantic import ValidationError

from src.chat_provider.types import (
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class TestTextBlock:
    def test_minimal(self):
        b = TextBlock(text="hello")
        assert b.type == "text"
        assert b.text == "hello"
        assert b.cache_hint is None

    def test_with_cache_hint(self):
        b = TextBlock(text="x", cache_hint="session")
        assert b.cache_hint == "session"

    def test_invalid_cache_hint(self):
        with pytest.raises(ValidationError):
            TextBlock(text="x", cache_hint="forever")  # type: ignore[arg-type]

    def test_round_trip(self):
        b = TextBlock(text="hi", cache_hint="turn")
        dumped = b.model_dump()
        assert dumped == {"type": "text", "text": "hi", "cache_hint": "turn"}
        assert TextBlock.model_validate(dumped) == b


class TestImageBlock:
    def test_url_source(self):
        b = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        assert b.type == "image"
        assert b.source.kind == "url"

    def test_base64_source_requires_data_and_media_type(self):
        # kind=base64 without data → validation error
        with pytest.raises(ValidationError):
            ImageSource(kind="base64", media_type="image/png")
        with pytest.raises(ValidationError):
            ImageSource(kind="base64", data="abc")

    def test_url_source_requires_url(self):
        with pytest.raises(ValidationError):
            ImageSource(kind="url")


class TestToolUseBlock:
    def test_minimal(self):
        b = ToolUseBlock(tool_use_id="t_1", tool_name="get_x", input={"q": 1})
        assert b.type == "tool_use"
        assert b.input == {"q": 1}

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            ToolUseBlock(tool_name="x", input={})  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ToolUseBlock(tool_use_id="t_1", input={})  # type: ignore[call-arg]

    def test_round_trip(self):
        b = ToolUseBlock(tool_use_id="t_1", tool_name="get_x", input={"q": 1})
        assert ToolUseBlock.model_validate(b.model_dump()) == b


class TestToolResultBlock:
    def test_text_content(self):
        b = ToolResultBlock(tool_use_id="t_1", content="ok")
        assert b.is_error is False

    def test_error_content(self):
        b = ToolResultBlock(tool_use_id="t_1", content="boom", is_error=True)
        assert b.is_error is True

    def test_block_list_content(self):
        b = ToolResultBlock(
            tool_use_id="t_1",
            content=[TextBlock(text="part 1"), TextBlock(text="part 2")],
        )
        assert isinstance(b.content, list)
        assert len(b.content) == 2
        assert all(isinstance(c, TextBlock) for c in b.content)

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            ToolResultBlock(content="x")  # type: ignore[call-arg]

    def test_round_trip(self):
        b = ToolResultBlock(tool_use_id="t_1", content="ok", is_error=True)
        assert ToolResultBlock.model_validate(b.model_dump()) == b


from src.chat_provider.types import (
    Message,
    OutputFormat,
    SystemPrompt,
    ToolDefinition,
)


class TestMessage:
    def test_user_text(self):
        m = Message(role="user", content=[TextBlock(text="hi")])
        assert m.role == "user"
        assert len(m.content) == 1

    def test_assistant_with_tool_use(self):
        m = Message(
            role="assistant",
            content=[
                TextBlock(text="let me check"),
                ToolUseBlock(tool_use_id="t_1", tool_name="lookup", input={"q": 1}),
            ],
        )
        assert m.content[1].type == "tool_use"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            Message(role="system", content=[TextBlock(text="x")])  # type: ignore[arg-type]

    def test_content_must_be_list(self):
        with pytest.raises(ValidationError):
            Message(role="user", content="just a string")  # type: ignore[arg-type]


class TestToolDefinition:
    def test_minimal(self):
        t = ToolDefinition(
            name="get_weather",
            description="Get the weather",
            input_schema={"type": "object", "properties": {}},
        )
        assert t.name == "get_weather"


class TestSystemPrompt:
    def test_with_blocks(self):
        sp = SystemPrompt(
            blocks=[
                TextBlock(text="You are helpful.", cache_hint="session"),
                TextBlock(text="Today's user is Aniket.", cache_hint="turn"),
            ]
        )
        assert len(sp.blocks) == 2
        assert sp.blocks[0].cache_hint == "session"


class TestOutputFormat:
    def test_minimal(self):
        of = OutputFormat(
            type="json_schema",
            schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        assert of.strict is True

    def test_strict_false(self):
        of = OutputFormat(type="json_schema", schema={}, strict=False)
        assert of.strict is False


from src.chat_provider.types import ChatRequest, ChatResponse, TokenUsage


class TestChatRequest:
    def test_minimal(self):
        r = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        assert r.tools == []
        assert r.tool_choice == "auto"
        assert r.output_format is None
        assert r.max_tokens == 4096

    def test_force_tool_by_name(self):
        r = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tool_choice="my_tool",
        )
        assert r.tool_choice == "my_tool"

    def test_empty_messages_is_allowed_by_the_model(self):
        r = ChatRequest(messages=[])
        assert r.messages == []


class TestTokenUsage:
    def test_default_all_none(self):
        u = TokenUsage()
        assert u.input_tokens is None
        assert u.cache_read_tokens is None

    def test_partial(self):
        u = TokenUsage(input_tokens=10, output_tokens=5)
        assert u.cache_read_tokens is None


class TestChatResponse:
    def test_minimal_text(self):
        r = ChatResponse(
            content=[TextBlock(text="hi back")],
            stop_reason="end_turn",
            model_used="claude-test",
            usage=TokenUsage(input_tokens=1, output_tokens=2),
        )
        assert r.parsed_output is None

    def test_with_parsed_output(self):
        r = ChatResponse(
            content=[TextBlock(text='{"x": 1}')],
            parsed_output={"x": 1},
            stop_reason="end_turn",
            model_used="claude-test",
            usage=TokenUsage(),
        )
        assert r.parsed_output == {"x": 1}

    def test_invalid_stop_reason(self):
        with pytest.raises(ValidationError):
            ChatResponse(
                content=[],
                stop_reason="something",  # type: ignore[arg-type]
                model_used="x",
                usage=TokenUsage(),
            )
