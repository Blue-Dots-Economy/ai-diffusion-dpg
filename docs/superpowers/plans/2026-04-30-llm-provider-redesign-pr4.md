# LLM Provider Redesign — PR4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Migrate the remaining adapter consumers off `LLMWrapperBase`. After this PR, nothing in `agent_core/src/` outside `chat_provider/` and `llm_wrapper/` itself imports the legacy wrapper. PR5 then deletes the adapter entirely.

**Architecture:** `nlu_processor.py` and `language_normalisation.py` each take a `chat_provider: ChatProviderBase` at init time and use it directly — `model_override` at the wrapper boundary disappears. Orchestrator constructs a NLU-fast provider (Haiku-shaped) via `build_chat_provider(agent_config_with_override_model)` alongside the primary provider, and injects each. `turn_assembler.py` renames its passthrough kwarg `llm_wrapper` → `chat_provider`. `llm_proxy_server.py` accepts `ChatRequest` JSON and returns `ChatResponse` JSON. The legacy adapter shim stays in place — nothing in agent_core consumes it after this PR (deleted in PR5).

**Tech Stack:** Python 3.11+, Pydantic v2, existing `chat_provider/`, FastAPI, `pytest`, `uv`.

**Tracking:** Parent #287; resolves #291. Branch `pr4/migrate-remaining-callers` off `feature/llm-provider-redesign`.

---

## File structure

```
agent_core/src/
├── preprocessing/
│   ├── nlu_processor.py         # MODIFIED — chat_provider at init, no model_override
│   └── language_normalisation.py # MODIFIED — same pattern
├── turn_assembler.py            # MODIFIED — kwarg rename only
├── servers/
│   └── llm_proxy_server.py      # MODIFIED — body shape becomes ChatRequest/ChatResponse
└── orchestrator.py              # MODIFIED — constructs the NLU/lang-norm providers and injects them

agent_core/tests/
├── test_nlu_processor.py            # MODIFIED — fixtures switch to ChatProviderBase mock
├── test_language_normalisation.py   # MODIFIED — same
├── test_turn_assembler.py           # MODIFIED — kwarg name only (mostly)
├── test_llm_proxy_server.py         # MODIFIED — request/response shape
└── test_orchestrator.py             # MODIFIED — orchestrator now builds two providers
```

Out of scope:
- `llm_wrapper/claude_wrapper.py`, `llm_wrapper/base.py`, `LLMResponse` — deleted in PR5.
- Domain config updates (setting `agent.provider: anthropic`) — PR5.

---

## Conventions

- `cwd = agent_core/` for test runs unless stated.
- Test command: `uv run pytest -x` for focused runs.
- Branch: every commit lands on `pr4/migrate-remaining-callers`. Verify with `git branch --show-current` before each commit.

---

## Task 1: Branch off

- [ ] **Step 1:** `git fetch origin && git checkout -b pr4/migrate-remaining-callers origin/feature/llm-provider-redesign`
- [ ] **Step 2:** Verify branch.

---

## Task 2: nlu_processor — accept `chat_provider` at init; remove `cache_control` literal

**Files:**
- Modify: `agent_core/src/preprocessing/nlu_processor.py`
- Modify: `agent_core/tests/test_nlu_processor.py`

### Migration

1. **Init signature** — add a `chat_provider: ChatProviderBase` parameter to `NLUProcessor.__init__`. Store as `self._chat_provider`. Drop the per-call `llm` parameter from `process()`. The processor's effective model is now decided by which provider is injected (the orchestrator builds the right one).

2. **Imports** — drop `from src.llm_wrapper.base import LLMWrapperBase`. Add `from src.chat_provider.base import ChatProviderBase`. Add `from src.chat_provider.types import (ChatRequest, ChatResponse, Message, OutputFormat, SystemPrompt, TextBlock)`.

3. **Remove `cache_control` literal (lines 295–304)** — replace the whole `system_payload` block with a `SystemPrompt`:

```python
if self._prompt_cache_enabled:
    system_payload = SystemPrompt(blocks=[
        TextBlock(text=system_prompt_text, cache_hint="session"),
    ])
else:
    system_payload = SystemPrompt(blocks=[
        TextBlock(text=system_prompt_text),
    ])
```

(Or unify: always pass cache_hint conditionally — equivalent, slightly less branchy.)

