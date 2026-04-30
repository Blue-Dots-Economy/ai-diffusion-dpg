"""AnthropicChatProvider — only file in agent_core that imports `anthropic`.

Translates neutral chat_provider types to/from Anthropic SDK shapes.
Lifts the retry/backoff/timeout/OTel scaffolding from the legacy
agent_core/src/llm_wrapper/claude_wrapper.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderConfigError,
)
from src.chat_provider.types import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


# Minimum size below which we skip cache_control. Anthropic ignores
# cache markers on prompts shorter than ~1024 tokens; ~4 chars/token is
# a conservative English estimate. Lifted unchanged from legacy
# claude_wrapper.py:113.
_CACHE_MIN_CHARS = 3000

# Default response-token ceiling when ChatRequest.max_tokens is missing
# (it's not, since the model has a default of 4096, but we keep the
# constant so the provider can override consistently if needed).
_DEFAULT_MAX_TOKENS = 4096


class AnthropicChatProvider(ChatProviderBase):
    """Anthropic implementation of ChatProviderBase.

    Reads runtime config from a dict; nothing hardcoded.

    Required keys:
        primary_model    (str) Claude model id
        timeout_ms       (int) per-request timeout in ms
        retry_attempts   (int) attempts before giving up (min 1)

    Optional keys (defaults shown):
        retry_backoff_seconds  list[float]  [0, 0.5, 1.0]
        features.prompt_cache  bool         True  (capability default)
        features.streaming     bool         True
        features.image_input   bool         True
    """

    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )

    def __init__(self, config: dict) -> None:
        """Initialise the provider from a config dict.

        Args:
            config: Runtime configuration dict with required and optional keys.

        Raises:
            ProviderConfigError: If required keys are missing or invalid.
        """
        if not config:
            raise ProviderConfigError(
                "AnthropicChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid Claude model id, or set CONFIG_FOLDER in .env.local "
                "to point at your domain configs folder."
            )
        if "timeout_ms" not in config:
            raise ProviderConfigError("agent.timeout_ms is required")
        if "retry_attempts" not in config:
            raise ProviderConfigError("agent.retry_attempts is required")

        self._primary_model: str = primary_model
        self._timeout_s: float = config["timeout_ms"] / 1000
        self._max_attempts: int = max(1, config["retry_attempts"])
        self._backoff_seconds: list[float] = config.get(
            "retry_backoff_seconds", [0, 0.5, 1.0]
        )

        # Effective per-deployment features (AND of capability and config).
        feats = dict(config.get("features") or {})
        self._features: dict[str, bool] = {
            "prompt_cache": bool(feats.get("prompt_cache", self.capabilities.supports_prompt_cache))
                            and self.capabilities.supports_prompt_cache,
            "streaming": bool(feats.get("streaming", self.capabilities.supports_streaming))
                         and self.capabilities.supports_streaming,
            "image_input": bool(feats.get("image_input", self.capabilities.supports_image_input))
                           and self.capabilities.supports_image_input,
        }

        self._active_model: str = self._primary_model
        self._client = anthropic.Anthropic()
        self._async_client = anthropic.AsyncAnthropic()

    # ------------------------------------------------------------------
    # Public ChatProviderBase methods (filled in subsequent tasks)
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a synchronous LLM call.

        Args:
            request: The chat request to send to the model.

        Returns:
            ChatResponse with the model's reply.

        Raises:
            NotImplementedError: Until implemented in Task 11.
        """
        raise NotImplementedError("Implemented in Task 11")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream an LLM response token by token.

        Args:
            request: The chat request to send to the model.
            abort_event: Optional event to signal early termination.

        Yields:
            str tokens as they arrive from the model.

        Raises:
            NotImplementedError: Until implemented in Task 12.
        """
        raise NotImplementedError("Implemented in Task 12")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        """Return the currently active model identifier.

        Returns:
            The model id string in use for this provider instance.
        """
        return self._active_model
