"""ChatProviderBase, Capabilities, and chat_provider error types.

Provider implementations subclass ChatProviderBase and declare a
class-level `capabilities` attribute. Callers depend only on this base
class — never on a concrete provider class.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from src.chat_provider.types import ChatRequest, ChatResponse, ToolUseBlock


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capabilities:
    """Static feature flags declared per provider class.

    Read at provider __init__. YAML configuration may tighten a True
    capability to False for a deployment, but cannot widen — a provider
    that lacks a capability cannot be configured to support it.
    """

    supports_tools: bool
    supports_streaming: bool
    supports_prompt_cache: bool
    supports_image_input: bool
    supports_audio_input: bool
    supports_structured_output: bool
    supports_force_tool_choice: bool


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ChatProviderError(Exception):
    """Base for all chat_provider failures the caller should programmatically handle."""


class UnsupportedFeatureError(ChatProviderError):
    """Raised when a request uses a feature the active provider lacks
    (either intrinsically or because deployment config disabled it).
    """


class ProviderConfigError(ChatProviderError):
    """Raised at provider init when YAML config is invalid or incomplete."""


class ProviderAPIError(ChatProviderError):
    """Non-retryable provider-side error (auth failure, persistent 4xx/5xx)."""


class ToolUseRequested(Exception):
    """Streaming-only signal: model emitted tool_use blocks; caller executes and resumes.

    NOT a ChatProviderError — this is normal control flow for the
    streaming tool loop, not an exceptional condition.
    """

    def __init__(self, tool_calls: list[ToolUseBlock]) -> None:
        self.tool_calls = tool_calls
        names = ", ".join(tc.tool_name for tc in tool_calls)
        super().__init__(f"LLM requested tool use: {names}")
