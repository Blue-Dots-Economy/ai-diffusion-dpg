"""
agent_core/llm_wrapper/claude_wrapper.py

Concrete LLM wrapper for the Anthropic Claude API.
THIS IS THE ONLY FILE IN THE ENTIRE CODEBASE THAT IMPORTS OR CALLS anthropic SDK.

Responsibilities:
- Executes LLM calls with an explicit timeout on every request.
- Retries transient failures (rate limits, timeouts) with exponential backoff.
- Switches to the fallback model after primary model exhaustion.
- Emits a structured log entry for every call attempt.
- Never raises — all failures are returned as LLMResponse(stop_reason="error").
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import anthropic

from src.exceptions import LLMCallError, LLMFallbackError
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class _RetryableExhausted(Exception):
    """Internal sentinel: all retry attempts on transient errors were consumed.
    Raised only from _call_with_retry; caught only in call() to trigger fallback.
    Never surfaces outside ClaudeLLMWrapper.
    """


class ClaudeLLMWrapper(LLMWrapperBase):
    """
    Anthropic Claude implementation of LLMWrapperBase.

    Reads all runtime values from the injected config dict — nothing hardcoded.
    Expected config keys:
        primary_model   (str)   Claude model ID for primary calls
        fallback_model  (str)   Claude model ID used after primary exhaustion
        timeout_ms      (int)   Per-request timeout in milliseconds
        retry_attempts  (int)   Max attempts before switching to fallback (min 1)
    """

    def __init__(self, config: dict) -> None:
        if not config:
            raise ValueError("ClaudeLLMWrapper requires a non-empty config dict")

        primary_model = config.get("primary_model", "")
        fallback_model = config.get("fallback_model", "")
        if not primary_model:
            raise ValueError(
                "agent.primary_model is not set. Ensure your domain config has a valid "
                "Claude model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )
        if not fallback_model:
            raise ValueError(
                "agent.fallback_model is not set. Ensure your domain config has a valid "
                "Claude model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )

        self._primary_model: str = primary_model
        self._fallback_model: str = fallback_model
        self._timeout_s: float = config["timeout_ms"] / 1000
        self._max_attempts: int = max(1, config["retry_attempts"])
        self._backoff_seconds: list[float] = config.get("retry_backoff_seconds", [0, 0.5, 1.0])

        self._active_model: str = self._primary_model
        self._client = anthropic.Anthropic()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        model_override: Optional[str] = None,
        output_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Execute an LLM call with automatic retries and fallback model switching.

        Args:
            messages: List of message dicts with role and content.
            tools: List of tool definitions the LLM can call.
            system: System prompt text.
            model_override: Optional model ID to override the active model.
            output_format: Optional structured output format dict for the response.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            ValueError: If messages is empty.
        """
        if not messages:
            raise ValueError("messages must not be empty")

        model = model_override or self._active_model

        try:
            return self._call_with_retry(model, messages, tools, system, output_format)
        except _RetryableExhausted:
            if model != self._primary_model:
                # Already on fallback — nothing left to try
                return LLMResponse(content=None, stop_reason="error")
            logger.warning(
                "llm_wrapper.fallback_triggered",
                extra={"operation": "llm_wrapper.call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                return self._call_with_retry(self._fallback_model, messages, tools, system, output_format)
            except _RetryableExhausted:
                return LLMResponse(content=None, stop_reason="error")

    def get_active_model(self) -> str:
        return self._active_model

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_with_retry(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        system: str,
        output_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Internal retry loop for a single LLM call with exponential backoff.

        Args:
            model: Model ID to use for this call.
            messages: List of message dicts with role and content.
            tools: List of tool definitions the LLM can call.
            system: System prompt text.
            output_format: Optional structured output format dict for the response.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            _RetryableExhausted: If all retry attempts are exhausted on transient errors.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
            if delay > 0:
                time.sleep(delay)

            start = time.time()
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": 4096,
                    "system": system,
                    "messages": messages,
                    "timeout": self._timeout_s,
                }
                if tools:
                    kwargs["tools"] = tools
                if output_format:
                    kwargs["response_format"] = output_format

                raw = self._client.messages.create(**kwargs)
                response = self._parse_response(raw, model)

                logger.info(
                    "llm_wrapper.call",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "success",
                        "model": model,
                        "attempt": attempt + 1,
                        "latency_ms": int((time.time() - start) * 1000),
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                    },
                )
                return response

            except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "llm_wrapper.retryable_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

            except anthropic.APIError as e:
                logger.error(
                    "llm_wrapper.api_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return LLMResponse(content=None, stop_reason="error")

            except Exception as e:
                # Catches SDK errors raised before the HTTP request fires —
                # e.g. TypeError from missing API key during header validation.
                # These are non-retryable configuration errors.
                logger.error(
                    "llm_wrapper.unexpected_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return LLMResponse(content=None, stop_reason="error")

        logger.error(
            "llm_wrapper.exhausted",
            extra={
                "operation": "llm_wrapper.call",
                "status": "failure",
                "model": model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(f"All {self._max_attempts} retry attempts exhausted for model {model}")

    def _switch_to_fallback(self) -> None:
        self._active_model = self._fallback_model

    def _parse_response(self, raw: anthropic.types.Message, model: str) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        text_content: Optional[str] = None

        for block in raw.content:
            if block.type == "text":
                text_content = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        tool_name=block.name,
                        tool_use_id=block.id,
                        input_params=block.input,
                    )
                )

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=raw.stop_reason or "end_turn",
            model_used=model,
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
        )
