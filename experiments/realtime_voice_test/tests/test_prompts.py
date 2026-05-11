"""Tests for prompts.py."""
from prompts import DEFAULT_PROMPT


def test_default_prompt_is_nonempty_string():
    """DEFAULT_PROMPT is a usable string."""
    assert isinstance(DEFAULT_PROMPT, str)
    assert len(DEFAULT_PROMPT) > 20


def test_default_prompt_pins_hindi_output():
    """The prompt explicitly tells the model to reply in Hindi.

    Reason: `input_audio_transcription.language="hi"` is a STT hint only —
    OpenAI Realtime has no separate output-language parameter, so the only
    way to ensure the bot replies in Hindi (regardless of what language the
    user opens with) is via an explicit instruction in the system prompt.
    This was verified on real test calls.
    """
    assert "Hindi" in DEFAULT_PROMPT
