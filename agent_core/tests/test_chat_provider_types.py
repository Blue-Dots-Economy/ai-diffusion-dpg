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
