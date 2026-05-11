"""Tests for prompts.py: prompt registry + lookup."""
import pytest

from prompts import PROMPTS, get_prompt


def test_three_prompts_registered():
    """The registry exposes the three named Hindi prompts."""
    assert set(PROMPTS.keys()) == {"SHORT_HINDI", "KKB_PERSONA", "STRICT_HINDI_ONLY"}


def test_each_prompt_is_nonempty_string():
    """Every registered prompt is a non-empty string."""
    for name, text in PROMPTS.items():
        assert isinstance(text, str)
        assert len(text) > 20, f"prompt {name} suspiciously short"


def test_get_prompt_returns_named_prompt():
    """get_prompt('SHORT_HINDI') returns the SHORT_HINDI string."""
    assert get_prompt("SHORT_HINDI") == PROMPTS["SHORT_HINDI"]


def test_get_prompt_unknown_raises():
    """Unknown prompt name raises KeyError with available names listed."""
    with pytest.raises(KeyError) as exc:
        get_prompt("NOT_A_REAL_PROMPT")
    msg = str(exc.value)
    assert "SHORT_HINDI" in msg
    assert "KKB_PERSONA" in msg
