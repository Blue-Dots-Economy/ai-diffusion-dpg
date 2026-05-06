"""Neutral Pydantic types for chat_provider.

Imported by ChatProviderBase and every concrete provider. Callers in
agent_core build these types directly; concrete providers translate
to/from their SDK shapes via _to_wire / _from_wire.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Content blocks — discriminated union via the `type` field
# ---------------------------------------------------------------------------


class TextBlock(BaseModel):
    """Plain text content with an optional caching hint.

    `cache_hint` is intent-only. The Anthropic provider translates it to
    `cache_control={"type": "ephemeral"}`. Providers without prompt-cache
    capability raise UnsupportedFeatureError when this is set.
    """

    type: Literal["text"] = "text"
    text: str
    cache_hint: Literal["session", "turn"] | None = None


class ImageSource(BaseModel):
    """Where to fetch image bytes from.

    kind="url"     → `url` is required.
    kind="base64"  → `media_type` and `data` are both required.
    """

    kind: Literal["url", "base64"]
    url: str | None = None
    media_type: str | None = None  # e.g. "image/png"
    data: str | None = None        # base64-encoded payload

    @model_validator(mode="after")
    def _validate_kind(self) -> "ImageSource":
        if self.kind == "url":
            if self.url is None:
                raise ValueError("ImageSource(kind='url') requires url")
        else:  # base64
            if self.media_type is None or self.data is None:
                raise ValueError(
                    "ImageSource(kind='base64') requires both media_type and data"
                )
        return self


class ImageBlock(BaseModel):
    """Image input. Requires capability supports_image_input."""

    type: Literal["image"] = "image"
    source: ImageSource


class ToolUseBlock(BaseModel):
    """A tool invocation request emitted by the model."""

    type: Literal["tool_use"] = "tool_use"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    """The result of executing a tool call, fed back to the model."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[TextBlock]
    is_error: bool = False


ContentBlock = Annotated[
    Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Messages, tools, system prompt, output format
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """One conversational turn.

    `role` is restricted to user/assistant; the system prompt does not
    travel as a Message — it sits on ChatRequest.system as a SystemPrompt.
    `content` is always a list, even for plain text.
    """

    role: Literal["user", "assistant"]
    content: list[ContentBlock]


class ToolDefinition(BaseModel):
    """Tool contract presented to the model.

    `input_schema` is a JSON Schema dict; both Anthropic and OpenAI
    accept this shape natively (Anthropic as `input_schema`, OpenAI as
    `function.parameters`).
    """

    name: str
    description: str
    input_schema: dict[str, Any]


class SystemPrompt(BaseModel):
    """Ordered list of system text blocks.

    Each block may carry a cache_hint so callers (e.g. ManagerAgent) can
    mark cache boundaries without writing provider-specific dicts.
    """

    blocks: list[TextBlock]


class OutputFormat(BaseModel):
    """Structured-output contract.

    Only json_schema is supported in PR1. OpenAI uses this natively
    (response_format strict mode); Anthropic emulates via tool-coercion.
    `_validate_request` forbids OutputFormat on stream() for all providers.
    """

    type: Literal["json_schema"] = "json_schema"
    schema: dict[str, Any]
    strict: bool = True


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """A single request to a chat provider.

    Provider/model selection lives on the ChatProviderBase instance; this
    request is provider-agnostic.
    """

    messages: list[Message]
    system: SystemPrompt | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    tool_choice: Literal["auto", "any", "none"] | str = "auto"
    output_format: OutputFormat | None = None
    max_tokens: int = 4096


class TokenUsage(BaseModel):
    """Per-call token accounting.

    None means "the provider does not report this", not "zero".
    Concrete providers populate the fields they have; everything else
    stays None so dashboards can distinguish missing from zero.

    Provider asymmetry on cache fields:
      - Anthropic reports cache_creation_input_tokens and
        cache_read_input_tokens as disjoint counters; both populate.
      - OpenAI's prompt caching is automatic — there is no separate
        creation event, only a `cached_tokens` hit count. OpenAI
        therefore populates cache_read_tokens only and leaves
        cache_creation_tokens permanently None.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class ChatResponse(BaseModel):
    """Provider-neutral response.

    `content` mirrors the message-input shape (list of ContentBlock) so a
    follow-up turn can be built by appending Message(role="assistant",
    content=response.content).

    `parsed_output` is populated iff the request specified output_format
    and the model returned valid JSON for the schema. On parse/validation
    failure, parsed_output is None and stop_reason is "error".

    `raw` is the provider's underlying response object (or a dict
    reduction of it) and is intended for debugging only — never relied on
    by callers.
    """

    content: list[ContentBlock]
    parsed_output: dict | None = None
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "error"]
    model_used: str
    usage: TokenUsage
    raw: dict | None = None
