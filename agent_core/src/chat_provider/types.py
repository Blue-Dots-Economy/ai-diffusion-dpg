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
            if not self.url:
                raise ValueError("ImageSource(kind='url') requires url")
        else:  # base64
            if not self.media_type or not self.data:
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
