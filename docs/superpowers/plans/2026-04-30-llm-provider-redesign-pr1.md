# LLM Provider Redesign — PR1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the new `agent_core/src/chat_provider/` package with neutral Pydantic types, `ChatProviderBase`, `AnthropicChatProvider`, and a build factory; convert the existing `ClaudeLLMWrapper` into a thin adapter so every one of today's 457+ tests still passes.

**Architecture:** New package lives alongside the old `llm_wrapper/`. The adapter (renamed but in-place `ClaudeLLMWrapper`) translates today's Anthropic-shaped inputs into neutral `ChatRequest`, calls `AnthropicChatProvider`, and translates `ChatResponse` back into today's `LLMResponse` — including the existing fallback model behaviour. The new provider has *no* fallback logic of its own; that lives only in the adapter and is removed for good in PR5.

**Tech Stack:** Python 3.11+, Pydantic v2, `anthropic` SDK, `opentelemetry`, `pytest` + `pytest-asyncio` + `pytest-mock`, `uv` for env management.

**Tracking:** Parent issue [#287](https://github.com/sanketika-labs/ai-diffusion-dpg/sub-issues/287); this PR resolves [#288](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/288). Branch: a feature branch off `feature/llm-provider-redesign`. PR target: `feature/llm-provider-redesign` (the integration branch), **not** `main`.

**Spec:** `docs/superpowers/specs/2026-04-30-llm-provider-redesign-design.md`

---

## File structure

```
agent_core/src/chat_provider/
├── __init__.py            # public exports + build_chat_provider() factory
├── base.py                # ChatProviderBase (ABC), Capabilities, error types, _validate_request
├── types.py               # neutral Pydantic types
├── anthropic_provider.py  # AnthropicChatProvider — only file in agent_core that imports `anthropic`
└── metrics.py             # provider-agnostic OTel instruments + _record_call_metrics

agent_core/tests/
├── test_chat_provider_types.py        # neutral type round-trip + validation
├── test_chat_provider_base.py         # _validate_request rejection rules
├── test_chat_provider_metrics.py      # OTel instrument creation smoke test
├── test_chat_provider_anthropic.py    # AnthropicChatProvider unit tests + wire-format snapshots
└── test_chat_provider_factory.py      # build_chat_provider() factory
```

Modified:
- `agent_core/src/llm_wrapper/claude_wrapper.py` — becomes the adapter (delegates to `AnthropicChatProvider`).
- `agent_core/src/llm_wrapper/__init__.py` — exports unchanged (`LLMWrapperBase`, `ClaudeLLMWrapper`).
- `agent_core/src/llm_wrapper/base.py` — unchanged (deleted in PR5).
- `agent_core/src/exceptions.py` — `ToolUseRequested` re-exported from `chat_provider/base.py` to keep the existing import path stable.

Untouched in PR1:
- All existing tests under `agent_core/tests/` — they keep targeting `LLMWrapperBase`/`ClaudeLLMWrapper` and pass against the adapter.
- All callers (`orchestrator.py`, `manager_agent.py`, `nlu_processor.py`, `turn_assembler.py`, `llm_proxy_server.py`).

---

## Conventions

- Each step assumes `cwd = agent_core/`.
- Test command: `uv run pytest -x` (stop at first failure).
- Lint/type-check: project uses neither ruff nor mypy in CI gating, so we don't add a check step. The pyproject coverage gate (`fail_under = 70`) is enforced by `pytest-cov`.
- Pydantic v2 idioms: `BaseModel`, `Field`, `model_validator`, discriminated unions via `Annotated[Union[...], Field(discriminator="type")]`.
- All Python source files use the project's existing module-level docstring style (see `claude_wrapper.py:1-13` for a template).
- Every commit body ends with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` per repo convention.

---

## Task 1: Branch off the integration branch and scaffold empty package

**Files:**
- Create: `agent_core/src/chat_provider/__init__.py`
- Create: `agent_core/src/chat_provider/types.py` (empty placeholder)
- Create: `agent_core/src/chat_provider/base.py` (empty placeholder)
- Create: `agent_core/src/chat_provider/metrics.py` (empty placeholder)
- Create: `agent_core/src/chat_provider/anthropic_provider.py` (empty placeholder)

- [ ] **Step 1: Create a feature branch off the integration branch**

```bash
git fetch origin
git checkout -b pr1/scaffold-chat-provider origin/feature/llm-provider-redesign
```

- [ ] **Step 2: Create the package directory and empty modules**

```bash
mkdir -p agent_core/src/chat_provider
```

Create `agent_core/src/chat_provider/__init__.py` with:

```python
"""
agent_core/src/chat_provider — provider-neutral LLM interface.

Public surface:
    ChatProviderBase  — ABC every provider implements.
    Capabilities      — frozen dataclass declared per provider class.
    build_chat_provider(config) — factory; sole construction path.

All other names (TextBlock, ChatRequest, etc.) are exposed via
chat_provider.types.

This package replaces agent_core/src/llm_wrapper/ over PRs #288–#292.
"""
```

Create `agent_core/src/chat_provider/types.py` with a single docstring:

```python
"""Neutral Pydantic types for chat_provider.

Imported by ChatProviderBase and every concrete provider. Callers in
agent_core build these types directly; concrete providers translate
to/from their SDK shapes via _to_wire / _from_wire.
"""
```

Create `agent_core/src/chat_provider/base.py`:

```python
"""ChatProviderBase, Capabilities, and chat_provider error types."""
```

Create `agent_core/src/chat_provider/metrics.py`:

```python
"""Provider-agnostic OTel instruments shared by every ChatProviderBase implementation."""
```

Create `agent_core/src/chat_provider/anthropic_provider.py`:

```python
"""AnthropicChatProvider — only file in agent_core that imports `anthropic`."""
```

- [ ] **Step 3: Verify import path resolves**

Run: `cd agent_core && uv run python -c "import src.chat_provider"`
Expected: exits 0, no output.

- [ ] **Step 4: Commit**

```bash
git add agent_core/src/chat_provider/
git commit -m "$(cat <<'EOF'
chore(agent-core): scaffold empty chat_provider/ package (#288)

Empty modules with docstrings only. Subsequent commits fill them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Neutral content block types — TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock

**Files:**
- Modify: `agent_core/src/chat_provider/types.py`
- Create: `agent_core/tests/test_chat_provider_types.py`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_chat_provider_types.py`:

```python
"""Tests for chat_provider.types — neutral Pydantic models."""

import pytest
from pydantic import ValidationError

from src.chat_provider.types import (
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class TestTextBlock:
    def test_minimal(self):
        b = TextBlock(text="hello")
        assert b.type == "text"
        assert b.text == "hello"
        assert b.cache_hint is None

    def test_with_cache_hint(self):
        b = TextBlock(text="x", cache_hint="session")
        assert b.cache_hint == "session"

    def test_invalid_cache_hint(self):
        with pytest.raises(ValidationError):
            TextBlock(text="x", cache_hint="forever")  # type: ignore[arg-type]

    def test_round_trip(self):
        b = TextBlock(text="hi", cache_hint="turn")
        dumped = b.model_dump()
        assert dumped == {"type": "text", "text": "hi", "cache_hint": "turn"}
        assert TextBlock.model_validate(dumped) == b


class TestImageBlock:
    def test_url_source(self):
        b = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        assert b.type == "image"
        assert b.source.kind == "url"

    def test_base64_source_requires_data_and_media_type(self):
        # kind=base64 without data → validation error
        with pytest.raises(ValidationError):
            ImageSource(kind="base64", media_type="image/png")
        with pytest.raises(ValidationError):
            ImageSource(kind="base64", data="abc")

    def test_url_source_requires_url(self):
        with pytest.raises(ValidationError):
            ImageSource(kind="url")


class TestToolUseBlock:
    def test_minimal(self):
        b = ToolUseBlock(tool_use_id="t_1", tool_name="get_x", input={"q": 1})
        assert b.type == "tool_use"
        assert b.input == {"q": 1}


class TestToolResultBlock:
    def test_text_content(self):
        b = ToolResultBlock(tool_use_id="t_1", content="ok")
        assert b.is_error is False

    def test_error_content(self):
        b = ToolResultBlock(tool_use_id="t_1", content="boom", is_error=True)
        assert b.is_error is True

    def test_block_list_content(self):
        b = ToolResultBlock(
            tool_use_id="t_1",
            content=[TextBlock(text="part 1"), TextBlock(text="part 2")],
        )
        assert isinstance(b.content, list)
        assert len(b.content) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: ImportError or collection error — `TextBlock`, `ImageBlock`, etc. are not defined.

- [ ] **Step 3: Implement the types**

Replace `agent_core/src/chat_provider/types.py` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/types.py agent_core/tests/test_chat_provider_types.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add neutral content-block types (#288)

Adds TextBlock, ImageBlock + ImageSource, ToolUseBlock, ToolResultBlock,
plus the ContentBlock discriminated union. Validation rejects malformed
ImageSource and unknown cache_hint values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Message, ToolDefinition, SystemPrompt, OutputFormat

**Files:**
- Modify: `agent_core/src/chat_provider/types.py`
- Modify: `agent_core/tests/test_chat_provider_types.py`

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_types.py`:

```python
from src.chat_provider.types import (
    Message,
    OutputFormat,
    SystemPrompt,
    ToolDefinition,
)


class TestMessage:
    def test_user_text(self):
        m = Message(role="user", content=[TextBlock(text="hi")])
        assert m.role == "user"
        assert len(m.content) == 1

    def test_assistant_with_tool_use(self):
        m = Message(
            role="assistant",
            content=[
                TextBlock(text="let me check"),
                ToolUseBlock(tool_use_id="t_1", tool_name="lookup", input={"q": 1}),
            ],
        )
        assert m.content[1].type == "tool_use"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            Message(role="system", content=[TextBlock(text="x")])  # type: ignore[arg-type]

    def test_content_must_be_list(self):
        with pytest.raises(ValidationError):
            Message(role="user", content="just a string")  # type: ignore[arg-type]


class TestToolDefinition:
    def test_minimal(self):
        t = ToolDefinition(
            name="get_weather",
            description="Get the weather",
            input_schema={"type": "object", "properties": {}},
        )
        assert t.name == "get_weather"


class TestSystemPrompt:
    def test_with_blocks(self):
        sp = SystemPrompt(
            blocks=[
                TextBlock(text="You are helpful.", cache_hint="session"),
                TextBlock(text="Today's user is Aniket.", cache_hint="turn"),
            ]
        )
        assert len(sp.blocks) == 2
        assert sp.blocks[0].cache_hint == "session"


class TestOutputFormat:
    def test_minimal(self):
        of = OutputFormat(
            type="json_schema",
            schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        assert of.strict is True

    def test_strict_false(self):
        of = OutputFormat(type="json_schema", schema={}, strict=False)
        assert of.strict is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: ImportError on `Message`, `ToolDefinition`, `SystemPrompt`, `OutputFormat`.

- [ ] **Step 3: Append the types**

Append to `agent_core/src/chat_provider/types.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/types.py agent_core/tests/test_chat_provider_types.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add Message, ToolDefinition, SystemPrompt, OutputFormat (#288)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: ChatRequest, ChatResponse, TokenUsage

**Files:**
- Modify: `agent_core/src/chat_provider/types.py`
- Modify: `agent_core/tests/test_chat_provider_types.py`

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_types.py`:

```python
from src.chat_provider.types import ChatRequest, ChatResponse, TokenUsage


class TestChatRequest:
    def test_minimal(self):
        r = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        assert r.tools == []
        assert r.tool_choice == "auto"
        assert r.output_format is None
        assert r.max_tokens == 4096

    def test_force_tool_by_name(self):
        r = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tool_choice="my_tool",
        )
        assert r.tool_choice == "my_tool"

    def test_messages_required(self):
        with pytest.raises(ValidationError):
            ChatRequest(messages=[])  # empty list — caller-side guard, but model allows


class TestTokenUsage:
    def test_default_all_none(self):
        u = TokenUsage()
        assert u.input_tokens is None
        assert u.cache_read_tokens is None

    def test_partial(self):
        u = TokenUsage(input_tokens=10, output_tokens=5)
        assert u.cache_read_tokens is None  # not "0" — distinguishes "not supported"


class TestChatResponse:
    def test_minimal_text(self):
        r = ChatResponse(
            content=[TextBlock(text="hi back")],
            stop_reason="end_turn",
            model_used="claude-test",
            usage=TokenUsage(input_tokens=1, output_tokens=2),
        )
        assert r.parsed_output is None

    def test_with_parsed_output(self):
        r = ChatResponse(
            content=[TextBlock(text='{"x": 1}')],
            parsed_output={"x": 1},
            stop_reason="end_turn",
            model_used="claude-test",
            usage=TokenUsage(),
        )
        assert r.parsed_output == {"x": 1}

    def test_invalid_stop_reason(self):
        with pytest.raises(ValidationError):
            ChatResponse(
                content=[],
                stop_reason="something",  # type: ignore[arg-type]
                model_used="x",
                usage=TokenUsage(),
            )
```

Note: The first test asserts `ChatRequest(messages=[])` does not raise — empty messages is rejected at provider call-time via a `ValueError`, not at model-validation time. Update the test to assert the model accepts it:

Replace the `test_messages_required` test body with:

```python
    def test_empty_messages_is_allowed_by_the_model(self):
        # Empty-message rejection lives at provider.call(), not on the model.
        r = ChatRequest(messages=[])
        assert r.messages == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: ImportError on `ChatRequest`, `ChatResponse`, `TokenUsage`.

- [ ] **Step 3: Append the types**

Append to `agent_core/src/chat_provider/types.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_types.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/types.py agent_core/tests/test_chat_provider_types.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add ChatRequest, ChatResponse, TokenUsage (#288)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Capabilities + error types in base.py

**Files:**
- Modify: `agent_core/src/chat_provider/base.py`
- Create: `agent_core/tests/test_chat_provider_base.py`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_chat_provider_base.py`:

```python
"""Tests for chat_provider.base — Capabilities, errors, and ChatProviderBase."""

import pytest
from dataclasses import FrozenInstanceError

from src.chat_provider.base import (
    Capabilities,
    ChatProviderError,
    ProviderAPIError,
    ProviderConfigError,
    ToolUseRequested,
    UnsupportedFeatureError,
)
from src.chat_provider.types import ToolUseBlock


class TestCapabilities:
    def test_create(self):
        caps = Capabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_prompt_cache=True,
            supports_image_input=True,
            supports_audio_input=False,
            supports_structured_output=True,
            supports_force_tool_choice=True,
        )
        assert caps.supports_tools is True

    def test_frozen(self):
        caps = Capabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_prompt_cache=False,
            supports_image_input=False,
            supports_audio_input=False,
            supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        with pytest.raises(FrozenInstanceError):
            caps.supports_tools = False  # type: ignore[misc]


class TestErrors:
    def test_hierarchy(self):
        assert issubclass(UnsupportedFeatureError, ChatProviderError)
        assert issubclass(ProviderConfigError, ChatProviderError)
        assert issubclass(ProviderAPIError, ChatProviderError)

    def test_tool_use_requested_carries_calls(self):
        calls = [ToolUseBlock(tool_use_id="t_1", tool_name="x", input={})]
        e = ToolUseRequested(calls)
        assert e.tool_calls == calls
        # Not a subclass of ChatProviderError — it's a control-flow signal.
        assert not isinstance(e, ChatProviderError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_base.py -v`
Expected: ImportError on `Capabilities` and friends.

- [ ] **Step 3: Implement Capabilities and errors**

Replace `agent_core/src/chat_provider/base.py` with:

```python
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


class ToolUseRequested(Exception):
    """Streaming-only signal: model emitted tool_use blocks; caller executes and resumes.

    NOT a ChatProviderError — this is normal control flow for the
    streaming tool loop, not an exceptional condition.
    """

    def __init__(self, tool_calls: list[ToolUseBlock]) -> None:
        self.tool_calls = tool_calls
        names = ", ".join(tc.tool_name for tc in tool_calls)
        super().__init__(f"LLM requested tool use: {names}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_base.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/base.py agent_core/tests/test_chat_provider_base.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add Capabilities and error types (#288)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: ChatProviderBase ABC + _validate_request

**Files:**
- Modify: `agent_core/src/chat_provider/base.py`
- Modify: `agent_core/tests/test_chat_provider_base.py`

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_base.py`:

```python
from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import (
    ChatRequest,
    ImageBlock,
    ImageSource,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
)


class _DummyProvider(ChatProviderBase):
    """Test double: a ChatProviderBase with all capabilities False."""

    def __init__(self, caps: Capabilities) -> None:
        self.capabilities = caps

    def call(self, request):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, request, *, abort_event=None):  # pragma: no cover
        if False:
            yield ""

    def get_active_model(self) -> str:  # pragma: no cover
        return "dummy"


def _all_off() -> Capabilities:
    return Capabilities(
        supports_tools=False,
        supports_streaming=False,
        supports_prompt_cache=False,
        supports_image_input=False,
        supports_audio_input=False,
        supports_structured_output=False,
        supports_force_tool_choice=False,
    )


def _all_on() -> Capabilities:
    return Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=True,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )


def _basic_request(**overrides) -> ChatRequest:
    base = dict(messages=[Message(role="user", content=[TextBlock(text="hi")])])
    base.update(overrides)
    return ChatRequest(**base)


class TestValidateRequest:
    def test_passthrough_when_all_caps_on(self):
        p = _DummyProvider(_all_on())
        p._validate_request(_basic_request(), is_stream=False)
        p._validate_request(_basic_request(), is_stream=True)

    def test_rejects_tools_without_capability(self):
        p = _DummyProvider(_all_off())
        req = _basic_request(
            tools=[
                ToolDefinition(name="x", description="d", input_schema={"type": "object"})
            ]
        )
        with pytest.raises(UnsupportedFeatureError, match="tools"):
            p._validate_request(req, is_stream=False)

    def test_rejects_image_block_without_capability(self):
        caps = Capabilities(
            supports_tools=False, supports_streaming=False,
            supports_prompt_cache=False, supports_image_input=False,
            supports_audio_input=False, supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        p = _DummyProvider(caps)
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        with pytest.raises(UnsupportedFeatureError, match="image"):
            p._validate_request(req, is_stream=False)

    def test_rejects_cache_hint_in_message_without_capability(self):
        p = _DummyProvider(_all_off())
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="hi", cache_hint="session")])
            ]
        )
        with pytest.raises(UnsupportedFeatureError, match="prompt cach"):
            p._validate_request(req, is_stream=False)

    def test_rejects_cache_hint_in_system_without_capability(self):
        p = _DummyProvider(_all_off())
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(
                blocks=[TextBlock(text="sys", cache_hint="session")]
            ),
        )
        with pytest.raises(UnsupportedFeatureError, match="prompt cach"):
            p._validate_request(req, is_stream=False)

    def test_rejects_output_format_without_capability(self):
        p = _DummyProvider(_all_off())
        req = _basic_request(output_format=OutputFormat(schema={}))
        with pytest.raises(UnsupportedFeatureError, match="structured"):
            p._validate_request(req, is_stream=False)

    def test_rejects_output_format_on_stream_even_with_capability(self):
        p = _DummyProvider(_all_on())
        req = _basic_request(output_format=OutputFormat(schema={}))
        with pytest.raises(UnsupportedFeatureError, match="stream"):
            p._validate_request(req, is_stream=True)

    def test_rejects_forced_tool_choice_without_capability(self):
        caps = Capabilities(
            supports_tools=True, supports_streaming=True,
            supports_prompt_cache=False, supports_image_input=False,
            supports_audio_input=False, supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        p = _DummyProvider(caps)
        req = _basic_request(
            tools=[ToolDefinition(name="x", description="d", input_schema={"type": "object"})],
            tool_choice="any",
        )
        with pytest.raises(UnsupportedFeatureError, match="tool_choice"):
            p._validate_request(req, is_stream=False)

    def test_named_tool_choice_with_capability_passes(self):
        p = _DummyProvider(_all_on())
        req = _basic_request(
            tools=[ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})],
            tool_choice="my_tool",
        )
        p._validate_request(req, is_stream=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_base.py -v`
Expected: ImportError on `ChatProviderBase` (the symbol exists in module but the abstract class is not yet defined with `_validate_request`).

- [ ] **Step 3: Append ChatProviderBase to base.py**

Append to `agent_core/src/chat_provider/base.py`:

```python
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
                if (
                    block.type == "text"
                    and block.cache_hint
                    and not caps.supports_prompt_cache
                ):
                    raise UnsupportedFeatureError(
                        f"{cls} does not support prompt caching; "
                        f"remove cache_hint from message blocks."
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_base.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/base.py agent_core/tests/test_chat_provider_base.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add ChatProviderBase ABC with _validate_request (#288)

Centralises capability + sync-only-structured-output enforcement so
each concrete provider just calls _validate_request() at entry. The
output_format-on-stream rule is enforced here regardless of provider
capabilities.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Lift OTel metrics into chat_provider/metrics.py

**Files:**
- Modify: `agent_core/src/chat_provider/metrics.py`
- Create: `agent_core/tests/test_chat_provider_metrics.py`

This task moves the existing metrics machinery from `claude_wrapper.py:39-105, 683-729` into a provider-agnostic module. The behaviour is identical; only the names of the histograms remain stable to keep Grafana dashboards working. The public surface is two functions: `get_metrics()` and `record_call_metrics(...)`.

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_chat_provider_metrics.py`:

```python
"""Smoke tests for chat_provider.metrics — instruments are lazy and idempotent."""

from src.chat_provider.metrics import get_metrics, record_call_metrics
from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage


def test_get_metrics_returns_named_instruments():
    m = get_metrics()
    expected_keys = {
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "calls",
        "cache_hits",
    }
    assert expected_keys.issubset(m.keys())


def test_get_metrics_idempotent():
    a = get_metrics()
    b = get_metrics()
    assert a is b or all(a[k] is b[k] for k in a)


def test_record_call_metrics_does_not_raise_on_minimal_response():
    response = ChatResponse(
        content=[TextBlock(text="hi")],
        stop_reason="end_turn",
        model_used="claude-test",
        usage=TokenUsage(input_tokens=1, output_tokens=1, cache_read_tokens=0,
                         cache_creation_tokens=0),
    )
    # Should not raise even without an installed MeterProvider
    record_call_metrics(
        model="claude-test",
        call_kind="sync",
        status="success",
        latency_ms=42,
        response=response,
    )


def test_record_call_metrics_handles_none_token_fields():
    response = ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-test",
        usage=TokenUsage(),  # all None — provider didn't report
    )
    # Should not raise on None token fields
    record_call_metrics(
        model="claude-test", call_kind="sync", status="failure",
        latency_ms=10, response=response,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_metrics.py -v`
Expected: ImportError on `get_metrics` and `record_call_metrics`.

- [ ] **Step 3: Implement metrics module**

Replace `agent_core/src/chat_provider/metrics.py` with:

```python
"""Provider-agnostic OTel instruments shared by every ChatProviderBase implementation.

Lifted from agent_core/src/llm_wrapper/claude_wrapper.py (GH-151).
Instrument names are unchanged so existing Grafana dashboards keep
working.
"""

from __future__ import annotations

from typing import Optional

from opentelemetry import metrics as otel_metrics

from src.chat_provider.types import ChatResponse


_METRICS_INITIALIZED = False
_LLM_LATENCY_HIST = None
_LLM_INPUT_TOKENS_HIST = None
_LLM_OUTPUT_TOKENS_HIST = None
_LLM_CACHE_READ_HIST = None
_LLM_CALL_COUNTER = None
_LLM_CACHE_HIT_COUNTER = None


def get_metrics() -> dict:
    """Initialise (once) and return the chat-provider metrics instruments.

    Re-resolves on first use rather than at import time so that
    dpg_telemetry has an opportunity to install a real MeterProvider
    during app startup. Missing entries are None if metrics are
    disabled, which every caller must be able to handle.
    """
    global _METRICS_INITIALIZED, _LLM_LATENCY_HIST, _LLM_INPUT_TOKENS_HIST
    global _LLM_OUTPUT_TOKENS_HIST, _LLM_CACHE_READ_HIST, _LLM_CALL_COUNTER
    global _LLM_CACHE_HIT_COUNTER

    if _METRICS_INITIALIZED:
        return {
            "latency_ms": _LLM_LATENCY_HIST,
            "input_tokens": _LLM_INPUT_TOKENS_HIST,
            "output_tokens": _LLM_OUTPUT_TOKENS_HIST,
            "cache_read_tokens": _LLM_CACHE_READ_HIST,
            "calls": _LLM_CALL_COUNTER,
            "cache_hits": _LLM_CACHE_HIT_COUNTER,
        }

    meter = otel_metrics.get_meter(__name__)
    _LLM_LATENCY_HIST = meter.create_histogram(
        "agent_core.llm.call.duration_ms",
        unit="ms",
        description="Wall-clock latency of a single LLM call, tagged by call_kind (sync|stream) and model.",
    )
    _LLM_INPUT_TOKENS_HIST = meter.create_histogram(
        "agent_core.llm.call.input_tokens",
        unit="tokens",
        description="Input token count per LLM call.",
    )
    _LLM_OUTPUT_TOKENS_HIST = meter.create_histogram(
        "agent_core.llm.call.output_tokens",
        unit="tokens",
        description="Output token count per LLM call.",
    )
    _LLM_CACHE_READ_HIST = meter.create_histogram(
        "agent_core.llm.call.cache_read_tokens",
        unit="tokens",
        description="Tokens served from the prompt cache on this call (provider-specific).",
    )
    _LLM_CALL_COUNTER = meter.create_counter(
        "agent_core.llm.calls_total",
        description="Total LLM calls, tagged by model, call_kind, and status.",
    )
    _LLM_CACHE_HIT_COUNTER = meter.create_counter(
        "agent_core.llm.cache_events_total",
        description="Prompt-cache events — tag event=hit|create|miss so the hit ratio can be derived.",
    )
    _METRICS_INITIALIZED = True
    return {
        "latency_ms": _LLM_LATENCY_HIST,
        "input_tokens": _LLM_INPUT_TOKENS_HIST,
        "output_tokens": _LLM_OUTPUT_TOKENS_HIST,
        "cache_read_tokens": _LLM_CACHE_READ_HIST,
        "calls": _LLM_CALL_COUNTER,
        "cache_hits": _LLM_CACHE_HIT_COUNTER,
    }


def record_call_metrics(
    *,
    model: str,
    call_kind: str,
    status: str,
    latency_ms: int,
    response: Optional[ChatResponse] = None,
    provider_system: str | None = None,
) -> None:
    """Emit chat-provider call metrics via OTel.

    Safe on both success and failure paths. When a MeterProvider is not
    installed, instrument creation succeeds against the no-op default
    provider and writes are silently discarded.

    Args:
        model: model id used for the call.
        call_kind: "sync" or "stream".
        status: "success" | "failure" | "retry".
        latency_ms: wall-clock duration of the attempt.
        response: parsed ChatResponse on success; None on failure.
        provider_system: "anthropic" | "openai" — added in PR1 so
            dashboards can split per provider. Optional for backward
            compatibility with the adapter shim.
    """
    try:
        m = get_metrics()
        attrs: dict[str, str] = {
            "model": model,
            "call_kind": call_kind,
            "status": status,
        }
        if provider_system:
            attrs["gen_ai.system"] = provider_system

        if m["latency_ms"] is not None:
            m["latency_ms"].record(latency_ms, attrs)
        if m["calls"] is not None:
            m["calls"].add(1, attrs)

        if response is not None:
            usage = response.usage
            if usage.input_tokens is not None and m["input_tokens"] is not None:
                m["input_tokens"].record(usage.input_tokens, attrs)
            if usage.output_tokens is not None and m["output_tokens"] is not None:
                m["output_tokens"].record(usage.output_tokens, attrs)
            if usage.cache_read_tokens is not None and m["cache_read_tokens"] is not None:
                m["cache_read_tokens"].record(usage.cache_read_tokens, attrs)
            if m["cache_hits"] is not None:
                read = usage.cache_read_tokens or 0
                created = usage.cache_creation_tokens or 0
                if read > 0:
                    m["cache_hits"].add(1, {**attrs, "event": "hit"})
                elif created > 0:
                    m["cache_hits"].add(1, {**attrs, "event": "create"})
                else:
                    m["cache_hits"].add(1, {**attrs, "event": "miss"})
    except Exception:  # noqa: BLE001
        # Metrics must never fail an LLM call; swallow instrumentation errors.
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_metrics.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/metrics.py agent_core/tests/test_chat_provider_metrics.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): lift OTel metrics into provider-agnostic module (#288)

Names match the existing instruments in claude_wrapper.py so Grafana
dashboards survive untouched. Adds optional gen_ai.system attribute
for per-provider splits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: AnthropicChatProvider — class skeleton, capabilities, init validation

**Files:**
- Modify: `agent_core/src/chat_provider/anthropic_provider.py`
- Create: `agent_core/tests/test_chat_provider_anthropic.py`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_chat_provider_anthropic.py`:

```python
"""Tests for AnthropicChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.anthropic_provider import AnthropicChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError


VALID_CONFIG = {
    "primary_model": "claude-sonnet-4-5-20250514",
    "timeout_ms": 5000,
    "retry_attempts": 2,
    "retry_backoff_seconds": [0, 0.0, 0.0],
    "features": {
        "prompt_cache": True,
        "streaming": True,
        "image_input": True,
    },
}


class TestInit:
    def test_capabilities_are_declared(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        caps = p.capabilities
        assert isinstance(caps, Capabilities)
        assert caps.supports_tools is True
        assert caps.supports_prompt_cache is True
        assert caps.supports_image_input is True
        assert caps.supports_audio_input is False
        assert caps.supports_streaming is True
        assert caps.supports_structured_output is True
        assert caps.supports_force_tool_choice is True

    def test_features_disable_caching(self):
        cfg = {**VALID_CONFIG, "features": {**VALID_CONFIG["features"], "prompt_cache": False}}
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(cfg)
        # Capabilities are still True (intrinsic), but the effective
        # feature for this deployment is False — _validate_request reads
        # from self._features.
        assert p._features["prompt_cache"] is False
        assert p.capabilities.supports_prompt_cache is True

    def test_empty_config_raises(self):
        with pytest.raises(ProviderConfigError):
            AnthropicChatProvider({})

    def test_missing_primary_model_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("primary_model")
        with pytest.raises(ProviderConfigError, match="primary_model"):
            AnthropicChatProvider(cfg)

    def test_missing_timeout_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("timeout_ms")
        with pytest.raises(ProviderConfigError, match="timeout_ms"):
            AnthropicChatProvider(cfg)

    def test_get_active_model(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(VALID_CONFIG)
        assert p.get_active_model() == "claude-sonnet-4-5-20250514"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py -v`
Expected: ImportError on `AnthropicChatProvider`.

- [ ] **Step 3: Implement init + capabilities**

Replace `agent_core/src/chat_provider/anthropic_provider.py` with:

```python
"""AnthropicChatProvider — only file in agent_core that imports `anthropic`.

Translates neutral chat_provider types to/from Anthropic SDK shapes.
Lifts the retry/backoff/timeout/OTel scaffolding from the legacy
agent_core/src/llm_wrapper/claude_wrapper.py.
"""

from __future__ import annotations

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
        raise NotImplementedError("Implemented in Task 11")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,  # noqa: F821
    ) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Implemented in Task 12")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        return self._active_model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py -v -k TestInit`
Expected: all `TestInit` cases pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/anthropic_provider.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): scaffold AnthropicChatProvider with init + capabilities (#288)

call() and stream() raise NotImplementedError; filled in subsequent
commits. Capability-vs-config reconciliation lands in this commit so
features dict is correct from day one.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: AnthropicChatProvider — _to_wire (request translation)

**Files:**
- Modify: `agent_core/src/chat_provider/anthropic_provider.py`
- Modify: `agent_core/tests/test_chat_provider_anthropic.py`

- [ ] **Step 1: Append the failing wire-format snapshot tests**

Append to `agent_core/tests/test_chat_provider_anthropic.py`:

```python
from src.chat_provider.types import (
    ChatRequest,
    ImageBlock,
    ImageSource,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


def _make_provider(features: dict | None = None) -> AnthropicChatProvider:
    cfg = dict(VALID_CONFIG)
    if features is not None:
        cfg["features"] = features
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        return AnthropicChatProvider(cfg)


class TestToWire:
    def test_minimal_text_request(self):
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire == {
            "model": "claude-sonnet-4-5-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "timeout": 5.0,
        }

    def test_system_prompt_with_cache_hint_long_enough(self):
        p = _make_provider()
        long_text = "x" * 3500   # over _CACHE_MIN_CHARS
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint="session")]),
        )
        wire = p._to_wire(req)
        assert wire["system"] == [
            {"type": "text", "text": long_text, "cache_control": {"type": "ephemeral"}}
        ]

    def test_system_prompt_short_skips_cache_marker(self):
        p = _make_provider()
        # Below 3000 chars — cache_hint is honoured at intent level but
        # provider doesn't bother emitting a marker Anthropic would ignore.
        short = "you are helpful"
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=short, cache_hint="session")]),
        )
        wire = p._to_wire(req)
        assert wire["system"] == [{"type": "text", "text": short}]

    def test_system_prompt_caching_disabled_drops_marker(self):
        p = _make_provider(features={"prompt_cache": False, "streaming": True, "image_input": True})
        # _validate_request would normally reject this — but _to_wire is
        # callable directly in tests, and we want to assert that even if
        # something slipped past, the wire-format never includes a
        # cache_control marker when feature is off.
        long_text = "x" * 3500
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint=None)]),
        )
        wire = p._to_wire(req)
        assert "cache_control" not in wire["system"][0]

    def test_tool_definition_passthrough(self):
        p = _make_provider()
        td = ToolDefinition(
            name="get_x",
            description="get x",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
        )
        wire = p._to_wire(req)
        assert wire["tools"] == [
            {
                "name": "get_x",
                "description": "get x",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]

    def test_tool_choice_auto(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="auto",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "auto"}

    def test_tool_choice_any(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="any",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "any"}

    def test_tool_choice_named(self):
        p = _make_provider()
        td = ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="my_tool",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {"type": "tool", "name": "my_tool"}

    def test_tool_choice_none_drops_tools(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td],
            tool_choice="none",
        )
        wire = p._to_wire(req)
        assert "tools" not in wire and "tool_choice" not in wire

    def test_image_block_url(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"][0] == {
            "type": "image",
            "source": {"type": "url", "url": "https://x/y.png"},
        }

    def test_image_block_base64(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="base64", media_type="image/png", data="ABC=="))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"][0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "ABC=="},
        }

    def test_assistant_message_with_tool_use_and_tool_result(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="look it up")]),
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking"),
                        ToolUseBlock(tool_use_id="t_1", tool_name="lookup", input={"q": "x"}),
                    ],
                ),
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="t_1", content="42")],
                ),
            ]
        )
        wire = p._to_wire(req)
        assert wire["messages"][1]["content"] == [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "t_1", "name": "lookup", "input": {"q": "x"}},
        ]
        assert wire["messages"][2]["content"] == [
            {"type": "tool_result", "tool_use_id": "t_1", "content": "42", "is_error": False}
        ]

    def test_output_format_emulated_via_tool_coercion(self):
        p = _make_provider()
        of = OutputFormat(
            schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="answer")])],
            output_format=of,
        )
        wire = p._to_wire(req)
        # A synthetic tool is appended and tool_choice forces it.
        assert wire["tools"] == [
            {
                "name": "respond_with_json",
                "description": "Return the response as JSON conforming to the schema.",
                "input_schema": of.schema,
            }
        ]
        assert wire["tool_choice"] == {"type": "tool", "name": "respond_with_json"}

    def test_max_tokens_passed_through(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            max_tokens=200,
        )
        wire = p._to_wire(req)
        assert wire["max_tokens"] == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestToWire -v`
Expected: AttributeError on `_to_wire`.

- [ ] **Step 3: Implement _to_wire**

Append to `agent_core/src/chat_provider/anthropic_provider.py`:

```python
    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> Anthropic SDK shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into Anthropic SDK kwargs.

        The dict returned here is passed directly to
        anthropic.Anthropic.messages.create() (or .stream()).

        Caching: a TextBlock with cache_hint set produces a
        cache_control marker only when (a) features.prompt_cache is on
        and (b) the text exceeds _CACHE_MIN_CHARS — Anthropic ignores
        markers on shorter prompts, and emitting them just bloats the
        request.

        Output format: emulated by appending a synthetic
        respond_with_json tool with the supplied schema and forcing
        tool_choice to it. _from_wire reverses this on the response side.
        """
        wire: dict[str, Any] = {
            "model": self._active_model,
            "max_tokens": request.max_tokens,
            "messages": [self._message_to_wire(m) for m in request.messages],
            "timeout": self._timeout_s,
        }

        if request.system is not None:
            wire["system"] = [
                self._system_block_to_wire(b) for b in request.system.blocks
            ]

        # Tools — combine declared tools with synthetic respond_with_json
        # if output_format is set.
        tools = list(request.tools)
        forced_tool_name: str | None = None
        if request.output_format is not None:
            tools.append(
                ToolDefinition(
                    name="respond_with_json",
                    description="Return the response as JSON conforming to the schema.",
                    input_schema=request.output_format.schema,
                )
            )
            forced_tool_name = "respond_with_json"

        if tools and request.tool_choice != "none":
            wire["tools"] = [self._tool_to_wire(t) for t in tools]

        # tool_choice mapping
        choice = forced_tool_name or request.tool_choice
        if choice == "auto":
            if "tools" in wire:
                wire["tool_choice"] = {"type": "auto"}
        elif choice == "any":
            wire["tool_choice"] = {"type": "any"}
        elif choice == "none":
            # Already handled above by skipping wire["tools"].
            pass
        else:
            # Named tool (either user-forced or synthetic respond_with_json)
            wire["tool_choice"] = {"type": "tool", "name": choice}

        return wire

    @staticmethod
    def _tool_to_wire(t: ToolDefinition) -> dict[str, Any]:
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }

    def _system_block_to_wire(self, block: TextBlock) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "text", "text": block.text}
        if (
            block.cache_hint
            and self._features["prompt_cache"]
            and len(block.text) >= _CACHE_MIN_CHARS
        ):
            out["cache_control"] = {"type": "ephemeral"}
        return out

    def _message_to_wire(self, msg: Message) -> dict[str, Any]:
        return {
            "role": msg.role,
            "content": [self._content_block_to_wire(b) for b in msg.content],
        }

    def _content_block_to_wire(self, block) -> dict[str, Any]:
        # block is one of TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock
        if block.type == "text":
            out: dict[str, Any] = {"type": "text", "text": block.text}
            if (
                block.cache_hint
                and self._features["prompt_cache"]
                and len(block.text) >= _CACHE_MIN_CHARS
            ):
                out["cache_control"] = {"type": "ephemeral"}
            return out

        if block.type == "image":
            src = block.source
            if src.kind == "url":
                return {
                    "type": "image",
                    "source": {"type": "url", "url": src.url},
                }
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": src.media_type,
                    "data": src.data,
                },
            }

        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.tool_use_id,
                "name": block.tool_name,
                "input": block.input,
            }

        if block.type == "tool_result":
            content: Any
            if isinstance(block.content, str):
                content = block.content
            else:
                content = [{"type": "text", "text": tb.text} for tb in block.content]
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content,
                "is_error": block.is_error,
            }

        raise AssertionError(f"unknown block type {block.type!r}")
