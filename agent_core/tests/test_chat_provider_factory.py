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


def test_openai_not_implemented_yet():
    # PR1 ships Anthropic only; OpenAI lands in PR2.
    cfg = {**VALID_CONFIG["agent"], "provider": "openai"}
    with pytest.raises(ProviderConfigError, match="openai"):
        build_chat_provider(cfg)


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