4. **The LLM call (lines 332–337)** — replace:

```python
llm_response = llm.call(
    messages=messages,
    tools=[],
    system=system_payload,
    model_override=self._model,
)
```

with:

```python
neutral_messages = [
    Message(role="user", content=[TextBlock(text=user_message_text)])
]
request = ChatRequest(
    messages=neutral_messages,
    system=system_payload,
)
llm_response = self._chat_provider.call(request)
```

The output format (NLU classification produces JSON) — if today's code parses `llm_response.content` as JSON via `self._parse_nlu_json(...)`, keep that pattern after migration but read text via the boundary helper:

```python
text = next((b.text for b in llm_response.content if b.type == "text"), None)
if llm_response.stop_reason == "error" or not text:
    ...
parsed = self._parse_nlu_json(text)
```

(Alternative: have NLU pass `output_format=OutputFormat(schema=...)` and read `chat_response.parsed_output` directly. This is cleaner BUT changes the prompt semantics — the LLM is now asked for strict JSON via `response_format`, which Anthropic emulates via tool-coercion. Risk: prompt-cache invalidation because the request shape changed. **Stay with the manual `_parse_nlu_json` for PR4** — preserves the existing prompt structure and cache hits. Future ticket can migrate to native structured-output.)

5. **`self._model` field** — keep it for now if other code paths reference it (e.g. logging). It's no longer USED to override; the provider is pre-configured. Mark with a comment: `# Stored only for telemetry/logging; the override now happens at provider construction.`

### Test migration

- Drop `from src.llm_wrapper.base import LLMWrapperBase`. Add the chat_provider imports.
- Test fixtures: build a `MagicMock(spec=ChatProviderBase)` and pass it to `NLUProcessor(chat_provider=mock_provider, ...)`.
- `mock_provider.call.return_value` returns a `ChatResponse(content=[TextBlock(text='{"intent": "..."}')], stop_reason="end_turn", model_used="claude-haiku-test", usage=TokenUsage(...))`.
- Tests that called `processor.process(..., llm=mock_wrapper, ...)` — drop the `llm=` kwarg.
- Tests asserting on `model_override` parameter — those expectations go away. If a test asserts on which model was used, it asserts on the provider's `get_active_model()` instead, or on the model_used field of the response.

### Verify

```bash
cd agent_core && uv run pytest tests/test_nlu_processor.py -v
```

All NLU tests pass.

### Commit