```

Add the missing imports at the top of the file (right after the existing imports):

```python
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    TextBlock,
    ToolDefinition,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestToWire -v`
Expected: all `TestToWire` cases pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/anthropic_provider.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): translate ChatRequest to Anthropic SDK shape (#288)

Implements AnthropicChatProvider._to_wire with snapshot tests for every
content-block type, system-prompt caching, tool definitions, all four
tool_choice variants, image input (URL + base64), and the
respond_with_json output_format emulation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: AnthropicChatProvider — _from_wire (response translation)

**Files:**
- Modify: `agent_core/src/chat_provider/anthropic_provider.py`
- Modify: `agent_core/tests/test_chat_provider_anthropic.py`

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_anthropic.py`:

```python
from unittest.mock import MagicMock


def _mk_anthropic_message(
    text: str | None = None,
    tool_use: dict | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_create: int = 0,
) -> MagicMock:
    """Build a MagicMock that mimics anthropic.types.Message."""
    raw = MagicMock()
    blocks: list = []
    if text is not None:
        b = MagicMock()
        b.type = "text"
        b.text = text
        blocks.append(b)
    if tool_use is not None:
        b = MagicMock()
        b.type = "tool_use"
        b.id = tool_use["id"]
        b.name = tool_use["name"]
        b.input = tool_use["input"]
        blocks.append(b)
    raw.content = blocks
    raw.stop_reason = stop_reason
    raw.usage.input_tokens = input_tokens
    raw.usage.output_tokens = output_tokens
    raw.usage.cache_read_input_tokens = cache_read
    raw.usage.cache_creation_input_tokens = cache_create
    return raw


class TestFromWire:
    def test_text_only(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="hello back", input_tokens=12, output_tokens=4)
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "end_turn"
        assert resp.model_used == "claude-sonnet-4-5-20250514"
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert resp.content[0].text == "hello back"
        assert resp.parsed_output is None
        assert resp.usage.input_tokens == 12
        assert resp.usage.output_tokens == 4
        assert resp.usage.cache_read_tokens == 0
        assert resp.usage.cache_creation_tokens == 0

    def test_tool_use(self):
        p = _make_provider()
        raw = _mk_anthropic_message(
            text="checking",
            tool_use={"id": "t_1", "name": "lookup", "input": {"q": "x"}},
            stop_reason="tool_use",
        )
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "tool_use"
        assert len(resp.content) == 2
        assert resp.content[1].type == "tool_use"
        assert resp.content[1].tool_name == "lookup"

    def test_output_format_unwraps_synthetic_tool_use(self):
        p = _make_provider()
        of = OutputFormat(schema={"type": "object", "properties": {"answer": {"type": "string"}}})
        raw = _mk_anthropic_message(
            tool_use={"id": "t_x", "name": "respond_with_json", "input": {"answer": "42"}},
            stop_reason="tool_use",
        )
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output == {"answer": "42"}
        # Stop reason is normalised back to end_turn — caller sees a clean response.
        assert resp.stop_reason == "end_turn"
        # Content carries a synthesised TextBlock with the JSON string.
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert '"answer"' in resp.content[0].text

    def test_cache_token_fields_use_safe_int(self):
        p = _make_provider()
        raw = _mk_anthropic_message()
        # Simulate a missing field (older SDK / mocked response without cache fields)
        del raw.usage.cache_read_input_tokens
        del raw.usage.cache_creation_input_tokens
        resp = p._from_wire(raw, output_format=None)
        assert resp.usage.cache_read_tokens == 0
        assert resp.usage.cache_creation_tokens == 0

    def test_max_tokens_stop_reason_passthrough(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="truncated", stop_reason="max_tokens")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "max_tokens"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestFromWire -v`
