"""
agent_core/llm_wrapper/ollama_wrapper.py

Concrete LLM wrapper for Ollama local models.
Implements LLMWrapperBase methods directly using the 
OpenAI-compatible /v1 endpoint provided by Ollama natively.

Requires:
    agent.primary_model to match Ollama tags (e.g. "llama3.1:8b")
    agent.ollama_base_url (e.g. "http://localhost:11434")

Does NOT require OPENAI_API_KEY or ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Optional

import openai
from opentelemetry import trace as otel_trace

from src.exceptions import ToolUseRequested
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class _RetryableExhausted(Exception):
    """Internal sentinel: all retry attempts on transient errors were consumed."""


class OllamaLLMWrapper(LLMWrapperBase):
    """
    Ollama implementation of LLMWrapperBase.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the wrapper from the agent config section."""
        if not config:
            raise ValueError("OllamaLLMWrapper requires a non-empty config dict")

        primary_model = config.get("primary_model", "")
        fallback_model = config.get("fallback_model", "")
        if not primary_model:
            raise ValueError("agent.primary_model is not set for ollama provider.")
        if not fallback_model:
            raise ValueError("agent.fallback_model is not set for ollama provider.")

        self._primary_model: str = primary_model
        self._fallback_model: str = fallback_model
        self._timeout_s: float = config.get("timeout_ms", 30000) / 1000
        self._max_attempts: int = max(1, config.get("retry_attempts", 3))
        self._backoff_seconds: list[float] = config.get("retry_backoff_seconds", [0, 0.5, 1.0])

        self._active_model: str = self._primary_model

        ollama_base_url = config.get("ollama_base_url", "http://localhost:11434").rstrip("/")
        if not ollama_base_url.endswith("/v1"):
            ollama_base_url = f"{ollama_base_url}/v1"
        
        client_kwargs: dict = {
            "api_key": "ollama",
            "base_url": ollama_base_url
        }

        self._client = openai.OpenAI(**client_kwargs)
        self._async_client = openai.AsyncOpenAI(**client_kwargs)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        """Execute an LLM call with automatic retries and fallback model switching."""
        if not messages:
            raise ValueError("messages must not be empty")

        model = model_override or self._active_model

        try:
            return self._call_with_retry(model, messages, tools, system)
        except _RetryableExhausted:
            if model != self._primary_model:
                return LLMResponse(content=None, stop_reason="error")
            logger.warning(
                "llm_wrapper.fallback_triggered",
                extra={"operation": "llm_wrapper.call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                return self._call_with_retry(self._fallback_model, messages, tools, system)
            except _RetryableExhausted:
                return LLMResponse(content=None, stop_reason="error")

    def get_active_model(self) -> str:
        """Return the name of the currently active model."""
        return self._active_model

    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        model_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream text tokens from the Ollama API."""
        if not messages:
            raise ValueError("messages must not be empty")

        model = model_override or self._active_model

        try:
            async for token in self._stream_with_retry(model, messages, tools, system):
                yield token
        except _RetryableExhausted:
            if model != self._primary_model:
                return
            logger.warning(
                "llm_wrapper.stream_fallback_triggered",
                extra={"operation": "llm_wrapper.stream_call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                async for token in self._stream_with_retry(self._fallback_model, messages, tools, system):
                    yield token
            except _RetryableExhausted:
                return

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_with_retry(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> LLMResponse:
        """Internal retry loop for a single LLM call with exponential backoff."""
        last_error: Optional[Exception] = None
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else []

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
            if delay > 0:
                time.sleep(delay)

            start = time.time()
            _tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": 4096,
                    "messages": oai_messages,
                    "timeout": self._timeout_s,
                }
                if oai_tools:
                    kwargs["tools"] = oai_tools

                with _tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.model", model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    raw = self._client.chat.completions.create(**kwargs)
                    logger.debug(f"[OllamaLLMWrapper] RAW RESPONSE: {raw}")
                    response = self._parse_response(raw, model)
                    span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)

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

            except (openai.RateLimitError, openai.APITimeoutError) as e:
                last_error = e
                self._log_error("retryable_error", e, "call", model, attempt + 1, start)

            except openai.APIError as e:
                self._log_error("api_error", e, "call", model, attempt + 1, start)
                return LLMResponse(content=None, stop_reason="error")

            except Exception as e:
                self._log_error("unexpected_error", e, "call", model, attempt + 1, start)
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

    async def _stream_with_retry(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
    ) -> AsyncGenerator[str, None]:
        """Internal retry loop for streaming with exponential backoff."""
        last_error: Exception | None = None
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else []

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
            if delay > 0:
                await asyncio.sleep(delay)

            start = time.time()
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": 4096,
                    "messages": oai_messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "timeout": self._timeout_s,
                }
                if oai_tools:
                    kwargs["tools"] = oai_tools

                accumulated_tool_calls: dict[int, dict] = {}
                finish_reason: str | None = None
                input_tokens = 0
                output_tokens = 0

                async for chunk in await self._async_client.chat.completions.create(**kwargs):
                    if not chunk.choices:
                        if chunk.usage:
                            input_tokens = chunk.usage.prompt_tokens
                            output_tokens = chunk.usage.completion_tokens
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    if hasattr(delta, "content") and delta.content:
                        yield delta.content

                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            idx = tc_chunk.index
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc_chunk.id:
                                accumulated_tool_calls[idx]["id"] += tc_chunk.id
                            if hasattr(tc_chunk, "function") and tc_chunk.function:
                                if hasattr(tc_chunk.function, "name") and tc_chunk.function.name:
                                    accumulated_tool_calls[idx]["name"] += tc_chunk.function.name
                                if hasattr(tc_chunk.function, "arguments") and tc_chunk.function.arguments:
                                    accumulated_tool_calls[idx]["arguments"] += tc_chunk.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                        logger.debug(f"[OllamaLLMWrapper] STREAM FINISH REASON: {finish_reason}")

                logger.info(
                    "llm_wrapper.stream_call",
                    extra={
                        "operation": "llm_wrapper.stream_call",
                        "status": "success",
                        "model": model,
                        "attempt": attempt + 1,
                        "latency_ms": int((time.time() - start) * 1000),
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "finish_reason": finish_reason,
                    },
                )

                if finish_reason in ["tool_calls", "stop"] and accumulated_tool_calls:
                    logger.debug(f"[OllamaLLMWrapper] ACCUMULATED TOOL CALLS: {accumulated_tool_calls}")
                    tool_calls: list[ToolCall] = []
                    for idx in sorted(accumulated_tool_calls.keys()):
                        tc = accumulated_tool_calls[idx]
                        try:
                            input_params = json.loads(tc["arguments"])
                        except (json.JSONDecodeError, TypeError):
                            input_params = {}
                        tool_calls.append(
                            ToolCall(
                                tool_name=tc["name"],
                                tool_use_id=tc["id"],
                                input_params=input_params,
                            )
                        )
                    raise ToolUseRequested(tool_calls)

                return

            except ToolUseRequested:
                raise

            except (openai.RateLimitError, openai.APITimeoutError) as e:
                last_error = e
                self._log_error("retryable_error", e, "stream_call", model, attempt + 1, start)

            except openai.APIError as e:
                self._log_error("api_error", e, "stream_call", model, attempt + 1, start)
                return

            except Exception as e:
                self._log_error("unexpected_error", e, "stream_call", model, attempt + 1, start)
                return

        logger.error(
            "llm_wrapper.stream_exhausted",
            extra={
                "operation": "llm_wrapper.stream_call",
                "status": "failure",
                "model": model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(f"All {self._max_attempts} stream retry attempts exhausted for model {model}")

    def _switch_to_fallback(self) -> None:
        self._active_model = self._fallback_model

    def _log_error(self, error_type: str, e: Exception, operation: str, model: str, attempt: int, start_time: float) -> None:
        latency_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e) if isinstance(e, openai.APIError) else f"{type(e).__name__}: {e}"
        
        extra = {
            "operation": f"llm_wrapper.{operation}",
            "status": "failure",
            "model": model,
            "attempt": attempt,
            "error": error_msg,
            "latency_ms": latency_ms,
        }
        
        prefix = "stream_" if operation == "stream_call" else ""
        log_name = f"llm_wrapper.{prefix}{error_type}"
        
        if error_type == "retryable_error":
            logger.warning(log_name, extra=extra)
        else:
            logger.error(log_name, extra=extra)

    def _parse_response(self, raw, model: str) -> LLMResponse:
        choice = raw.choices[0]
        message = choice.message

        text_content: Optional[str] = message.content
        tool_calls: list[ToolCall] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    input_params = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    input_params = {}
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.function.name,
                        tool_use_id=tc.id,
                        input_params=input_params,
                    )
                )

        stop_reason = self._map_stop_reason(choice.finish_reason)
        if tool_calls:
            stop_reason = "tool_use"

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model_used=model,
            input_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            output_tokens=raw.usage.completion_tokens if raw.usage else 0,
        )

    def _map_stop_reason(self, finish_reason: str | None) -> str:
        mapping: dict[str, str] = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        return mapping.get(finish_reason or "", "end_turn")

    def _convert_messages(self, messages: list[dict], system: str | None) -> list[dict]:
        result: list[dict] = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str):
                result.append({"role": role, "content": content})
            elif not isinstance(content, list):
                result.append({"role": role, "content": str(content)})
            elif role == "assistant":
                result.append(self._convert_assistant_blocks(content))
            elif role == "user":
                result.extend(self._convert_user_blocks(content))

        return result

    def _convert_assistant_blocks(self, content: list) -> dict:
        text_parts: list[str] = []
        oai_tool_calls: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                oai_tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
        oai_msg: dict = {
            "role": "assistant",
            "content": " ".join(text_parts) if text_parts else None,
        }
        if oai_tool_calls:
            oai_msg["tool_calls"] = oai_tool_calls
        return oai_msg

    def _convert_user_blocks(self, content: list) -> list[dict]:
        result: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "tool_result":
                tool_content = block.get("content", "")
                if isinstance(tool_content, list):
                    text = " ".join(b.get("text", "") for b in tool_content if isinstance(b, dict) and b.get("type") == "text")
                else:
                    text = str(tool_content) if tool_content is not None else ""
                result.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": text,
                })
            elif block_type == "text":
                result.append({"role": "user", "content": block.get("text", "")})
        return result

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
            for tool in tools
        ]
