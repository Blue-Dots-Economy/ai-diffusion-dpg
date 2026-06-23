"""OpenAIChatProvider — only file in agent_core that imports `openai`.

Translates neutral chat_provider types to/from OpenAI Chat Completions
SDK shapes. Mirrors the structure of anthropic_provider.py: capabilities
declared on the class, init validates required config, _to_wire and
_from_wire handle every translation, retry loops live in private
helpers.

OpenAI exposes automatic prompt caching: prefixes ≥1024 tokens on
supported models are cached transparently, and the response usage
block surfaces the hit count under
`prompt_tokens_details.cached_tokens`. _from_wire reads that field
into TokenUsage.cache_read_tokens. There is no creation-vs-read
split — caching is implicit — so cache_creation_tokens stays None on
this provider, and per-block cache_hint markers are accepted by the
validator but not emitted on the wire.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import openai
from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderAPIError,
    ProviderConfigError,
    UnsupportedFeatureError,
)
from src.chat_provider.metrics import record_call_metrics
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    ImageBlock,
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


def _extract_cached_tokens(usage_obj) -> int | None:
    """Read OpenAI's prompt_tokens_details.cached_tokens with safe fall-through.

    Returns None when the SDK / model does not report the field
    (preserving the "None means provider does not report this"
    contract); returns an int (possibly 0) when the field is present.
    """
    if usage_obj is None:
        return None
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is None:
        return None
    raw = getattr(details, "cached_tokens", None)
    if raw is None:
        return None
    return _safe_int(raw)


class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed.

    Caught only inside OpenAIChatProvider.call() / .stream() to
    transition into the error-response path. Never escapes.
    """

    def __init__(self, message: str, error_type: str, error_message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.error_message = error_message


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
        supports_prompt_cache=True,
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
        """Execute a single OpenAI call with retries on transient failures.

        Args:
            request: The chat request containing messages, tools, and config.

        Returns:
            ChatResponse with content blocks and token usage, or an error
            response if all retry attempts are exhausted or a non-retryable
            error occurs.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If the request uses capabilities not
                supported by OpenAI (e.g. prompt caching).
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=False)

        try:
            return self._call_with_retry(request)
        except _RetryableExhausted as e:
            return ChatResponse(
                content=[],
                stop_reason="error",
                error_type=e.error_type,
                error_message=e.error_message,
                model_used=self._active_model,
                usage=TokenUsage(),
            )

    def _call_with_retry(self, request: ChatRequest) -> ChatResponse:
        """Execute the OpenAI API call with retry logic for transient errors.

        Args:
            request: The chat request to execute.

        Returns:
            ChatResponse on success.

        Raises:
            _RetryableExhausted: If all retry attempts are consumed by
                transient errors (rate limits, timeouts).
        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[
                min(attempt, len(self._backoff_seconds) - 1)
            ]
            if delay > 0:
                time.sleep(delay)

            start = time.time()
            tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs = self._to_wire(request)
                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "openai")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")
                    raw = self._client.chat.completions.create(**kwargs)
                    response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)
                    span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens or 0)
                    span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens or 0)
                    span.set_attribute(
                        "gen_ai.usage.cache_read_input_tokens",
                        response.usage.cache_read_tokens or 0,
                    )

                record_call_metrics(
                    model=self._active_model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                    provider_system="openai",
                )
                logger.info(
                    "chat_provider.openai.call",
                    extra={
                        "operation": "chat_provider.openai.call",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_read_input_tokens": response.usage.cache_read_tokens,
                    },
                )
                return response

            except (openai.APITimeoutError, openai.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "chat_provider.openai.retryable_error",
                    extra={
                        "operation": "chat_provider.openai.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
            except openai.APIError as e:
                # Surface the OpenAI error in the log MESSAGE itself — not just
                # in `extra` — so a default logging.basicConfig deployment
                # shows what went wrong without needing a structured-extras
                # formatter. Captures status code, request id, and validation
                # message body where available.
                _body = (
                    getattr(getattr(e, "response", None), "text", None)
                    or getattr(e, "message", None)
                    or str(e)
                )
                logger.error(
                    "chat_provider.openai.api_error: %s — %s",
                    type(e).__name__,
                    _body,
                    extra={
                        "operation": "chat_provider.openai.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ChatResponse(
                    content=[],
                    stop_reason="error",
                    error_type="api_error",
                    error_message="We're having trouble connecting to the AI service right now. Please try again shortly.",
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )
            except Exception as e:
                logger.error(
                    "chat_provider.openai.unexpected_error",
                    extra={
                        "operation": "chat_provider.openai.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ChatResponse(
                    content=[],
                    stop_reason="error",
                    error_type="api_error",
                    error_message="We're having trouble connecting to the AI service right now. Please try again shortly.",
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

        logger.error(
            "chat_provider.openai.exhausted",
            extra={
                "operation": "chat_provider.openai.call",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        error_type = "rate_limit" if isinstance(last_error, openai.RateLimitError) else "timeout"
        error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."
        raise _RetryableExhausted(
            f"All {self._max_attempts} retry attempts exhausted for model {self._active_model}",
            error_type=error_type,
            error_message=error_message,
        )

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text tokens from OpenAI Chat Completions.

        Same retry contract as call(). Yields text deltas as they
        arrive. After the stream closes, if any tool_calls were emitted,
        raises ToolUseRequested with the accumulated calls.

        Args:
            request: The chat request containing messages, tools, and config.
            abort_event: Optional asyncio Event; when set, streaming stops early.

        Yields:
            Text token strings as they arrive from the API.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If request uses output_format (not
                compatible with streaming).
            ToolUseRequested: After the stream closes, if the model emitted
                tool calls (finish_reason="tool_calls").
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=True)

        try:
            async for token in self._stream_with_retry(request, abort_event):
                yield token
        except _RetryableExhausted as e:
            raise ProviderAPIError(
                f"All {self._max_attempts} stream retry attempts exhausted: {e.error_message}",
                error_type=e.error_type,
                error_message=e.error_message,
            ) from e

    async def _stream_with_retry(
        self,
        request: ChatRequest,
        abort_event: "asyncio.Event | None",
    ) -> AsyncGenerator[str, None]:
        """Inner retry loop for stream().

        Args:
            request: The chat request to stream.
            abort_event: Optional event to abort mid-stream.

        Yields:
            Text token strings.

        Raises:
            _RetryableExhausted: If all attempts are consumed by transient errors.
            ToolUseRequested: If the model returns tool_calls finish_reason.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[
                min(attempt, len(self._backoff_seconds) - 1)
            ]
            if delay > 0:
                await asyncio.sleep(delay)

            start = time.time()
            tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs = self._to_wire(request)
                kwargs["stream"] = True
                kwargs["stream_options"] = {"include_usage": True}

                tool_call_buf: dict[int, dict] = {}
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens: int | None = None

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "openai")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    stream_obj = await self._async_client.chat.completions.create(**kwargs)
                    async for chunk in stream_obj:
                        if abort_event is not None and abort_event.is_set():
                            return

                        choice = chunk.choices[0] if chunk.choices else None
                        if choice is not None:
                            delta = choice.delta
                            if delta is not None:
                                if delta.content:
                                    yield delta.content
                                if delta.tool_calls:
                                    for tc_delta in delta.tool_calls:
                                        idx = tc_delta.index
                                        slot = tool_call_buf.setdefault(
                                            idx, {"id": None, "name": None, "args": ""}
                                        )
                                        if tc_delta.id is not None:
                                            slot["id"] = tc_delta.id
                                        fn = tc_delta.function
                                        if fn is not None and fn.name:
                                            slot["name"] = fn.name
                                        if fn is not None and fn.arguments:
                                            slot["args"] += fn.arguments
                            if choice.finish_reason is not None:
                                stop_reason = choice.finish_reason

                        # Final chunk: usage block.
                        if getattr(chunk, "usage", None) is not None:
                            input_tokens = _safe_int(getattr(chunk.usage, "prompt_tokens", 0))
                            output_tokens = _safe_int(getattr(chunk.usage, "completion_tokens", 0))
                            cache_read_tokens = _extract_cached_tokens(chunk.usage)

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    span.set_attribute(
                        "gen_ai.usage.cache_read_input_tokens",
                        cache_read_tokens or 0,
                    )

                latency_ms = int((time.time() - start) * 1000)
                synth_resp = ChatResponse(
                    content=[],
                    stop_reason=(
                        "tool_use" if stop_reason == "tool_calls"
                        else "end_turn" if stop_reason == "stop"
                        else "max_tokens" if stop_reason == "length"
                        else "error" if stop_reason == "content_filter"
                        else "end_turn"
                    ),
                    model_used=self._active_model,
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens,
                        cache_creation_tokens=None,
                    ),
                )
                record_call_metrics(
                    model=self._active_model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=synth_resp,
                    provider_system="openai",
                )
                logger.info(
                    "chat_provider.openai.stream",
                    extra={
                        "operation": "chat_provider.openai.stream",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_input_tokens": cache_read_tokens,
                        "stop_reason": stop_reason,
                    },
                )

                if stop_reason == "content_filter":
                    raise ProviderAPIError(
                        "OpenAI stream stopped due to content_filter block",
                        error_type="safety_blocked",
                        error_message="We're experiencing a temporary issue with the AI service. Please try again.",
                    )

                # If any tool calls accumulated, raise ToolUseRequested.
                if stop_reason == "tool_calls" and tool_call_buf:
                    from src.chat_provider.base import ToolUseRequested
                    tool_calls: list[ToolUseBlock] = []
                    for idx in sorted(tool_call_buf.keys()):
                        slot = tool_call_buf[idx]
                        try:
                            parsed = json.loads(slot["args"]) if slot["args"] else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        tool_calls.append(
                            ToolUseBlock(
                                tool_use_id=slot["id"] or f"call_{idx}",
                                tool_name=slot["name"] or "",
                                input=parsed,
                            )
                        )
                    raise ToolUseRequested(tool_calls)

                return

            except _RetryableExhausted:
                raise
            except Exception as e:
                from src.chat_provider.base import ToolUseRequested
                if isinstance(e, ToolUseRequested):
                    raise
                if isinstance(e, (openai.APITimeoutError, openai.RateLimitError)):
                    last_error = e
                    logger.warning(
                        "chat_provider.openai.stream_retryable_error",
                        extra={
                            "operation": "chat_provider.openai.stream",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    continue
                logger.error(
                    "chat_provider.openai.stream_error",
                    extra={
                        "operation": "chat_provider.openai.stream",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                raise ProviderAPIError("We're having trouble connecting to the AI service right now. Please try again shortly.") from e

        logger.error(
            "chat_provider.openai.stream_exhausted",
            extra={
                "operation": "chat_provider.openai.stream",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        error_type = "rate_limit" if isinstance(last_error, openai.RateLimitError) else "timeout"
        error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model {self._active_model}",
            error_type=error_type,
            error_message=error_message,
        )

    def get_active_model(self) -> str:
        """Return the model identifier currently active for API calls.

        Returns:
            The active model string (primary or fallback after a switch).
        """
        return self._active_model

    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> OpenAI Chat Completions shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into chat.completions.create kwargs.

        Differences from Anthropic translation:
          - System prompt becomes the first message (role="system").
          - Tool results become separate role="tool" messages.
          - Mixed image+text content uses the content-parts array; pure
            text uses a plain string for the content field.
          - response_format is native (no tool-coercion emulation).

        Args:
            request: The neutral chat request to translate.

        Returns:
            A dict of kwargs ready to pass to openai.chat.completions.create.
        """
        wire_messages: list[dict[str, Any]] = []

        # System prompt → first message.
        if request.system is not None:
            joined = "\n\n".join(b.text for b in request.system.blocks)
            wire_messages.append({"role": "system", "content": joined})

        # Conversation messages.
        for msg in request.messages:
            wire_messages.extend(self._message_to_wire(msg))

        wire: dict[str, Any] = {
            "model": self._active_model,
            "max_completion_tokens": request.max_tokens,
            "messages": wire_messages,
            "timeout": self._timeout_s,
        }

        # Tools.
        if request.tools and request.tool_choice != "none":
            wire["tools"] = [self._tool_to_wire(t) for t in request.tools]
            wire["tool_choice"] = self._tool_choice_to_wire(request.tool_choice)

        # Structured output.
        if request.output_format is not None:
            wire["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "out",
                    "schema": request.output_format.schema,
                    "strict": request.output_format.strict,
                },
            }

        return wire

    @staticmethod
    def _tool_to_wire(t: ToolDefinition) -> dict[str, Any]:
        """Translate a neutral ToolDefinition to an OpenAI function tool dict.

        Args:
            t: The tool definition to translate.

        Returns:
            An OpenAI-shaped tool dict with type, function name, description, parameters.
        """
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    @staticmethod
    def _tool_choice_to_wire(choice: str) -> Any:
        """Translate a neutral tool_choice string to the OpenAI wire shape.

        Args:
            choice: One of "auto", "any", or a specific tool name.

        Returns:
            "auto", "required", or {"type": "function", "function": {"name": ...}}.
        """
        if choice == "auto":
            return "auto"
        if choice == "any":
            return "required"
        # Named tool.
        return {"type": "function", "function": {"name": choice}}

    def _message_to_wire(self, msg: Message) -> list[dict[str, Any]]:
        """Translate one neutral Message into one or more OpenAI messages.

        Returns a list because a single user-role Message containing
        ToolResultBlocks expands into multiple role="tool" messages.

        Args:
            msg: The neutral Message to translate.

        Returns:
            A list of OpenAI message dicts.
        """
        # Separate tool_results — each becomes its own role="tool" message.
        tool_results = [b for b in msg.content if b.type == "tool_result"]
        non_tool_results = [b for b in msg.content if b.type != "tool_result"]

        out: list[dict[str, Any]] = []

        if non_tool_results:
            out.append(self._build_primary_message(msg.role, non_tool_results))

        for tr in tool_results:
            content: str
            if isinstance(tr.content, str):
                content = tr.content
            else:
                content = "".join(tb.text for tb in tr.content)
            out.append({
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": content,
            })

        return out

    def _build_primary_message(self, role: str, blocks: list) -> dict[str, Any]:
        """Build one OpenAI message from a list of content blocks (sans tool_results).

        Args:
            role: The message role (e.g. "user", "assistant").
            blocks: Content blocks excluding ToolResultBlocks.

        Returns:
            A single OpenAI message dict.
        """
        text_blocks = [b for b in blocks if b.type == "text"]
        image_blocks = [b for b in blocks if b.type == "image"]
        tool_use_blocks = [b for b in blocks if b.type == "tool_use"]

        msg: dict[str, Any] = {"role": role}

        # Content shape: string if only TextBlocks, else parts array.
        if image_blocks:
            parts: list[dict[str, Any]] = []
            for tb in text_blocks:
                parts.append({"type": "text", "text": tb.text})
            for ib in image_blocks:
                parts.append({"type": "image_url", "image_url": self._image_url(ib)})
            msg["content"] = parts
        elif text_blocks:
            # Concatenate multiple text blocks (rare on OpenAI side).
            msg["content"] = (
                "\n\n".join(tb.text for tb in text_blocks)
                if len(text_blocks) > 1
                else text_blocks[0].text
            )
        else:
            # No text, no images — assistant turn that's just tool_calls.
            msg["content"] = None if tool_use_blocks else ""

        # Assistant tool_calls (prior-turn replays).
        if tool_use_blocks:
            msg["tool_calls"] = [
                {
                    "id": tu.tool_use_id,
                    "type": "function",
                    "function": {
                        "name": tu.tool_name,
                        "arguments": json.dumps(tu.input),
                    },
                }
                for tu in tool_use_blocks
            ]

        return msg

    @staticmethod
    def _image_url(block: ImageBlock) -> dict[str, str]:
        """Build an OpenAI image_url dict from a neutral ImageBlock.

        Args:
            block: The ImageBlock containing source information.

        Returns:
            A dict with a single "url" key, either a direct URL or a data URL.
        """
        src = block.source
        if src.kind == "url":
            return {"url": src.url}
        # base64 → data URL
        return {"url": f"data:{src.media_type};base64,{src.data}"}

    def _from_wire(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an OpenAI ChatCompletion into a neutral ChatResponse.

        Args:
            raw: The raw OpenAI ChatCompletion response object.
            output_format: If provided, attempt to parse msg.content as JSON
                and populate parsed_output; marks stop_reason="error" on failure.

        Returns:
            A ChatResponse with content blocks, token usage, and stop_reason.
        """
        choice = raw.choices[0]
        msg = choice.message

        content_blocks: list = []
        if msg.content:
            content_blocks.append(TextBlock(text=msg.content))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    parsed_input = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    parsed_input = {}
                content_blocks.append(
                    ToolUseBlock(
                        tool_use_id=tc.id,
                        tool_name=tc.function.name,
                        input=parsed_input,
                    )
                )

        usage = TokenUsage(
            input_tokens=_safe_int(getattr(raw.usage, "prompt_tokens", 0)),
            output_tokens=_safe_int(getattr(raw.usage, "completion_tokens", 0)),
            cache_read_tokens=_extract_cached_tokens(raw.usage),
            cache_creation_tokens=None,
        )

        finish_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
            "content_filter": "error",
            "function_call": "tool_use",
        }
        stop_reason = finish_map.get(choice.finish_reason, "end_turn")
        error_type: str | None = None
        error_message: str | None = None

        if choice.finish_reason == "content_filter":
            error_type = "safety_blocked"
            error_message = "We're experiencing a temporary issue with the AI service. Please try again."

        # Structured output unwrap.
        parsed_output: dict | None = None
        if output_format is not None:
            if msg.content:
                try:
                    parsed_output = json.loads(msg.content)
                except (json.JSONDecodeError, TypeError):
                    parsed_output = None
                    stop_reason = "error"
            else:
                stop_reason = "error"

        return ChatResponse(
            content=content_blocks,
            parsed_output=parsed_output,
            stop_reason=stop_reason,
            error_type=error_type,
            error_message=error_message,
            model_used=self._active_model,
            usage=usage,
        )