Expected: AttributeError on `_from_wire`.

- [ ] **Step 3: Implement _from_wire and the safe-int helper**

Append to `agent_core/src/chat_provider/anthropic_provider.py`:

```python
import json
from src.chat_provider.types import (
    OutputFormat,
    TextBlock as _TextBlock,
    TokenUsage,
    ToolUseBlock,
)


def _safe_int(value) -> int:
    """Coerce a possibly-missing usage field to int.

    Mirrors the behaviour of llm_wrapper.claude_wrapper._safe_int — keeps
    MagicMock and None values from poisoning metric streams.
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


# ---- continue inside class AnthropicChatProvider ----
```

Add the following methods inside the `AnthropicChatProvider` class (after `_content_block_to_wire`):

```python
    def _from_wire(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an Anthropic Message into a neutral ChatResponse.

        When output_format was set on the request, the response was
        forced into a respond_with_json tool call. We unwrap that here:
        parsed_output is set to the tool input, content is replaced with
        a single TextBlock carrying the JSON string, and stop_reason is
        normalised to "end_turn" so the caller sees a clean response
        rather than tool_use semantics.
        """
        content_blocks: list = []
        synthetic_input: dict | None = None
        for block in raw.content:
            if block.type == "text":
                content_blocks.append(_TextBlock(text=block.text))
            elif block.type == "tool_use":
                if (
                    output_format is not None
                    and block.name == "respond_with_json"
                ):
                    synthetic_input = block.input  # already a dict
                else:
                    content_blocks.append(
                        ToolUseBlock(
                            tool_use_id=block.id,
                            tool_name=block.name,
                            input=block.input,
                        )
                    )

        usage = raw.usage
        token_usage = TokenUsage(
            input_tokens=_safe_int(getattr(usage, "input_tokens", 0)),
            output_tokens=_safe_int(getattr(usage, "output_tokens", 0)),
            cache_read_tokens=_safe_int(getattr(usage, "cache_read_input_tokens", 0)),
            cache_creation_tokens=_safe_int(getattr(usage, "cache_creation_input_tokens", 0)),
        )

        if output_format is not None and synthetic_input is not None:
            return ChatResponse(
                content=[_TextBlock(text=json.dumps(synthetic_input))],
                parsed_output=synthetic_input,
                stop_reason="end_turn",
                model_used=self._active_model,
                usage=token_usage,
            )

        # Standard path
        stop_reason = raw.stop_reason or "end_turn"
        return ChatResponse(
            content=content_blocks,
            parsed_output=None,
            stop_reason=stop_reason,
            model_used=self._active_model,
            usage=token_usage,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestFromWire -v`
