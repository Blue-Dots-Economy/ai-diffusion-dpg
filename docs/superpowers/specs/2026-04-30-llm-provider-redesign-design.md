# LLM Provider Redesign — `chat_provider/` package

**Status:** Draft (under review)
**Date:** 2026-04-30
**Tracking issue:** [sanketika-labs/ai-diffusion-dpg#287](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/287)
**Branch:** `feature/llm-provider-redesign` (integration branch; sub-issue PRs target this branch)
**Sub-issues:** #288 (PR1), #289 (PR2), #290 (PR3), #291 (PR4), #292 (PR5)

---

## 1. Problem

`agent_core/src/llm_wrapper/` is provider-agnostic in name only. Three concrete leaks make it impossible to swap Anthropic for another provider without touching unrelated files:

1. **`LLMWrapperBase.call(messages, tools, system, ...)` accepts Anthropic-shaped dicts.** Callers (`orchestrator`, `manager_agent`, `nlu_processor`, `turn_assembler`) construct messages and tool definitions in Anthropic's wire format and pass them through.
2. **Provider-specific cache markers leak into callers.** `manager_agent.py:398,404` and `nlu_processor.py:300` write `cache_control: {"type": "ephemeral"}` literals into content blocks themselves. The wrapper isn't the only place that knows Anthropic's shape — neither is it the only place we'd have to change to support OpenAI.
3. **`LLMResponse` carries Anthropic-specific fields** — `cache_read_input_tokens`, `cache_creation_input_tokens` — and a `content: Optional[str]` + `tool_calls: list[ToolCall]` split that mirrors Anthropic's response, not a neutral structure.

The redesign delivers a single provider-neutral abstraction (`ChatProviderBase`), keeps every provider SDK import inside one provider file, and removes provider-specific concepts from every Agent Core caller.

## 2. Goals

- Agent Core depends only on `ChatProviderBase` and Pydantic neutral types.
- `import anthropic` lives only in `chat_provider/anthropic_provider.py`; `import openai` only in `chat_provider/openai_provider.py`.
- Two providers ship in this redesign: **Anthropic** and **OpenAI** (Chat Completions API).
- AzureOpenAI and Ollama are explicit follow-ups — capability flags are plumbed so neither requires reworking the abstraction.
- Multimodal *input* is supported day one (`ImageBlock`). Vision-as-output, audio I/O, and realtime APIs are out of scope.
- Capabilities are declared in code per provider; configuration is YAML-only and plain-language.
- Three-layer fail-loud validation: schema → capability reconciliation at startup → per-request guard.
- Behaviour-preserving for the Anthropic path — all 457+ existing tests pass against the adapter in PR1.
- OTel metric names are unchanged; Grafana dashboards survive.

## 3. Non-goals

- No cross-provider fallback or routing. One provider per process, picked from YAML.
- No `model_override` parameter on the public surface (today it exists only to support the wrapper's internal fallback, which is being deleted).
- No image generation, TTS, ASR, or realtime APIs in this redesign — those are separate abstractions.
- No emulation of unsupported features (e.g., no fake tool-use on Ollama).

## 4. Architecture overview

```
agent_core/src/chat_provider/
├── __init__.py           # public exports + build_chat_provider(config) factory
├── base.py               # ChatProviderBase (ABC), Capabilities (frozen), errors, _validate_request
├── types.py              # neutral Pydantic types
├── anthropic_provider.py # AnthropicChatProvider — only file that imports `anthropic`
├── openai_provider.py    # OpenAIChatProvider     — only file that imports `openai`
└── metrics.py            # provider-agnostic OTel instruments (lifted from claude_wrapper.py)
```

Invariants:

- `ChatProviderBase` is the only type any other Agent Core file depends on. Concrete providers are not imported outside `chat_provider/__init__.py`.
- Each provider owns translation **both ways**: `_to_wire(neutral_request) → sdk_kwargs` and `_from_wire(sdk_response) → ChatResponse`.
- Provider-specific concepts (`cache_control`, `response_format`, `tool_choice` shapes) stay inside the provider file. Callers express intent (`cache_hint`, `output_format`, `tool_choice="auto"|"any"|"none"|<name>`); providers translate.
- `Capabilities` is declared statically per provider class. YAML configuration may *tighten* (set a `True` capability to `False` for a deployment) but cannot widen.
- `build_chat_provider(config)` is the only construction path.

## 5. Neutral types (`chat_provider/types.py`)

Pydantic models. The discriminator on content blocks is the `type` field.

```python
class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_hint: Literal["session", "turn"] | None = None
    # Anthropic translates → cache_control={"type": "ephemeral"} on the block.
    # OpenAI raises if cache_hint set and features.prompt_cache=False.

class ImageSource(BaseModel):
    kind: Literal["url", "base64"]
    url: str | None = None
    media_type: str | None = None  # e.g. "image/png" — required when kind="base64"
    data: str | None = None        # base64 — required when kind="base64"

class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    # Requires capabilities.supports_image_input.

class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]

class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[TextBlock]
    is_error: bool = False

ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]    # always a list, even for plain text

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]   # JSON Schema; both providers accept this shape

class SystemPrompt(BaseModel):
    blocks: list[TextBlock]        # ordered; cache hints per block

class OutputFormat(BaseModel):
    type: Literal["json_schema"]
    schema: dict[str, Any]
    strict: bool = True

class ChatRequest(BaseModel):
    messages: list[Message]
    system: SystemPrompt | None = None
    tools: list[ToolDefinition] = []
    tool_choice: Literal["auto", "any", "none"] | str = "auto"  # str = force named tool
    output_format: OutputFormat | None = None
    max_tokens: int = 4096

class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    # None means "provider does not report this", not "zero".

class ChatResponse(BaseModel):
    content: list[ContentBlock]    # text + tool_use blocks the model emitted
    parsed_output: dict | None = None    # populated iff request.output_format was set
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "error"]
    model_used: str
    usage: TokenUsage
    raw: dict | None = None        # provider raw response, for debugging only
```

Two intentional deltas from today's `LLMResponse`:

1. **Symmetric content.** Today's `content: Optional[str]` + `tool_calls: list[ToolCall]` becomes a single `content: list[ContentBlock]` mirroring the input shape, so the next assistant turn can be appended back as a `Message` directly. Both Anthropic and OpenAI return mixed text/tool blocks natively.
2. **`SystemPrompt` is structured.** Today's `system: str | list[dict]` (Anthropic-shaped) becomes `SystemPrompt(blocks=[TextBlock(...)])`. Callers mark cache boundaries via `cache_hint`, never via raw `cache_control` dicts. `manager_agent.py`'s tier-1 / tier-2 caching survives — it just expresses itself in neutral form.

`tool_choice` mapping:

| Neutral | Anthropic | OpenAI |
|---|---|---|
| `"auto"` | `{"type": "auto"}` | `"auto"` |
| `"any"` | `{"type": "any"}` | `"required"` |
| `"none"` | omit + `tools=[]` | `"none"` |
| `<name>` | `{"type": "tool", "name": <name>}` | `{"type": "function", "function": {"name": <name>}}` |

## 6. Capabilities vs. Configuration

Two separate concepts. Mixing them was the root cause of the leakage.

| | Capabilities | Configuration |
|---|---|---|
| What | Intrinsic facts about a provider | Deployment choices |
| Source | Declared in code on the provider class | YAML (`dev-kit/configs/<domain>/agent_core.yaml`) |
| Edited by | DPG framework devs | Domain config authors (often non-technical) |
| Mutable at runtime | Never | Read once at startup |

Capabilities live in code only — never in YAML. That removes the failure mode where a config author would lie about whether a provider supports a feature.

```python
# anthropic_provider.py
class AnthropicChatProvider(ChatProviderBase):
    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,    # via tool-coercion
        supports_force_tool_choice=True,
    )

# openai_provider.py
class OpenAIChatProvider(ChatProviderBase):
    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,        # follow-up ticket flips this
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,    # native strict JSON schema
        supports_force_tool_choice=True,
    )
```

YAML expresses deployment intent in plain words:

```yaml
# dev-kit/configs/<domain>/agent_core.yaml
agent:
  provider: anthropic              # 'anthropic' | 'openai'
  primary_model: claude-sonnet-4-5-20250514
  api_key_env: ANTHROPIC_API_KEY   # name of env var to read
  timeout_ms: 30000
  retry_attempts: 3
  retry_backoff_seconds: [0, 0.5, 1.0]
  max_tokens: 4096

  features:                        # optional; default = provider's capability
    prompt_cache: true
    streaming: true
    image_input: true
```

Three-layer fail-loud validation:

1. **Schema (YAML loader).** `provider` is a known enum, required fields present, types correct → `ProviderConfigError` at startup.
2. **Capability reconciliation (factory).** Compare `features.*` against the provider's `capabilities`. Any `features.X=true` where `capabilities.supports_X=false` → `ProviderConfigError("Provider 'openai' does not support prompt_cache; set features.prompt_cache=false or pick a different provider.")` at startup.
3. **Request guard (`ChatProviderBase._validate_request`).** Per-call check against the effective feature set (AND of capability and config). Any unsupported feature → `UnsupportedFeatureError`. Never silent.

Effective rule for any feature: **usable ⇔ `capabilities.supports_X` AND `features.X`**.

When `features.*` is omitted, it defaults to the provider's capability — so a config author who knows nothing about caching gets caching automatically on Anthropic and not on OpenAI, with no required reading.

## 7. `ChatProviderBase` — interface and shared scaffolding

```python
@dataclass(frozen=True)
class Capabilities:
    supports_tools: bool
    supports_streaming: bool
    supports_prompt_cache: bool
    supports_image_input: bool
    supports_audio_input: bool
    supports_structured_output: bool
    supports_force_tool_choice: bool


class ChatProviderError(Exception): ...
class UnsupportedFeatureError(ChatProviderError): ...
class ProviderConfigError(ChatProviderError): ...
class ProviderAPIError(ChatProviderError): ...

class ToolUseRequested(Exception):
    """Streaming-only signal: model emitted tool_use blocks; caller executes and resumes."""
    def __init__(self, tool_calls: list[ToolUseBlock]):
        self.tool_calls = tool_calls


class ChatProviderBase(ABC):
    capabilities: Capabilities  # set by subclass

    @abstractmethod
    def call(self, request: ChatRequest) -> ChatResponse:
        """Synchronous single call.

        Returns ChatResponse — never raises for transient failures.
        On exhausted retries returns ChatResponse(stop_reason='error', content=[], usage=TokenUsage()).
        Raises UnsupportedFeatureError if request uses a capability the provider lacks.
        Raises ProviderConfigError for misconfiguration (e.g., auth missing).
        """

    @abstractmethod
    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text deltas as they arrive.

        Raises ToolUseRequested if the model emits tool_use blocks.
        On exhausted retries, generator returns silently.
        """

    @abstractmethod
    def get_active_model(self) -> str: ...

    def _validate_request(self, request: ChatRequest, *, is_stream: bool) -> None:
        """Enforce capability + sync-only-structured-output rules. Centralised."""
```

Per-method capability matrix — single set of flags but `_validate_request` enforces a stricter subset on `stream()`:

| Feature | `call()` | `stream()` |
|---|---|---|
| tools | ✅ | ✅ |
| prompt cache hints | ✅ | ✅ |
| image input | ✅ | ✅ |
| forced `tool_choice` | ✅ | ✅ |
| `output_format` (structured output) | ✅ | ❌ — raises `UnsupportedFeatureError` for **all providers** |

Rationale: OpenAI streams JSON natively, Anthropic emulates structured output via tool-coercion which is incompatible with text streaming. Forbidding structured output on `stream()` for both providers keeps the public surface symmetric.

Three deliberate calls preserved from today's design:

1. **`call()` never raises on transient failures.** Today's `LLMWrapperBase.call()` returns `LLMResponse(stop_reason='error')` on exhausted retries; every caller (orchestrator's tool-use loop, manager_agent, nlu_processor) relies on this. Same contract on the new `call()`. `UnsupportedFeatureError` and `ProviderConfigError` *do* raise — those are programmer/config errors, not runtime failures.
2. **Retry/timeout are per-provider, not per-request.** Read once from config at provider init.
3. **`Capabilities` is `dataclass(frozen=True)`, not Pydantic.** Capabilities are declared inline in code, never parsed from YAML, and frozen-by-default catches accidental mutation.

`_validate_request` rejects:

- `request.tools` set when `not features.supports_tools`.
- `request.output_format` set when `not features.supports_structured_output` **OR** when `is_stream=True`.
- Any `ImageBlock` when `not features.supports_image_input`.
- Any `cache_hint` (system or message) when `not features.supports_prompt_cache`.
- `request.tool_choice` outside `{"auto", "none"}` when `not features.supports_force_tool_choice`.

Errors include the provider class name and a remediation hint.

## 8. Anthropic & OpenAI provider implementations

Same retry / timeout / OTel scaffolding both sides. Only `_to_wire` and `_from_wire` differ. Both lift today's logic from `claude_wrapper.py` rather than rewriting it.

### `anthropic_provider.py`

- **`_to_wire(request)`** — neutral → Anthropic SDK kwargs. `Message.content` blocks → Anthropic content blocks. `TextBlock.cache_hint` → `cache_control: {"type": "ephemeral"}` when `features.prompt_cache`. Today's `_CACHE_MIN_CHARS=3000` heuristic survives. `SystemPrompt.blocks` → Anthropic `system: list[block]`. `ToolDefinition` passes through (already JSON-Schema-shaped). `tool_choice` mapped per §5. **`OutputFormat` emulated** via a synthetic `respond_with_json` tool plus `tool_choice={"type":"tool","name":"respond_with_json"}`. `_from_wire` unwraps the resulting tool-call into `parsed_output` and synthesises a `TextBlock(text=json.dumps(tool_input))` so callers see a normal text content block.

- **`_from_wire(raw)`** — Anthropic Message → `ChatResponse`. Content blocks → neutral `TextBlock` / `ToolUseBlock`. `usage` → `TokenUsage` with all four fields populated. `stop_reason` passes through.

- **`stream()`** mirrors today's `_stream_with_retry`. After the stream closes, `final_message` is parsed; if it has `tool_use` blocks, raises `ToolUseRequested([ToolUseBlock(...)])`. `output_format` is rejected by `_validate_request(is_stream=True)` so the tool-coercion path is sync-only.

### `openai_provider.py`

- **`_to_wire(request)`** — neutral → `chat.completions.create()` kwargs. `SystemPrompt.blocks` → joined into a single `{"role":"system","content":<concat>}` at the head of `messages` (OpenAI doesn't accept multiple system messages). `Message` → OpenAI's `{role, content}` shape; `content` is a string when only `TextBlock`s, else OpenAI's content-parts array (`{"type":"text"|"image_url",...}`). Assistant prior-turn `ToolUseBlock`s → `tool_calls: [{id, type:"function", function:{name, arguments:json.dumps(input)}}]`. `ToolResultBlock`s → separate `{"role":"tool","tool_call_id":...,"content":...}` messages immediately after the assistant message that requested them. `ToolDefinition` → `tools: [{"type":"function","function":{name, description, parameters:input_schema}}]`. `tool_choice` mapped per §5. `OutputFormat` → native `response_format={"type":"json_schema","json_schema":{"name":"out","schema":...,"strict":True}}`. `max_tokens` → either `max_tokens` or `max_completion_tokens` based on model family.

- **`_from_wire(raw)`** — `ChatCompletion` → `ChatResponse`. `choices[0].message.content` → `TextBlock` if non-empty. `choices[0].message.tool_calls` → `ToolUseBlock`s with `input=json.loads(tc.function.arguments)`. `usage` → `TokenUsage(input_tokens=..., output_tokens=..., cache_read_tokens=None, cache_creation_tokens=None)`. `finish_reason` mapped: `stop→"end_turn"`, `tool_calls→"tool_use"`, `length→"max_tokens"`, `content_filter→"error"`. When `request.output_format` was set, `parsed_output = json.loads(message.content)`; on JSON-decode failure or schema-validation failure, `parsed_output=None` and `stop_reason="error"`.

- **`stream()`** uses OpenAI's `stream=True`. Yields `delta.content` text. Tool calls arrive as deltas with `delta.tool_calls`; provider accumulates partial JSON arguments per `tool_call_id`, finalises after stream close, raises `ToolUseRequested` if any tool was called.

### Shared scaffolding (no duplication)

Retry loop, OTel span emission, and `_record_call_metrics` move into `chat_provider/metrics.py` and a small `_call_with_retry` helper on `ChatProviderBase`. Today's claude_wrapper already has all of this — we move it, not rewrite.

### Cache-hint enforcement on OpenAI

OpenAI doesn't support prompt caching today. Default `features.prompt_cache=false` (matches `capabilities.supports_prompt_cache=false`). If a caller sets `cache_hint` on any block, `_validate_request` raises `UnsupportedFeatureError`. **Never silently dropped** — fail-loud is the rule.

### What deliberately isn't implemented

- No fallback model logic. Deleted per design decision.
- No `model_override` parameter (verified during PR1 — if a non-fallback caller surfaces, preserved for that case only).
- No silent feature dropping.
- No emulated streaming on OpenAI (it streams natively).

## 9. Migration plan

Approach B from brainstorming: parallel package + adapter shim, callers migrate one at a time. Each PR ships green and is individually reviewable.

### PR1 — scaffold `chat_provider/` + adapter (#288)

Add the package alongside the existing wrapper. `LLMWrapperBase.call()` / `stream_call()` become a ~60-line adapter that translates today's Anthropic-shaped inputs to neutral types, calls `AnthropicChatProvider`, and translates `ChatResponse` back into today's `LLMResponse`. The adapter preserves `LLMResponse` field semantics exactly (including `cache_read_input_tokens` / `cache_creation_input_tokens` ints).

Files added: `chat_provider/{__init__.py, base.py, types.py, anthropic_provider.py, metrics.py}`.
Files modified: `llm_wrapper/claude_wrapper.py` (becomes the adapter); `llm_wrapper/__init__.py` (exports unchanged).

All 457+ existing tests continue to pass — proves the neutral types round-trip without behaviour loss. New tests added for `chat_provider/` against neutral inputs, including a wire-format snapshot test (given a fixed `ChatRequest`, assert the dict passed to the Anthropic SDK).

### PR2 — `OpenAIChatProvider` + factory + config validation (#289)

Add `openai_provider.py`. `build_chat_provider(config)` selects between Anthropic and OpenAI by `config.agent.provider`. Three-layer config validation lands here. YAML keys `agent.provider` and `agent.features.{prompt_cache, streaming, image_input}` added to `dev-kit/dpg/agent_core.yaml`.

`base.py` enforces: `output_format` on `stream()` raises for all providers (sync-only structured output).

Tests: provider unit tests, factory matrix (every `(provider, features)` combination either initialises cleanly or raises with the expected message), `_validate_request` cases.

No callers change in this PR.

### PR3 — migrate orchestrator + manager_agent (#290)

Orchestrator now takes a `ChatProviderBase` directly. Builds `ChatRequest` instead of Anthropic dicts. Reads `ChatResponse.parsed_output` instead of re-parsing JSON for structured-output subagents.

`manager_agent.py.build_system_prompt()` returns `SystemPrompt(blocks=[TextBlock(cache_hint="session"|"turn", ...)])`. Both tier-1 and tier-2 cache markers move from caller-built dicts into the provider's `_to_wire`. Every `cache_control` literal in `manager_agent.py` is removed.

After this PR: `grep 'cache_control' agent_core/src` returns matches only inside `chat_provider/anthropic_provider.py`.

### PR4 — migrate nlu_processor, turn_assembler, llm_proxy_server (#291)

`nlu_processor.py` — `cache_control` block becomes `SystemPrompt(blocks=[TextBlock(cache_hint=...)])`. NLU calls `chat_provider.call(ChatRequest(...))` and reads `ChatResponse.parsed_output`.

`turn_assembler.py` — accepts `ChatProviderBase`. Imports `ToolUseRequested` from `chat_provider.base` (re-export from `src.exceptions` retained for one PR cycle, then removed in PR5).

`llm_proxy_server.py` — `POST /internal/llm/call` accepts a JSON body that maps directly onto `ChatRequest` (Pydantic validates at the HTTP boundary). Response body is `ChatResponse`. Documented in the proxy server's docstring.

After this PR: every Agent Core component talks to `ChatProviderBase` directly. The adapter shim is unused.

### PR5 — delete `llm_wrapper/`, update docs (#292)

Delete `agent_core/src/llm_wrapper/` package and any adapter-only tests. Rename `test_llm_wrapper*.py` → `test_chat_provider_*.py`.

Docs updated: `CLAUDE.md` (point at `chat_provider/`), `ARCHITECTURE.md` (paragraph under Agent Core), every shipped domain config (`dev-kit/configs/<domain>/agent_core.yaml`) gets `agent.provider: anthropic` so existing deployments behave unchanged.

After this PR: `grep -r 'llm_wrapper' agent_core/` returns nothing; `grep -r 'import anthropic' agent_core/src` returns only `chat_provider/anthropic_provider.py`.

## 10. Testing strategy

Per `.claude/rules/testing-requirements.md` — three categories, mocked external deps.

**Provider unit tests** (per provider, mock SDK):

- Normal: text-only, tools, image input, prompt cache, structured output, streaming.
- Edge: empty messages → `ValueError`; empty system; missing optional config fields.
- Failure: `APITimeoutError` / `RateLimitError` retried; `APIError` returns `ChatResponse(stop_reason="error")`; auth missing → `ProviderConfigError`; unsupported feature → `UnsupportedFeatureError`.
- Wire-format snapshots: given a fixed `ChatRequest`, assert the dict passed to the SDK equals an expected JSON snapshot. Catches translation drift on either side.

**Capability/config reconciliation tests** — every `(provider, features.*)` combination either initialises cleanly or raises `ProviderConfigError` at factory time with the expected message.

**Integration tests** (`pytest -m integration`, opt-in) — real API keys via env. One real request per provider, asserts non-error response. Skipped in CI by default; run before release.

Coverage target ≥70% on `agent_core/src/chat_provider/` per CLAUDE.md.

## 11. Observability

OTel spans + metrics + structured logs from today's `claude_wrapper.py` move into `chat_provider/metrics.py` unchanged in shape. No metric names change — Grafana dashboards survive untouched.

Two small additions:

- Span attribute `gen_ai.system: "anthropic" | "openai"` so dashboards can split per provider.
- `UnsupportedFeatureError` and `ProviderConfigError` emit a one-line structured log at startup / first call so config bugs are visible without reading a stack trace.

Existing instruments preserved by name and meaning:

- `agent_core.llm.call.duration_ms`
- `agent_core.llm.call.input_tokens`
- `agent_core.llm.call.output_tokens`
- `agent_core.llm.call.cache_read_tokens`
- `agent_core.llm.calls_total`
- `agent_core.llm.cache_events_total`

## 12. Documentation updates

In-tree, part of this redesign:

- `ARCHITECTURE.md` — short paragraph under Agent Core: `ChatProviderBase` is the single LLM-call point, multi-provider abstraction.
- `CLAUDE.md` — update the line "All Anthropic API calls go through `agent_core/src/llm_wrapper/claude_wrapper.py`" to point at `agent_core/src/chat_provider/`.
- `dev-kit/dpg/agent_core.yaml` — add `agent.provider` + `agent.features.*` keys with comments.
- `dev-kit/configs/<domain>/agent_core.yaml` (every shipped domain) — add `agent.provider: anthropic`.

## 13. Out of scope

Tracked separately, *not* done in this redesign:

- AzureOpenAI provider — config + auth differences over OpenAI; ~1 day.
- Ollama provider — capability flags off, `base_url` override; ~1 day.
- Google Gemini provider.
- Cross-provider fallback / routing.
- Image generation, TTS, ASR, Realtime — separate base classes.
- OpenAI prompt-caching wiring (when added, flip `OpenAIChatProvider.capabilities.supports_prompt_cache=True` and a small `_to_wire` change).

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Adapter shim hides a translation bug → existing tests pass against bad output | Wire-format snapshot tests in PR1 lock the Anthropic translation |
| OpenAI tool-call delta accumulation off-by-one | Dedicated streaming tests in PR2 with multi-chunk fixtures |
| Anthropic structured-output emulation drops the synthesised text block, breaking callers that read `content` | `_from_wire` synthesises a `TextBlock(text=json.dumps(...))` so `content` always carries something readable |
| YAML config authors omit `agent.provider` after upgrade | PR5 adds `agent.provider: anthropic` to every shipped domain config; loader raises `ProviderConfigError` with remediation hint if missing |
| Translation drift over time as providers add features | Wire-format snapshot tests; new feature work touches `_to_wire` and the snapshot in the same PR |

## 15. Success criteria

- All sub-issue PRs (#288–#292) merged into `feature/llm-provider-redesign`.
- Final integration PR `feature/llm-provider-redesign → main` is green: every test in `agent_core/` passes; coverage ≥70%; no `import anthropic` outside `chat_provider/anthropic_provider.py`; no `import openai` outside `chat_provider/openai_provider.py`; no `cache_control` literal outside `chat_provider/anthropic_provider.py`.
- A deployment configured with `agent.provider: openai` runs end-to-end against OpenAI for at least one full turn (verified via integration test).
- A deployment configured with `agent.provider: anthropic` is byte-for-byte indistinguishable from today's behaviour on the OTel metrics axis.
