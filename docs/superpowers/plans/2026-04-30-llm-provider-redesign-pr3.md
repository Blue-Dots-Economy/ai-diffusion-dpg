# LLM Provider Redesign — PR3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Migrate `orchestrator.py` and `manager_agent.py` off the legacy `LLMWrapperBase` adapter and onto `ChatProviderBase` directly. Remove every `cache_control: {"type": "ephemeral"}` literal from caller code — the Anthropic provider owns that translation.

**Architecture:** `manager_agent.build_system_prompt()` now returns `SystemPrompt` (with `cache_hint="session"` on tier-1 + tier-2 blocks, no hint on tier-3). `manager_agent.build_messages()` returns `list[Message]`. `manager_agent.run_turn()` calls `ChatProviderBase.call()` internally. Orchestrator accepts a `ChatProviderBase` (the same instance the adapter wraps under the hood today), builds `ChatRequest`, calls `provider.call(request)` and `provider.stream(request)`. At the chat-provider boundary, `ToolUseBlock` is converted to `ToolCall` (the domain type already in `src.models`) for everything downstream — `manager_agent.run_turn` return value, `TurnEvent.tool_calls`, Action Gateway calls. `nlu_processor`, `turn_assembler`, `llm_proxy_server`, and `language_normalisation` continue to use the adapter (PR4 scope).

**Tech Stack:** Python 3.11+, Pydantic v2, existing `chat_provider/`, `pytest`, `uv`.

**Tracking:** Parent #287; resolves #290. Branch `pr3/migrate-orchestrator` off `feature/llm-provider-redesign`.

**Spec:** `docs/superpowers/specs/2026-04-30-llm-provider-redesign-design.md`

---

## File structure

```
agent_core/src/
├── manager_agent.py     # MODIFIED — types changed, cache_control literals removed, run_turn() migrated
├── orchestrator.py      # MODIFIED — accepts ChatProviderBase, 4 call sites migrate, ChatResponse handling
└── models.py            # NO CHANGE (LLMResponse stays for nlu_processor + adapter compat — removed in PR5)

agent_core/tests/
├── test_manager_agent.py     # MODIFIED — fixture switches to ChatProviderBase mock; expectations updated
├── test_orchestrator.py      # MODIFIED — same
├── test_stream_turn.py       # MODIFIED — wherever it constructs orchestrator with llm_wrapper kwarg
└── test_voice_length_cap.py  # MODIFIED if it touches the same constructor; otherwise NO CHANGE
```

Out of scope for PR3:
- `nlu_processor.py`, `turn_assembler.py`, `llm_proxy_server.py`, `language_normalisation.py` (PR4).
- `llm_wrapper/claude_wrapper.py` adapter shim (deleted in PR5).
- `LLMResponse` dataclass — used by adapter and remaining callers. Removed in PR5.
- `ToolCall` dataclass — domain type used by `TurnEvent` and Action Gateway; keep.

---

## Task 1: Branch off; helper to convert ToolUseBlock → ToolCall

**Why:** The orchestrator's tool-use loop reads `tc.input_params` (legacy `ToolCall` field name); `ToolUseBlock.input` differs. A small conversion helper keeps the orchestrator's existing internal references unchanged. The helper lives in `orchestrator.py` as a private free function — doesn't pollute domain models.

- [ ] **Step 1: Branch off**

```bash
git fetch origin
git checkout -b pr3/migrate-orchestrator origin/feature/llm-provider-redesign
```

- [ ] **Step 2: No code change in this task — just branch.**

