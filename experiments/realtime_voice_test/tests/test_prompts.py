"""Tests for prompts.py."""
from prompts import DEFAULT_PROMPT


def test_default_prompt_is_nonempty_string():
    """DEFAULT_PROMPT is a usable string."""
    assert isinstance(DEFAULT_PROMPT, str)
    assert len(DEFAULT_PROMPT) > 20


def test_default_prompt_does_not_force_language():
    """Language enforcement is the LANGUAGE env var's job, not the prompt's.

    If this test starts failing we've reintroduced a language directive in
    the prompt — fine if intentional (e.g., the model drifted to English
    on real calls), but the test should be updated to reflect the new
    intent.
    """
    assert "Hindi" not in DEFAULT_PROMPT
    assert "हिन्दी" not in DEFAULT_PROMPT
