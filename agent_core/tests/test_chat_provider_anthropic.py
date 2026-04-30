"""Tests for AnthropicChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.anthropic_provider import AnthropicChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError


VALID_CONFIG = {
    "primary_model": "claude-sonnet-4-5-20250514",
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
    def test_capabilities_are_declared(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        caps = p.capabilities
        assert isinstance(caps, Capabilities)
        assert caps.supports_tools is True
        assert caps.supports_prompt_cache is True
        assert caps.supports_image_input is True
        assert caps.supports_audio_input is False
        assert caps.supports_streaming is True
        assert caps.supports_structured_output is True
        assert caps.supports_force_tool_choice is True

    def test_features_disable_caching(self):
        cfg = {**VALID_CONFIG, "features": {**VALID_CONFIG["features"], "prompt_cache": False}}
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(cfg)
        # Capabilities are still True (intrinsic), but the effective
        # feature for this deployment is False — _validate_request reads
        # from self._features.
        assert p._features["prompt_cache"] is False
        assert p.capabilities.supports_prompt_cache is True

    def test_empty_config_raises(self):
        with pytest.raises(ProviderConfigError):
            AnthropicChatProvider({})

    def test_missing_primary_model_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("primary_model")
        with pytest.raises(ProviderConfigError, match="primary_model"):
            AnthropicChatProvider(cfg)

    def test_missing_timeout_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("timeout_ms")
        with pytest.raises(ProviderConfigError, match="timeout_ms"):
            AnthropicChatProvider(cfg)

    def test_get_active_model(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        assert p.get_active_model() == "claude-sonnet-4-5-20250514"