(The conversion helper will be added inline in Task 5 where it's first used.)

---

## Task 2: manager_agent — `build_system_prompt` returns `SystemPrompt`

**Files:**
- Modify: `agent_core/src/manager_agent.py` (lines 254–408)
- Modify: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Update the source.**

Open `agent_core/src/manager_agent.py`. Replace the `build_system_prompt` method (current lines 254–408) so:

- Return type annotation changes from `list[dict]` to `SystemPrompt`.
- Tier 1 (session-stable) → `TextBlock(text=tier1, cache_hint="session")`
- Tier 2 (state-stable) → `TextBlock(text=tier2, cache_hint="session")`
- Tier 3 (dynamic) → `TextBlock(text=tier3, cache_hint=None)`
- Remove every literal `"cache_control": {"type": "ephemeral"}` dict. The block-construction at the bottom uses neutral `TextBlock`.
- Update the docstring: replace mentions of "cache_control: ephemeral" with "cache_hint='session'"; replace "Anthropic content-block dicts" with "neutral SystemPrompt with TextBlock entries; the Anthropic provider translates session-tier blocks into cache_control markers."

The final block-assembly section (was lines 392–408) becomes:

```python
        # ── Assemble blocks ───────────────────────────────────────────
        from src.chat_provider.types import SystemPrompt, TextBlock

        blocks: list[TextBlock] = []
        if tier1:
            blocks.append(TextBlock(text=tier1, cache_hint="session"))
        if tier2:
            blocks.append(TextBlock(text=tier2, cache_hint="session"))
        if tier3:
            blocks.append(TextBlock(text=tier3))
        return SystemPrompt(blocks=blocks)
```

(Move the `from src.chat_provider.types import` line to the top of the file with the other imports — keeping it at module scope is cleaner.)

- [ ] **Step 2: Update the test fixtures.**

In `agent_core/tests/test_manager_agent.py`, every test that calls `build_system_prompt(...)` and asserts against `list[dict]` shape (`blocks[0]["text"]`, `blocks[0]["cache_control"]`) must change. New shape:

```python
prompt = manager.build_system_prompt(...)
# prompt is SystemPrompt
assert isinstance(prompt, SystemPrompt)
assert prompt.blocks[0].text == "..."
assert prompt.blocks[0].cache_hint == "session"
# Tier 3 has no cache hint
assert prompt.blocks[-1].cache_hint is None
```

Add `from src.chat_provider.types import SystemPrompt, TextBlock` to the test file imports.

There are roughly 8–12 test methods that touch `build_system_prompt` — find them by `grep -n build_system_prompt agent_core/tests/test_manager_agent.py` and update each to use the new shape.

- [ ] **Step 3: Run manager_agent tests**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py -v
```

Tests that exercise `build_system_prompt` should pass; everything else (build_messages, run_turn) is unchanged at this point. If `test_run_turn*` fails because `build_system_prompt`'s return type now flows through to a downstream code path that still expects a list of dicts — STOP and report. We'll handle that in Task 4.

If the orchestrator (which still consumes `build_system_prompt` output) now sees `SystemPrompt` where it expected `list[dict]`, **that's expected** — orchestrator migration happens in Tasks 5–7.

If the existing legacy tests in `test_orchestrator.py` are now broken because of this change — that's also expected, and they fix in Task 8.

- [ ] **Step 4: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "$(cat <<'EOF'
refactor(manager-agent): build_system_prompt returns neutral SystemPrompt (#290)

Removes cache_control literals from manager_agent.py. Tier 1 and Tier 2
blocks carry cache_hint="session"; Tier 3 has no hint. The Anthropic
provider translates session hints to cache_control={"type": "ephemeral"}
on long-enough blocks.

Orchestrator-side adaptation lands in subsequent commits; expect
test_orchestrator.py and run_turn-related manager_agent tests to fail
in the interim.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: manager_agent — `build_messages` returns `list[Message]`

**Files:**
- Modify: `agent_core/src/manager_agent.py` (around line 410)
- Modify: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Update `build_messages`**

Change the return type from `list[dict]` to `list[Message]`. The current implementation builds dicts like `{"role": "user", "content": [{"type": "text", "text": ...}]}`. Replace with:

```python
from src.chat_provider.types import Message, TextBlock

def build_messages(self, user_message: str, current_question: str) -> list[Message]:
    """..."""
    # ... existing logic that builds the prefix string ...
    return [Message(role="user", content=[TextBlock(text=full_text)])]
```

Inspect the existing implementation carefully — it may emit additional content blocks for prior tool exchanges. Each `{"type": "text", "text": ...}` becomes a `TextBlock(text=...)` in the same order. Each `{"type": "tool_use", ...}` becomes a `ToolUseBlock`. Each `{"type": "tool_result", ...}` becomes a `ToolResultBlock`. Preserve the structure exactly.

If the implementation imports tool exchange records from a helper, you'll likely also need to update that helper. Look for the helper around `_build_tool_exchange_messages` (line 1585 area in `manager_agent.py` — it returns a flat list of message dicts; change to `list[Message]`).

- [ ] **Step 2: Update tests**

In `test_manager_agent.py`, locate every assertion against `messages[i]["role"]` / `messages[i]["content"][j]["type"]` etc. Update to:

```python
assert messages[0].role == "user"
assert isinstance(messages[0].content[0], TextBlock)
assert messages[0].content[0].text == "..."
```

- [ ] **Step 3: Run tests**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py -v -k "build_messages or tool_exchange"
```

These targeted tests should pass. The broader `test_manager_agent.py::test_run_turn*` will still be broken until Task 4.

- [ ] **Step 4: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "$(cat <<'EOF'
refactor(manager-agent): build_messages returns list[Message] (#290)

Tool exchange records now build neutral ToolUseBlock / ToolResultBlock
instances. _build_tool_exchange_messages migrated to neutral types too.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: manager_agent — `run_turn` uses `ChatProviderBase`

**Files:**
- Modify: `agent_core/src/manager_agent.py` (lines around 942 — its internal LLM call)
- Modify: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Update `__init__`**

Change `self._llm: LLMWrapperBase = llm_wrapper` to `self._llm: ChatProviderBase = chat_provider`. Update parameter name (`llm_wrapper` → `chat_provider`) and the constructor signature. Update the import: `from src.chat_provider.base import ChatProviderBase` (replace `from src.llm_wrapper.base import LLMWrapperBase`). Keep the `llm_wrapper=` keyword as a backward-compat alias for one cycle if existing tests pass it positionally — easier to just rename and update tests.

- [ ] **Step 2: Update `run_turn`'s LLM call**

Find the `self._llm.call(...)` call in `run_turn`. Today it's:

```python
response = self._llm.call(
    messages=messages,                # list[dict]
    tools=active_tools,                # list[dict]
    system=system,                     # list[dict] | str
    output_format=output_format,
)
# response is LLMResponse
text = response.content
calls = response.tool_calls   # list[ToolCall]
```

After:

```python
from src.chat_provider.types import ChatRequest, ToolDefinition

# Convert legacy `tools` list[dict] to list[ToolDefinition]:
neutral_tools = [
    ToolDefinition(name=t["name"], description=t.get("description", ""),
                   input_schema=t.get("input_schema", {}))
    for t in active_tools
]

# `system` is now SystemPrompt (from Task 2).
# `messages` is now list[Message] (from Task 3).

# Build OutputFormat from output_format dict if present.
neutral_of = None
if output_format is not None:
    from src.chat_provider.types import OutputFormat
    neutral_of = OutputFormat(schema=output_format.get("schema", output_format))

request = ChatRequest(
    messages=messages,
    system=system,
    tools=neutral_tools,
    output_format=neutral_of,
)
chat_response = self._llm.call(request)
# chat_response is ChatResponse
```

Then where the code reads `response.content` (the assistant's text) and `response.tool_calls`:

```python
# Extract text content (first TextBlock) and tool calls.
text: str | None = None
tool_calls: list[ToolCall] = []
for block in chat_response.content:
    if block.type == "text" and text is None:
        text = block.text
    elif block.type == "tool_use":
        tool_calls.append(ToolCall(
            tool_name=block.tool_name,
            tool_use_id=block.tool_use_id,
            input_params=block.input,
        ))
```

The `ToolCall` import (`from src.models import ToolCall`) is already in `manager_agent.py`.

`run_turn`'s return signature `(final_text, tool_calls, tool_results)` is unchanged — the orchestrator is happy.

For logging that previously read `response.input_tokens` / `response.output_tokens`, change to `chat_response.usage.input_tokens or 0` / `chat_response.usage.output_tokens or 0`.

For `response.stop_reason` — same name on `ChatResponse`.

For `response.model_used` — same name.

For tools-loop continuation: the second LLM call after tool execution (line 2172 in orchestrator counterpart? double-check inside manager_agent) gets the same `request` style. If `manager_agent.run_turn` builds and reuses the request across rounds, just rebuild it once with the new messages/results.

- [ ] **Step 3: Update orchestrator's manager-agent construction site**

`agent_core/src/orchestrator.py` constructs `ManagerAgent(llm_wrapper=...)` somewhere. Update that constructor argument name and value source — but for now (Task 4 only), pass the same provider both to manager_agent and (after Tasks 5–7) to orchestrator.

Practical interim: in orchestrator's `__init__`, before Tasks 5–7 land, you can do:

```python
# Temporary: extract the provider from the legacy adapter.
chat_provider = llm_wrapper._primary  # type: ignore[attr-defined]
self._manager_agent = ManagerAgent(chat_provider=chat_provider, ...)
```

This is a known-private access; it's fine for one PR cycle and goes away in Task 5.

- [ ] **Step 4: Update tests**

`test_manager_agent.py::test_run_turn*` tests today mock `LLMWrapperBase.call` to return a fake `LLMResponse`. Change the mock to a `ChatProviderBase` mock returning a fake `ChatResponse`:

```python
from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage

mock_provider = MagicMock(spec=ChatProviderBase)
mock_provider.call.return_value = ChatResponse(
    content=[TextBlock(text="ok")],
    stop_reason="end_turn",
    model_used="claude-test",
    usage=TokenUsage(input_tokens=10, output_tokens=5),
)
manager = ManagerAgent(chat_provider=mock_provider, ...)
```

For tests that simulate tool_use, build a `ChatResponse` with both `TextBlock` and `ToolUseBlock` content.

- [ ] **Step 5: Run tests**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py -v
```

Expected: all manager_agent tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/src/orchestrator.py agent_core/tests/test_manager_agent.py
git commit -m "$(cat <<'EOF'
refactor(manager-agent): run_turn uses ChatProviderBase directly (#290)

Manager Agent no longer touches LLMWrapperBase. Builds ChatRequest,
calls provider.call(), walks ChatResponse.content. ToolUseBlock
converted to ToolCall (domain type) at the boundary so run_turn's
return shape and downstream consumers (TurnEvent, Action Gateway) are
unchanged.

Orchestrator continues to receive an LLMWrapperBase for now and reaches
into wrapper._primary to feed the manager — temporary, removed in the
orchestrator migration commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: orchestrator — accept `ChatProviderBase`; migrate main sync call

**Files:**
- Modify: `agent_core/src/orchestrator.py`

- [ ] **Step 1: Update `__init__`**

Change parameter `llm_wrapper: LLMWrapperBase` to `chat_provider: ChatProviderBase`. Update import: `from src.chat_provider.base import ChatProviderBase`. Drop the `from src.llm_wrapper.base import LLMWrapperBase` import.

`self._llm = chat_provider` (rename or keep `self._llm` — keep, smaller diff).

Remove the temporary `wrapper._primary` extraction from Task 4 — pass `self._llm` to `ManagerAgent` directly.

- [ ] **Step 2: Migrate the main sync call (line 942)**

Today:

```python
llm_response = self._llm.call(
    messages=messages,           # list[dict] (now list[Message] after Task 3)
    tools=active_tools,          # list[dict]
    system=system,               # list[dict] (now SystemPrompt after Task 2)
    output_format=output_format, # dict | None
)
# llm_response is LLMResponse
```

After:

```python
from src.chat_provider.types import ChatRequest, OutputFormat, ToolDefinition

neutral_tools = [
    ToolDefinition(name=t["name"], description=t.get("description", ""),
                   input_schema=t.get("input_schema", {}))
    for t in active_tools
]
neutral_of = (
    OutputFormat(schema=output_format.get("schema", output_format))
    if output_format else None
)
request = ChatRequest(
    messages=messages,           # list[Message] from manager_agent.build_messages
    system=system,               # SystemPrompt from manager_agent.build_system_prompt
    tools=neutral_tools,
    output_format=neutral_of,
)
chat_response = self._llm.call(request)
```

Then update every read of `llm_response.X` in this scope:
- `llm_response.stop_reason` → `chat_response.stop_reason`
- `llm_response.model_used` → `chat_response.model_used`
- `llm_response.input_tokens` → `chat_response.usage.input_tokens or 0`
- `llm_response.output_tokens` → `chat_response.usage.output_tokens or 0`
- `llm_response.content` (str|None) → walk `chat_response.content`, take first `TextBlock.text`. Helper:

  ```python
  def _text_of(resp: ChatResponse) -> str | None:
      for b in resp.content:
          if b.type == "text":
              return b.text
      return None
  ```

  Drop this helper at module scope or keep it inline once. Use `_text_of(chat_response)`.

- `llm_response.tool_calls` (list[ToolCall]) → walk `chat_response.content`, build `list[ToolCall]`. Helper:

  ```python
  def _tool_calls_of(resp: ChatResponse) -> list[ToolCall]:
      return [
          ToolCall(
              tool_name=b.tool_name,
              tool_use_id=b.tool_use_id,
              input_params=b.input,
          )
          for b in resp.content if b.type == "tool_use"
      ]
  ```

  Module-scope helper.

- For the parsed-output path (when `output_format` is set) the orchestrator currently does `json.loads(llm_response.content)`. With the new API, `chat_response.parsed_output` is already the parsed dict — use it directly:

  ```python
  parsed = chat_response.parsed_output
  ```

  Remove the inline `json.loads` at this site.

- [ ] **Step 3: Run targeted tests**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -v -x 2>&1 | tail -30
```

Expect failures because `test_orchestrator.py` mocks the legacy wrapper. The implementation is correct; the tests need Task 8.

If a NON-test failure surfaces (e.g., `AttributeError` on a real production code path) — STOP and fix the implementation before proceeding.

- [ ] **Step 4: Commit**

```bash
git add agent_core/src/orchestrator.py
git commit -m "$(cat <<'EOF'
refactor(orchestrator): main sync LLM call uses ChatProviderBase (#290)

Orchestrator now receives a ChatProviderBase directly (formerly
LLMWrapperBase). Step-8 sync call builds neutral ChatRequest and reads
ChatResponse fields. _text_of and _tool_calls_of helpers extract the
text content and ToolCall list at the boundary.

Tests for this path are updated in a subsequent commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: orchestrator — migrate stream calls (lines 3141 and 3299)

**Files:**
- Modify: `agent_core/src/orchestrator.py`

- [ ] **Step 1: Update both `stream_call` invocations**

Each:

```python
async for token in self._llm.stream_call(
    messages=messages,
    tools=active_tools if active_tools else None,
    system=system,
    max_tokens=channel_max_tokens,
    abort_event=abort_event,
):
```

becomes:

```python
request = ChatRequest(
    messages=messages,
    system=system,
    tools=neutral_tools if active_tools else [],
    max_tokens=channel_max_tokens or 4096,
)
async for token in self._llm.stream(request, abort_event=abort_event):
```

`neutral_tools` is constructed the same way as in Task 5 — share via a small helper if both call sites use it.

The exception handling for `ToolUseRequested` currently looks like:

```python
except ToolUseRequested as e:
    all_tool_calls = e.tool_calls   # list[ToolCall] today
    ...
```

`ToolUseRequested` is now imported from `src.chat_provider.base` (carries `list[ToolUseBlock]`). Update the import:

```python
from src.chat_provider.base import ToolUseRequested
```

(Drop the existing `from src.exceptions import ToolUseRequested`.)

Then convert at the catch site:

```python
except ToolUseRequested as e:
    all_tool_calls = [
        ToolCall(
            tool_name=tu.tool_name,
            tool_use_id=tu.tool_use_id,
            input_params=tu.input,
        )
        for tu in e.tool_calls
    ]
```

Both stream sites need this catch update.

There's a section around line 3284 that does `{"type": "tool_use", "id": tc.tool_use_id, "name": tc.tool_name, "input": tc.input_params}` — that's reconstructing an Anthropic-shape block to feed back into the *next* stream call's `messages`. That dict construction is already obsolete because the next call uses neutral types. Replace with appending a `ToolUseBlock` to the message content (or a Message containing one).

Carefully preserve the multi-round tool loop semantics: each round appends an assistant message with the tool_use blocks and a user message with tool_result blocks, then re-streams. Migrate that to:

```python
messages.append(Message(
    role="assistant",
    content=[
        ToolUseBlock(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, input=tc.input_params)
        for tc in _current_tool_calls
    ],
))
messages.append(Message(
    role="user",
    content=[
        ToolResultBlock(tool_use_id=tr.tool_use_id, content=tr.result_text or json.dumps(tr.result),
                        is_error=not tr.success)
        for tr in _current_tool_results
    ],
))
```

(Inspect the existing append code carefully — preserve every field the legacy version set.)

- [ ] **Step 2: Run targeted tests**

```bash
cd agent_core && uv run pytest tests/test_stream_turn.py -v -x 2>&1 | tail -20
```

Expect mock-shape failures; production-side errors should be zero.

- [ ] **Step 3: Commit**

```bash
git add agent_core/src/orchestrator.py
git commit -m "$(cat <<'EOF'
refactor(orchestrator): stream paths use ChatProviderBase (#290)

Both Step-9 stream sites build neutral ChatRequest and call
provider.stream(). ToolUseRequested now imported from
chat_provider.base; tool calls converted to ToolCall at catch sites.
Tool-loop continuation messages built with ToolUseBlock /
ToolResultBlock instead of Anthropic-shaped dicts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: orchestrator — migrate consent translation call (line 2172)

**Files:**
- Modify: `agent_core/src/orchestrator.py`

- [ ] **Step 1: Migrate the inline call**

```python
response = self._llm.call(
    messages=[{"role": "user", "content": message}],
    tools=[],
    system=(...),
)
```

becomes:

```python
request = ChatRequest(
    messages=[Message(role="user", content=[TextBlock(text=message)])],
    system=SystemPrompt(blocks=[TextBlock(text=(...))]),
)
response = self._llm.call(request)
```

`response.content` (was `str | None`) → use the `_text_of(response)` helper from Task 5.

- [ ] **Step 2: Smoke-test**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -v -k "consent" -x 2>&1 | tail -20
```

If a consent-translation test exists, it'll need its mock updated. Defer that to Task 8.

- [ ] **Step 3: Commit**

```bash
git add agent_core/src/orchestrator.py
git commit -m "$(cat <<'EOF'
refactor(orchestrator): consent translation uses ChatProviderBase (#290)

Final remaining sync call site migrated. Orchestrator no longer
imports from src.llm_wrapper anywhere.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Update orchestrator + stream-turn tests to mock `ChatProviderBase`

**Files:**
- Modify: `agent_core/tests/test_orchestrator.py`
- Modify: `agent_core/tests/test_stream_turn.py`
- Modify: `agent_core/tests/test_voice_length_cap.py` (if it constructs the orchestrator with `llm_wrapper=`)

This is the bulk of the test work.

- [ ] **Step 1: Identify the standard fixture pattern**

Today, tests typically do:

```python
mock_wrapper = MagicMock(spec=LLMWrapperBase)
mock_wrapper.call.return_value = LLMResponse(content="hi", tool_calls=[], stop_reason="end_turn", model_used="x", input_tokens=1, output_tokens=1)
orch = Orchestrator(llm_wrapper=mock_wrapper, ...)
```

Update to:

```python
from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage

mock_provider = MagicMock(spec=ChatProviderBase)
mock_provider.call.return_value = ChatResponse(
    content=[TextBlock(text="hi")],
    stop_reason="end_turn",
    model_used="x",
    usage=TokenUsage(input_tokens=1, output_tokens=1),
)
orch = Orchestrator(chat_provider=mock_provider, ...)
```

For tests that simulate tool_use, the response carries `ToolUseBlock`:

```python
ChatResponse(
    content=[
        TextBlock(text="checking"),
        ToolUseBlock(tool_use_id="t_1", tool_name="lookup", input={"q": "x"}),
    ],
    stop_reason="tool_use",
    model_used="x",
    usage=TokenUsage(),
)
```

For tests that exercise structured output:

```python
ChatResponse(
    content=[TextBlock(text='{"answer": "42"}')],
    parsed_output={"answer": "42"},
    stop_reason="end_turn",
    model_used="x",
    usage=TokenUsage(),
)
```

For streaming tests, mock `provider.stream(...)` returning an async generator that yields tokens, and raises `chat_provider.base.ToolUseRequested(list[ToolUseBlock])` when needed.

- [ ] **Step 2: Sweep test_orchestrator.py**

Find every constructor call to `Orchestrator(...)` and replace `llm_wrapper=` with `chat_provider=`. Replace every `LLMResponse(...)` mock return with `ChatResponse(...)`. Update assertion field accesses (e.g. `assert llm_response.input_tokens == X` → `assert chat_response.usage.input_tokens == X`).

Some tests assert wrapper-internal patches like `wrapper._primary._client.messages.create.assert_called_once_with(...)`. After migration the orchestrator talks to `chat_provider` directly — those tests should now patch on the provider:

```python
mock_provider = MagicMock(spec=ChatProviderBase)
mock_provider.call.assert_called_once_with(<expected ChatRequest>)
```

- [ ] **Step 3: Sweep test_stream_turn.py**

Same pattern. Replace mock streaming generator construction. Replace `ToolUseRequested` import to come from `src.chat_provider.base`. Replace `e.tool_calls` (legacy) with the neutral list — test assertions on `tool_use_id`, `tool_name`, `input` (not `input_params`).

- [ ] **Step 4: test_voice_length_cap.py**

Open the file. If it constructs `Orchestrator(llm_wrapper=...)`, do the same swap. If it doesn't, leave it alone. The pre-existing `test_kkb_voice_channel_max_tokens_validates_against_schema` failure remains unrelated.

- [ ] **Step 5: Run the full agent_core suite**

```bash
cd agent_core && uv run pytest 2>&1 | tail -10
```

Expected: all tests pass except the pre-existing `test_kkb_voice_channel_max_tokens_validates_against_schema`. **PR3 must keep this baseline** — no new failures.

If anything fails that wasn't previously failing, diagnose:
- Test-side mock shape: fix in this task.
- Production-side shape mismatch: STOP and report; back to Tasks 4–7 for the fix.

- [ ] **Step 6: Commit**

```bash
git add agent_core/tests/
git commit -m "$(cat <<'EOF'
test(orchestrator): switch fixtures to ChatProviderBase / ChatResponse (#290)

Updates test_orchestrator.py, test_stream_turn.py, and test_voice_length_cap.py
mocks to use the neutral chat_provider types in place of the legacy
LLMWrapperBase / LLMResponse. Tool-call assertions use input (neutral)
not input_params (legacy domain type).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final regression run, push, draft PR

I'll run this myself.

- [ ] **Step 1: Full run + leakage greps**

```bash
cd agent_core && uv run pytest 2>&1 | tail -5
grep -rn "cache_control" agent_core/src/ | grep -v __pycache__
grep -rn "from src.llm_wrapper" agent_core/src/orchestrator.py agent_core/src/manager_agent.py
```

Expectations:
- Same passing count as PR2 baseline minus the kkb pre-existing failure.
- `cache_control` matches only inside `chat_provider/anthropic_provider.py`.
- Orchestrator and manager_agent have zero `from src.llm_wrapper` imports.

- [ ] **Step 2: Push and open the draft PR**

```bash
git push -u origin pr3/migrate-orchestrator
gh pr create --base feature/llm-provider-redesign --head pr3/migrate-orchestrator --draft \
  --title "PR3: migrate orchestrator + manager_agent to ChatProviderBase" \
  --body "..."
```

PR body covers: scope (orchestrator + manager_agent only), what moved, what stayed (nlu_processor / turn_assembler / llm_proxy_server pending PR4), pre-existing kkb failure note.

---

## Self-review checklist (run before handoff)

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §9 PR3 — orchestrator takes ChatProviderBase | Task 5 |
| §9 PR3 — manager_agent emits SystemPrompt(blocks=[TextBlock(cache_hint=…)]) | Task 2 |
| §9 PR3 — every cache_control literal removed from manager_agent | Task 2 |
| §9 PR3 — orchestrator reads ChatResponse.parsed_output, drops inline json.loads | Task 5 |
| §9 PR3 — adapter shim still in place for nlu/turn_assembler/proxy | Untouched (preserved) |

**Placeholder scan:** every step has executable code or commands.

**Type consistency:** `SystemPrompt`, `Message`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ChatRequest`, `ChatResponse`, `ToolDefinition`, `OutputFormat`, `_text_of`, `_tool_calls_of`, `ChatProviderBase`, `ToolUseRequested` are all used after they are defined or imported.

**Scope:** PR3 only. nlu_processor / turn_assembler / llm_proxy_server / language_normalisation stay on the adapter (PR4). `LLMWrapperBase`, `ClaudeLLMWrapper`, `LLMResponse` all stay (PR5).
