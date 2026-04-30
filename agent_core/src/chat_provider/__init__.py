"""
agent_core/src/chat_provider — provider-neutral LLM interface.

Public surface:
    ChatProviderBase  — ABC every provider implements.
    Capabilities      — frozen dataclass declared per provider class.
    build_chat_provider(agent_config) — factory; sole construction path.

All other names (TextBlock, ChatRequest, etc.) are exposed via
chat_provider.types.

This package replaces agent_core/src/llm_wrapper/ over PRs #288–#292.
"""

from __future__ import annotations

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ChatProviderError,
    ProviderAPIError,
    ProviderConfigError,
    ToolUseRequested,
    UnsupportedFeatureError,
)


_KNOWN_FEATURE_KEYS = {"prompt_cache", "streaming", "image_input"}


def build_chat_provider(agent_config: dict) -> ChatProviderBase:
    """Construct the configured ChatProviderBase implementation.

    Args:
        agent_config: the `agent.*` sub-tree of the merged YAML config.
            Required keys: primary_model, timeout_ms, retry_attempts.
            Optional keys: provider (default 'anthropic'),
            retry_backoff_seconds, features.{prompt_cache, streaming,
            image_input}.

    Returns:
        ChatProviderBase: the concrete provider chosen by
        agent_config["provider"].

    Raises:
        ProviderConfigError: provider is unknown, or features carry an
            unrecognised key, or a required config field is missing.
    """
    provider_name = agent_config.get("provider", "anthropic")

    features = agent_config.get("features") or {}
    unknown = set(features.keys()) - _KNOWN_FEATURE_KEYS
    if unknown:
        raise ProviderConfigError(
            f"Unknown feature key(s) in agent.features: {sorted(unknown)}. "
            f"Known keys: {sorted(_KNOWN_FEATURE_KEYS)}."
        )

    if provider_name == "anthropic":
        # Lazy import keeps the dependency localised.
        from src.chat_provider.anthropic_provider import AnthropicChatProvider
        return AnthropicChatProvider(agent_config)

    if provider_name == "openai":
        raise ProviderConfigError(
            "Provider 'openai' is not yet implemented. "
            "OpenAI support lands in PR2 (issue #289)."
        )

    raise ProviderConfigError(
        f"Unknown provider '{provider_name}'. Known providers: 'anthropic'."
    )


__all__ = [
    "Capabilities",
    "ChatProviderBase",
    "ChatProviderError",
    "ProviderAPIError",
    "ProviderConfigError",
    "ToolUseRequested",
    "UnsupportedFeatureError",
    "build_chat_provider",
]
