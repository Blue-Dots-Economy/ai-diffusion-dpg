"""Tests for OpenAIChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.openai_provider import OpenAIChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError


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
