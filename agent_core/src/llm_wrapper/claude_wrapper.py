"""
agent_core/llm_wrapper/claude_wrapper.py — adapter shim.

Until PR5 deletes this package, callers continue to import
ClaudeLLMWrapper from here. This file now delegates every call to
agent_core.src.chat_provider.AnthropicChatProvider, translating the
legacy Anthropic-shaped inputs (list[dict] messages, str|list system)
into neutral ChatRequest, and translating ChatResponse back to the
legacy LLMResponse / ToolCall types. Fallback model behaviour is
implemented here by holding two AnthropicChatProvider instances; the
new provider has no fallback of its own.

This file is removed in PR5 (#292) once every caller has migrated to
ChatProviderBase directly (PRs #290 and #291).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Optional

from src.chat_provider.anthropic_provider import AnthropicChatProvider, _RetryableExhausted
from src.chat_provider.base import ToolUseRequested as _ChatToolUseRequested
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from src.exceptions import ToolUseRequested
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compat re-exports expected by test_llm_wrapper.py
# ---------------------------------------------------------------------------

# Minimum size below which caching is not applied (lifted from legacy).
_CACHE_MIN_CHARS = 3000

# Default max tokens for streaming (lifted from legacy).
_DEFAULT_MAX_TOKENS = 4096


def _safe_int(value) -> int:
    """Return value as int, defaulting to 0 on non-numeric (e.g. None, Mock).

    Rejects arbitrary objects that happen to define ``__int__`` (notably
    MagicMock) so tests that don't bother to set cache-usage fields can't
    accidentally propagate sentinel values into metrics.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