Expected: all `TestFromWire` cases pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/anthropic_provider.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): translate Anthropic SDK response to ChatResponse (#288)

Implements _from_wire with normal text, tool_use, output_format
unwrapping (synthetic respond_with_json tool), missing-field tolerance
on usage telemetry, and stop_reason passthrough.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: AnthropicChatProvider — call() with retry and OTel spans

**Files:**
- Modify: `agent_core/src/chat_provider/anthropic_provider.py`
- Modify: `agent_core/tests/test_chat_provider_anthropic.py`

This task lifts the retry loop from `agent_core/src/llm_wrapper/claude_wrapper.py:537-678` into the new provider, replacing `LLMResponse` with `ChatResponse` and using `record_call_metrics` from `chat_provider.metrics`.

**No fallback model logic** — fallback is removed in the new world. The adapter (Task 14) reproduces today's fallback behaviour by orchestrating two `AnthropicChatProvider` instances, leaving this provider single-model and clean.

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_anthropic.py`:

```python
import anthropic as _anthropic
from src.chat_provider.base import UnsupportedFeatureError


class TestCall:
    def test_normal_text_response(self):
        p = _make_provider()
        raw = _mk_anthropic_message(text="hi back", input_tokens=10, output_tokens=2)
        p._client.messages.create = MagicMock(return_value=raw)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert resp.content[0].text == "hi back"
        p._client.messages.create.assert_called_once()

    def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            p.call(req)

    def test_unsupported_feature_raises(self):
        p = _make_provider(features={"prompt_cache": False, "streaming": True, "image_input": True})
        long_text = "x" * 3500
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text=long_text, cache_hint="session")]),
        )
        with pytest.raises(UnsupportedFeatureError):
            p.call(req)

    def test_retry_on_rate_limit_then_success(self):
        p = _make_provider()
        raw_ok = _mk_anthropic_message(text="ok")
        rl_err = _anthropic.RateLimitError(
            message="slow down",
            response=MagicMock(status_code=429),
            body={"error": "rate"},
        )
        p._client.messages.create = MagicMock(side_effect=[rl_err, raw_ok])

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert p._client.messages.create.call_count == 2

    def test_exhausted_retries_returns_error_response(self):
        p = _make_provider()
        rl_err = _anthropic.RateLimitError(
            message="slow down",
            response=MagicMock(status_code=429),
            body={"error": "rate"},
        )
        p._client.messages.create = MagicMock(side_effect=rl_err)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert resp.content == []
        # retry_attempts in VALID_CONFIG is 2
        assert p._client.messages.create.call_count == 2

    def test_non_retryable_api_error_returns_error_response(self):
        p = _make_provider()
        api_err = _anthropic.APIError(
            message="bad",
            request=MagicMock(),
            body={"error": "x"},
        )
        p._client.messages.create = MagicMock(side_effect=api_err)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert p._client.messages.create.call_count == 1   # not retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestCall -v`
