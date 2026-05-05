# LLM Provider Redesign — PR2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add `OpenAIChatProvider` (the second concrete `ChatProviderBase`), wire it into the factory, and land the three-layer capability/config validation.

**Architecture:** New file `agent_core/src/chat_provider/openai_provider.py` is the only place in agent_core that imports `openai`. The provider implements `_to_wire` / `_from_wire` for OpenAI Chat Completions, sync `call()` and async `stream()` with tool-call delta accumulation, and structured output via native `response_format={"type": "json_schema", ...}`. `build_chat_provider()` selects between Anthropic and OpenAI by `agent.provider`. `schema/config.py` learns about `agent.provider` and `agent.features.*`. No callers change.

**Tech Stack:** Python 3.11+, Pydantic v2, `openai>=1.50.0` (new dep), existing `anthropic`, `opentelemetry`, `pytest` + `pytest-asyncio`, `uv`.

**Tracking:** Parent #287; this PR resolves #289. Branch: `pr2/openai-provider` off `feature/llm-provider-redesign`. PR target: `feature/llm-provider-redesign`.

**Spec:** `docs/superpowers/specs/2026-04-30-llm-provider-redesign-design.md`

---

## File structure

```
agent_core/src/chat_provider/
├── openai_provider.py           # NEW — only file that imports `openai`
├── __init__.py                  # MODIFIED — factory selects OpenAI when configured; capability reconciliation lands
├── base.py                      # UNCHANGED (PR1 already enforces output_format-on-stream rejection)
├── types.py                     # UNCHANGED
├── anthropic_provider.py        # UNCHANGED
└── metrics.py                   # UNCHANGED

agent_core/src/schema/
└── config.py                    # MODIFIED — AgentConfig gains `provider` and `features` fields

dev-kit/dpg/
└── agent_core.yaml              # MODIFIED — adds agent.provider + agent.features defaults

agent_core/
├── pyproject.toml               # MODIFIED — adds openai>=1.50.0 dep
└── tests/
    ├── test_chat_provider_openai.py    # NEW — provider unit tests + wire-format snapshots
    ├── test_chat_provider_factory.py   # MODIFIED — capability-reconciliation matrix
    └── test_schema_config.py           # MODIFIED — validates new agent.* fields
```

## Conventions

- Each step assumes `cwd = agent_core/` unless stated otherwise.
- Test command: `uv run pytest -x` (stop at first failure) for the focused runs; `uv run pytest` for the broad checks.
- Pydantic v2 idioms throughout. `extra="forbid"` is the existing schema policy.
- Every commit body ends with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- Branch policy: **every commit lands on `pr2/openai-provider`**. The implementer must `git branch --show-current` before any commit and `git checkout pr2/openai-provider` if it returns anything else.

## Critical OpenAI SDK behaviours to remember

- `openai.OpenAI()` and `openai.AsyncOpenAI()` need `OPENAI_API_KEY` in env (or `api_key=` kwarg). For tests, `patch("openai.OpenAI")` and `patch("openai.AsyncOpenAI")` intercept construction.
- Sync request: `client.chat.completions.create(**kwargs)` returns a `ChatCompletion` object.
- Stream: `client.chat.completions.create(stream=True, **kwargs)` returns a sync iterable; for async, `async_client.chat.completions.create(stream=True, ...)` returns an async iterable. Each chunk is `ChatCompletionChunk` with `choices[0].delta.content` (str | None) and `choices[0].delta.tool_calls` (list with partial `function.arguments` strings keyed by `index`).
- Tool-call deltas are partial: each delta carries `index`, optionally `id`, optionally `function.name`, and `function.arguments` (a string fragment). Accumulate strings keyed by index, then `json.loads` at stream end.
- `max_tokens` is deprecated in favour of `max_completion_tokens` for newer models (gpt-4o, o-series). Strategy in this PR: **always send `max_completion_tokens`** — it's accepted by every model OpenAI currently supports for chat completions, including older ones via SDK normalisation. If a model rejects it, the adapter user sees a clear error.
- `response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": True}}` is the strict structured-output mode (gpt-4o-2024-08-06+).
- OpenAI exception classes the retry loop catches: `openai.APITimeoutError`, `openai.RateLimitError` (transient — retry), `openai.APIError` (other non-retryable). Same shape as `anthropic.*`.
- OpenAI does not expose `cache_read_input_tokens` / `cache_creation_input_tokens` in the standard response. **Do not invent fields** — leave `cache_read_tokens` / `cache_creation_tokens` on `TokenUsage` as `None` to honour the "None means not supported" contract.

---

## Task 1: Branch off; add `openai` dependency

**Files:**
- Modify: `agent_core/pyproject.toml`

- [ ] **Step 1: Branch off**

```bash
git fetch origin
git checkout -b pr2/openai-provider origin/feature/llm-provider-redesign
```

- [ ] **Step 2: Add `openai>=1.50.0` to dependencies**

In `agent_core/pyproject.toml`, find the `dependencies = [ ... ]` block. Add the OpenAI SDK in alphabetical order with the existing `anthropic` line:

```toml
dependencies = [
    "anthropic>=0.40.0", # SDK used exclusively in chat_provider/anthropic_provider.py
    "openai>=1.50.0", # SDK used exclusively in chat_provider/openai_provider.py
    "httpx>=0.27.0", # HTTP clients for all downstream DPG services
    "pydantic>=2.0", # Request/response models for FastAPI server
    ...
]
```

The comment on the `anthropic` line in the existing file says `# SDK used exclusively in llm_wrapper/claude_wrapper.py` — update that to `# SDK used exclusively in chat_provider/anthropic_provider.py` so both lines are consistent and accurate post-PR1.

- [ ] **Step 3: Sync the env**

```bash
cd agent_core && uv sync
```

Expected: a new `openai` line (and any transitive deps) installed. No errors.

