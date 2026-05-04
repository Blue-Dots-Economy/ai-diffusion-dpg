"""Tests for build_chat_provider() factory."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider import (
    ChatProviderBase,
    Capabilities,
    ProviderConfigError,
    build_chat_provider,
)


VALID_CONFIG = {
    "agent": {
        "provider": "anthropic",
        "primary_model": "claude-sonnet-4-5-20250514",
        "timeout_ms": 5000,
        "retry_attempts": 2,
    }
}


def test_returns_chat_provider_for_anthropic():
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        p = build_chat_provider(VALID_CONFIG["agent"])
    assert isinstance(p, ChatProviderBase)


def test_unknown_provider_raises():
    cfg = {**VALID_CONFIG["agent"], "provider": "wat"}
    with pytest.raises(ProviderConfigError, match="provider"):
        build_chat_provider(cfg)


def test_openai_branch_now_works():
    """Sanity: the OpenAI branch returns an OpenAIChatProvider in PR2."""
    cfg = {**VALID_CONFIG["agent"], "provider": "openai", "primary_model": "gpt-4o-2024-08-06"}
    with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
        p = build_chat_provider(cfg)
    assert type(p).__name__ == "OpenAIChatProvider"


def test_default_provider_is_anthropic_when_unspecified():
    cfg = {**VALID_CONFIG["agent"]}
    cfg.pop("provider")
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        p = build_chat_provider(cfg)
    assert isinstance(p, ChatProviderBase)


def test_features_unknown_capability_raises():
    cfg = {
        **VALID_CONFIG["agent"],
        "features": {"prompt_cache": True, "made_up_feature": True},
    }
    with pytest.raises(ProviderConfigError, match="made_up_feature"):
        build_chat_provider(cfg)


def test_capabilities_is_re_exported():
    # The factory module must re-export Capabilities for downstream tests.
    assert Capabilities is not None


class TestOpenAIBranch:
    def test_returns_openai_provider(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = build_chat_provider(cfg)
        assert type(p).__name__ == "OpenAIChatProvider"


class TestCapabilityReconciliation:
    def test_anthropic_features_prompt_cache_true_passes(self):
        cfg = {
            "provider": "anthropic",
            "primary_model": "claude-sonnet-4-5-20250514",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            build_chat_provider(cfg)  # no exception

    def test_openai_features_prompt_cache_true_raises(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with pytest.raises(ProviderConfigError, match="prompt_cache"):
            build_chat_provider(cfg)

    def test_openai_features_image_input_true_passes(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"image_input": True},
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            build_chat_provider(cfg)

    def test_openai_features_streaming_true_passes(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"streaming": True},
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            build_chat_provider(cfg)

    def test_features_false_against_supported_capability_passes(self):
        cfg = {
            "provider": "anthropic",
            "primary_model": "claude-sonnet-4-5-20250514",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": False, "image_input": False},
        }
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            build_chat_provider(cfg)