Expected: NotImplementedError on the first test (call() raises) — every test fails.

- [ ] **Step 3: Replace the placeholder `call()` with the lifted retry loop**

Replace the `call()` method body in `agent_core/src/chat_provider/anthropic_provider.py` with the implementation below. Also append the helper `_RetryableExhausted` sentinel and the OTel imports. The structure mirrors `claude_wrapper.py:537-678` but uses neutral types and the new metrics module.

Add at the top of the file (with the other imports):

```python
import time

from opentelemetry import trace as otel_trace

from src.chat_provider.base import UnsupportedFeatureError  # ensure imported
from src.chat_provider.metrics import record_call_metrics
from src.chat_provider.types import TokenUsage  # ensure imported
```

Add the sentinel at module scope (above the class):

```python
class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed.

    Caught only inside AnthropicChatProvider.call() / .stream() to
    transition into the error-response path. Never escapes.
    """
```

Replace the `call()` method on `AnthropicChatProvider` with:

```python
    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a single Anthropic call with retries on transient failures."""
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=False)

        try:
            return self._call_with_retry(request)
        except _RetryableExhausted:
            return ChatResponse(
                content=[],
                stop_reason="error",
                model_used=self._active_model,
                usage=TokenUsage(),
            )

    def _call_with_retry(self, request: ChatRequest) -> ChatResponse:
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
                    span.set_attribute("gen_ai.system", "anthropic")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")
                    raw = self._client.messages.create(**kwargs)
                    response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)
                    span.set_attribute(
                        "gen_ai.usage.input_tokens", response.usage.input_tokens or 0
                    )
                    span.set_attribute(
                        "gen_ai.usage.output_tokens", response.usage.output_tokens or 0
                    )
                    span.set_attribute(
                        "gen_ai.usage.cache_read_input_tokens",
                        response.usage.cache_read_tokens or 0,
                    )
                    span.set_attribute(
                        "gen_ai.usage.cache_creation_input_tokens",
                        response.usage.cache_creation_tokens or 0,
                    )

                record_call_metrics(
                    model=self._active_model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                    provider_system="anthropic",
                )
                logger.info(
                    "chat_provider.anthropic.call",
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_read_input_tokens": response.usage.cache_read_tokens,
                        "cache_creation_input_tokens": response.usage.cache_creation_tokens,
                    },
                )
                return response

            except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "chat_provider.anthropic.retryable_error",
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

            except anthropic.APIError as e:
                logger.error(
                    "chat_provider.anthropic.api_error",
                    extra={
                        "operation": "chat_provider.anthropic.call",
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
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

            except Exception as e:
                logger.error(
                    "chat_provider.anthropic.unexpected_error",
                    extra={
                        "operation": "chat_provider.anthropic.call",
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
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

        logger.error(
            "chat_provider.anthropic.exhausted",
            extra={
                "operation": "chat_provider.anthropic.call",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(
            f"All {self._max_attempts} retry attempts exhausted for model "
            f"{self._active_model}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestCall -v`
