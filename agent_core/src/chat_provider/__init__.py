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


def _capability_attr(feature_key: str) -> str:
    """Map a YAML feature key to the matching Capabilities attribute name."""
    return {
        "prompt_cache": "supports_prompt_cache",
        "streaming": "supports_streaming",
        "image_input": "supports_image_input",
    }[feature_key]


def _reconcile_features(
    *,
    provider_name: str,
    capabilities: Capabilities,
    features: dict,
) -> None:
    """Layer 2 of three-layer validation: capability reconciliation.

    Raises ProviderConfigError if any features.X=True targets a
    capability the provider does not support. Tightening (True→False)
    is always allowed; widening is not.
    """
    for key, value in features.items():
        if value is None:
            continue
        if not value:
            continue   # explicit False → tightening; always allowed
        if key not in _KNOWN_FEATURE_KEYS:
            continue   # unknown keys handled by caller; defensive guard
        cap_attr = _capability_attr(key)
        if not getattr(capabilities, cap_attr):
            raise ProviderConfigError(
                f"Provider '{provider_name}' does not support {key}; "
                f"set agent.features.{key} to false (or remove it) or "
                f"pick a different provider."
            )


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
        ProviderConfigError: provider is unknown, features carries an
            unrecognised key, a required config field is missing, or
            features.X=True conflicts with provider capabilities.
    """
    provider_name = agent_config.get("provider", "anthropic")

    features = agent_config.get("features") or {}
    if hasattr(features, "model_dump"):  # FeaturesConfig pydantic instance
        features = features.model_dump()
    unknown = set(features.keys()) - _KNOWN_FEATURE_KEYS
    if unknown:
        raise ProviderConfigError(
            f"Unknown feature key(s) in agent.features: {sorted(unknown)}. "
            f"Known keys: {sorted(_KNOWN_FEATURE_KEYS)}."
        )

    if provider_name == "anthropic":
        from src.chat_provider.anthropic_provider import AnthropicChatProvider
        _reconcile_features(
            provider_name="anthropic",
            capabilities=AnthropicChatProvider.capabilities,
            features=features,
        )
        return AnthropicChatProvider(agent_config)

    if provider_name == "openai":
        from src.chat_provider.openai_provider import OpenAIChatProvider
        _reconcile_features(
            provider_name="openai",
            capabilities=OpenAIChatProvider.capabilities,
            features=features,
        )
        return OpenAIChatProvider(agent_config)

    raise ProviderConfigError(
        f"Unknown provider '{provider_name}'. "
        f"Known providers: 'anthropic', 'openai'."
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