- [ ] **Step 4: Confirm import works**

```bash
cd agent_core && uv run python -c "import openai; print(openai.__version__)"
```

Expected: prints a version `>=1.50.0`. If lower, the lockfile needs a refresh — re-run `uv sync` and re-check.

- [ ] **Step 5: Commit**

```bash
git add agent_core/pyproject.toml agent_core/uv.lock 2>/dev/null
git commit -m "$(cat <<'EOF'
chore(agent-core): add openai>=1.50.0 dependency for OpenAIChatProvider (#289)

PR2 introduces openai_provider.py as the sole place in agent_core that
imports openai. Updates the comment on the anthropic dependency so both
SDK references point at chat_provider/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If `uv.lock` doesn't exist yet (some repos commit it, some don't), only stage `pyproject.toml`.

---

## Task 2: Extend `AgentConfig` schema with `provider` and `features` fields

**Files:**
- Modify: `agent_core/src/schema/config.py` (around line 189 — `class AgentConfig`)
- Modify: `agent_core/tests/test_schema_config.py`

The schema is `extra="forbid"`, so without this task, any YAML containing `agent.provider` or `agent.features` would fail to load. After this task, both fields are accepted and validated.

- [ ] **Step 1: Append failing tests**

Append to `agent_core/tests/test_schema_config.py` (a new test class — don't disturb existing tests):

```python
class TestAgentProviderAndFeatures:
    """PR2 — agent.provider and agent.features schema additions."""

    def _base_agent(self) -> dict:
        return {"primary_model": "x", "fallback_model": "y"}

    def test_provider_defaults_to_anthropic(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate(self._base_agent())
        assert cfg.provider == "anthropic"

    def test_provider_accepts_known_values(self):
        from src.schema.config import AgentConfig
        for p in ("anthropic", "openai"):
            cfg = AgentConfig.model_validate({**self._base_agent(), "provider": p})
            assert cfg.provider == p

    def test_provider_rejects_unknown_values(self):
        from pydantic import ValidationError
        from src.schema.config import AgentConfig
        with pytest.raises(ValidationError):
            AgentConfig.model_validate({**self._base_agent(), "provider": "wat"})

    def test_features_default_all_none(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate(self._base_agent())
        # features is a sub-model with all-None defaults; provider defaults
        # are applied at the chat_provider factory layer.
        assert cfg.features.prompt_cache is None
        assert cfg.features.streaming is None
        assert cfg.features.image_input is None

    def test_features_accepts_partial(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate({
            **self._base_agent(),
            "features": {"prompt_cache": False},
        })
        assert cfg.features.prompt_cache is False
        assert cfg.features.streaming is None

    def test_features_rejects_unknown_keys(self):
        from pydantic import ValidationError
        from src.schema.config import AgentConfig
        with pytest.raises(ValidationError):
            AgentConfig.model_validate({
                **self._base_agent(),
                "features": {"made_up": True},
            })
```

(Make sure `pytest` is already imported at the top of the file. If not, add `import pytest`.)

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd agent_core && uv run pytest tests/test_schema_config.py::TestAgentProviderAndFeatures -v
```

Expected: every test fails with `ValidationError` because `provider` is currently rejected (`extra="forbid"`).

- [ ] **Step 3: Update `AgentConfig`**

In `agent_core/src/schema/config.py`, just before `class AgentConfig`, add a sibling sub-model:

```python
class FeaturesConfig(BaseModel):
    """Per-deployment chat-provider feature toggles.

    None means "use the provider's intrinsic capability." A bool tightens
    the effective feature for this deployment. Cannot widen — the
    chat_provider factory rejects True against a False capability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_cache: bool | None = None
    streaming: bool | None = None
    image_input: bool | None = None
```

Then in `class AgentConfig`, add two fields (place them right after `fallback_model`):

```python
    provider: Literal["anthropic", "openai"] = "anthropic"
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
```

`Literal` is already imported in this file (it's used for other enum-like fields). If not, add `from typing import Literal` to the imports at the top of the file.

- [ ] **Step 4: Run tests**

```bash
cd agent_core && uv run pytest tests/test_schema_config.py -v
```

Expected: every existing test still passes plus the 6 new ones.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/schema/config.py agent_core/tests/test_schema_config.py
git commit -m "$(cat <<'EOF'
feat(schema): add agent.provider + agent.features to AgentConfig (#289)

Adds the YAML keys the PR2 chat_provider factory will read:
- agent.provider — Literal["anthropic", "openai"], defaults to anthropic
- agent.features.{prompt_cache, streaming, image_input} — each
  optional bool|None, where None means "use provider capability"

FeaturesConfig keeps extra="forbid" so unknown feature keys fail at
config load time rather than being silently ignored.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `dev-kit/dpg/agent_core.yaml` with the new keys

**Files:**
- Modify: `dev-kit/dpg/agent_core.yaml`

This task adds the keys with defaults and inline comments. Domain configs (`dev-kit/configs/<domain>/agent_core.yaml`) are NOT updated here — that lives in PR5 alongside other doc/config touches.

- [ ] **Step 1: Edit the file**

Find the `agent:` block (around line 8). Add the two new keys at the bottom of the `agent:` block, just before any comment marking the end of the section:

```yaml
agent:
  ask_for_consent: false
  consent_prompt: ""
  timeout_ms: 10000
  retry_attempts: 2
  retry_backoff_seconds: [0, 0.5, 1.0]
  max_tool_rounds: 3
  termination_short_circuit:
    enabled: true
    confidence_threshold: 0.7
  recent_tool_exchanges:
    max_items: 3
    max_chars: 4000

  # Provider selection. Switching this away from 'anthropic' requires the
  # corresponding API key in the environment (OPENAI_API_KEY for openai).
  # Capabilities are intrinsic to the provider; this YAML can only
  # tighten optional feature flags via agent.features below.
  provider: anthropic

  # Optional per-deployment feature toggles. Each defaults to "use the
  # provider's intrinsic capability" when omitted. Setting a flag to
  # true against a provider that lacks the capability fails at startup
  # with a ProviderConfigError.
  features:
    # prompt_cache: true   # Anthropic: enabled by capability. OpenAI: not supported (yet).
    # streaming: true      # Both providers support streaming natively.
    # image_input: true    # Both providers support image input.
```

(Leave the example sub-keys commented out so the file documents the surface without forcing every domain to opt in.)

- [ ] **Step 2: Smoke-validate**

There is no automated YAML linter here; manual eye-check is sufficient. Confirm:
- `provider:` is at the same indent level as `timeout_ms:`.
- `features:` is at the same indent level as `provider:`.
- The commented example sub-keys are indented two further spaces (under `features:`).

- [ ] **Step 3: Commit**

```bash
git add dev-kit/dpg/agent_core.yaml
git commit -m "$(cat <<'EOF'
feat(dev-kit): add agent.provider + agent.features defaults to dpg yaml (#289)

Adds the new schema keys introduced in commit (Task 2) with comments
explaining the contract. Domain configs are updated in PR5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: OpenAIChatProvider — class skeleton, capabilities, init

**Files:**
- Create: `agent_core/src/chat_provider/openai_provider.py`
- Create: `agent_core/tests/test_chat_provider_openai.py`

- [ ] **Step 1: Write failing tests**

Create `agent_core/tests/test_chat_provider_openai.py`:

```python
"""Tests for OpenAIChatProvider."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.chat_provider.openai_provider import OpenAIChatProvider
from src.chat_provider.base import Capabilities, ProviderConfigError


VALID_CONFIG = {
    "primary_model": "gpt-4o-2024-08-06",
    "timeout_ms": 5000,
    "retry_attempts": 2,
    "retry_backoff_seconds": [0, 0.0, 0.0],
    "features": {
        "prompt_cache": False,   # OpenAI cap is False; matching here is a no-op.
        "streaming": True,
        "image_input": True,
    },
}


class TestInit:
    def test_capabilities(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(VALID_CONFIG)
        caps = p.capabilities
        assert isinstance(caps, Capabilities)
        assert caps.supports_tools is True
        assert caps.supports_streaming is True
        assert caps.supports_prompt_cache is False
        assert caps.supports_image_input is True
        assert caps.supports_audio_input is False
        assert caps.supports_structured_output is True
        assert caps.supports_force_tool_choice is True

    def test_features_defaults_match_capability(self):
        # Empty features dict → effective features come from capabilities.
        cfg = {**VALID_CONFIG, "features": {}}
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(cfg)
        assert p._features["streaming"] is True
        assert p._features["image_input"] is True
        assert p._features["prompt_cache"] is False  # capability is False

    def test_empty_config_raises(self):
        with pytest.raises(ProviderConfigError):
            OpenAIChatProvider({})

    def test_missing_primary_model_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("primary_model")
        with pytest.raises(ProviderConfigError, match="primary_model"):
            OpenAIChatProvider(cfg)

    def test_missing_timeout_raises(self):
        cfg = {**VALID_CONFIG}
        cfg.pop("timeout_ms")
        with pytest.raises(ProviderConfigError, match="timeout_ms"):
            OpenAIChatProvider(cfg)

    def test_get_active_model(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(VALID_CONFIG)
        assert p.get_active_model() == "gpt-4o-2024-08-06"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v
```

Expected: ImportError on `OpenAIChatProvider`.

- [ ] **Step 3: Create the provider skeleton**

Create `agent_core/src/chat_provider/openai_provider.py`:

```python
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
        raise NotImplementedError("Implemented in Task 7")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Implemented in Task 8")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        return self._active_model
```

- [ ] **Step 4: Run tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py::TestInit -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/tests/test_chat_provider_openai.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): scaffold OpenAIChatProvider with init + capabilities (#289)

Capability declaration matches the OpenAI Chat Completions API today:
streaming, tools, image input, structured output (native JSON schema),
forced tool_choice — yes. Prompt cache, audio input — no.

call() and stream() raise NotImplementedError; filled in subsequent
commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: OpenAIChatProvider `_to_wire`

**Files:**
- Modify: `agent_core/src/chat_provider/openai_provider.py`
- Modify: `agent_core/tests/test_chat_provider_openai.py`

OpenAI's request shape is fundamentally different from Anthropic's:
- System prompt is a **prepended message** with `role="system"`, not a top-level kwarg.
- `Message.content` is either a string (for plain text) or a list of content parts (when images are involved). Mixed text+image content always uses the parts array.
- Tool definitions live under `tools=[{"type": "function", "function": {name, description, parameters}}]`.
- `tool_choice`: `"auto"` | `"required"` | `"none"` | `{"type": "function", "function": {"name": ...}}`.
- Tool-use in **assistant prior turns** maps to `tool_calls=[{id, type:"function", function:{name, arguments: json.dumps(input)}}]` on the message.
- Tool results are SEPARATE messages with `role="tool"`, `tool_call_id`, `content`.
- `OutputFormat` → `response_format={"type":"json_schema","json_schema":{"name":"out","schema":..., "strict": ...}}`.
- `max_tokens` (legacy) vs `max_completion_tokens` (newer). This PR sends only `max_completion_tokens`.

- [ ] **Step 1: Append failing wire-format tests**

Append to `agent_core/tests/test_chat_provider_openai.py`:

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


def _make_provider(features: dict | None = None) -> OpenAIChatProvider:
    cfg = dict(VALID_CONFIG)
    if features is not None:
        cfg["features"] = features
    with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
        return OpenAIChatProvider(cfg)


class TestToWire:
    def test_minimal_text_request(self):
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire == {
            "model": "gpt-4o-2024-08-06",
            "max_completion_tokens": 4096,
            "messages": [{"role": "user", "content": "hi"}],
            "timeout": 5.0,
        }

    def test_system_prompt_concatenated_at_head(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[
                TextBlock(text="You are helpful."),
                TextBlock(text="Be concise."),
            ]),
        )
        wire = p._to_wire(req)
        assert wire["messages"][0] == {
            "role": "system",
            "content": "You are helpful.\n\nBe concise.",
        }
        assert wire["messages"][1]["role"] == "user"

    def test_text_only_message_uses_string_content(self):
        # Single TextBlock → content is a string, not a list of parts.
        p = _make_provider()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == "hi"

    def test_image_block_uses_content_parts(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="describe"), img])]
        )
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
        ]

    def test_image_base64_uses_data_url(self):
        p = _make_provider()
        img = ImageBlock(source=ImageSource(kind="base64", media_type="image/png", data="ABC=="))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        wire = p._to_wire(req)
        assert wire["messages"][0]["content"] == [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC=="}},
        ]

    def test_tool_definition(self):
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
        assert wire["tools"] == [{
            "type": "function",
            "function": {
                "name": "get_x",
                "description": "get x",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }]

    def test_tool_choice_auto(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="auto",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == "auto"

    def test_tool_choice_any_maps_to_required(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="any",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == "required"

    def test_tool_choice_none_drops_tools(self):
        p = _make_provider()
        td = ToolDefinition(name="x", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="none",
        )
        wire = p._to_wire(req)
        assert "tools" not in wire and "tool_choice" not in wire

    def test_tool_choice_named(self):
        p = _make_provider()
        td = ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            tools=[td], tool_choice="my_tool",
        )
        wire = p._to_wire(req)
        assert wire["tool_choice"] == {
            "type": "function",
            "function": {"name": "my_tool"},
        }

    def test_assistant_tool_use_and_tool_result_messages(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="look it up")]),
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking"),
                        ToolUseBlock(tool_use_id="call_abc", tool_name="lookup", input={"q": "x"}),
                    ],
                ),
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="call_abc", content="42")],
                ),
            ]
        )
        wire = p._to_wire(req)
        # Assistant message: text in content, tool_call in tool_calls.
        assert wire["messages"][1] == {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "x"}'},
            }],
        }
        # Tool result: separate role="tool" message with tool_call_id.
        assert wire["messages"][2] == {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "42",
        }

    def test_output_format_native_response_format(self):
        p = _make_provider()
        of = OutputFormat(
            schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
        )
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="answer")])],
            output_format=of,
        )
        wire = p._to_wire(req)
        assert wire["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "out",
                "schema": of.schema,
                "strict": True,
            },
        }

    def test_max_tokens_passed_through(self):
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            max_tokens=200,
        )
        wire = p._to_wire(req)
        assert wire["max_completion_tokens"] == 200
```

- [ ] **Step 2: Run tests; expect AttributeError on `_to_wire`.**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py::TestToWire -v
```

- [ ] **Step 3: Implement `_to_wire`**

Add `import json` to the imports at the top of `agent_core/src/chat_provider/openai_provider.py` if not already there.

Append the following methods INSIDE `class OpenAIChatProvider` (after `get_active_model`):

```python
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
        """Build one OpenAI message from a list of content blocks (sans tool_results)."""
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
            msg["content"] = "\n\n".join(tb.text for tb in text_blocks) if len(text_blocks) > 1 else text_blocks[0].text
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
    def _image_url(block) -> dict[str, str]:
        src = block.source
        if src.kind == "url":
            return {"url": src.url}
        # base64 → data URL
        return {"url": f"data:{src.media_type};base64,{src.data}"}
```

Note on the "assistant turn that's just tool_calls" branch (`msg["content"] = None if tool_use_blocks else ""`): some OpenAI SDK versions reject `content=""` for assistant messages with `tool_calls`; the strict-correct value is `None`. The current test suite doesn't exercise that exact case, but defaulting to `None` when tool_calls are present is safer.

- [ ] **Step 4: Run all tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v
```

Expected: 19 tests pass (6 init + 13 to_wire).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/tests/test_chat_provider_openai.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): translate ChatRequest to OpenAI SDK shape (#289)

Implements OpenAIChatProvider._to_wire. Snapshot-tested for every
content-block type, system-prompt prepending, tool definitions, all
tool_choice variants (auto/any→required/none/named), image input
(URL + base64 data URL), assistant prior-turn tool_calls, separate
role="tool" tool-result messages, and native response_format
structured output.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: OpenAIChatProvider `_from_wire`

**Files:**
- Modify: `agent_core/src/chat_provider/openai_provider.py`
- Modify: `agent_core/tests/test_chat_provider_openai.py`

OpenAI response shape:
- `choices[0].message.content` — string or None.
- `choices[0].message.tool_calls` — list of `{id, type:"function", function:{name, arguments: str (JSON)}}`. None when no tools.
- `choices[0].finish_reason` — `stop` | `tool_calls` | `length` | `content_filter` | `function_call` (legacy).
- `usage.prompt_tokens`, `usage.completion_tokens`.

Mapping:
- `finish_reason="stop"` → `stop_reason="end_turn"`
- `finish_reason="tool_calls"` → `stop_reason="tool_use"`
- `finish_reason="length"` → `stop_reason="max_tokens"`
- `finish_reason="content_filter"` → `stop_reason="error"`
- `finish_reason="function_call"` → `stop_reason="tool_use"` (legacy alias)
- `usage.prompt_tokens` → `TokenUsage.input_tokens`
- `usage.completion_tokens` → `TokenUsage.output_tokens`
- cache fields stay None

When `output_format` was set on the request: parse `message.content` as JSON into `parsed_output`. On `json.JSONDecodeError`, set `parsed_output=None` and `stop_reason="error"`.

- [ ] **Step 1: Append failing tests**

Append to `agent_core/tests/test_chat_provider_openai.py`:

```python
from unittest.mock import MagicMock


def _mk_openai_completion(
    text: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> MagicMock:
    """Build a MagicMock that mimics openai.ChatCompletion."""
    raw = MagicMock()
    msg = MagicMock()
    msg.content = text
    if tool_calls is not None:
        wire_calls = []
        for tc in tool_calls:
            wc = MagicMock()
            wc.id = tc["id"]
            wc.type = "function"
            wc.function = MagicMock()
            wc.function.name = tc["name"]
            wc.function.arguments = tc["arguments"]
            wire_calls.append(wc)
        msg.tool_calls = wire_calls
    else:
        msg.tool_calls = None

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    raw.choices = [choice]

    raw.usage.prompt_tokens = prompt_tokens
    raw.usage.completion_tokens = completion_tokens
    return raw


class TestFromWire:
    def test_text_only(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="hello back", prompt_tokens=12, completion_tokens=4)
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "end_turn"
        assert resp.model_used == "gpt-4o-2024-08-06"
        assert len(resp.content) == 1
        assert resp.content[0].type == "text"
        assert resp.content[0].text == "hello back"
        assert resp.parsed_output is None
        assert resp.usage.input_tokens == 12
        assert resp.usage.output_tokens == 4
        # OpenAI does not report these — None signals "not supported".
        assert resp.usage.cache_read_tokens is None
        assert resp.usage.cache_creation_tokens is None

    def test_tool_calls(self):
        p = _make_provider()
        raw = _mk_openai_completion(
            text=None,
            tool_calls=[{"id": "call_1", "name": "lookup", "arguments": '{"q": "x"}'}],
            finish_reason="tool_calls",
        )
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "tool_use"
        assert len(resp.content) == 1
        assert resp.content[0].type == "tool_use"
        assert resp.content[0].tool_name == "lookup"
        assert resp.content[0].input == {"q": "x"}

    def test_text_plus_tool_call(self):
        p = _make_provider()
        raw = _mk_openai_completion(
            text="checking",
            tool_calls=[{"id": "call_1", "name": "lookup", "arguments": "{}"}],
            finish_reason="tool_calls",
        )
        resp = p._from_wire(raw, output_format=None)
        assert len(resp.content) == 2
        assert resp.content[0].type == "text"
        assert resp.content[1].type == "tool_use"

    def test_finish_reason_length(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="trunc", finish_reason="length")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "max_tokens"

    def test_finish_reason_content_filter(self):
        p = _make_provider()
        raw = _mk_openai_completion(text=None, finish_reason="content_filter")
        resp = p._from_wire(raw, output_format=None)
        assert resp.stop_reason == "error"

    def test_output_format_parses_json(self):
        p = _make_provider()
        of = OutputFormat(schema={"type": "object", "properties": {"answer": {"type": "string"}}})
        raw = _mk_openai_completion(text='{"answer": "42"}')
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output == {"answer": "42"}
        assert resp.stop_reason == "end_turn"

    def test_output_format_with_invalid_json_marks_error(self):
        p = _make_provider()
        of = OutputFormat(schema={})
        raw = _mk_openai_completion(text='{"not valid')
        resp = p._from_wire(raw, output_format=of)
        assert resp.parsed_output is None
        assert resp.stop_reason == "error"
```

- [ ] **Step 2: Run tests; expect AttributeError on `_from_wire`.**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py::TestFromWire -v
```

- [ ] **Step 3: Implement `_from_wire`**

Append to the class (after the `_to_wire` family):

```python
    def _from_wire(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an OpenAI ChatCompletion into a neutral ChatResponse."""
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
            cache_read_tokens=None,
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
            model_used=self._active_model,
            usage=usage,
        )
```

- [ ] **Step 4: Run all OpenAI tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v
```

Expected: 26 passing (6 init + 13 to_wire + 7 from_wire).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/tests/test_chat_provider_openai.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): translate OpenAI SDK response to ChatResponse (#289)

Implements _from_wire. Maps finish_reason→stop_reason, parses
tool_call function.arguments as JSON, populates TokenUsage with
prompt/completion tokens (cache fields None — OpenAI does not report).
Structured-output unwrapping uses native response_format JSON.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: OpenAIChatProvider `call()` with retry + OTel

**Files:**
- Modify: `agent_core/src/chat_provider/openai_provider.py`
- Modify: `agent_core/tests/test_chat_provider_openai.py`

Same retry shape as the Anthropic provider, but catching the `openai.*` exception classes:
- Retryable: `openai.APITimeoutError`, `openai.RateLimitError`
- Non-retryable: `openai.APIError` and any other Exception
- Tests use minimal subclasses (mirroring PR1's approach for `anthropic.*`) to dodge constructor signature mismatch.

- [ ] **Step 1: Append failing tests**

Append to `agent_core/tests/test_chat_provider_openai.py`:

```python
import openai as _openai
from src.chat_provider.base import UnsupportedFeatureError


class _FakeRateLimit(_openai.RateLimitError):
    def __init__(self):  # noqa: D401
        pass


class _FakeAPIError(_openai.APIError):
    def __init__(self):  # noqa: D401
        pass


class TestCall:
    def test_normal_text_response(self):
        p = _make_provider()
        raw = _mk_openai_completion(text="hi back", prompt_tokens=10, completion_tokens=2)
        p._client.chat.completions.create = MagicMock(return_value=raw)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert resp.content[0].text == "hi back"
        p._client.chat.completions.create.assert_called_once()

    def test_empty_messages_raises_value_error(self):
        p = _make_provider()
        req = ChatRequest(messages=[])
        with pytest.raises(ValueError, match="messages must not be empty"):
            p.call(req)

    def test_unsupported_feature_raises(self):
        # OpenAI capability for prompt_cache is False; cache_hint should raise.
        p = _make_provider()
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(blocks=[TextBlock(text="x" * 3500, cache_hint="session")]),
        )
        with pytest.raises(UnsupportedFeatureError):
            p.call(req)

    def test_retry_on_rate_limit_then_success(self):
        p = _make_provider()
        raw_ok = _mk_openai_completion(text="ok")
        p._client.chat.completions.create = MagicMock(side_effect=[_FakeRateLimit(), raw_ok])

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "end_turn"
        assert p._client.chat.completions.create.call_count == 2

    def test_exhausted_retries_returns_error_response(self):
        p = _make_provider()
        p._client.chat.completions.create = MagicMock(side_effect=_FakeRateLimit())

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert resp.content == []
        assert p._client.chat.completions.create.call_count == 2

    def test_non_retryable_api_error_returns_error_response(self):
        p = _make_provider()
        p._client.chat.completions.create = MagicMock(side_effect=_FakeAPIError())

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        resp = p.call(req)

        assert resp.stop_reason == "error"
        assert p._client.chat.completions.create.call_count == 1
```

- [ ] **Step 2: Run tests; failures from NotImplementedError.**

- [ ] **Step 3: Replace `call()` body**

Replace the placeholder `call()` method with:

```python
    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a single OpenAI call with retries on transient failures."""
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
                    span.set_attribute("gen_ai.system", "openai")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")
                    raw = self._client.chat.completions.create(**kwargs)
                    response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)
                    span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens or 0)
                    span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens or 0)

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
                logger.error(
                    "chat_provider.openai.api_error",
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
        raise _RetryableExhausted(
            f"All {self._max_attempts} retry attempts exhausted for model {self._active_model}"
        )
```

- [ ] **Step 4: Run all OpenAI tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v
```

Expected: 32 passing (26 prior + 6 new).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/tests/test_chat_provider_openai.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): implement OpenAIChatProvider.call() with retry (#289)

Same retry/timeout/OTel scaffolding as AnthropicChatProvider but
catching openai.* exception classes. Emits gen_ai.system="openai" so
dashboards can split per provider.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: OpenAIChatProvider `stream()` with tool-call delta accumulation

**Files:**
- Modify: `agent_core/src/chat_provider/openai_provider.py`
- Modify: `agent_core/tests/test_chat_provider_openai.py`

The OpenAI streaming format is fundamentally chunked deltas:
- Each chunk has `choices[0].delta.content` (str fragment, may be None or "")
- Tool calls arrive as deltas with `delta.tool_calls=[{index, id?, function?: {name?, arguments?: str}}]`. The `index` keys partial arguments across chunks. Concatenate `arguments` strings, then `json.loads` at the end.
- Usage is reported only on the FINAL chunk when the SDK is configured for `stream_options={"include_usage": True}`. We always pass `stream_options={"include_usage": True}` in `_to_wire` for the streaming path.

- [ ] **Step 1: Append failing tests**

Append to `agent_core/tests/test_chat_provider_openai.py`:

```python
import asyncio
from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested


class _FakeOpenAIDelta:
    def __init__(self, *, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeOpenAIToolCallDelta:
    def __init__(self, *, index: int, id: str | None = None, name: str | None = None,
                 arguments: str | None = None) -> None:
        self.index = index
        self.id = id
        self.type = "function" if (name or arguments) else None
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = arguments


class _FakeOpenAIChunk:
    def __init__(self, *, content: str | None = None, tool_calls: list | None = None,
                 finish_reason: str | None = None, usage: dict | None = None) -> None:
        choice = MagicMock()
        choice.delta = _FakeOpenAIDelta(content=content, tool_calls=tool_calls)
        choice.finish_reason = finish_reason
        self.choices = [choice]
        if usage is not None:
            u = MagicMock()
            u.prompt_tokens = usage.get("prompt_tokens", 0)
            u.completion_tokens = usage.get("completion_tokens", 0)
            self.usage = u
        else:
            self.usage = None


class _FakeAsyncStream:
    def __init__(self, chunks: list[_FakeOpenAIChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_async_stream(provider: OpenAIChatProvider, chunks: list) -> None:
    async def _create(*args, **kwargs):
        return _FakeAsyncStream(chunks)
    provider._async_client.chat.completions.create = _create


class TestStream:
    @pytest.mark.asyncio
    async def test_streams_text(self):
        p = _make_provider()
        chunks = [
            _FakeOpenAIChunk(content="hello "),
            _FakeOpenAIChunk(content="there"),
            _FakeOpenAIChunk(finish_reason="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
        ]
        _install_async_stream(p, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req):
            out.append(token)
        assert out == ["hello ", "there"]

    @pytest.mark.asyncio
    async def test_tool_use_raises_after_accumulation(self):
        p = _make_provider()
        # Tool call arrives in 3 partial chunks: id+name first, then args fragments.
        chunks = [
            _FakeOpenAIChunk(content="checking"),
            _FakeOpenAIChunk(tool_calls=[_FakeOpenAIToolCallDelta(index=0, id="call_1", name="lookup", arguments='{"q": ')]),
            _FakeOpenAIChunk(tool_calls=[_FakeOpenAIToolCallDelta(index=0, arguments='"x"}')]),
            _FakeOpenAIChunk(finish_reason="tool_calls", usage={"prompt_tokens": 5, "completion_tokens": 4}),
        ]
        _install_async_stream(p, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        with pytest.raises(ChatToolUseRequested) as ei:
            async for token in p.stream(req):
                out.append(token)
        assert out == ["checking"]
        assert ei.value.tool_calls[0].tool_name == "lookup"
        assert ei.value.tool_calls[0].input == {"q": "x"}

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
        chunks = [
            _FakeOpenAIChunk(content="hel"),
            _FakeOpenAIChunk(content="lo"),
            _FakeOpenAIChunk(content=" "),
            _FakeOpenAIChunk(content="there"),
            _FakeOpenAIChunk(finish_reason="stop", usage={"prompt_tokens": 5, "completion_tokens": 2}),
        ]
        _install_async_stream(p, chunks)

        abort = asyncio.Event()
        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])])
        out = []
        async for token in p.stream(req, abort_event=abort):
            out.append(token)
            if len(out) == 2:
                abort.set()
        assert out == ["hel", "lo"]
```

- [ ] **Step 2: Run tests; failures from NotImplementedError.**

- [ ] **Step 3: Replace `stream()` body**

Update `_to_wire` in the provider to add `stream_options` when called from the streaming path. Cleanest is to factor out a small helper `_to_wire_for_stream(request)` that wraps `_to_wire(request)` and adds:
```python
{"stream": True, "stream_options": {"include_usage": True}}
```
But it's simpler to just have `stream()` build the kwargs inline. Since the spec already says `_to_wire(request)` returns the base kwargs, we'll pass extra kwargs in `stream()` directly — no change to `_to_wire`.

Replace the placeholder `stream()`:

```python
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
                kwargs["stream"] = True
                kwargs["stream_options"] = {"include_usage": True}

                # Accumulators
                tool_call_buf: dict[int, dict] = {}
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0

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

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

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
                    usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
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
                        "stop_reason": stop_reason,
                    },
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
                return

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
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model {self._active_model}"
        )
```

- [ ] **Step 4: Run all OpenAI tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v
```

Expected: 37 passing (32 prior + 5 new). Coverage on `openai_provider.py` should land in the high-80s.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/tests/test_chat_provider_openai.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): implement OpenAIChatProvider.stream() (#289)

Streaming with retry/abort_event support, tool-call delta accumulation
(partial function.arguments concatenated by index, JSON-parsed at
stream end), usage extraction from the final chunk via
stream_options.include_usage. ToolUseRequested raised when
finish_reason="tool_calls".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Factory wiring + capability reconciliation

**Files:**
- Modify: `agent_core/src/chat_provider/__init__.py`
- Modify: `agent_core/tests/test_chat_provider_factory.py`

The PR1 factory already:
- Defaults `provider` to `"anthropic"` when missing.
- Rejects unknown provider names.
- Validates `features.*` keys against `_KNOWN_FEATURE_KEYS`.
- Returns `AnthropicChatProvider`.
- Raises a `ProviderConfigError("openai not yet implemented")` for openai.

This task wires the OpenAI branch and adds Layer 2 (capability reconciliation).

- [ ] **Step 1: Append failing tests**

Append to `agent_core/tests/test_chat_provider_factory.py`:

```python
class TestOpenAIBranch:
    def test_returns_openai_provider(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = build_chat_provider(cfg)
        assert type(p).__name__ == "OpenAIChatProvider"


class TestCapabilityReconciliation:
    def test_anthropic_features_prompt_cache_true_passes(self):
        cfg = {
            "provider": "anthropic",
            "primary_model": "claude-sonnet-4-5-20250514",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            build_chat_provider(cfg)  # no exception

    def test_openai_features_prompt_cache_true_raises(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": True},
        }
        with pytest.raises(ProviderConfigError, match="prompt_cache"):
            build_chat_provider(cfg)

    def test_openai_features_image_input_true_passes(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"image_input": True},
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            build_chat_provider(cfg)

    def test_openai_features_streaming_true_passes(self):
        cfg = {
            "provider": "openai",
            "primary_model": "gpt-4o-2024-08-06",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"streaming": True},
        }
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            build_chat_provider(cfg)

    def test_features_false_against_supported_capability_passes(self):
        # Tightening (True→False for a capable provider) is always allowed.
        cfg = {
            "provider": "anthropic",
            "primary_model": "claude-sonnet-4-5-20250514",
            "timeout_ms": 5000,
            "retry_attempts": 2,
            "features": {"prompt_cache": False, "image_input": False},
        }
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            build_chat_provider(cfg)
```

You must ALSO update the existing `test_openai_not_implemented_yet` test — it now needs to cleanly succeed with a mocked OpenAI client. Replace its body with:

```python
def test_openai_branch_now_works():
    # Sanity: the OpenAI branch returns an OpenAIChatProvider in PR2.
    cfg = {**VALID_CONFIG["agent"], "provider": "openai", "primary_model": "gpt-4o-2024-08-06"}
    with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
        p = build_chat_provider(cfg)
    assert type(p).__name__ == "OpenAIChatProvider"
```

(Rename `test_openai_not_implemented_yet` to `test_openai_branch_now_works` — the assertion is now positive.)

- [ ] **Step 2: Run tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_factory.py -v
```

Expected: failures on the new tests (factory still raises "not yet implemented" for openai) and on the renamed test.

- [ ] **Step 3: Update the factory**

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


def _capability_attr(feature_key: str) -> str:
    """Map a YAML feature key to the matching Capabilities attribute name."""
    return {
        "prompt_cache": "supports_prompt_cache",
        "streaming": "supports_streaming",
        "image_input": "supports_image_input",
    }[feature_key]


def _reconcile_features(
    *,
    provider_name: str,
    capabilities: Capabilities,
    features: dict,
) -> None:
    """Layer 2 of three-layer validation: capability reconciliation.

    Raises ProviderConfigError if any features.X=True targets a
    capability the provider does not support. Tightening (True→False)
    is always allowed; widening is not.
    """
    for key, value in features.items():
        if value is None:
            continue
        if not value:
            continue   # explicit False → tightening; always allowed
        if key not in _KNOWN_FEATURE_KEYS:
            continue   # unknown keys handled by caller; defensive guard
        cap_attr = _capability_attr(key)
        if not getattr(capabilities, cap_attr):
            raise ProviderConfigError(
                f"Provider '{provider_name}' does not support {key}; "
                f"set agent.features.{key} to false (or remove it) or "
                f"pick a different provider."
            )


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
        ProviderConfigError: provider is unknown, features carries an
            unrecognised key, a required config field is missing, or
            features.X=True conflicts with provider capabilities.
    """
    provider_name = agent_config.get("provider", "anthropic")

    features = agent_config.get("features") or {}
    if hasattr(features, "model_dump"):  # FeaturesConfig pydantic instance
        features = features.model_dump()
    unknown = set(features.keys()) - _KNOWN_FEATURE_KEYS
    if unknown:
        raise ProviderConfigError(
            f"Unknown feature key(s) in agent.features: {sorted(unknown)}. "
            f"Known keys: {sorted(_KNOWN_FEATURE_KEYS)}."
        )

    if provider_name == "anthropic":
        from src.chat_provider.anthropic_provider import AnthropicChatProvider
        _reconcile_features(
            provider_name="anthropic",
            capabilities=AnthropicChatProvider.capabilities,
            features=features,
        )
        return AnthropicChatProvider(agent_config)

    if provider_name == "openai":
        from src.chat_provider.openai_provider import OpenAIChatProvider
        _reconcile_features(
            provider_name="openai",
            capabilities=OpenAIChatProvider.capabilities,
            features=features,
        )
        return OpenAIChatProvider(agent_config)

    raise ProviderConfigError(
        f"Unknown provider '{provider_name}'. "
        f"Known providers: 'anthropic', 'openai'."
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

The `hasattr(features, "model_dump")` shim handles the case where a caller passes an `AgentConfig` (Pydantic) sub-tree directly: the new `features` attribute is a `FeaturesConfig` model, and we want it as a dict for our key-validation logic.

- [ ] **Step 4: Run tests**

```bash
cd agent_core && uv run pytest tests/test_chat_provider_factory.py tests/test_chat_provider_openai.py tests/test_chat_provider_base.py tests/test_chat_provider_metrics.py tests/test_chat_provider_types.py tests/test_chat_provider_anthropic.py -v
```

Expected: full chat_provider suite green. Existing factory tests + 5 new = roughly 100+ tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/chat_provider/__init__.py agent_core/tests/test_chat_provider_factory.py
git commit -m "$(cat <<'EOF'
feat(chat-provider): wire OpenAIChatProvider into factory + capability reconciliation (#289)

build_chat_provider() now selects OpenAIChatProvider when
agent.provider="openai". Adds Layer 2 of three-layer validation:
features.X=true against a capability of False raises ProviderConfigError
at startup with a remediation hint.

The features dict accepts both raw YAML dicts and the FeaturesConfig
Pydantic model returned by AgentConfig.model_validate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Final regression run, push, draft PR

I'll run this myself rather than dispatching a subagent — it's coordination, not implementation.

- [ ] **Step 1: Full regression run**

```bash
cd agent_core && uv run pytest 2>&1 | tail -10
```

Expected: same number of tests as PR1 plus the new OpenAI/factory/schema tests, all passing except the pre-existing `test_voice_length_cap.py::test_kkb_voice_channel_max_tokens_validates_against_schema`.

- [ ] **Step 2: Coverage on chat_provider/**

```bash
cd agent_core && uv run pytest --cov=src/chat_provider --cov-report=term-missing tests/test_chat_provider_*.py
```

Expected: ≥85% on `openai_provider.py`; overall package ≥85%.

- [ ] **Step 3: Static greps**

```bash
grep -rn "import anthropic" agent_core/src/ | grep -v __pycache__
grep -rn "import openai" agent_core/src/ | grep -v __pycache__
```

Expected: `anthropic` only in `chat_provider/anthropic_provider.py`; `openai` only in `chat_provider/openai_provider.py`.

- [ ] **Step 4: Push and open the draft PR**

```bash
git push -u origin pr2/openai-provider
gh pr create \
  --base feature/llm-provider-redesign \
  --head pr2/openai-provider \
  --draft \
  --title "PR2: OpenAIChatProvider, factory selection, capability reconciliation" \
  --body "..."
```

The PR body should mirror PR1's structure: summary, notable decisions, test plan checklist, what's next. Mention the pre-existing voice-test failure remains and is unrelated.

---

## Self-review checklist (run before handoff)

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §6 Configuration: agent.provider, agent.features.* in YAML schema | Task 2, Task 3 |
| §6 Three-layer validation — Layer 1 (schema) | Task 2 |
| §6 Three-layer validation — Layer 2 (factory reconciliation) | Task 9 |
| §6 Three-layer validation — Layer 3 (per-request) | Already done in PR1 |
| §8 OpenAIChatProvider — `_to_wire` (system prepended, content-parts, tool messages, response_format, max_completion_tokens) | Task 5 |
| §8 OpenAIChatProvider — `_from_wire` (finish_reason mapping, JSON arguments parse, parsed_output) | Task 6 |
| §8 OpenAIChatProvider — `call()` retry + spans | Task 7 |
| §8 OpenAIChatProvider — `stream()` with tool-call delta accumulation | Task 8 |
| §11 Metric `gen_ai.system="openai"` attribute | Tasks 7, 8 |
| §13 OpenAI prompt-cache wiring deferred (capability stays False) | Task 4 |

**Placeholder scan:** every step has executable code or commands; no TBDs.

**Type consistency:** `OpenAIChatProvider`, `_to_wire`, `_from_wire`, `_call_with_retry`, `_stream_with_retry`, `_RetryableExhausted`, `_safe_int` are all defined before they're referenced. `_KNOWN_FEATURE_KEYS` set is consistent with the `FeaturesConfig` schema fields.

**Scope:** this plan only adds the OpenAI provider, the factory wiring, and the schema additions. It does NOT migrate any caller off the adapter (PR3/4) and does NOT delete `llm_wrapper/` (PR5).
