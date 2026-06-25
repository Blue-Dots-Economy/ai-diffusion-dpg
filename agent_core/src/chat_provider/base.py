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

    def __init__(self, message: str, error_type: str | None = None, error_message: str | None = None) -> None:
        """Initialise ProviderAPIError.

        Args:
            message: The raw error message string.
            error_type: The categorized error type (e.g. rate_limit, timeout, safety_blocked).
            error_message: The detailed, original error message string.
        """
        super().__init__(message)
        self.error_type = error_type
        self.error_message = error_message


class ToolUseRequested(Exception):
    """Streaming-only signal: model emitted tool_use blocks; caller executes and resumes.

    NOT a ChatProviderError — this is normal control flow for the
    streaming tool loop, not an exceptional condition.
    """

    def __init__(self, tool_calls: list[ToolUseBlock]) -> None:
        self.tool_calls = tool_calls
        names = ", ".join(tc.tool_name for tc in tool_calls)
        super().__init__(f"LLM requested tool use: {names}")



SAFE_MESSAGES: dict[str, str] = {
    "rate_limit": "We're receiving too many requests right now. Please wait a moment and try again.",
    "timeout": "We're having trouble connecting to the AI service right now. Please try again shortly.",
    "safety_blocked": "The request was blocked by the safety filters.",
    "recitation_blocked": "The request was blocked because it contained copyrighted or repetitive content.",
    "api_error": "We're having trouble connecting to the AI service right now. Please try again shortly.",
    "internal_server_error": "An unexpected server error occurred. Please try again shortly.",
    "empty_response": "We're having trouble connecting to the AI service right now. Please try again shortly.",
}

DEFAULT_SAFE_MESSAGE = "We're having trouble connecting to the AI service right now. Please try again shortly."


# ---------------------------------------------------------------------------
# ChatProviderBase
# ---------------------------------------------------------------------------


class ChatProviderBase(ABC):
    """Single-provider chat interface. Stateless across calls.

    Construction lives in chat_provider.build_chat_provider; this class
    is never instantiated directly outside its concrete subclasses.
    """

    capabilities: Capabilities  # set by every subclass

    @abstractmethod
    def call(self, request: ChatRequest) -> ChatResponse:
        """Synchronous single call.

        Returns ChatResponse — never raises for transient failures. On
        exhausted retries returns ChatResponse(stop_reason='error',
        content=[], usage=TokenUsage()).

        Raises:
            UnsupportedFeatureError: request uses a capability the
                provider lacks (or deployment config disabled).
            ProviderConfigError: provider was misconfigured at init.
            ValueError: request.messages is empty.
        """

    @abstractmethod
    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text deltas as they arrive.

        Yields text tokens. On exhausted retries the generator returns
        silently — matches today's stream_call() contract so consumers
        relying on graceful degradation don't break.

        Raises:
            ToolUseRequested: model emitted tool_use blocks; caller
                executes the tools and resumes by calling stream() again
                with the updated messages.
            UnsupportedFeatureError: same conditions as call(), plus
                output_format is forbidden on stream() for all providers.
        """
        if False:  # pragma: no cover — abstract; satisfy generator type
            yield ""

    @abstractmethod
    def get_active_model(self) -> str:
        """Name of the currently active model id."""

    # ------------------------------------------------------------------
    # Shared, non-abstract helpers
    # ------------------------------------------------------------------

    def _validate_request(self, request: ChatRequest, *, is_stream: bool) -> None:
        """Raise UnsupportedFeatureError if request needs capabilities we lack.

        Concrete providers call this at the top of call() and stream()
        with is_stream set appropriately. The output_format-on-stream
        rule is enforced here regardless of provider, per the spec
        (sync-only structured output).
        """
        caps = self.capabilities
        cls = type(self).__name__

        if request.tools and not caps.supports_tools:
            raise UnsupportedFeatureError(
                f"{cls} does not support tools; "
                f"remove the tools list or use a provider with supports_tools=True."
            )

        if request.output_format is not None:
            if is_stream:
                raise UnsupportedFeatureError(
                    f"{cls}: output_format is not supported on stream(); "
                    f"use call() for structured output."
                )
            if not caps.supports_structured_output:
                raise UnsupportedFeatureError(
                    f"{cls} does not support structured output; "
                    f"remove output_format or use a provider with "
                    f"supports_structured_output=True."
                )

        if (
            request.tool_choice not in ("auto", "none")
            and not caps.supports_force_tool_choice
        ):
            raise UnsupportedFeatureError(
                f"{cls} does not support forced tool_choice; "
                f"set tool_choice to 'auto' or 'none'."
            )

        if request.system is not None:
            for block in request.system.blocks:
                if block.cache_hint and not caps.supports_prompt_cache:
                    raise UnsupportedFeatureError(
                        f"{cls} does not support prompt caching; "
                        f"remove cache_hint from system blocks."
                    )

        for msg in request.messages:
            for block in msg.content:
                if block.type == "image" and not caps.supports_image_input:
                    raise UnsupportedFeatureError(
                        f"{cls} does not support image input."
                    )
                if getattr(block, "type", "") == "audio" and not caps.supports_audio_input:
                    raise UnsupportedFeatureError(
                        f"{cls} does not support audio input."
                    )
                if (
                    block.type == "text"
                    and block.cache_hint
                    and not caps.supports_prompt_cache
                ):
                    raise UnsupportedFeatureError(
                        f"{cls} does not support prompt caching; "
                        f"remove cache_hint from message blocks."
                    )
