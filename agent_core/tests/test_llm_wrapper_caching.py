"""Tests for ClaudeLLMWrapper prompt-caching behaviour (GH-176)."""

from __future__ import annotations

import pytest

from src.llm_wrapper.claude_wrapper import ClaudeLLMWrapper


def test_claude_wrapper_preserves_list_of_blocks_with_cache_control():
    """Regression: _wrap_system_for_caching must return list[dict] unchanged."""
    system_blocks = [
        {"type": "text", "text": "tier1 persona", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tier2 subagent", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tier3 dynamic"},
    ]
    result = ClaudeLLMWrapper._wrap_system_for_caching(system_blocks)
    assert result is system_blocks  # returned unchanged
    assert all("text" in b for b in result)
    assert result[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in result[-1]
