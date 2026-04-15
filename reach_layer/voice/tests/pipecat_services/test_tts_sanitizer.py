"""Tests for TTSTextSanitizerProcessor and _sanitize()."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pipecat.frames.frames import TTSSpeakFrame, EndFrame
from pipecat.processors.frame_processor import FrameDirection


# ---------------------------------------------------------------------------
# _sanitize() unit tests — one per rule
# ---------------------------------------------------------------------------

def sanitize(text):
    from src.pipecat_services.tts_sanitizer import _sanitize
    return _sanitize(text)


def test_empty_string_unchanged():
    assert sanitize("") == ""


def test_code_fence_omitted():
    text = "Here is code:\n```python\nprint('hello')\n```\nDone."
    assert sanitize(text) == "Here is code:. Done."


def test_inline_code_inner_text_kept():
    assert sanitize("Use `uv run pytest` to run tests.") == "Use uv run pytest to run tests."


def test_header_becomes_sentence():
    assert sanitize("## Getting Started") == "Getting Started. "


def test_bold_inner_text_kept():
    assert sanitize("This is **important** text.") == "This is important text."


def test_italic_inner_text_kept():
    assert sanitize("This is *emphasized* text.") == "This is emphasized text."


def test_bold_italic_inner_text_kept():
    assert sanitize("This is ***very important*** text.") == "This is very important text."


def test_underscore_bold_inner_text_kept():
    assert sanitize("This is __bold__ text.") == "This is bold text."


def test_underscore_italic_inner_text_kept():
    assert sanitize("This is _italic_ text.") == "This is italic text."


def test_ordered_list_first_next():
    text = "1. Alpha\n2. Beta\n3. Gamma"
    assert sanitize(text) == "First, Alpha. Next, Beta. Next, Gamma."


def test_ordered_list_more_than_three_items():
    text = "1. A\n2. B\n3. C\n4. D\n5. E"
    result = sanitize(text)
    assert result.startswith("First, A.")
    assert result.count("Next,") == 4


def test_unordered_list_dash():
    text = "- Apple\n- Banana\n- Cherry"
    assert sanitize(text) == "Apple. Banana. Cherry."


def test_unordered_list_asterisk():
    text = "* One\n* Two"
    assert sanitize(text) == "One. Two."


def test_link_label_kept_url_removed():
    assert sanitize("See [the docs](https://example.com) for details.") == "See the docs for details."


def test_horizontal_rule_becomes_pause():
    assert sanitize("Section A\n---\nSection B") == "Section A. Section B."


def test_newline_becomes_pause():
    assert sanitize("Hello\nWorld") == "Hello. World."


def test_multiple_newlines_become_single_pause():
    assert sanitize("Hello\n\n\nWorld") == "Hello. World."


def test_emoji_removed():
    assert sanitize("Great job! 🎉") == "Great job!"


def test_multiple_emojis_removed():
    assert sanitize("👋 Hello 🌍") == "Hello"


def test_devanagari_preserved():
    """Hindi text must not be stripped — Devanagari is not emoji."""
    text = "नमस्ते, मैं आपकी मदद कर सकता हूँ।"
    assert sanitize(text) == text


def test_japanese_preserved():
    assert sanitize("こんにちは") == "こんにちは"


def test_arabic_preserved():
    assert sanitize("مرحبا") == "مرحبا"


def test_chinese_preserved():
    assert sanitize("你好") == "你好"


def test_combined_bold_inside_list():
    text = "- **Important**: Do this first\n- Regular item"
    assert sanitize(text) == "Important: Do this first. Regular item."


def test_multiple_spaces_collapsed():
    assert sanitize("Hello   world") == "Hello world"


def test_leading_trailing_whitespace_stripped():
    assert sanitize("  Hello world  ") == "Hello world"


# ---------------------------------------------------------------------------
# TTSTextSanitizerProcessor integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_frame_sanitizes_tts_speak_frame():
    """TTSSpeakFrame text is sanitized and a new TTSSpeakFrame is pushed with original direction."""
    from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor

    processor = TTSTextSanitizerProcessor()
    pushed = []

    async def capture(frame, direction=None):
        pushed.append((frame, direction))

    processor.push_frame = capture

    frame = TTSSpeakFrame(text="**Hello** world\n- Item one\n- Item two")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert isinstance(pushed[0][0], TTSSpeakFrame)
    assert pushed[0][0].text == "Hello world. Item one. Item two."
    assert pushed[0][1] == FrameDirection.DOWNSTREAM


@pytest.mark.asyncio
async def test_process_frame_passes_non_tts_frame_through():
    """Non-TTSSpeakFrame frames pass through unchanged."""
    from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor

    processor = TTSTextSanitizerProcessor()
    pushed = []

    async def capture(frame, direction=None):
        pushed.append((frame, direction))

    processor.push_frame = capture

    frame = EndFrame()
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert isinstance(pushed[0][0], EndFrame)
    assert pushed[0][1] == FrameDirection.DOWNSTREAM