```bash
git add agent_core/src/preprocessing/nlu_processor.py agent_core/tests/test_nlu_processor.py
git commit -m "$(cat <<'EOF'
refactor(nlu): migrate NLUProcessor to ChatProviderBase (#291)

NLUProcessor accepts a chat_provider at init time and uses it directly.
The legacy model_override pattern is gone — orchestrator now builds the
NLU-fast provider once and injects it. Removes the last cache_control
literal in caller code: NLU's prompt-cache request now uses
SystemPrompt(blocks=[TextBlock(cache_hint="session")]).

Manual _parse_nlu_json kept (not native output_format / parsed_output)
to preserve current prompt-cache hits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: language_normalisation — accept `chat_provider`

**Files:**
- Modify: `agent_core/src/preprocessing/language_normalisation.py`
- Modify: `agent_core/tests/test_language_normalisation.py`

Same pattern as Task 2.

1. **Init** — accept `chat_provider: ChatProviderBase`. Store as `self._chat_provider`.
2. **`process(..., llm: LLMWrapperBase, ...)`** signature: drop `llm` parameter. Use `self._chat_provider`.
3. **The LLM call (around line 197)** — convert to `ChatRequest`. The current call passes `model_override=model_override`; that goes away — the provider is pre-configured.
4. **Imports** — drop `from src.llm_wrapper.base import LLMWrapperBase`. Add chat_provider imports.

The existing test in `test_language_normalisation.py` mocks `LLMWrapperBase`. Update to `ChatProviderBase` mock with `ChatResponse` return values. If tests assert on the `model_override` kwarg passed to the wrapper, those expectations go away — the provider's identity carries the model.

### Verify + commit

```bash
cd agent_core && uv run pytest tests/test_language_normalisation.py -v
```

Then commit:

```bash
git add agent_core/src/preprocessing/language_normalisation.py agent_core/tests/test_language_normalisation.py
git commit -m "$(cat <<'EOF'
refactor(lang-norm): migrate LanguageNormaliser to ChatProviderBase (#291)

Same pattern as NLU: chat_provider at init, no model_override at the
wrapper boundary. Orchestrator constructs the lang-norm provider with
the configured model and injects it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: orchestrator — build the NLU/lang-norm providers and inject them

**Files:**
- Modify: `agent_core/src/orchestrator.py`
- Modify: `agent_core/tests/test_orchestrator.py`

### Migration

1. **In `AgentCore.__init__`**, after `self._llm = chat_provider`, build the helper providers:

```python
from src.chat_provider import build_chat_provider

# NLU and language_normalisation may use a smaller, cheaper model than
# the primary chat path. Build dedicated providers only when their
# config specifies a different model; otherwise reuse the main provider.
nlu_model = (config.get("nlu") or {}).get("model")
if nlu_model and nlu_model != config["agent"]["primary_model"]:
    self._nlu_chat_provider = build_chat_provider({
        **config["agent"],
        "primary_model": nlu_model,
    })
else:
    self._nlu_chat_provider = self._llm

lang_model = (config.get("language_normalisation") or {}).get("model")
if lang_model and lang_model != config["agent"]["primary_model"]:
    self._lang_chat_provider = build_chat_provider({
        **config["agent"],
        "primary_model": lang_model,
    })
else:
    self._lang_chat_provider = self._llm
```

(Adjust the config-key paths to match the actual config structure — inspect `agent_core/src/schema/config.py` for `nlu.model` and `language_normalisation.model` location.)

2. **`NLUProcessor` construction** — pass `chat_provider=self._nlu_chat_provider`. Drop any `model_override` plumbing.

3. **`LanguageNormaliser` construction** — pass `chat_provider=self._lang_chat_provider`. Drop any `model_override` plumbing.

4. **Call sites** for NLU and language_normalisation — remove the `llm=...` kwarg from any `process(...)` invocation; the helpers now use their stored provider.

### Tests

- `test_orchestrator.py` fixtures construct the orchestrator. After this task, the orchestrator builds extra providers internally — those calls are intercepted by the global `with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic")` blocks already common in tests. If a test asserts on which provider was used for NLU, it now inspects `self._nlu_chat_provider.call.assert_called_once_with(<ChatRequest>)`.
- If any test mocks `language_normalisation` calls expecting `model_override=...`, drop those expectations.

### Verify + commit

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py tests/test_nlu_processor.py tests/test_language_normalisation.py -v
```

Commit:

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
refactor(orchestrator): build NLU + lang-norm providers and inject them (#291)

Orchestrator now constructs separate ChatProviderBase instances for
NLU and language normalisation when they're configured to use a
different model than the primary. Otherwise reuses the main provider.
NLUProcessor and LanguageNormaliser receive their chat_provider at
init time; no per-call model_override.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: turn_assembler — rename passthrough kwarg

**Files:**
- Modify: `agent_core/src/turn_assembler.py`
- Modify: `agent_core/tests/test_turn_assembler.py`

`turn_assembler.py` accepts `llm_wrapper: Any = None` and stores `self._llm = llm_wrapper`. It only passes this through to NLU/language_normalisation. After Tasks 2–4, NLU and lang_norm don't need an llm parameter at process-call time — they own the provider.

So `turn_assembler` no longer needs to hold an llm reference at all. Inspect the `turn_assembler.py` code to confirm no other use; if there's any direct `self._llm.call(...)` invocation, migrate it to `ChatRequest` like in PR3 patterns.

If `turn_assembler` only passes the wrapper through, drop the parameter entirely. If a constructor caller passes it positionally, the rename/drop is a small caller-side fix.

If turn_assembler DOES make its own LLM call (unlikely — confirm by grep), migrate to `chat_provider: ChatProviderBase` and `ChatRequest`.

### Tests

`test_turn_assembler.py` likely has 4 references to `llm_wrapper`. Update them.

### Verify + commit

```bash
cd agent_core && uv run pytest tests/test_turn_assembler.py -v
```

Commit:

```bash
git add agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler.py
git commit -m "$(cat <<'EOF'
refactor(turn-assembler): drop llm_wrapper passthrough; downstream owns provider (#291)

After NLU/language_normalisation migration, the helpers receive their
own chat_provider at init time. TurnAssembler no longer needs to hold
an LLM reference.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: llm_proxy_server — body shape becomes `ChatRequest` / `ChatResponse`

**Files:**
- Modify: `agent_core/src/servers/llm_proxy_server.py`
- Modify: `agent_core/tests/test_llm_proxy_server.py`

### Migration

1. **`create_app(llm: LLMWrapperBase)`** → `create_app(chat_provider: ChatProviderBase)`. Update the docstring.
2. **The `POST /internal/llm/call` endpoint** — body validation switches to a Pydantic model that wraps `ChatRequest`. Response body is `ChatResponse`. Both serialise via Pydantic.
3. **Drop `from src.llm_wrapper.base import LLMWrapperBase`** and add the chat_provider imports.

Example endpoint shape:

```python
from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatRequest, ChatResponse


def create_app(chat_provider: ChatProviderBase) -> FastAPI:
    """..."""
    app = FastAPI(...)

    @app.post("/internal/llm/call", response_model=ChatResponse)
    def llm_call(request: ChatRequest) -> ChatResponse:
        return chat_provider.call(request)

    return app
```

If the existing endpoint accepts a different body shape (legacy `messages: list[dict]`, `tools: list[dict]`, etc.), the schema change is a breaking change to any external HTTP client. Per the spec, the proxy is "implemented, not yet wired" — no production callers — so the breaking change is acceptable. Document it in the commit body.

### Tests

`test_llm_proxy_server.py` constructs the app with a fake wrapper and POSTs JSON. Update:
- Construct the app with a `MagicMock(spec=ChatProviderBase)`.
- POST a `ChatRequest`-shaped JSON body.
- Assert the response matches a serialised `ChatResponse`.

### Verify + commit

```bash
cd agent_core && uv run pytest tests/test_llm_proxy_server.py -v
```

Commit:

```bash
git add agent_core/src/servers/llm_proxy_server.py agent_core/tests/test_llm_proxy_server.py
git commit -m "$(cat <<'EOF'
refactor(llm-proxy): accept ChatRequest, return ChatResponse (#291)

create_app now takes ChatProviderBase. Endpoint body is the neutral
ChatRequest; response body is ChatResponse. Pydantic handles both.

The proxy was implemented but not wired in production, so the body
shape change is non-breaking for live traffic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final regression run, push, draft PR

I'll run this myself.

- [ ] **Step 1** — Full agent_core suite. Expected: same baseline, only the pre-existing kkb voice failure.

```bash
cd agent_core && uv run pytest 2>&1 | tail -5
```

- [ ] **Step 2** — Leakage greps:

```bash
grep -rn "from src.llm_wrapper" agent_core/src/ | grep -v llm_wrapper/ | grep -v __pycache__
grep -rn "cache_control" agent_core/src/ | grep -v __pycache__
```

Expected after PR4:
- `from src.llm_wrapper` matches only inside `agent_core/src/llm_wrapper/` itself.
- `cache_control` literal matches only inside `agent_core/src/chat_provider/anthropic_provider.py` and the legacy adapter `llm_wrapper/claude_wrapper.py`. NLU's literal is gone.

- [ ] **Step 3** — Push and open the draft PR.

```bash
git push -u origin pr4/migrate-remaining-callers
gh pr create --base feature/llm-provider-redesign --head pr4/migrate-remaining-callers --draft \
  --title "PR4: migrate nlu_processor + language_normalisation + turn_assembler + llm_proxy_server" \
  --body "..."
```

PR body covers: scope, the model_override → injected-provider strategy, the proxy body change, what stays for PR5.

---

## Self-review checklist

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §9 PR4 — nlu_processor migrated, cache_hint instead of cache_control | Task 2 |
| §9 PR4 — turn_assembler accepts ChatProviderBase | Task 5 |
| §9 PR4 — llm_proxy_server body becomes ChatRequest/ChatResponse | Task 6 |
| §9 PR4 — every caller uses ChatProviderBase | Tasks 2, 3, 4, 5, 6 |

**Type consistency:** `ChatProviderBase`, `ChatRequest`, `ChatResponse`, `Message`, `TextBlock`, `SystemPrompt`, `OutputFormat` are imported where used. The new orchestrator attributes `_nlu_chat_provider` and `_lang_chat_provider` are referenced only inside the orchestrator.

**Scope:** PR4 only. Adapter and `LLMResponse` deletion is PR5.
