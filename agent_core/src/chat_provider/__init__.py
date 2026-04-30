"""
agent_core/src/chat_provider — provider-neutral LLM interface.

Public surface:
    ChatProviderBase  — ABC every provider implements.
    Capabilities      — frozen dataclass declared per provider class.
    build_chat_provider(config) — factory; sole construction path.

All other names (TextBlock, ChatRequest, etc.) are exposed via
chat_provider.types.

This package replaces agent_core/src/llm_wrapper/ over PRs #288–#292.
"""