class ClaudeLLMWrapper(LLMWrapperBase):
    """Legacy adapter — delegates to AnthropicChatProvider.

    Holds two providers internally:
        _primary  — calls always start here.
        _fallback — used after the primary returns stop_reason="error",
                    mirroring today's behaviour. The flip is permanent
                    for the life of the wrapper.
    """

    def __init__(self, config: dict) -> None:
        """Initialise primary and fallback AnthropicChatProvider instances.

        Args:
            config: Runtime configuration dict (same shape as the legacy wrapper).

        Raises:
            ValueError: If config is empty or missing primary_model/fallback_model.
        """
        if not config:
            raise ValueError("ClaudeLLMWrapper requires a non-empty config dict")
        if not config.get("primary_model"):
            raise ValueError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid Claude model id."
            )
        if not config.get("fallback_model"):
            raise ValueError(
                "agent.fallback_model is not set. Ensure your domain config has "
                "a valid Claude model id."
            )

        primary_cfg = {**config, "primary_model": config["primary_model"]}
        fallback_cfg = {**config, "primary_model": config["fallback_model"]}
        self._primary = AnthropicChatProvider(primary_cfg)
        self._fallback = AnthropicChatProvider(fallback_cfg)
        self._active = self._primary

    # ------------------------------------------------------------------
    # Public legacy interface
    # ------------------------------------------------------------------

    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | list[dict],
        model_override: Optional[str] = None,
        output_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Execute an LLM call with automatic retries and fallback model switching.

        Args:
            messages: List of message dicts with role and content.
            tools: List of tool definitions the LLM can call.
            system: System prompt as a string or list of Anthropic content blocks.
            model_override: Optional model ID to override the active model.
            output_format: Optional structured output format dict.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            ValueError: If messages is empty.
        """
        if not messages:
            raise ValueError("messages must not be empty")
        request = self._build_request(messages, tools, system, output_format)

        if model_override is not None:
            override_cfg = {**self._provider_config_snapshot(), "primary_model": model_override}
            override_provider = AnthropicChatProvider(override_cfg)
            response = override_provider.call(request)
            return self._to_legacy_response(response)

        # Call _call_with_retry directly so we can distinguish retry exhaustion
        # (→ trigger fallback) from non-retryable errors (→ return error immediately).
        try:
            response = self._active._call_with_retry(request)
            return self._to_legacy_response(response)
        except _RetryableExhausted:
            if self._active is not self._primary:
                return LLMResponse(content=None, stop_reason="error")
            logger.warning(
                "llm_wrapper.fallback_triggered",
                extra={
                    "operation": "llm_wrapper.call",
                    "primary_model": self._primary.get_active_model(),
                },
            )
            self._active = self._fallback
            try:
                response = self._active._call_with_retry(request)
                return self._to_legacy_response(response)
            except _RetryableExhausted:
                return LLMResponse(content=None, stop_reason="error")

    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | list[dict] | None = None,
        model_override: str | None = None,
        max_tokens: int | None = None,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream text tokens from the Anthropic API.

        Same retry + fallback logic as call(). Yields raw text tokens.
        Raises ToolUseRequested if the LLM returns a tool_use stop reason.

        Args:
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions. None or empty for no tools.
            system: System prompt as string or list of Anthropic content blocks.
            model_override: Optional model ID override.
            max_tokens: Optional cap on response tokens. Defaults to 4096.
            abort_event: Optional asyncio.Event. When set during streaming,
                the chunk loop exits cleanly between chunks.

        Yields:
            str: Individual text tokens.

        Raises:
            ToolUseRequested: If the LLM requests tool use.
            ValueError: If messages is empty.
        """
        if not messages:
            raise ValueError("messages must not be empty")

        effective_max_tokens = max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS
        request = self._build_request(
            messages, tools or [], system or "", output_format=None, max_tokens=effective_max_tokens
        )

        if model_override is not None:
            override_cfg = {**self._provider_config_snapshot(), "primary_model": model_override}
            active = AnthropicChatProvider(override_cfg)
        else:
            active = self._active

        # Use _stream_with_retry directly to distinguish retry exhaustion
        # (→ trigger fallback) from non-retryable silent exits.
        try:
            async for token in active._stream_with_retry(request, abort_event):
                yield token
            return  # completed without tool_use or exhaustion
        except _ChatToolUseRequested as exc:
            legacy_calls = [
                ToolCall(
                    tool_name=tu.tool_name,
                    tool_use_id=tu.tool_use_id,
                    input_params=tu.input,
                )
                for tu in exc.tool_calls
            ]
            raise ToolUseRequested(legacy_calls)
        except _RetryableExhausted:
            # Primary exhausted all retries on transient errors; try fallback.
            if model_override is not None or active is self._fallback:
                return
            logger.warning(
                "llm_wrapper.stream_fallback_triggered",
                extra={
                    "operation": "llm_wrapper.stream_call",
                    "primary_model": self._primary.get_active_model(),
                },
            )
            self._active = self._fallback
            try:
                async for token in self._fallback._stream_with_retry(request, abort_event):
                    yield token
            except _ChatToolUseRequested as exc:
                legacy_calls = [
                    ToolCall(
                        tool_name=tu.tool_name,
                        tool_use_id=tu.tool_use_id,
                        input_params=tu.input,
                    )
                    for tu in exc.tool_calls
                ]
                raise ToolUseRequested(legacy_calls)
            except _RetryableExhausted:
                return

    def get_active_model(self) -> str:
        """Return the currently active model identifier.

        Returns:
            The model id string currently in use.
        """
        return self._active.get_active_model()

    # ------------------------------------------------------------------
    # Legacy static helpers kept for backward compat (tested directly)
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_system_for_caching(system):
        """Wrap a system prompt string as an Anthropic cache-control block.

        When the caller has already built a list of content blocks, it is
        returned unchanged. Strings shorter than _CACHE_MIN_CHARS are
        returned as-is. This is kept as a static method for backward
        compatibility with tests that call it directly.

        Args:
            system: System prompt string or pre-formed list.

        Returns:
            The original value, or a list with a single cache-control block.
        """
        if not system:
            return system
        if not isinstance(system, str):
            return system  # already structured; trust the caller
        if len(system) < _CACHE_MIN_CHARS:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _provider_config_snapshot(self) -> dict:
        """Return a minimal config dict from the primary provider's state.

        Returns:
            Dict with the keys AnthropicChatProvider.__init__ requires.
        """
        return {
            "primary_model": self._primary.get_active_model(),
            "timeout_ms": int(self._primary._timeout_s * 1000),
            "retry_attempts": self._primary._max_attempts,
            "retry_backoff_seconds": self._primary._backoff_seconds,
            "features": self._primary._features,
        }

    def _build_request(
        self,
        messages: list[dict],
        tools: list[dict],
        system,
        output_format: Optional[dict],
        max_tokens: int | None = None,
    ) -> ChatRequest:
        """Translate legacy Anthropic-shaped inputs into a neutral ChatRequest.

        Args:
            messages: Legacy message dicts.
            tools: Legacy tool definition dicts.
            system: System prompt string or list of Anthropic content blocks.
            output_format: Optional structured output format dict.
            max_tokens: Optional token cap; defaults to ChatRequest default (4096).

        Returns:
            Neutral ChatRequest ready for AnthropicChatProvider.
        """
        neutral_messages = [self._message_from_legacy(m) for m in messages]
        neutral_system = self._system_from_legacy(system)
        neutral_tools = [self._tool_from_legacy(t) for t in tools]
        of = None
        if output_format is not None:
            of = OutputFormat(schema=output_format.get("schema", output_format))
        kwargs: dict = dict(
            messages=neutral_messages,
            system=neutral_system,
            tools=neutral_tools,
            tool_choice="auto",
            output_format=of,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatRequest(**kwargs)

    @staticmethod
    def _message_from_legacy(msg: dict) -> Message:
        """Translate a legacy Anthropic message dict into a neutral Message.

        Args:
            msg: Dict with role and content (string or list of blocks).

        Returns:
            Neutral Message with appropriate ContentBlock types.
        """
        role = msg["role"]
        raw_content = msg["content"]
        blocks: list = []
        if isinstance(raw_content, str):
            blocks.append(TextBlock(text=raw_content))
        else:
            for b in raw_content:
                if b.get("type") == "text":
                    blocks.append(
                        TextBlock(
                            text=b["text"],
                            cache_hint=("session" if b.get("cache_control") else None),
                        )
                    )
                elif b.get("type") == "tool_use":
                    blocks.append(
                        ToolUseBlock(
                            tool_use_id=b["id"],
                            tool_name=b["name"],
                            input=b.get("input", {}),
                        )
                    )
                elif b.get("type") == "tool_result":
                    blocks.append(
                        ToolResultBlock(
                            tool_use_id=b["tool_use_id"],
                            content=b["content"]
                            if isinstance(b["content"], str)
                            else [TextBlock(text=p["text"]) for p in b["content"]],
                            is_error=b.get("is_error", False),
                        )
                    )
                elif b.get("type") == "image":
                    continue  # images dropped at the adapter boundary
        return Message(role=role, content=blocks)

    @staticmethod
    def _system_from_legacy(system) -> SystemPrompt | None:
        """Translate a legacy system prompt into a neutral SystemPrompt.

        Args:
            system: System prompt as a string or list of Anthropic content blocks.

        Returns:
            Neutral SystemPrompt, or None if system is empty/None.
        """
        if not system:
            return None
        if isinstance(system, str):
            return SystemPrompt(blocks=[TextBlock(text=system, cache_hint="session")])
        # Already a list of Anthropic-shaped blocks.
        blocks: list[TextBlock] = []
        for b in system:
            blocks.append(
                TextBlock(
                    text=b.get("text", ""),
                    cache_hint=("session" if b.get("cache_control") else None),
                )
            )
        return SystemPrompt(blocks=blocks)

    @staticmethod
    def _tool_from_legacy(t: dict) -> ToolDefinition:
        """Translate a legacy Anthropic tool dict into a neutral ToolDefinition.

        Args:
            t: Dict with name, description, and input_schema keys.

        Returns:
            Neutral ToolDefinition.
        """
        return ToolDefinition(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("input_schema", {}),
        )

    @staticmethod
    def _to_legacy_response(resp: ChatResponse) -> LLMResponse:
        """Translate a neutral ChatResponse into a legacy LLMResponse.

        Args:
            resp: ChatResponse from AnthropicChatProvider.

        Returns:
            LLMResponse with the same content, tool calls, and token counts.
        """
        text_content: Optional[str] = None
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text" and text_content is None:
                text_content = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        tool_name=block.tool_name,
                        tool_use_id=block.tool_use_id,
                        input_params=block.input,
                    )
                )
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            model_used=resp.model_used,
            input_tokens=resp.usage.input_tokens or 0,
            output_tokens=resp.usage.output_tokens or 0,
            cache_read_input_tokens=resp.usage.cache_read_tokens or 0,
            cache_creation_input_tokens=resp.usage.cache_creation_tokens or 0,
        )
