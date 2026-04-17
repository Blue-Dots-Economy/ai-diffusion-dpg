"""
agent_core/llm_wrapper/openai_wrapper.py

Concrete LLM wrapper for the OpenAI API and any OpenAI-compatible endpoint
(Azure OpenAI, Together AI, Fireworks, etc.).
THIS IS THE ONLY FILE IN THE ENTIRE CODEBASE THAT IMPORTS OR CALLS the openai SDK.

Responsibilities:
- Converts Anthropic-format messages and tool definitions to OpenAI chat format
  before every call; converts OpenAI responses back to LLMResponse.
- Executes LLM calls with an explicit timeout on every request.
- Retries transient failures (rate limits, timeouts) with exponential backoff.
- Switches to the fallback model after primary model exhaustion.
- Emits a structured log entry for every call attempt.
- Never raises — all failures are returned as LLMResponse(stop_reason="error").
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
    """Internal sentinel: all retry attempts on transient errors were consumed.
    Raised only from _call_with_retry; caught only in call() to trigger fallback.
    Never surfaces outside OpenAILLMWrapper.
    """


class OpenAILLMWrapper(LLMWrapperBase):
    """
    OpenAI (and OpenAI-compatible endpoint) implementation of LLMWrapperBase.

    Reads all runtime values from the injected config dict — nothing hardcoded.
    Expected config keys:
        primary_model   (str)           OpenAI model ID for primary calls (e.g. "gpt-4o")
        fallback_model  (str)           Model ID used after primary exhaustion
        timeout_ms      (int)           Per-request timeout in milliseconds
        retry_attempts  (int)           Max attempts before switching to fallback (min 1)
        llm_base_url    (str, optional) Custom base URL for OpenAI-compatible endpoints.
                                        Omit or leave empty to use api.openai.com.

    Auth:
        Reads OPENAI_API_KEY from the environment via the OpenAI SDK.
        Never hardcoded here.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the wrapper from the agent config section.

        Args:
            config: The agent config dict (config["agent"] from merged YAML).

        Raises:
            ValueError: If config is empty, or primary_model / fallback_model are missing.
        """
        if not config:
            raise ValueError("OpenAILLMWrapper requires a non-empty config dict")

        primary_model = config.get("primary_model", "")
        fallback_model = config.get("fallback_model", "")
        if not primary_model:
            raise ValueError(
                "agent.primary_model is not set. Ensure your domain config has a valid "
                "OpenAI model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )
        if not fallback_model:
            raise ValueError(
                "agent.fallback_model is not set. Ensure your domain config has a valid "
                "OpenAI model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )

        self._primary_model: str = primary_model
        self._fallback_model: str = fallback_model
        self._timeout_s: float = config["timeout_ms"] / 1000
        self._max_attempts: int = max(1, config["retry_attempts"])
        self._backoff_seconds: list[float] = config.get("retry_backoff_seconds", [0, 0.5, 1.0])

        self._active_model: str = self._primary_model

        client_kwargs: dict = {}
        base_url = config.get("llm_base_url", "")
        if base_url:
            client_kwargs["base_url"] = base_url

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
        """Execute an LLM call with automatic retries and fallback model switching.

        Converts Anthropic-format messages and tools to OpenAI format internally.
        Converts the OpenAI response back to LLMResponse before returning.

        Args:
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions in Anthropic format. Pass empty list if no tools.
            system: System prompt string.
            model_override: Optional model ID to override the active model.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.
            On failure, returns LLMResponse with stop_reason="error" and content=None.

        Raises:
            ValueError: If messages is empty.
        """
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
        """Return the name of the currently active model.

        Returns:
            The primary model under normal conditions; the fallback model after
            a primary exhaustion event.
        """
        return self._active_model

    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        model_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream text tokens from the OpenAI API.

        Same retry + fallback logic as call(). Yields raw text tokens.
        Raises ToolUseRequested if the LLM returns a tool_calls finish reason.

        Args:
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions in Anthropic format. None or empty for no tools.
            system: System prompt string.
            model_override: Optional model ID override.

        Yields:
            str: Individual text tokens.

        Raises:
            ToolUseRequested: If the LLM requests tool use.
            ValueError: If messages is empty.
        """
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
        """Internal retry loop for a single LLM call with exponential backoff.

        Args:
            model: Model ID to use for this call.
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions in Anthropic format.
            system: System prompt text.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            _RetryableExhausted: If all retry attempts are exhausted on transient errors.
        """
        last_error: Optional[Exception] = None
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else []

        for attempt in range(self._max_attempts):
            self._wait_before_retry(attempt)
            
            try:
                response = self._execute_call(model, oai_messages, oai_tools, attempt)
                if response:
                    return response
            except (openai.RateLimitError, openai.APITimeoutError) as e:
                last_error = e
                self._log_error("retryable_error", e, "call", model, attempt + 1, time.time())
            except (openai.APIError, Exception) as e:
                self._log_error("non_retryable_error", e, "call", model, attempt + 1, time.time())
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
        """Internal retry loop for streaming with exponential backoff.

        Streams text tokens and raises ToolUseRequested when the model requests
        tool use. Accumulates tool call argument chunks across stream events.

        Args:
            model: Model ID to use.
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions in Anthropic format.
            system: System prompt.

        Yields:
            str: Text tokens from the stream.

        Raises:
            _RetryableExhausted: If all retry attempts are exhausted.
            ToolUseRequested: If the LLM requests tool use.
        """
        last_error: Exception | None = None
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else []

        for attempt in range(self._max_attempts):
            await self._async_wait_before_retry(attempt)
            
            start = time.time()
            try:
                # Local state for this session
                accumulated_tool_calls: dict[int, dict] = {}
                state = {"finish_reason": None, "input_tokens": 0, "output_tokens": 0}

                async for chunk in await self._async_client.chat.completions.create(
                    model=model,
                    messages=oai_messages,
                    tools=oai_tools,
                    stream=True,
                    stream_options={"include_usage": True},
                    timeout=self._timeout_s,
                    max_tokens=4096
                ):
                    message = self._process_stream_chunk(chunk, accumulated_tool_calls, state)
                    if message:
                        yield message

                self._log_success("stream_call", model, attempt + 1, start, 
                                 in_tokens=state["input_tokens"], 
                                 out_tokens=state["output_tokens"], 
                                 finish_reason=state["finish_reason"])

                if state["finish_reason"] == "tool_calls" and accumulated_tool_calls:
                    raise ToolUseRequested(self._finalize_tool_calls(accumulated_tool_calls))
                return
            except ToolUseRequested:
                raise
            except (openai.RateLimitError, openai.APITimeoutError) as e:
                last_error = e
                self._log_error("retryable_error", e, "stream_call", model, attempt + 1, start)
            except (openai.APIError, Exception) as e:
                self._log_error("non_retryable_error", e, "stream_call", model, attempt + 1, start)
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

    def _wait_before_retry(self, attempt: int) -> None:
        """Apply exponential backoff delay before a retry attempt."""
        delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
        if delay > 0:
            time.sleep(delay)

    def _execute_call(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        attempt_idx: int,
    ) -> Optional[LLMResponse]:
        """Execute a single OpenAI API call tracked with OpenTelemetry."""
        start = time.time()
        tracer = otel_trace.get_tracer(__name__)
        
        kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
            "timeout": self._timeout_s,
        }
        if tools:
            kwargs["tools"] = tools

        with tracer.start_as_current_span("llm.call") as span:
            span.set_attribute("gen_ai.model", model)
            span.set_attribute("llm.attempt", attempt_idx + 1)
            raw = self._client.chat.completions.create(**kwargs)
            response = self._parse_response(raw, model)
            span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)

        self._log_success("call", model, attempt_idx + 1, start, response)
        return response

    def _log_success(
        self,
        op: str,
        model: str,
        attempt: int,
        start_time: float,
        resp: Optional[LLMResponse] = None,
        in_tokens: int = 0,
        out_tokens: int = 0,
        finish_reason: str = "stop"
    ) -> None:
        """Log a successful LLM call or stream session."""
        latency = int((time.time() - start_time) * 1000)
        tokens_in = resp.input_tokens if resp else in_tokens
        tokens_out = resp.output_tokens if resp else out_tokens
        
        logger.info(
            f"llm_wrapper.{op}",
            extra={
                "operation": f"llm_wrapper.{op}",
                "status": "success",
                "model": model,
                "attempt": attempt,
                "latency_ms": latency,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "finish_reason": finish_reason,
            },
        )

    def _accumulate_tool_call(self, tc_chunk: dict | object, accumulated: dict[int, dict]) -> None:
        """Helper to append streamed tool call chunks into a single tracked dictionary."""
        idx = tc_chunk.index
        if idx not in accumulated:
            accumulated[idx] = {"id": "", "name": "", "arguments": ""}
        if tc_chunk.id:
            accumulated[idx]["id"] += tc_chunk.id
        if tc_chunk.function:
            if tc_chunk.function.name:
                accumulated[idx]["name"] += tc_chunk.function.name
            if tc_chunk.function.arguments:
                accumulated[idx]["arguments"] += tc_chunk.function.arguments

    def _finalize_tool_calls(self, accumulated: dict[int, dict]) -> list[ToolCall]:
        """Convert accumulated string arguments back into JSON objects for ToolCall creation."""
        tool_calls: list[ToolCall] = []
        for idx in sorted(accumulated.keys()):
            tc = accumulated[idx]
            try:
                input_params = json.loads(tc["arguments"])
            except (json.JSONDecodeError, TypeError):
                input_params = {}
            tool_calls.append(ToolCall(
                tool_name=tc["name"],
                tool_use_id=tc["id"],
                input_params=input_params,
            ))
        return tool_calls

    async def _async_wait_before_retry(self, attempt: int) -> None:
        """Apply async delay before a retry attempt."""
        delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
        if delay > 0:
            await asyncio.sleep(delay)

    def _process_stream_chunk(
        self,
        chunk: object,
        accumulated: dict[int, dict],
        state: dict
    ) -> Optional[str]:
        """Update session state from a chunk; return content token if present."""
        if not chunk.choices:
            if chunk.usage:
                state["input_tokens"] = chunk.usage.prompt_tokens
                state["output_tokens"] = chunk.usage.completion_tokens
            return None

        choice = chunk.choices[0]
        delta = choice.delta

        if delta.tool_calls:
            for tc_chunk in delta.tool_calls:
                self._accumulate_tool_call(tc_chunk, accumulated)

        if choice.finish_reason:
            state["finish_reason"] = choice.finish_reason

        return delta.content

    def _log_error(self, error_type: str, e: Exception, operation: str, model: str, attempt: int, start_time: float) -> None:
        """Helper to uniformly log API errors."""
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
        """Convert an OpenAI chat completion response to LLMResponse.

        Args:
            raw: The raw openai.types.chat.ChatCompletion object.
            model: The model ID used for this call.

        Returns:
            LLMResponse with normalised fields.
        """
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

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=self._map_stop_reason(choice.finish_reason),
            model_used=model,
            input_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            output_tokens=raw.usage.completion_tokens if raw.usage else 0,
        )

    def _map_stop_reason(self, finish_reason: str | None) -> str:
        """Map an OpenAI finish_reason to the normalised LLMResponse stop_reason.

        Args:
            finish_reason: The finish_reason string from the OpenAI response.

        Returns:
            Normalised stop_reason: "end_turn" | "tool_use" | "max_tokens" | "end_turn" (default).
        """
        mapping: dict[str, str] = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        return mapping.get(finish_reason or "", "end_turn")

    def _convert_messages(self, messages: list[dict], system: str | None) -> list[dict]:
        """Convert Anthropic-format messages to OpenAI chat message format.

        Handles:
        - Simple string content for user/assistant turns.
        - Assistant messages with tool_use blocks → OpenAI tool_calls format.
        - User messages with tool_result blocks → OpenAI tool role messages.
        - System prompt injected as the first message with role "system".

        Args:
            messages: Conversation messages in Anthropic format.
            system: System prompt string. Prepended as a system role message if non-empty.

        Returns:
            List of message dicts in OpenAI chat format.
        """
        result: list[dict] = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            result.extend(self._convert_single_message(msg))
        return result

    def _convert_single_message(self, msg: dict) -> list[dict]:
        """Convert a single Anthropic-format message to OpenAI chat format(s)."""
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if isinstance(content, str):
            return [{"role": role, "content": content}]
        
        if not isinstance(content, list):
            return [{"role": role, "content": str(content)}]
            
        if role == "assistant":
            return [self._convert_assistant_blocks(content)]
        
        if role == "user":
            return self._convert_user_blocks(content)
            
        return []

    def _convert_assistant_blocks(self, content: list) -> dict:
        """Convert a list of Anthropic assistant content blocks to an OpenAI message dict.

        Args:
            content: List of Anthropic content blocks (type "text" or "tool_use").

        Returns:
            An OpenAI assistant message dict, with tool_calls populated if any tool_use
            blocks were present.
        """
        text_parts: list[str] = []
        oai_tool_calls: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            
            self._process_assistant_block(block, text_parts, oai_tool_calls)
        oai_msg: dict = {
            "role": "assistant",
            "content": " ".join(text_parts) if text_parts else None,
        }
        if oai_tool_calls:
            oai_msg["tool_calls"] = oai_tool_calls
        return oai_msg

    def _convert_user_blocks(self, content: list) -> list[dict]:
        """Convert a list of Anthropic user content blocks to OpenAI message dicts.

        Each tool_result block becomes a separate OpenAI "tool" role message.
        Plain text blocks become "user" role messages.

        Args:
            content: List of Anthropic content blocks (type "tool_result" or "text").

        Returns:
            List of OpenAI message dicts in the order the blocks appear.
        """
        result: list[dict] = []
        for block in content:
            if isinstance(block, dict):
                result.append(self._convert_user_block(block))
        return [r for r in result if r]

    def _process_assistant_block(self, block: dict, text_parts: list[str], tools: list[dict]) -> None:
        """Process a single assistant content block."""
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tools.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    def _convert_user_block(self, block: dict) -> Optional[dict]:
        """Convert a single user content block to an OpenAI-format message."""
        block_type = block.get("type", "")
        if block_type == "tool_result":
            return {
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": self._resolve_tool_content(block.get("content", ""))
            }
        
        if block_type == "text":
            return {"role": "user", "content": block.get("text", "")}
            
        return None

    def _resolve_tool_content(self, content: any) -> str:
        """Normalize tool result content to a string."""
        if isinstance(content, list):
            return " ".join(
                b.get("text", "") 
                for b in content 
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return str(content) if content is not None else ""

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert neutral DPG tool definitions to OpenAI function-calling format.

        Args:
            tools: Tool definitions in DPG neutral format (name, description, parameters).

        Returns:
            Tool definitions in OpenAI format wrapped with type "function".
        """
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
