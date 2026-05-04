"""OpenAIChatProvider — only file in agent_core that imports `openai`.

Translates neutral chat_provider types to/from OpenAI Chat Completions
SDK shapes. Mirrors the structure of anthropic_provider.py: capabilities
declared on the class, init validates required config, _to_wire and
_from_wire handle every translation, retry loops live in private
helpers.

OpenAI does not currently report cache hit/miss information through the
SDK, so TokenUsage.cache_read_tokens / cache_creation_tokens stay None
to preserve the "None means not supported" contract.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import openai
from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderConfigError,
)
from src.chat_provider.metrics import record_call_metrics
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    OutputFormat,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


_DEFAULT_MAX_TOKENS = 4096


def _safe_int(value) -> int:
    """Coerce a possibly-missing usage field to int."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed.

    Caught only inside OpenAIChatProvider.call() / .stream() to
    transition into the error-response path. Never escapes.
    """


class OpenAIChatProvider(ChatProviderBase):
    """OpenAI Chat Completions implementation of ChatProviderBase.

    Required config keys:
        primary_model    (str) e.g. "gpt-4o-2024-08-06"
        timeout_ms       (int)
        retry_attempts   (int) min 1

    Optional:
        retry_backoff_seconds  list[float]  [0, 0.5, 1.0]
        features.prompt_cache  bool         False  (capability default)
        features.streaming     bool         True
        features.image_input   bool         True
    """

    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )

    def __init__(self, config: dict) -> None:
        """Initialise the provider, validate required config, and build SDK clients.

        Args:
            config: Provider configuration dict. Must contain primary_model,
                timeout_ms, and retry_attempts.

        Raises:
            ProviderConfigError: If any required config key is missing or invalid.
        """
        if not config:
            raise ProviderConfigError(
                "OpenAIChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid OpenAI model id (e.g. gpt-4o-2024-08-06)."
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
        self._client = openai.OpenAI()
        self._async_client = openai.AsyncOpenAI()

    # ------------------------------------------------------------------
    # Public ChatProviderBase methods (filled in subsequent tasks)
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Send a synchronous chat request to the OpenAI API.

        Args:
            request: The chat request containing messages, tools, and config.

        Returns:
            ChatResponse with content blocks and token usage.

        Raises:
            NotImplementedError: Until Task 7 is implemented.
        """
        raise NotImplementedError("Implemented in Task 7")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response from the OpenAI API.

        Args:
            request: The chat request containing messages, tools, and config.
            abort_event: Optional asyncio Event; when set, streaming stops early.

        Yields:
            Text chunks as they arrive from the API.

        Raises:
            NotImplementedError: Until Task 8 is implemented.
        """
        raise NotImplementedError("Implemented in Task 8")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        """Return the model identifier currently active for API calls.

        Returns:
            The active model string (primary or fallback after a switch).
        """
        return self._active_model