Expected: all `TestCall` cases pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/anthropic_provider.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): implement AnthropicChatProvider.call() with retry (#288)

Lifts the retry/timeout/OTel-span machinery from claude_wrapper.py and
emits provider-tagged metrics. No fallback-model logic — that lives
only in the adapter shim until PR5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: AnthropicChatProvider — stream() with retry and ToolUseRequested

**Files:**
- Modify: `agent_core/src/chat_provider/anthropic_provider.py`
- Modify: `agent_core/tests/test_chat_provider_anthropic.py`

This task lifts the streaming retry loop from `claude_wrapper.py:344-535`. The translation to neutral types is small: yields are still raw text; on tool_use the loop raises `ToolUseRequested(list[ToolUseBlock])`.

- [ ] **Step 1: Append the failing tests**

Append to `agent_core/tests/test_chat_provider_anthropic.py`:

```python
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested


class _FakeStreamEvent:
    def __init__(self, type_: str, text: str | None = None) -> None:
        self.type = type_
        self.delta = MagicMock()
        if text is not None:
            self.delta.text = text


class _FakeStream:
    def __init__(
        self,
        text_deltas: list[str],
        final_message: MagicMock,
    ) -> None:
        self._events = [_FakeStreamEvent("content_block_delta", text=t) for t in text_deltas]
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


def _install_stream(provider: AnthropicChatProvider, stream: _FakeStream) -> None:
    """Replace messages.stream(...) on the async client with a callable returning the stream."""
    provider._async_client.messages.stream = MagicMock(return_value=stream)


class TestStream:
    @pytest.mark.asyncio
    async def test_streams_text(self):
        p = _make_provider()
        final = _mk_anthropic_message(text="hello there", stop_reason="end_turn")
        stream = _FakeStream(text_deltas=["hello ", "there"], final_message=final)
        _install_stream(p, stream)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req):
            out.append(token)
        assert out == ["hello ", "there"]

    @pytest.mark.asyncio
    async def test_tool_use_raises(self):
        p = _make_provider()
        final = _mk_anthropic_message(
            tool_use={"id": "t_1", "name": "lookup", "input": {"q": "x"}},
            stop_reason="tool_use",
        )
        stream = _FakeStream(text_deltas=["checking"], final_message=final)
        _install_stream(p, stream)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        with pytest.raises(ChatToolUseRequested) as ei:
            async for token in p.stream(req):
                out.append(token)
        assert out == ["checking"]
        assert ei.value.tool_calls[0].tool_name == "lookup"

    @pytest.mark.asyncio
    async def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            async for _ in p.stream(req):
                pass

    @pytest.mark.asyncio
    async def test_output_format_on_stream_raises(self):
        p = _make_provider()
        of = OutputFormat(schema={})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            output_format=of,
        )
        with pytest.raises(UnsupportedFeatureError, match="stream"):
            async for _ in p.stream(req):
                pass

    @pytest.mark.asyncio
    async def test_abort_event_short_circuits(self):
        p = _make_provider()
        final = _mk_anthropic_message(text="hello there")
        stream = _FakeStream(text_deltas=["hel", "lo", " ", "there"], final_message=final)
        _install_stream(p, stream)

        abort = asyncio.Event()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req, abort_event=abort):
            out.append(token)
            if len(out) == 2:
                abort.set()
        assert out == ["hel", "lo"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestStream -v`
Expected: NotImplementedError or AttributeError on `stream`.

- [ ] **Step 3: Replace the placeholder `stream()` with the lifted streaming loop**

Append `import asyncio` to the imports section.

Replace `stream()` on `AnthropicChatProvider` with:

```python
    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text tokens from Anthropic.

        Same retry contract as call(); on exhausted retries the
        generator returns silently. Raises ToolUseRequested if the model
        emits any tool_use blocks (caller executes tools and resumes).
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=True)

        try:
            async for token in self._stream_with_retry(request, abort_event):
                yield token
        except _RetryableExhausted:
            return

    async def _stream_with_retry(
        self,
        request: ChatRequest,
        abort_event: "asyncio.Event | None",
    ) -> AsyncGenerator[str, None]:
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
                # The streaming SDK accepts the same kwargs as create(),
                # except `timeout` is also accepted as a positional
                # passthrough — we hand it the same dict.
                tool_calls: list[ToolUseBlock] = []
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0
                cache_creation_tokens = 0

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "anthropic")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    async with self._async_client.messages.stream(**kwargs) as stream:
                        async for event in stream:
                            if abort_event is not None and abort_event.is_set():
                                return
                            if hasattr(event, "type") and event.type == "content_block_delta":
                                if hasattr(event.delta, "text"):
                                    yield event.delta.text

                        final_message = await stream.get_final_message()
                        stop_reason = final_message.stop_reason
                        input_tokens = _safe_int(getattr(final_message.usage, "input_tokens", 0))
                        output_tokens = _safe_int(getattr(final_message.usage, "output_tokens", 0))
                        cache_read_tokens = _safe_int(
                            getattr(final_message.usage, "cache_read_input_tokens", 0)
                        )
                        cache_creation_tokens = _safe_int(
                            getattr(final_message.usage, "cache_creation_input_tokens", 0)
                        )
                        for block in final_message.content:
                            if block.type == "tool_use":
                                tool_calls.append(
                                    ToolUseBlock(
                                        tool_use_id=block.id,
                                        tool_name=block.name,
                                        input=block.input,
                                    )
                                )

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    span.set_attribute("gen_ai.usage.cache_read_input_tokens", cache_read_tokens)
                    span.set_attribute("gen_ai.usage.cache_creation_input_tokens", cache_creation_tokens)

                latency_ms = int((time.time() - start) * 1000)
                # Build a synthetic ChatResponse for metrics emission.
                synth_usage = TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                )
                synth_resp = ChatResponse(
                    content=[],
                    stop_reason=stop_reason or "end_turn",
                    model_used=self._active_model,
                    usage=synth_usage,
                )
                record_call_metrics(
                    model=self._active_model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=synth_resp,
                    provider_system="anthropic",
                )
                logger.info(
                    "chat_provider.anthropic.stream",
                    extra={
                        "operation": "chat_provider.anthropic.stream",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_input_tokens": cache_read_tokens,
                        "cache_creation_input_tokens": cache_creation_tokens,
                        "stop_reason": stop_reason,
                    },
                )

                if stop_reason == "tool_use" and tool_calls:
                    from src.chat_provider.base import ToolUseRequested
                    raise ToolUseRequested(tool_calls)

                return

            except _RetryableExhausted:
                raise

            except Exception as e:
                from src.chat_provider.base import ToolUseRequested
                if isinstance(e, ToolUseRequested):
                    raise
                if isinstance(e, (anthropic.APITimeoutError, anthropic.RateLimitError)):
                    last_error = e
                    logger.warning(
                        "chat_provider.anthropic.stream_retryable_error",
                        extra={
                            "operation": "chat_provider.anthropic.stream",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    continue
                # Non-retryable
                logger.error(
                    "chat_provider.anthropic.stream_error",
                    extra={
                        "operation": "chat_provider.anthropic.stream",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return

        logger.error(
            "chat_provider.anthropic.stream_exhausted",
            extra={
                "operation": "chat_provider.anthropic.stream",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model "
            f"{self._active_model}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestStream -v`
Expected: all `TestStream` cases pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/anthropic_provider.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): implement AnthropicChatProvider.stream() (#288)

Streaming with retry/backoff, abort_event support, OTel spans tagged
gen_ai.system=anthropic, and ToolUseRequested raised when the final
message includes tool_use blocks. output_format on stream() is rejected
by _validate_request per spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: build_chat_provider() factory + package exports

**Files:**
- Modify: `agent_core/src/chat_provider/__init__.py`
- Create: `agent_core/tests/test_chat_provider_factory.py`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_chat_provider_factory.py`:

```python
"""Tests for build_chat_provider() factory."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider import (
    ChatProviderBase,
    Capabilities,
    ProviderConfigError,
    build_chat_provider,
)


VALID_CONFIG = {
    "agent": {
        "provider": "anthropic",
        "primary_model": "claude-sonnet-4-5-20250514",
        "timeout_ms": 5000,
        "retry_attempts": 2,
    }
}


def test_returns_chat_provider_for_anthropic():
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        p = build_chat_provider(VALID_CONFIG["agent"])
    assert isinstance(p, ChatProviderBase)


def test_unknown_provider_raises():
    cfg = {**VALID_CONFIG["agent"], "provider": "wat"}
    with pytest.raises(ProviderConfigError, match="provider"):
        build_chat_provider(cfg)


def test_openai_not_implemented_yet():
    # PR1 ships Anthropic only; OpenAI lands in PR2.
    cfg = {**VALID_CONFIG["agent"], "provider": "openai"}
    with pytest.raises(ProviderConfigError, match="openai"):
        build_chat_provider(cfg)


def test_default_provider_is_anthropic_when_unspecified():
    cfg = {**VALID_CONFIG["agent"]}
    cfg.pop("provider")
    with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
        p = build_chat_provider(cfg)
    assert isinstance(p, ChatProviderBase)


def test_features_unknown_capability_raises():
    cfg = {
        **VALID_CONFIG["agent"],
        "features": {"prompt_cache": True, "made_up_feature": True},
    }
    with pytest.raises(ProviderConfigError, match="made_up_feature"):
        build_chat_provider(cfg)


def test_capabilities_is_re_exported():
    # The factory module must re-export Capabilities for downstream tests.
    assert Capabilities is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_factory.py -v`
Expected: ImportError on `build_chat_provider` and re-exports.

- [ ] **Step 3: Implement the factory and re-exports**

Replace `agent_core/src/chat_provider/__init__.py` with:

```python
"""
agent_core/src/chat_provider — provider-neutral LLM interface.

Public surface:
    ChatProviderBase  — ABC every provider implements.
    Capabilities      — frozen dataclass declared per provider class.
    build_chat_provider(agent_config) — factory; sole construction path.

All other names (TextBlock, ChatRequest, etc.) are exposed via
chat_provider.types.

This package replaces agent_core/src/llm_wrapper/ over PRs #288–#292.
"""

from __future__ import annotations

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ChatProviderError,
    ProviderAPIError,
    ProviderConfigError,
    ToolUseRequested,
    UnsupportedFeatureError,
)


_KNOWN_FEATURE_KEYS = {"prompt_cache", "streaming", "image_input"}


def build_chat_provider(agent_config: dict) -> ChatProviderBase:
    """Construct the configured ChatProviderBase implementation.

    Args:
        agent_config: the `agent.*` sub-tree of the merged YAML config.
            Required keys: primary_model, timeout_ms, retry_attempts.
            Optional keys: provider (default 'anthropic'),
            retry_backoff_seconds, features.{prompt_cache, streaming,
            image_input}.

    Returns:
        ChatProviderBase: the concrete provider chosen by
        agent_config["provider"].

    Raises:
        ProviderConfigError: provider is unknown, or features carry an
            unrecognised key, or a required config field is missing.
    """
    provider_name = agent_config.get("provider", "anthropic")

    features = agent_config.get("features") or {}
    unknown = set(features.keys()) - _KNOWN_FEATURE_KEYS
    if unknown:
        raise ProviderConfigError(
            f"Unknown feature key(s) in agent.features: {sorted(unknown)}. "
            f"Known keys: {sorted(_KNOWN_FEATURE_KEYS)}."
        )

    if provider_name == "anthropic":
        # Lazy import keeps the dependency localised.
        from src.chat_provider.anthropic_provider import AnthropicChatProvider
        return AnthropicChatProvider(agent_config)

    if provider_name == "openai":
        raise ProviderConfigError(
            "Provider 'openai' is not yet implemented. "
            "OpenAI support lands in PR2 (issue #289)."
        )

    raise ProviderConfigError(
        f"Unknown provider '{provider_name}'. Known providers: 'anthropic'."
    )


__all__ = [
    "Capabilities",
    "ChatProviderBase",
    "ChatProviderError",
    "ProviderAPIError",
    "ProviderConfigError",
    "ToolUseRequested",
    "UnsupportedFeatureError",
    "build_chat_provider",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_factory.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/__init__.py agent_core/tests/test_chat_provider_factory.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): add build_chat_provider() factory and package exports (#288)

Selects between providers by agent.provider; rejects unknown providers
and unknown feature keys at startup. OpenAI support is gated until PR2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Convert ClaudeLLMWrapper into an adapter over AnthropicChatProvider

**Files:**
- Modify: `agent_core/src/llm_wrapper/claude_wrapper.py`

This is the centerpiece of PR1: rewrite `ClaudeLLMWrapper` so that internally it delegates to one or two `AnthropicChatProvider` instances (primary + fallback), translates the legacy Anthropic-shaped inputs into neutral `ChatRequest`, and translates `ChatResponse` back into today's `LLMResponse`.

**Critical contract:** every behaviour observable by today's tests in `test_llm_wrapper.py` and `test_llm_wrapper_caching.py` must be preserved — including primary→fallback model switching on retry exhaustion. The new `AnthropicChatProvider` has no fallback; the adapter implements fallback by holding two providers and orchestrating between them.

The adapter's `LLMResponse` mapping:

| `LLMResponse` field          | Source                                       |
|------------------------------|----------------------------------------------|
| `content`                    | first `TextBlock.text` in `ChatResponse.content`, else `None` |
| `tool_calls`                 | every `ToolUseBlock` in `ChatResponse.content`, mapped to `ToolCall` |
| `stop_reason`                | `ChatResponse.stop_reason`                   |
| `model_used`                 | `ChatResponse.model_used`                    |
| `input_tokens`               | `ChatResponse.usage.input_tokens or 0`       |
| `output_tokens`              | `ChatResponse.usage.output_tokens or 0`      |
| `cache_read_input_tokens`    | `ChatResponse.usage.cache_read_tokens or 0`  |
| `cache_creation_input_tokens`| `ChatResponse.usage.cache_creation_tokens or 0` |

- [ ] **Step 1: Replace claude_wrapper.py with the adapter**

Replace the entire content of `agent_core/src/llm_wrapper/claude_wrapper.py` with:

```python
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

from src.chat_provider.anthropic_provider import AnthropicChatProvider
from src.chat_provider.base import ToolUseRequested as _ChatToolUseRequested
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
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


class ClaudeLLMWrapper(LLMWrapperBase):
    """Legacy adapter — delegates to AnthropicChatProvider.

    Holds two providers internally:
        _primary  — calls always start here.
        _fallback — used after the primary exhausts its retries on
                    transient errors, mirroring today's behaviour.

    The fallback flips permanently for the life of the wrapper, matching
    pre-redesign semantics.
    """

    def __init__(self, config: dict) -> None:
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

        primary_cfg = {
            **config,
            "primary_model": config["primary_model"],
        }
        fallback_cfg = {
            **config,
            "primary_model": config["fallback_model"],
        }
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
        if not messages:
            raise ValueError("messages must not be empty")
        request = self._build_request(messages, tools, system, output_format)

        if model_override is not None:
            # Honour explicit override regardless of fallback state.
            override_provider = AnthropicChatProvider(
                {**self._primary_config_snapshot(), "primary_model": model_override}
            )
            response = override_provider.call(request)
            return self._to_legacy_response(response)

        response = self._active.call(request)
        if response.stop_reason == "error" and self._active is self._primary:
            logger.warning(
                "llm_wrapper.fallback_triggered",
                extra={
                    "operation": "llm_wrapper.call",
                    "primary_model": self._primary.get_active_model(),
                },
            )
            self._active = self._fallback
            response = self._active.call(request)
        return self._to_legacy_response(response)

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
        if not messages:
            raise ValueError("messages must not be empty")
        request = self._build_request(
            messages, tools or [], system or "", output_format=None, max_tokens=max_tokens
        )

        active = self._active if model_override is None else AnthropicChatProvider(
            {**self._primary_config_snapshot(), "primary_model": model_override}
        )

        any_yielded = False
        try:
            async for token in active.stream(request, abort_event=abort_event):
                any_yielded = True
                yield token
            return
        except _ChatToolUseRequested as exc:
            # Translate neutral ToolUseBlock list to legacy ToolCall list.
            legacy_calls = [
                ToolCall(
                    tool_name=tu.tool_name,
                    tool_use_id=tu.tool_use_id,
                    input_params=tu.input,
                )
                for tu in exc.tool_calls
            ]
            raise ToolUseRequested(legacy_calls)
        except Exception:
            # Same fallback semantics as the legacy wrapper:
            # only swap to fallback if we yielded nothing yet.
            if any_yielded or model_override is not None or active is self._fallback:
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
                async for token in self._fallback.stream(request, abort_event=abort_event):
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

    def get_active_model(self) -> str:
        return self._active.get_active_model()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _primary_config_snapshot(self) -> dict:
        # Reconstruct minimum config for spawning a one-off provider.
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
        neutral_messages = [self._message_from_legacy(m) for m in messages]
        neutral_system = self._system_from_legacy(system)
        neutral_tools = [self._tool_from_legacy(t) for t in tools]
        of = None
        if output_format is not None:
            from src.chat_provider.types import OutputFormat
            of = OutputFormat(schema=output_format.get("schema", output_format))
        kwargs = dict(
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
                    # Legacy callers don't construct images; skip.
                    continue
        return Message(role=role, content=blocks)

    @staticmethod
    def _system_from_legacy(system) -> SystemPrompt | None:
        if not system:
            return None
        if isinstance(system, str):
            return SystemPrompt(blocks=[TextBlock(text=system, cache_hint="session")])
        # Already a list of Anthropic-shaped blocks
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
        return ToolDefinition(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("input_schema", {}),
        )

    @staticmethod
    def _to_legacy_response(resp: ChatResponse) -> LLMResponse:
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
```

- [ ] **Step 2: Run the existing wrapper tests**

Run: `cd agent_core && uv run pytest tests/test_llm_wrapper.py tests/test_llm_wrapper_caching.py -v`
Expected: all tests pass. The legacy tests mock `anthropic.Anthropic.messages.create` and `anthropic.AsyncAnthropic.messages.stream` — those are now reached via the adapter's two underlying providers. Some tests instantiate two clients (primary + fallback) and assert call counts on each. Adapt the test side only if a test inspects the wrapper's *internal* `_client` directly; in that case patch the providers on the adapter (`wrapper._primary._client`, `wrapper._fallback._client`).

If any test fails because it patches `wrapper._client` directly, change those patches to `wrapper._primary._client` (and `wrapper._fallback._client` for fallback assertions). Document each such change in the commit message so reviewers understand the scope.

- [ ] **Step 3: Run the broader test suite**

Run: `cd agent_core && uv run pytest -x`
Expected: all tests pass. If anything fails, fix the smallest possible thing and re-run.

- [ ] **Step 4: Commit**

```bash
git add agent_core/src/llm_wrapper/claude_wrapper.py agent_core/tests/test_llm_wrapper.py agent_core/tests/test_llm_wrapper_caching.py
git commit -m "$(cat <<'EOF'
refactor(llm-wrapper): make ClaudeLLMWrapper a thin adapter over AnthropicChatProvider (#288)

Holds two AnthropicChatProvider instances (primary + fallback) and
orchestrates fallback at the adapter level — the new provider has no
fallback of its own. Translates legacy Anthropic-shaped messages and
LLMResponse to/from the neutral chat_provider types.

Existing tests updated only where they patched _client directly; they
now patch wrapper._primary._client / wrapper._fallback._client.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Coverage check, broad regression run, push, open draft PR

- [ ] **Step 1: Run the full test suite with coverage**

Run: `cd agent_core && uv run pytest --cov=src/chat_provider --cov-report=term-missing`
Expected: all tests pass. Coverage on `agent_core/src/chat_provider/` is ≥ 70%. If any line in `_to_wire` / `_from_wire` / `call` / `stream` is uncovered, add a targeted test before continuing.

- [ ] **Step 2: Run the entire agent_core suite**

Run: `cd agent_core && uv run pytest`
Expected: every existing test passes (the spec mandates 457+ tests still green).

- [ ] **Step 3: Static grep for accidental Anthropic SDK leakage**

Run: `grep -rn "import anthropic" agent_core/src/`
Expected: matches only inside `agent_core/src/chat_provider/anthropic_provider.py` and `agent_core/src/llm_wrapper/claude_wrapper.py`. The latter is the adapter and is removed in PR5.

Run: `grep -rn "import openai" agent_core/src/`
Expected: zero matches (OpenAI lands in PR2).

- [ ] **Step 4: Push and open the draft PR**

```bash
git push -u origin pr1/scaffold-chat-provider
gh pr create \
  --base feature/llm-provider-redesign \
  --head pr1/scaffold-chat-provider \
  --title "PR1: scaffold chat_provider/ package + AnthropicChatProvider + adapter shim" \
  --body "$(cat <<'EOF'
Closes #288. Parent: #287.

## Summary

- New package `agent_core/src/chat_provider/` with neutral Pydantic types, `ChatProviderBase`, `AnthropicChatProvider`, and a `build_chat_provider()` factory.
- `ClaudeLLMWrapper` becomes a thin adapter over two `AnthropicChatProvider` instances (primary + fallback). The new provider has no fallback logic; that lives only in the adapter until PR5.
- Existing 457+ tests still target `LLMWrapperBase` and pass against the adapter, proving the round-trip is behaviour-preserving.

## Test plan

- [x] `uv run pytest` passes
- [x] `uv run pytest --cov=src/chat_provider` reports ≥70% coverage
- [x] `grep -rn "import anthropic" agent_core/src/` returns only `chat_provider/anthropic_provider.py` and `llm_wrapper/claude_wrapper.py`
- [x] `grep -rn "import openai" agent_core/src/` returns zero matches

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" \
  --draft
```

- [ ] **Step 5: Final commit if anything was tweaked while running coverage**

If the coverage step revealed a missing test or untouched edge, commit the addition with:

```bash
git add agent_core/tests/
git commit -m "$(cat <<'EOF'
test(chat-provider): close coverage gaps revealed by --cov-report (#288)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push
```

---

## Self-review checklist (run after writing the plan, before handoff)

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §4 Architecture & file layout | Task 1 |
| §5 Neutral types — content blocks | Tasks 2, 3, 4 |
| §6 Capabilities + configuration validation | Tasks 5, 8 (init), 13 (factory) |
| §7 ChatProviderBase ABC + `_validate_request` | Tasks 5, 6 |
| §7 Per-method capability matrix (output_format on stream raises) | Task 6 (`test_rejects_output_format_on_stream_even_with_capability`); enforced in Task 12 |
| §8 Anthropic `_to_wire` (every block, tool_choice variants, image, output_format emulation) | Task 9 |
| §8 Anthropic `_from_wire` (text, tool_use, output_format unwrap) | Task 10 |
| §8 Anthropic `call()` retry + spans | Task 11 |
| §8 Anthropic `stream()` + ToolUseRequested + abort_event | Task 12 |
| §9 Migration — adapter preserves all old tests | Task 14 |
| §10 Wire-format snapshot tests + capability matrix tests | Tasks 9, 13 |
| §11 OTel metrics moved with same names + `gen_ai.system` attribute | Tasks 7, 11, 12 |

**Placeholder scan:** none — every step has executable code or commands.

**Type consistency:** every reference (`TextBlock`, `ChatRequest`, `Capabilities`, `_validate_request(is_stream=…)`, `record_call_metrics`, `_RetryableExhausted`, `ToolUseRequested`) is defined in an earlier task before it is used in a later one.

**Scope:** PR1 only. PR2 (OpenAI), PR3–PR4 (caller migrations), PR5 (cleanup) are explicit out-of-scope for this plan and have their own sub-issues.
