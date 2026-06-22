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


class TestGoogleBranch:
    def test_returns_google_provider(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
        cfg = {
            "provider": "google",
            "primary_model": "gemini-3.5-flash",
            "timeout_ms": 5000,
            "retry_attempts": 2,
        }
        with patch("src.chat_provider.google_provider.genai.Client"):
            p = build_chat_provider(cfg)
        assert type(p).__name__ == "GoogleChatProvider"

    def test_returns_google_provider_with_gemini_api_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "dummy")
        cfg = {
            "provider": "gemini",
            "primary_model": "gemini-3.5-flash",
            "timeout_ms": 5000,
            "retry_attempts": 2,
        }
        with patch("src.chat_provider.google_provider.genai.Client"):
            p = build_chat_provider(cfg)
        assert type(p).__name__ == "GoogleChatProvider"


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

    def test_openai_features_prompt_cache_true_passes(self):
        # OpenAI now declares supports_prompt_cache=True (#304); the
        # Layer-2 reconciliation must accept the matrix entry.
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            build_chat_provider(cfg)  # no exception

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

    def test_google_features_prompt_cache_true_passes(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
        cfg = {
            "provider": "google",
            "primary_model": "gemini-3.5-flash",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with patch("src.chat_provider.google_provider.genai.Client"):
            build_chat_provider(cfg)  # no exception
