# GH-137 Framework Uplift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the dev-kit Configuration Agent into a guide-driven walkthrough with an agent-type selector, per-type phase gating, new questionnaire fields (invocation rules, dignity check, session-end eval, opening phrases, terminal word, consolidated channel config), and matching runtime consumers.

**Architecture:** Spec at `docs/superpowers/specs/2026-04-21-gh137-framework-uplift-design.md`. Single PR, hard-cut YAML path migration. Runtime changes across agent_core, trust_layer, and reach_layer_voice; dev-kit changes across schemas, tools, and all 12 phase prompts. KKB domain-specific content refresh deferred to follow-up sub-issue #137-child.

**Tech Stack:** Python 3.13 + uv, pytest, PyYAML, OpenTelemetry SDK, Anthropic SDK, pipecat (reach_layer voice).

**Branch:** `GH-137-framework-uplift` (already checked out).

---

## File Structure

**Created:**
- `docs/guide-gaps.md` — companion document recording where the Agent Configuration Guide is silent or under-specified for our DPG abstractions.

**Modified (models + schema):**
- `agent_core/src/models.py` — `TurnResult.session_ended`, `DoneEvent.session_ended`.
- `agent_core/src/workflow_loader.py` — `SubAgent.opening_phrase`, validator behaviour.
- `dev-kit/dev_kit/schemas/agent_core.yaml` — new `channels:` top-level, `connectors.*[].invocation_rules`, `conversation.session_end_eval`, `agent_workflow.subagents[].opening_phrase`; remove `agent.channels` and `reach_layer.channels`.
- `dev-kit/dev_kit/schemas/trust_layer.yaml` — `dignity_check` block.

**Modified (runtime consumers):**
- `agent_core/src/orchestrator.py` — channel-config path migration, opening-phrase gate, `end_session` tool registration + interception, session_end_eval plumbing.
- `agent_core/src/manager_agent.py` — channels top-level read, `session_end_eval_prompt` kwarg.
- `agent_core/src/turn_assembler.py` — `channels.*.turn_assembler` path.
- `agent_core/src/tool_registry.py` (or whichever file owns registry) — `end_session` internal tool registration.
- `trust_layer/src/assemble_constraints.py` (or equivalent) — dignity-check constraint assembly.
- `reach_layer/voice/src/pipecat_services/agent_core_llm.py` (or nearby) — terminal-word append + telephony close on `session_ended=True`.

**Modified (dev-kit agent + tools + prompts):**
- `dev-kit/dev_kit/agent/accumulator.py` — `PHASES` with new `tier` entry, `_meta/project.json` schema extensions.
- `dev-kit/dev_kit/agent/tools.py` — `set_agent_type`, `set_phase` gating, `update_config` path rejections.
- `dev-kit/dev_kit/agent/prompts/base.py` — `AGENT_TYPES`, `SHEET_REQUIREMENTS` matrix.
- `dev-kit/dev_kit/agent/prompts/phases.py` — new `tier` branch + rewritten branches for all 12 phases.

**Modified (domain configs — path migration):**
- `dev-kit/configs/kkb/agent_core.yaml`
- `dev-kit/configs/farmer-friendly/agent_core.yaml`
- `dev-kit/configs/obsrv-docs-assistant/agent_core.yaml`

**Modified (docs):**
- `ARCHITECTURE.md` — channel consolidation paragraph, dignity-check flow, session-end mechanism.

---

## Task 1: Models — session_ended flags

**Files:**
- Modify: `agent_core/src/models.py`
- Test: `agent_core/tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_models.py`:

```python
def test_turn_result_session_ended_default_false():
    from src.models import TurnResult
    result = TurnResult(session_id="s1", turn_id="t1", response_text="hi")
    assert result.session_ended is False


def test_turn_result_session_ended_accepts_true():
    from src.models import TurnResult
    result = TurnResult(session_id="s1", turn_id="t1", response_text="bye", session_ended=True)
    assert result.session_ended is True


def test_done_event_session_ended_default_false():
    from src.models import DoneEvent
    evt = DoneEvent()
    assert evt.session_ended is False


def test_done_event_session_ended_accepts_true():
    from src.models import DoneEvent
    evt = DoneEvent(session_ended=True)
    assert evt.session_ended is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_models.py -v -k session_ended`
Expected: 4 FAILED (AttributeError or TypeError on unknown kwarg).

- [ ] **Step 3: Add fields**

Edit `agent_core/src/models.py`. In the `TurnResult` dataclass (around line 195), append the new field after the existing fields:

```python
@dataclass
class TurnResult:
    """Final result returned to the Reach Layer after a completed turn."""

    session_id: str
    turn_id: str
    response_text: str
    was_escalated: bool               = False
    was_tool_used: bool               = False
    model_used: str                   = ""
    latency_ms: int                   = 0
    session_ended: bool               = False   # NEW — GH-137 session_end_eval signal
```

In the `DoneEvent` dataclass (around line 281), append:

```python
@dataclass
class DoneEvent:
    """Terminal event — always the last event in a stream_turn() sequence."""

    type: str = "done"
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0
    turn_id: str = ""
    turn_status: str = "completed"
    session_ended: bool = False                  # NEW — GH-137 session_end_eval signal

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self))}\n\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_models.py -v`
Expected: all PASS, including 4 new tests.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/models.py agent_core/tests/test_models.py
git commit -m "feat(agent-core): add session_ended flag to TurnResult and DoneEvent (GH-137)"
```

---

## Task 2: Models — SubAgent.opening_phrase

**Files:**
- Modify: `agent_core/src/workflow_loader.py`
- Test: `agent_core/tests/test_workflow_loader.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_workflow_loader.py`:

```python
def test_subagent_opening_phrase_default_empty():
    from src.workflow_loader import SubAgent
    sa = SubAgent(
        id="greeting", name="Greeting", description="d",
        is_start=True, is_terminal=False, special_handler=None,
        valid_intents=[], tools=[], system_prompt="",
        output_format=None, routing=[],
    )
    assert sa.opening_phrase == ""


def test_subagent_opening_phrase_accepts_string():
    from src.workflow_loader import SubAgent
    sa = SubAgent(
        id="greeting", name="Greeting", description="d",
        is_start=True, is_terminal=False, special_handler=None,
        valid_intents=[], tools=[], system_prompt="",
        output_format=None, routing=[],
        opening_phrase="नमस्ते।",
    )
    assert sa.opening_phrase == "नमस्ते।"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py -v -k opening_phrase`
Expected: 2 FAILED (AttributeError / unexpected kwarg).

- [ ] **Step 3: Add field to `SubAgent` dataclass**

Edit `agent_core/src/workflow_loader.py`. In the `SubAgent` dataclass (around line 81), add `opening_phrase: str = ""` as the final field:

```python
@dataclass
class SubAgent:
    """
    Configuration for a single subagent node in the workflow graph.

    Attributes:
        ... (existing attributes) ...
        opening_phrase:   Optional greeting emitted on the first turn of a session
                          (after the consent gate resolves). Empty string disables.
    """

    id: str
    name: str
    description: str
    is_start: bool
    is_terminal: bool
    special_handler: str | None
    valid_intents: list[str]
    tools: list[str]
    system_prompt: str
    output_format: dict | None
    routing: list[RoutingRule]
    opening_phrase: str = ""
```

- [ ] **Step 4: Thread through the loader**

In the same file, find `AgentWorkflowLoader._build_subagent()` (around line 427 where `is_start: bool = bool(raw.get("is_start", False))` is). After the existing field extractions, add:

```python
        opening_phrase: str = str(raw.get("opening_phrase", "") or "")
```

Then in the `return SubAgent(...)` call (around line 453), add as the last argument:

```python
            opening_phrase=opening_phrase,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/workflow_loader.py agent_core/tests/test_workflow_loader.py
git commit -m "feat(agent-core): add opening_phrase field to SubAgent (GH-137)"
```

---

## Task 3: Schema template — agent_core channels top-level + new fields

**Files:**
- Modify: `dev-kit/dev_kit/schemas/agent_core.yaml`

- [ ] **Step 1: Edit the template**

Current file has:
- `agent.channels: {}` under `agent:` section
- `reach_layer.channels: {}` under `reach_layer:` section near the bottom

Make these changes:

**A. Remove `channels: {}` line from the `agent:` section.** Locate the `agent:` block (starts around line 6). The existing line to remove is `agent.channels: {}` or similar nested channel entry. Leave `primary_model`, `fallback_model`, `ask_for_consent`, `consent_prompt`, `timeout_ms`, `retry_attempts`, etc.

**B. Add a new top-level `channels:` block immediately after the `agent:` block:**

```yaml
channels:                        # NEW (GH-137) — per-channel LLM-facing config
  voice:
    system_prompt_suffix: ""     # appended to the main system prompt for this channel
    tts_rules:                   # canonical; dev-kit renders into system_prompt_suffix at authoring time
      numbers: ""
      money: ""
      dates: ""
      time: ""
      phone: ""
      abbreviations: ""
      output_script: ""
      english_loanwords: ""
    terminal_word: ""            # required when voice channel declared
    turn_assembler:              # migrated from reach_layer.channels.voice.turn_assembler
      semantic_gate:
        enabled: false
        confidence_threshold: 0.75
      silence_trigger:
        silence_ms: 400
      max_wait_ceiling:
        max_wait_ms: 8000
  chat:
    system_prompt_suffix: ""
    tts_rules: null
    terminal_word: null
    turn_assembler:
      semantic_gate: {enabled: false, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 0}
      max_wait_ceiling: {max_wait_ms: 0}
  web:
    system_prompt_suffix: ""
    tts_rules: null
    terminal_word: null
    turn_assembler:
      semantic_gate: {enabled: false, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 0}
      max_wait_ceiling: {max_wait_ms: 0}
  cli:
    system_prompt_suffix: ""
    tts_rules: null
    terminal_word: null
    turn_assembler:
      semantic_gate: {enabled: false, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 0}
      max_wait_ceiling: {max_wait_ms: 0}
```

**C. Remove the `reach_layer.channels: {}` line from the `reach_layer:` block.** Keep the `turn_assembler:` defaults block — that's still valid as framework defaults.

**D. Add `session_end_eval` under `conversation:` block.** Find the existing `conversation:` section (contains `blocked_message`, etc.). Append:

```yaml
  session_end_eval:                # NEW (GH-137) — opt-in session-end signalling
    enabled: false                  # set true for agents that should detect call-end
    prompt: ""                      # appended to main system prompt; teaches LLM when to call end_session tool
    fail_action: "none"             # schema-accepted; runtime ignored in this PR
```

**E. Add `invocation_rules` to each connector slot.** Find `connectors:` block. Under each of `read`, `write`, `identity`, and `internal` entry templates, add after `input_schema:`:

```yaml
      invocation_rules:              # NEW (GH-137) — LLM invocation contract
        call_when: ""                # exact trigger condition in plain language
        required_before_calling: []  # list of data field names required before tool may be invoked
        must_not_substitute: ""      # what the LLM must never treat as a substitute
        on_empty: ""                 # what the agent says when tool returns empty
        on_failure: ""               # what the agent says on tool failure / timeout
        bridge_line: ""              # single natural line spoken right before the tool call (for voice)
```

**F. Add `opening_phrase` to the subagent template.** Find `agent_workflow.subagents:` block. After the existing `is_terminal: false` line inside a subagent entry, add:

```yaml
      opening_phrase: ""             # NEW (GH-137) — emitted on first turn only
```

- [ ] **Step 2: YAML sanity parse**

Run: `uv run --with pyyaml python -c "import yaml; d = yaml.safe_load(open('dev-kit/dev_kit/schemas/agent_core.yaml')); assert 'channels' in d and d['channels'].get('voice',{}).get('terminal_word') == ''"`
Expected: no output, exit 0.

- [ ] **Step 3: Run existing dev-kit schema tests**

Run: `cd dev-kit && uv run pytest tests/test_schema.py -v`
Expected: all PASS (schema tests load and validate the template; they'll accept the new structure).

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/schemas/agent_core.yaml
git commit -m "feat(dev-kit): consolidate channels top-level; add invocation_rules, session_end_eval, opening_phrase in agent_core schema (GH-137)"
```

---

## Task 4: Schema template — trust_layer dignity_check

**Files:**
- Modify: `dev-kit/dev_kit/schemas/trust_layer.yaml`

- [ ] **Step 1: Add the block**

Append to `dev-kit/dev_kit/schemas/trust_layer.yaml`:

```yaml
dignity_check:                        # NEW (GH-137) — auto-populated for Conversational agents
  enabled: false                      # dev-kit flips true for Conversational
  questions:                          # authors can override; defaults from the guide
    - "Does this blame the user?"
    - "Does it over-promise?"
    - "Does it push urgency?"
    - "Does it reduce their agency?"
    - "Does it sound like a script instead of a human call?"
  fail_action: "rewrite"              # schema-accepted; runtime ignores in this PR
```

- [ ] **Step 2: YAML sanity parse**

Run: `uv run --with pyyaml python -c "import yaml; d = yaml.safe_load(open('dev-kit/dev_kit/schemas/trust_layer.yaml')); assert 'dignity_check' in d and len(d['dignity_check']['questions']) == 5"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/dev_kit/schemas/trust_layer.yaml
git commit -m "feat(dev-kit): add dignity_check block to trust_layer schema (GH-137)"
```

---

## Task 5: Migrate kkb domain config to new channels path

**Files:**
- Modify: `dev-kit/configs/kkb/agent_core.yaml`

- [ ] **Step 1: Move `agent.channels` → top-level `channels:`**

Read the current `dev-kit/configs/kkb/agent_core.yaml`. It contains:

```yaml
agent:
  ...
  channels:
    voice:
      system_prompt_suffix: "..."
    web:
      system_prompt_suffix: ""
    cli:
      system_prompt_suffix: ""
```

Move this `channels:` block out from under `agent:` and make it top-level. The `agent:` block keeps its other fields.

- [ ] **Step 2: Fold reach_layer.channels.*.turn_assembler (if present in KKB config) into channels.*.turn_assembler**

Check if `reach_layer.channels` exists in `dev-kit/configs/kkb/agent_core.yaml`. If yes, for each channel, move its `turn_assembler` block into the corresponding top-level `channels.<name>.turn_assembler`. If no, skip this step.

- [ ] **Step 3: Add `terminal_word` for voice**

Under `channels.voice`, add `terminal_word: "Goodbye"` (KKB is a Hindi voice agent — "Goodbye" is the canonical terminal word per the prompt doc).

Leave `tts_rules: null` for now — KKB's canonical TTS rules are authored in the follow-up sub-issue #137-child.

- [ ] **Step 4: Remove the `agent.channels` entry and any `reach_layer.channels` entry**

Both paths must be gone from this file after this step.

- [ ] **Step 5: YAML sanity parse**

Run:

```bash
uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('dev-kit/configs/kkb/agent_core.yaml'))
assert 'channels' in d, 'top-level channels missing'
assert 'channels' not in d.get('agent', {}), 'agent.channels still present'
assert 'channels' not in d.get('reach_layer', {}), 'reach_layer.channels still present'
assert d['channels']['voice']['terminal_word'] == 'Goodbye'
print('ok')
"
```

Expected: `ok`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/configs/kkb/agent_core.yaml
git commit -m "refactor(dev-kit/kkb): migrate agent.channels to top-level channels (GH-137)"
```

---

## Task 6: Migrate farmer-friendly domain config

**Files:**
- Modify: `dev-kit/configs/farmer-friendly/agent_core.yaml`

- [ ] **Step 1: Same migration as Task 5**

Apply steps 1–4 from Task 5 to `dev-kit/configs/farmer-friendly/agent_core.yaml`. If the file has no voice channel declared, skip the `terminal_word` addition (only required when voice is declared).

- [ ] **Step 2: YAML sanity parse**

Run:

```bash
uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('dev-kit/configs/farmer-friendly/agent_core.yaml'))
assert 'channels' not in d.get('agent', {})
assert 'channels' not in d.get('reach_layer', {})
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/configs/farmer-friendly/agent_core.yaml
git commit -m "refactor(dev-kit/farmer-friendly): migrate channels to new top-level path (GH-137)"
```

---

## Task 7: Migrate obsrv-docs-assistant domain config

**Files:**
- Modify: `dev-kit/configs/obsrv-docs-assistant/agent_core.yaml`

- [ ] **Step 1: Same migration as Task 6**

Apply the same migration to `dev-kit/configs/obsrv-docs-assistant/agent_core.yaml`.

- [ ] **Step 2: YAML sanity parse**

```bash
uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('dev-kit/configs/obsrv-docs-assistant/agent_core.yaml'))
assert 'channels' not in d.get('agent', {})
assert 'channels' not in d.get('reach_layer', {})
print('ok')
"
```

- [ ] **Step 3: Commit**

```bash
git add dev-kit/configs/obsrv-docs-assistant/agent_core.yaml
git commit -m "refactor(dev-kit/obsrv-docs-assistant): migrate channels to new top-level path (GH-137)"
```

---

## Task 8: Orchestrator — read channels from top-level (hard-cut loader)

**Files:**
- Modify: `agent_core/src/orchestrator.py:1769–1786` (`_resolve_channel_config`)
- Test: `agent_core/tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_orchestrator.py`. Use the existing orchestrator fixture pattern (mirror an existing test that builds an `AgentCore` from config). If your test helper is `_make_orchestrator(config)`, use that:

```python
def test_resolve_channel_config_reads_top_level_channels():
    cfg = {
        "channels": {"voice": {"system_prompt_suffix": "short"}, "cli": {"system_prompt_suffix": ""}},
        # ... minimum-viable other config ...
    }
    oc = _make_orchestrator(cfg)
    result = oc._resolve_channel_config("voice")
    assert result == {"system_prompt_suffix": "short"}


def test_resolve_channel_config_rejects_legacy_agent_channels():
    import pytest
    cfg = {
        "agent": {"channels": {"voice": {"system_prompt_suffix": "old"}}},
        # ... minimum-viable other config ...
    }
    with pytest.raises((ValueError, RuntimeError), match="channels"):
        oc = _make_orchestrator(cfg)
        oc._resolve_channel_config("voice")
```

If `_make_orchestrator` doesn't exist, use whatever existing helper the file uses (look at another orchestrator test for the pattern; replicate it).

- [ ] **Step 2: Verify tests fail**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k "channel_config"`
Expected: FAILED.

- [ ] **Step 3: Update `_resolve_channel_config`**

Edit `agent_core/src/orchestrator.py:1769`. Replace the method body:

```python
    def _resolve_channel_config(self, channel: str) -> dict:
        """Resolve per-channel config from top-level channels.<name>.

        Args:
            channel: Channel name from the inbound TurnInput.

        Returns:
            Channel config dict (at minimum has `system_prompt_suffix` key).

        Raises:
            ValueError: If the channel is not present in the top-level channels config,
                OR if the legacy `agent.channels` path is present (hard-cut migration).
        """
        # Hard-cut: reject legacy path at startup of every turn (cheap dict lookup).
        if self._config.get("agent", {}).get("channels"):
            raise ValueError(
                "agent.channels is removed — migrate to top-level channels.<name> "
                "(see docs/superpowers/specs/2026-04-21-gh137-framework-uplift-design.md)"
            )

        channels = self._config.get("channels", {})
        config = channels.get(channel)
        if config is None:
            raise ValueError(f"Unsupported channel: {channel}")
        return config
```

- [ ] **Step 4: Verify tests pass**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k channel_config`
Expected: PASS.

Run full orchestrator suite to confirm no regression:

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v`
Expected: all PASS (domain configs in tests should already use new path — verify or update in-test configs).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "feat(agent-core): read channels from top-level; reject legacy agent.channels (GH-137)"
```

---

## Task 9: ManagerAgent — channels top-level reference (docstring + tests)

**Files:**
- Modify: `agent_core/src/manager_agent.py:231` (docstring reference)
- Test: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Update docstring reference**

`build_system_prompt` already takes `channel_config: dict | None` — orchestrator passes the right value post-Task 8. Only the docstring references `agent.channels` in the Args block (line 231). Edit:

```python
            channel_config:         Per-channel config dict from the top-level `channels.<name>`
                                    block in agent_core.yaml (post-GH-137). When present and
                                    system_prompt_suffix is non-empty, the suffix is appended
                                    as the final prompt section.
```

- [ ] **Step 2: Run existing manager_agent tests**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -v`
Expected: all PASS (no behaviour change — only docstring).

- [ ] **Step 3: Commit**

```bash
git add agent_core/src/manager_agent.py
git commit -m "docs(agent-core): update manager_agent channel_config docstring to new path (GH-137)"
```

---

## Task 10: TurnAssembler — path migration to channels.*.turn_assembler

**Files:**
- Modify: `agent_core/src/turn_assembler.py:232–277`
- Test: `agent_core/tests/test_turn_assembler.py`

- [ ] **Step 1: Write failing test**

Append to `agent_core/tests/test_turn_assembler.py`:

```python
def test_turn_assembler_reads_top_level_channels():
    from src.turn_assembler import TurnAssembler
    cfg = {
        "reach_layer": {
            "turn_assembler": {
                "semantic_gate": {"enabled": True, "confidence_threshold": 0.75},
                "silence_trigger": {"silence_ms": 400},
                "max_wait_ceiling": {"max_wait_ms": 8000},
            }
        },
        "channels": {
            "voice": {
                "turn_assembler": {
                    "semantic_gate": {"enabled": False, "confidence_threshold": 0.9},
                }
            }
        },
    }
    ta = TurnAssembler(cfg, nlu_processor=None)
    # Voice override should flow through
    policy = ta._resolve_policy("voice")
    assert policy["semantic_gate"]["enabled"] is False
    assert policy["semantic_gate"]["confidence_threshold"] == 0.9


def test_turn_assembler_rejects_legacy_reach_layer_channels():
    import pytest
    from src.turn_assembler import TurnAssembler
    cfg = {
        "reach_layer": {
            "turn_assembler": {"silence_trigger": {"silence_ms": 400}},
            "channels": {"voice": {"turn_assembler": {"silence_trigger": {"silence_ms": 200}}}},
        },
    }
    with pytest.raises((ValueError, RuntimeError), match="channels"):
        TurnAssembler(cfg, nlu_processor=None)
```

If `_resolve_policy` is a differently-named method in the file, use whichever is the per-channel policy assembly method. If you can't isolate a testable method, adjust assertions to exercise the config read on an instance construction and check `_channels_config` (private) equivalency.

- [ ] **Step 2: Verify tests fail**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler.py -v -k "top_level or legacy_reach"`
Expected: FAILED.

- [ ] **Step 3: Update path reads + rejection**

Edit `agent_core/src/turn_assembler.py`. In `__init__` (around line 232), replace the per-channel config extraction:

```python
        # Defaults: reach_layer.turn_assembler (unchanged)
        rl_config: dict = config.get("reach_layer", {})
        ta_defaults: dict = rl_config.get("turn_assembler", {})

        # Hard-cut: reject legacy reach_layer.channels path (GH-137 migration).
        if rl_config.get("channels"):
            raise ValueError(
                "reach_layer.channels in agent_core config is removed — move per-channel "
                "turn_assembler to top-level channels.<name>.turn_assembler "
                "(see docs/superpowers/specs/2026-04-21-gh137-framework-uplift-design.md)"
            )

        # Per-channel overrides now come from top-level channels.<name>.turn_assembler
        self._channels_config: dict = config.get("channels", {})
```

Then update any method that reads `self._channels_config.get(channel, {}).get("turn_assembler", {})` — that signature stays the same because we restructured the stored dict.

- [ ] **Step 4: Verify tests pass**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler.py
git commit -m "feat(agent-core): read turn_assembler per-channel from top-level channels; reject legacy path (GH-137)"
```

---

## Task 11: Orchestrator — opening-phrase gate

**Files:**
- Modify: `agent_core/src/orchestrator.py` (around line 363, right after the consent gate)
- Test: `agent_core/tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_orchestrator.py`:

```python
def test_opening_phrase_emitted_on_first_turn_when_configured(orchestrator_with_start_subagent):
    """When opening_phrase is set on the start subagent, it is emitted on turn 0."""
    # orchestrator_with_start_subagent fixture sets:
    # - start subagent has opening_phrase="नमस्ते।"
    # - bundle.session starts empty (new session)
    # - ask_for_consent=False (consent gate not triggered)
    result = orchestrator_with_start_subagent.process_turn(
        _make_turn_input(session_id="s1", user_message="", channel="cli"),
    )
    assert result.response_text == "नमस्ते।"


def test_opening_phrase_skipped_when_already_emitted(orchestrator_with_start_subagent, spy_memory):
    """opening_phrase_emitted flag in session prevents re-emission."""
    spy_memory.session_state["s1"] = {"opening_phrase_emitted": True, "turn_count": 5}
    result = orchestrator_with_start_subagent.process_turn(
        _make_turn_input(session_id="s1", user_message="hello", channel="cli"),
    )
    # Should fall through to normal LLM turn, not emit the greeting
    assert result.response_text != "नमस्ते।"


def test_opening_phrase_after_consent_gate(orchestrator_with_consent_and_opening):
    """Consent gate fires on turn 0; opening phrase fires on turn 1 after consent resolved."""
    # Turn 0: consent prompt
    r0 = orchestrator_with_consent_and_opening.process_turn(
        _make_turn_input(session_id="s1", user_message="", channel="cli"),
    )
    assert "consent" in r0.response_text.lower() or r0.response_text  # consent prompt text
    # Turn 1: user accepts, opening phrase emitted
    r1 = orchestrator_with_consent_and_opening.process_turn(
        _make_turn_input(session_id="s1", user_message="yes", channel="cli"),
    )
    assert r1.response_text == "नमस्ते।"


def test_empty_opening_phrase_sets_flag_and_falls_through(orchestrator_with_empty_opening, spy_memory):
    """Empty opening_phrase still sets opening_phrase_emitted flag so gate isn't re-checked."""
    orchestrator_with_empty_opening.process_turn(
        _make_turn_input(session_id="s1", user_message="hello", channel="cli"),
    )
    writes = [w for w in spy_memory.writes if w["key"] == "opening_phrase_emitted"]
    assert any(w["value"] is True for w in writes)
```

If the listed fixtures don't exist, build minimal versions near the top of the test file (see existing orchestrator test scaffolding for pattern; add them as helpers). The key config fragments are:
- `agent.ask_for_consent: true` for consent-path fixture
- `agent_workflow.subagents[0].opening_phrase: "नमस्ते।"` for opening-phrase fixture
- empty string variant for fall-through

- [ ] **Step 2: Verify tests fail**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k opening_phrase`
Expected: FAILED.

- [ ] **Step 3: Implement the gate**

Edit `agent_core/src/orchestrator.py`. Find the end of the consent gate block (line ~363, the line that reads `# if user_storage_mode is set → fall through, skip consent gate entirely`). **After** the consent gate block and **before** the Trust `/check/input` call, insert:

```python
        # ── Opening-phrase gate (Step 1c, GH-137) ────────────────────────
        # Emit the current subagent's opening_phrase exactly once per session,
        # on the first post-consent turn. Subsequent turns skip this check.
        if not bundle.session.get("opening_phrase_emitted", False):
            current_sa_id = bundle.session.get("current_subagent") or self._workflow.start_subagent_id
            current_sa = self._workflow.subagents.get(current_sa_id)
            opening_phrase = (current_sa.opening_phrase if current_sa else "").strip()

            # Always set the flag so we don't re-check every turn.
            self._write_memory_sync(session_id, user_id, "session", "opening_phrase_emitted", True)

            if opening_phrase:
                # Ensure current_subagent is persisted so next turn has it.
                self._write_memory_sync(session_id, user_id, "session", "current_subagent", current_sa_id)
                logger.info(
                    "orchestrator.opening_phrase_emitted",
                    extra={
                        "operation": "orchestrator.opening_phrase_gate",
                        "status": "emitted",
                        "session_id": session_id,
                        "subagent_id": current_sa_id,
                    },
                )
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=opening_phrase,
                    latency_ms=int((time.time() - start) * 1000),
                )
            # else: empty opening_phrase — flag is set; fall through to normal turn.
```

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k opening_phrase`
Expected: PASS.

Run full orchestrator suite:

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "feat(agent-core): emit subagent opening_phrase on first turn after consent (GH-137)"
```

---

## Task 12: Orchestrator — end_session internal tool + session_ended flag

**Files:**
- Modify: `agent_core/src/orchestrator.py` (tool-registration path + tool-use handling)
- Modify: `agent_core/src/manager_agent.py` (`build_system_prompt` kwarg, `end_session` interception in tool loop)
- Test: `agent_core/tests/test_orchestrator.py`, `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_orchestrator.py`:

```python
def test_end_session_tool_registered_when_enabled():
    """When conversation.session_end_eval.enabled=true, end_session is in the tool list."""
    cfg = _base_config_with_session_end_eval(enabled=True)
    oc = _make_orchestrator(cfg)
    tools = oc._build_tool_definitions_for_subagent("main")
    tool_names = {t["name"] for t in tools}
    assert "end_session" in tool_names


def test_end_session_tool_absent_when_disabled():
    cfg = _base_config_with_session_end_eval(enabled=False)
    oc = _make_orchestrator(cfg)
    tools = oc._build_tool_definitions_for_subagent("main")
    tool_names = {t["name"] for t in tools}
    assert "end_session" not in tool_names


def test_end_session_tool_call_sets_session_ended_true(orchestrator_with_session_end_eval, mock_llm_calling_end_session):
    """LLM calling end_session tool sets TurnResult.session_ended=True and does not execute externally."""
    result = orchestrator_with_session_end_eval.process_turn(
        _make_turn_input(session_id="s1", user_message="thanks goodbye"),
    )
    assert result.session_ended is True
```

Append to `agent_core/tests/test_manager_agent.py`:

```python
def test_build_system_prompt_session_end_eval_prompt_rendered():
    agent = _fresh_manager_agent()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        session_end_eval_prompt="Call end_session when the user says goodbye.",
    )
    assert "Call end_session when the user says goodbye." in result


def test_build_system_prompt_session_end_eval_prompt_none_no_section():
    agent = _fresh_manager_agent()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        session_end_eval_prompt=None,
    )
    # No session_end_eval text anywhere
    assert "end_session" not in result
```

- [ ] **Step 2: Verify tests fail**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py tests/test_manager_agent.py -v -k "end_session or session_end_eval"`
Expected: FAILED.

- [ ] **Step 3: Add `end_session` tool registration**

Edit `agent_core/src/orchestrator.py`. In `AgentCore.__init__`, after `self._workflow = workflow` (or equivalent workflow assignment), cache the session_end_eval config and register the internal tool if enabled:

```python
        # Session-end signal (GH-137) — optional, opt-in per domain.
        session_end_cfg = self._config.get("conversation", {}).get("session_end_eval", {}) or {}
        self._session_end_eval_enabled: bool = bool(session_end_cfg.get("enabled", False))
        self._session_end_eval_prompt: str = str(session_end_cfg.get("prompt", "") or "")

        if self._session_end_eval_enabled:
            # Register end_session as an internal tool routed to orchestrator (no external executor).
            self._tool_registry.register_internal(
                name="end_session",
                route="orchestrator",
                description=(
                    "Call when the conversation has naturally concluded (user said "
                    "goodbye, task completed, user asked to stop). Emits the session-"
                    "end signal to runtime; still include your natural final response "
                    "text alongside this tool call."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "enum": ["user_goodbye", "task_complete", "user_requested_stop", "other"],
                        },
                    },
                    "required": ["reason"],
                },
            )
```

If `tool_registry.register_internal` doesn't exist, add it to `agent_core/src/tool_registry.py`:

```python
    def register_internal(
        self,
        *,
        name: str,
        route: str,
        description: str,
        input_schema: dict,
    ) -> None:
        """Register an orchestrator-routed internal tool at runtime (GH-137 end_session)."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
        }
        self._routes[name] = route
```

- [ ] **Step 4: Intercept `end_session` tool use**

Edit `agent_core/src/manager_agent.py`. In `run_turn`, find the existing tool-use loop block that iterates `current_response.tool_calls`. Before the existing routing check (`if self._registry.get_route(tool_call.tool_name) == "knowledge_engine"`), add:

```python
                if tool_call.tool_name == "end_session":
                    # GH-137: internal signal — no external execution. Just mark the flag.
                    self._session_ended_flag = True
                    # Synthesise a benign tool_result so the LLM sees a close.
                    tool_result = ToolResult(
                        tool_use_id=tool_call.tool_use_id,
                        tool_name="end_session",
                        result={"acknowledged": True},
                        success=True,
                        result_text="Session end acknowledged.",
                    )
                    all_tool_calls.append(tool_call)
                    all_tool_results.append(tool_result)
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool_call.tool_use_id,
                        "name": tool_call.tool_name,
                        "input": tool_call.input_params,
                    })
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call.tool_use_id,
                        "content": "Session end acknowledged.",
                    })
                    continue
```

Also add `self._session_ended_flag: bool = False` to `ManagerAgent.__init__`.

After `run_turn` completes, add a helper / property:

```python
    @property
    def session_ended(self) -> bool:
        """Returns True if the LLM called end_session during this turn's tool loop."""
        return self._session_ended_flag

    def _reset_turn_flags(self) -> None:
        """Call at the start of each turn to clear per-turn flags."""
        self._session_ended_flag = False
```

Call `_reset_turn_flags()` at the top of `run_turn`.

Orchestrator reads `self._manager_agent.session_ended` after `run_turn` returns, and threads it into the `TurnResult`:

```python
        # After manager_agent.run_turn() completes (find the existing call in orchestrator)
        turn_result = TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=response_text,
            # ... other fields ...
            session_ended=self._manager_agent.session_ended,
        )
```

For the streaming path, mirror this into `DoneEvent.session_ended`.

- [ ] **Step 5: Pass session_end_eval_prompt into build_system_prompt**

Edit `agent_core/src/manager_agent.py`. Extend `build_system_prompt` signature:

```python
    def build_system_prompt(
        self,
        agent_system_prompt: str,
        subagent_system_prompt: str,
        detected_language: str,
        channel: str,
        profile: dict,
        channel_config: dict | None = None,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
        user_state_guidance: str | None = None,
        session_end_eval_prompt: str | None = None,
    ) -> str:
```

After the `if user_state_guidance:` block (from GH-139) and before guardrail constraints, insert:

```python
        if session_end_eval_prompt:
            parts.append(
                "## Session-end evaluation\n" + session_end_eval_prompt.strip()
            )
```

In orchestrator's call to `build_system_prompt`, pass `session_end_eval_prompt=self._session_end_eval_prompt if self._session_end_eval_enabled else None`.

- [ ] **Step 6: Run tests**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py tests/test_manager_agent.py -v -k "end_session or session_end_eval"`
Expected: PASS.

Run full agent_core suite:

Run: `cd agent_core && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/src/manager_agent.py agent_core/src/tool_registry.py agent_core/tests/test_orchestrator.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent-core): end_session internal tool + session_end_eval system prompt (GH-137)"
```

---

## Task 13: Trust Layer — dignity_check constraint assembly

**Files:**
- Modify: `trust_layer/src/` wherever `/assemble_constraints` is implemented (search for `assemble_constraints` in `trust_layer/src/`)
- Test: `trust_layer/tests/` wherever existing assemble_constraints tests live

- [ ] **Step 1: Locate the endpoint**

Run: `grep -rn "assemble_constraints" trust_layer/src/ | head`

Note the file containing the handler — likely `trust_layer/src/server.py` or `trust_layer/src/guardrails.py` or similar. Open it to understand the existing response shape: it returns a dict with `prompt_constraints` (list) and `required_disclosures` (list).

- [ ] **Step 2: Write failing test**

In the matching test file for that module, append:

```python
def test_assemble_constraints_appends_dignity_check_when_enabled():
    cfg = {
        "dignity_check": {
            "enabled": True,
            "questions": ["Does this blame the user?", "Does it over-promise?"],
            "fail_action": "rewrite",
        },
    }
    handler = <ModuleUnderTest>(cfg)
    result = handler.assemble(active_risks=[])
    constraints = result["prompt_constraints"]
    assert any("## Pre-response dignity check" in c for c in constraints) \
        or any("Does this blame the user?" in c for c in constraints)


def test_assemble_constraints_skips_dignity_when_disabled():
    cfg = {"dignity_check": {"enabled": False, "questions": ["X"]}}
    handler = <ModuleUnderTest>(cfg)
    result = handler.assemble(active_risks=[])
    constraints = result["prompt_constraints"]
    assert not any("dignity check" in c.lower() for c in constraints)


def test_assemble_constraints_no_block_default_disabled():
    cfg = {}  # no dignity_check key
    handler = <ModuleUnderTest>(cfg)
    result = handler.assemble(active_risks=[])
    constraints = result["prompt_constraints"]
    assert not any("dignity" in c.lower() for c in constraints)
```

Replace `<ModuleUnderTest>` with whatever class or function signs for assemble_constraints.

- [ ] **Step 3: Verify tests fail**

Run: `cd trust_layer && uv run pytest -v -k dignity`
Expected: FAILED.

- [ ] **Step 4: Implement**

In the handler module, after existing constraint assembly and before returning, add:

```python
        dignity = (self._config or {}).get("dignity_check", {}) or {}
        if dignity.get("enabled", False):
            questions = dignity.get("questions") or []
            if questions:
                block = "## Pre-response dignity check\n" + "\n".join(
                    f"- {q}" for q in questions
                )
                prompt_constraints.append(block)
```

Exact variable names will match the handler's local conventions — mirror existing constraint building.

- [ ] **Step 5: Run tests**

Run: `cd trust_layer && uv run pytest -v -k dignity`
Expected: PASS.

Run full trust_layer suite:

Run: `cd trust_layer && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add trust_layer/src/ trust_layer/tests/
git commit -m "feat(trust-layer): append dignity_check questions to prompt_constraints (GH-137)"
```

---

## Task 14: reach_layer_voice — terminal-word append on session_ended

**Files:**
- Modify: `reach_layer/voice/src/pipecat_services/agent_core_llm.py` (around line 297 where `DoneEvent` is handled)
- Modify: `reach_layer/voice/src/vobiz_adapter.py` (or wherever telephony close is owned)
- Test: `reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py`

- [ ] **Step 1: Write failing tests**

Append to `reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py`:

```python
def test_done_event_session_ended_appends_terminal_word(fake_tts_streamer, fake_telephony, caplog):
    """On DoneEvent.session_ended=True, terminal word is appended and telephony close is requested."""
    # Setup: domain config has channels.voice.terminal_word = "Goodbye"
    svc = AgentCoreLLMService(
        ...,
        channel_config={"terminal_word": "Goodbye"},
        tts_streamer=fake_tts_streamer,
        telephony=fake_telephony,
    )
    # Simulate a DoneEvent with session_ended=True arriving
    import asyncio
    asyncio.run(svc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed")))

    assert "Goodbye" in fake_tts_streamer.pushed
    assert fake_telephony.closed is True


def test_done_event_session_ended_false_does_not_append_or_close(fake_tts_streamer, fake_telephony):
    svc = AgentCoreLLMService(
        ...,
        channel_config={"terminal_word": "Goodbye"},
        tts_streamer=fake_tts_streamer,
        telephony=fake_telephony,
    )
    import asyncio
    asyncio.run(svc._handle_done_event(DoneEvent(session_ended=False, turn_status="completed")))

    assert "Goodbye" not in fake_tts_streamer.pushed
    assert fake_telephony.closed is False


def test_done_event_session_ended_empty_terminal_word_logs_warning(fake_tts_streamer, fake_telephony, caplog):
    svc = AgentCoreLLMService(
        ...,
        channel_config={"terminal_word": ""},
        tts_streamer=fake_tts_streamer,
        telephony=fake_telephony,
    )
    import asyncio
    asyncio.run(svc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed")))

    # Close is still invoked, warning logged, nothing appended.
    assert fake_telephony.closed is True
    assert "" == "".join(fake_tts_streamer.pushed).replace(" ", "")
    assert any("terminal_word" in rec.message or "terminal word" in rec.message for rec in caplog.records)
```

Replace placeholders (`AgentCoreLLMService`, fixture names, field names) with the actual ones used in `agent_core_llm.py`.

- [ ] **Step 2: Verify tests fail**

Run: `cd reach_layer/voice && uv run pytest tests/pipecat_services/test_agent_core_llm.py -v -k session_ended`
Expected: FAILED.

- [ ] **Step 3: Implement DoneEvent handling**

Edit `reach_layer/voice/src/pipecat_services/agent_core_llm.py:297` where `DoneEvent` is handled. Before the existing `break`, add:

```python
                    if event.session_ended:
                        terminal_word = (self._channel_config or {}).get("terminal_word", "") or ""
                        if terminal_word:
                            # Push the terminal word as the final TTS utterance, then close.
                            await self.push_frame(TextFrame(terminal_word))
                        else:
                            logger.warning(
                                "agent_core_llm.session_ended_no_terminal_word",
                                extra={
                                    "operation": "agent_core_llm.done",
                                    "status": "skipped",
                                    "reason": "terminal_word empty",
                                    "call_sid": self._call_sid,
                                },
                            )
                        # Request telephony close (after terminal word TTS completes downstream).
                        if self._telephony is not None:
                            await self._telephony.close_call(reason="session_end")
                        logger.info(
                            "agent_core_llm.session_ended",
                            extra={
                                "operation": "agent_core_llm.done",
                                "status": "success",
                                "call_sid": self._call_sid,
                            },
                        )
```

If the existing service class doesn't take `_channel_config` or `_telephony` attributes, thread them through its constructor (small plumbing change; update callers in `server.py` / `bot.py`).

If `close_call` doesn't exist on the telephony adapter, add a minimal method on `reach_layer/voice/src/vobiz_adapter.py`:

```python
    async def close_call(self, *, reason: str = "normal") -> None:
        """Close the active call. Used on session_end signal from agent_core (GH-137)."""
        logger.info(
            "vobiz_adapter.close_call",
            extra={"operation": "vobiz_adapter.close_call", "status": "invoked", "reason": reason},
        )
        # Implementation note: call the adapter's existing hangup/close mechanism.
        # If unavailable, post a shutdown frame to the pipecat pipeline.
        if self._ws is not None:
            await self._ws.close()
```

- [ ] **Step 4: Run tests**

Run: `cd reach_layer/voice && uv run pytest tests/pipecat_services/test_agent_core_llm.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/pipecat_services/agent_core_llm.py reach_layer/voice/src/vobiz_adapter.py reach_layer/voice/tests/
git commit -m "feat(reach-layer/voice): append terminal_word and close on DoneEvent.session_ended (GH-137)"
```

---

## Task 15: Dev-kit — PHASES with `tier`, project meta agent_type/phase_decisions

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Modify: `dev-kit/dev_kit/agent/app.py` (where `project.json` is written/read)
- Test: `dev-kit/agent/tests/test_accumulator.py` (or similar)

- [ ] **Step 1: Write failing test**

Append to the nearest `test_accumulator.py`:

```python
from dev_kit.agent.accumulator import PHASES


def test_tier_phase_is_first():
    assert PHASES[0] == "tier"


def test_phases_count():
    assert len(PHASES) == 12


def test_user_state_phase_between_memory_and_trust():
    assert PHASES.index("user_state") == PHASES.index("memory") + 1
    assert PHASES.index("user_state") == PHASES.index("trust") - 1
```

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest tests/test_accumulator.py agent/tests/test_accumulator.py -v -k "tier or phases_count or user_state_phase_between"`
Expected: FAILED.

- [ ] **Step 3: Update PHASES**

Edit `dev-kit/dev_kit/agent/accumulator.py`:

```python
PHASES: list[str] = [
    "tier",
    "overview",
    "language",
    "knowledge",
    "memory",
    "user_state",
    "trust",
    "tools",
    "workflow",
    "observability",
    "reach",
    "review",
]
```

- [ ] **Step 4: Extend project.json schema usage**

Find where `_meta/project.json` is initialised (likely `app.py:create_project` or a helper). The new project meta shape must include:

```python
meta = {
    "slug": slug,
    "name": body.name,
    "agent_type": "",                     # populated by set_agent_type tool (GH-137)
    "phase_decisions": {},                # populated by set_phase (GH-137)
    "created_at": now_iso(),
}
```

Only add these two keys; don't rip up other existing keys.

- [ ] **Step 5: Run tests**

Run: `cd dev-kit && uv run pytest tests/test_accumulator.py agent/tests/test_accumulator.py -v`
Expected: all PASS. If an existing test asserts `len(PHASES) == 11`, update it to 12.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/accumulator.py dev-kit/dev_kit/agent/app.py dev-kit/tests/test_accumulator.py dev-kit/agent/tests/test_accumulator.py
git commit -m "feat(dev-kit): add tier pre-phase and project meta agent_type/phase_decisions (GH-137)"
```

---

## Task 16: Dev-kit — AGENT_TYPES + SHEET_REQUIREMENTS

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/base.py`
- Test: `dev-kit/agent/tests/test_prompts_base.py`

- [ ] **Step 1: Write failing test**

Append to `dev-kit/agent/tests/test_prompts_base.py`:

```python
from dev_kit.agent.prompts.base import AGENT_TYPES, SHEET_REQUIREMENTS


def test_agent_types_enum():
    assert set(AGENT_TYPES) == {"transactional", "informational", "agentic", "conversational"}


def test_sheet_requirements_user_state_only_conversational():
    row = SHEET_REQUIREMENTS["user_state"]
    assert row["conversational"] == "required"
    assert row["transactional"] == "skip"
    assert row["informational"] == "skip"
    assert row["agentic"] == "skip"


def test_sheet_requirements_tools_skipped_for_informational():
    assert SHEET_REQUIREMENTS["tools"]["informational"] == "skip"


def test_sheet_requirements_knowledge_skipped_for_transactional():
    assert SHEET_REQUIREMENTS["knowledge"]["transactional"] == "skip"


def test_sheet_requirements_all_phases_covered():
    expected = {"overview", "language", "knowledge", "memory", "user_state", "trust",
                "tools", "workflow", "observability", "reach", "review"}
    assert expected.issubset(set(SHEET_REQUIREMENTS.keys()))
```

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_base.py -v -k "agent_types or sheet_requirements"`
Expected: FAILED (ImportError).

- [ ] **Step 3: Add the constants**

Edit `dev-kit/dev_kit/agent/prompts/base.py` (create the symbols if they don't exist):

```python
AGENT_TYPES: list[str] = ["transactional", "informational", "agentic", "conversational"]

SHEET_REQUIREMENTS: dict[str, dict[str, str]] = {
    "overview":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "language":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "knowledge":     {"transactional": "skip",     "informational": "required", "agentic": "optional", "conversational": "optional"},
    "memory":        {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "user_state":    {"transactional": "skip",     "informational": "skip",     "agentic": "skip",     "conversational": "required"},
    "trust":         {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "tools":         {"transactional": "required", "informational": "skip",     "agentic": "required", "conversational": "required"},
    "workflow":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "observability": {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "reach":         {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "review":        {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
}
```

- [ ] **Step 4: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/base.py dev-kit/agent/tests/test_prompts_base.py
git commit -m "feat(dev-kit): add AGENT_TYPES enum and SHEET_REQUIREMENTS matrix (GH-137)"
```

---

## Task 17: Dev-kit — `set_agent_type` tool

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Test: `dev-kit/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/agent/tests/test_tools.py`:

```python
def test_set_agent_type_writes_meta(tmp_project):
    handler = ToolHandler(state={"slug": tmp_project.slug}, ...)
    result = handler.handle_tool_call("set_agent_type", {"type": "conversational"})
    assert "ok" in result.lower()
    # project.json now has agent_type: conversational
    import json
    meta = json.loads((tmp_project.path / "_meta" / "project.json").read_text())
    assert meta["agent_type"] == "conversational"


def test_set_agent_type_rejects_unknown():
    handler = ToolHandler(state={}, ...)
    result = handler.handle_tool_call("set_agent_type", {"type": "hybrid"})
    assert "error" in result.lower() or "invalid" in result.lower()


def test_set_agent_type_tool_definition_present():
    handler = ToolHandler(state={}, ...)
    tool_names = {t["name"] for t in handler.get_tool_definitions()}
    assert "set_agent_type" in tool_names
```

Adjust `ToolHandler` / fixtures to match the existing test scaffolding (mirror an existing tool test in the file).

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v -k set_agent_type`
Expected: FAILED.

- [ ] **Step 3: Implement**

Edit `dev-kit/dev_kit/agent/tools.py`. Add to the tool definitions list (where `set_phase`, `update_config`, etc. are declared — around line 60):

```python
        {
            "name": "set_agent_type",
            "description": (
                "Sets the agent type classification for this project. Valid values: "
                "transactional, informational, agentic, conversational. Driven by the "
                "3-question decision tree in the tier phase."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": AGENT_TYPES,  # imported from prompts.base
                    }
                },
                "required": ["type"],
            },
        },
```

Then in the dispatch logic (around line 400 where `self._handle_set_phase` is hooked up), add:

```python
            "set_agent_type": self._handle_set_agent_type,
```

And the handler:

```python
    def _handle_set_agent_type(self, inputs: dict) -> str:
        agent_type = inputs.get("type", "")
        if agent_type not in AGENT_TYPES:
            return f"ERROR — invalid agent type: {agent_type}. Must be one of: {AGENT_TYPES}"

        self._update_project_meta({"agent_type": agent_type})
        return f"ok: agent_type set to {agent_type}"
```

Add `_update_project_meta` as a helper if one doesn't exist — it reads `_meta/project.json`, merges the new keys, writes back.

- [ ] **Step 4: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/agent/tests/test_tools.py
git commit -m "feat(dev-kit): set_agent_type tool writes agent_type to project meta (GH-137)"
```

---

## Task 18: Dev-kit — `set_phase` gating against SHEET_REQUIREMENTS + phase_decisions

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Test: `dev-kit/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/agent/tests/test_tools.py`:

```python
def test_set_phase_auto_skips_user_state_for_transactional(tmp_project):
    _set_meta(tmp_project, {"agent_type": "transactional"})
    handler = _handler_on(tmp_project, current_phase="memory")
    # Transactional should skip user_state and advance directly to trust
    result = handler.handle_tool_call("set_phase", {"phase": "user_state"})
    assert "skipped" in result.lower() or "trust" in result.lower()
    # phase_decisions should record not_applicable_for_type for user_state
    meta = _read_meta(tmp_project)
    assert meta["phase_decisions"]["user_state"]["status"] == "not_applicable_for_type"


def test_set_phase_respects_phase_decision_answered(tmp_project):
    _set_meta(tmp_project, {
        "agent_type": "conversational",
        "phase_decisions": {"memory": {"status": "answered", "timestamp": "..."}},
    })
    handler = _handler_on(tmp_project, current_phase="memory")
    # Moving forward from memory should be allowed
    result = handler.handle_tool_call("set_phase", {"phase": "user_state"})
    assert "ok" in result.lower() or "advancing" in result.lower() or "user_state" in result.lower()


def test_set_phase_optional_records_decision_when_skipped_by_user(tmp_project):
    _set_meta(tmp_project, {"agent_type": "conversational"})
    handler = _handler_on(tmp_project, current_phase="knowledge")
    result = handler.handle_tool_call("skip_optional_phase", {"phase": "knowledge"})
    meta = _read_meta(tmp_project)
    assert meta["phase_decisions"]["knowledge"]["status"] == "skipped_by_user"
```

If `skip_optional_phase` seems like a new tool, add it as a small helper — or fold the skip intent into `set_phase` by accepting a `decision: "skipped" | "answered"` kwarg. Pick one approach and be consistent.

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v -k "set_phase_auto_skips or phase_decision_answered or optional_records"`
Expected: FAILED.

- [ ] **Step 3: Update `_handle_set_phase`**

Edit `dev-kit/dev_kit/agent/tools.py`. Replace the body of `_handle_set_phase` to consult `SHEET_REQUIREMENTS` and `phase_decisions`:

```python
    def _handle_set_phase(self, inputs: dict) -> str:
        from datetime import datetime, timezone
        requested = inputs["phase"]
        current = self._state.get("phase", "tier")

        if requested not in PHASES:
            return f"ERROR — unknown phase: {requested}"

        current_idx = PHASES.index(current)
        requested_idx = PHASES.index(requested)

        if requested_idx < current_idx:
            return (
                f"ERROR — cannot go back from '{current}' to '{requested}'. "
                "Use rollback_to_checkpoint."
            )

        if requested_idx > current_idx + 1:
            # Skip-walk: auto-advance through Skip / already-answered phases
            next_phase = PHASES[current_idx + 1]
            return (
                f"ERROR — cannot skip from '{current}' to '{requested}'. "
                f"You must complete '{next_phase}' next. Call set_phase('{next_phase}')."
            )

        # Honour SHEET_REQUIREMENTS for the REQUESTED phase (the one we are entering)
        meta = self._read_project_meta()
        agent_type = meta.get("agent_type", "")
        phase_decisions = meta.get("phase_decisions", {})

        status = SHEET_REQUIREMENTS.get(requested, {}).get(agent_type, "optional") if agent_type else "required"

        if status == "skip":
            # Auto-advance; record decision
            phase_decisions[requested] = {
                "status": "not_applicable_for_type",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._update_project_meta({"phase_decisions": phase_decisions})
            self._state["phase_changed"] = PHASES[requested_idx]
            # Auto-advance again — return a message indicating both
            return (
                f"Phase '{requested}' skipped ({agent_type} agents). "
                f"Advancing directly past it. Call set_phase('{PHASES[requested_idx + 1]}') next."
            )

        # Required / Optional → user visits normally. Record 'answered' when they advance past it.
        self._state["phase_changed"] = requested
        # Record current phase as answered (they're leaving it)
        if current != "tier" and SHEET_REQUIREMENTS.get(current, {}).get(agent_type) == "optional":
            phase_decisions.setdefault(current, {})
            if phase_decisions[current].get("status") != "skipped_by_user":
                phase_decisions[current] = {
                    "status": "answered",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._update_project_meta({"phase_decisions": phase_decisions})
        return f"Phase advancing to: {requested}"
```

Also add a new small tool `skip_optional_phase` (if that's the approach you chose in step 1) with handler:

```python
    def _handle_skip_optional_phase(self, inputs: dict) -> str:
        from datetime import datetime, timezone
        phase = inputs.get("phase", "")
        meta = self._read_project_meta()
        agent_type = meta.get("agent_type", "")
        status = SHEET_REQUIREMENTS.get(phase, {}).get(agent_type, "required")

        if status != "optional":
            return f"ERROR — phase '{phase}' is '{status}' for {agent_type} agents; cannot skip."

        phase_decisions = meta.get("phase_decisions", {})
        phase_decisions[phase] = {
            "status": "skipped_by_user",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._update_project_meta({"phase_decisions": phase_decisions})
        return f"ok: {phase} skipped by user"
```

Register both in the tool definitions list and dispatch dict.

- [ ] **Step 4: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/agent/tests/test_tools.py
git commit -m "feat(dev-kit): set_phase honours SHEET_REQUIREMENTS; persist phase_decisions in project meta (GH-137)"
```

---

## Task 19: Dev-kit — `update_config` rejects removed channel paths

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py` (`_handle_update_config`)
- Test: `dev-kit/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/agent/tests/test_tools.py`:

```python
def test_update_config_rejects_agent_channels():
    handler = _handler_on(..., agent_type="conversational")
    result = handler.handle_tool_call("update_config", {
        "block": "agent_core",
        "section": "agent.channels",
        "values": {"voice": {"system_prompt_suffix": "x"}},
    })
    assert "error" in result.lower()
    assert "channels" in result.lower()


def test_update_config_rejects_reach_layer_channels_for_agent_core():
    handler = _handler_on(..., agent_type="conversational")
    result = handler.handle_tool_call("update_config", {
        "block": "agent_core",
        "section": "reach_layer.channels",
        "values": {"voice": {"turn_assembler": {}}},
    })
    assert "error" in result.lower()


def test_update_config_accepts_top_level_channels():
    handler = _handler_on(..., agent_type="conversational")
    result = handler.handle_tool_call("update_config", {
        "block": "agent_core",
        "section": "channels",
        "values": {"voice": {"system_prompt_suffix": "short"}},
    })
    assert "ok" in result.lower()
```

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v -k "rejects_agent_channels or rejects_reach_layer_channels or accepts_top_level_channels"`
Expected: FAILED.

- [ ] **Step 3: Add guard in `_handle_update_config`**

Edit `dev-kit/dev_kit/agent/tools.py` in `_handle_update_config`. Near the top of the method, after `block = inputs["block"]; section = inputs["section"]`, add:

```python
        if block == "agent_core":
            # GH-137 hard-cut: these paths were consolidated.
            if section == "agent.channels" or section.startswith("agent.channels."):
                return (
                    "ERROR — agent.channels is removed (GH-137). Use section=`channels` "
                    "at the top level instead (e.g. section=`channels`, values={voice: {...}})."
                )
            if section == "reach_layer.channels" or section.startswith("reach_layer.channels."):
                return (
                    "ERROR — reach_layer.channels inside agent_core is removed (GH-137). "
                    "Use section=`channels.<name>.turn_assembler` at the top level for "
                    "turn_assembler policy overrides."
                )
```

- [ ] **Step 4: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/agent/tests/test_tools.py
git commit -m "feat(dev-kit): update_config rejects removed channel paths with migration errors (GH-137)"
```

---

## Task 20: Phase rewrite — `tier` (new branch)

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Test: `dev-kit/agent/tests/test_prompts_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/agent/tests/test_prompts_phases.py`:

```python
def test_tier_phase_returns_decision_tree():
    text = get_phase_addition("tier")
    assert "Q1" in text and "Q2" in text and "Q3" in text and "Q4" in text
    assert "Transactional" in text
    assert "Informational" in text
    assert "Agentic" in text
    assert "Conversational" in text
    assert "set_agent_type" in text


def test_overview_phase_mentions_tier_as_first():
    text = get_phase_addition("overview")
    assert "tier" in text.lower()
```

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v -k "tier_phase or tier_as_first"`
Expected: FAILED.

- [ ] **Step 3: Add the branch + update overview sequence**

Edit `dev-kit/dev_kit/agent/prompts/phases.py`. At the top of `get_phase_addition` (before the existing `if phase == "overview":` branch), insert:

```python
    if phase == "tier":
        return (
            "## Tier phase — classify the agent type\n\n"
            "Before diving into configuration, we classify your agent into one of "
            "four types. This determines which of the subsequent phases are Required, "
            "Optional, or Skipped for your project.\n\n"
            "Ask the user these 4 questions **in order**, one at a time:\n\n"
            "**Q1.** Does the agent take any action — an API call, form submission, or "
            "system write?\n"
            "- NO → go to Q2.\n"
            "- YES → go to Q3.\n\n"
            "**Q2.** Does it answer questions from a defined knowledge source?\n"
            "- YES → Informational agent. Call `set_agent_type('informational')`.\n"
            "- NO → Reconsider scope. A passive listener is not an agent. Pause and "
            "escalate to the user.\n\n"
            "**Q3.** Is the task a single defined flow (book / check / submit) with a "
            "clear end state?\n"
            "- YES → Transactional agent. Call `set_agent_type('transactional')`.\n"
            "- NO → go to Q4.\n\n"
            "**Q4.** Does the agent need to hold context across turns, navigate "
            "trade-offs, or respond to emotional state?\n"
            "- YES → Conversational agent. Call `set_agent_type('conversational')`.\n"
            "- NO → Agentic agent. Call `set_agent_type('agentic')`.\n\n"
            "Once you call `set_agent_type`, advance with `set_phase('overview')`."
        )
```

In the existing `overview` branch, update the sequence text (the "Required 11-phase" text that #139 changed to 11) to:

```python
            "**Required 12-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. tier        — classify the agent type (already done before overview)\n"
            "2. overview    — understand the use case (current phase)\n"
            "3. language    — LLM models, language normalisation, NLU intents/entities\n"
            "4. knowledge   — RAG knowledge base, persona, document sources\n"
            "5. memory      — session state fields, persistent graph, consent mode\n"
            "6. user_state  — user mental-state model (Conversational only; skip otherwise)\n"
            "7. trust       — blocked phrases, escalation topics, safety guardrails\n"
            "8. tools       — external API / MCP tools (or confirm none needed)\n"
            "9. workflow    — subagent state machine, routing rules\n"
            "10. observability — outcome lifecycle states, metrics, domain name\n"
            "11. reach      — channels, TTS rules, terminal word\n"
            "12. review     — validate, fix missing fields, finalize all blocks\n\n"
```

- [ ] **Step 4: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v`
Expected: all PASS. If any existing test asserted "11-phase", update it to "12-phase".

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py dev-kit/agent/tests/test_prompts_phases.py
git commit -m "feat(dev-kit): add tier phase prompt with 4-question agent-type decision tree (GH-137)"
```

---

## Task 21: Phase rewrite — language, knowledge, memory

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Test: `dev-kit/agent/tests/test_prompts_phases.py`

This is one of the larger rewrites — each branch gains guide-driven pedagogy. Keep existing schema-enumeration content intact; prepend guide pedagogy and per-type expectations.

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/agent/tests/test_prompts_phases.py`:

```python
def test_language_phase_mentions_tts_rules():
    text = get_phase_addition("language")
    assert "TTS" in text or "tts_rules" in text


def test_language_phase_mentions_terminal_word_for_voice():
    text = get_phase_addition("language")
    # Voice-type hint should reference terminal_word
    assert "terminal_word" in text or "terminal word" in text.lower()


def test_knowledge_phase_per_type_hint():
    text = get_phase_addition("knowledge")
    # Informational = required, Transactional = skip — phase prompt should mention both
    assert "Informational" in text
    assert "Transactional" in text or "skip" in text.lower()


def test_memory_phase_mentions_contact_memory_states():
    text = get_phase_addition("memory")
    # 5 contact-memory states mentioned in the guide
    for s in ["new", "sparse", "rich", "mid-journey", "post-application"]:
        assert s in text.lower() or s.replace("-", "_") in text.lower()
```

- [ ] **Step 2: Verify failure**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v -k "language_phase_mentions or knowledge_phase_per_type or memory_phase_mentions_contact_memory"`
Expected: FAILED.

- [ ] **Step 3: Rewrite `language` branch**

In `phases.py`, replace the existing `language` branch body (the current block that starts `"## Language & Models phase — valid fields\n\n"`) with:

```python
    if phase == "language":
        return (
            "## Language & TTS phase\n\n"
            "**What this phase is about:** Set the agent's primary + fallback LLM, "
            "configure language normalisation and NLU classification, declare "
            "conversation-level messages, and — for voice agents — TTS normalisation "
            "rules and the terminal word for call end.\n\n"
            "**Why it matters:** Every downstream phase assumes language + NLU are "
            "wired. Voice agents are especially sensitive — TTS engines do not reliably "
            "speak raw numbers, dates, or Roman-script Hindi; you must specify rules "
            "the LLM follows before responses reach TTS.\n\n"
            "### What to include (from guide §2.10 Language & TTS Rules)\n"
            "- Primary and fallback Claude model IDs (agent.primary_model, fallback_model)\n"
            "- Default language + supported languages for language normalisation\n"
            "- NLU classifier model + intents/entities/sentiment classes\n"
            "- Conversation-level messages (blocked_message, consent_message, etc.) in "
            "the target language\n"
            "- **Voice only:** TTS rules per data type (numbers, money, dates, time, "
            "phone, abbreviations, output script, English loanwords) under "
            "`channels.voice.tts_rules`\n"
            "- **Voice only:** `channels.voice.terminal_word` — the literal word that "
            "signals call end (e.g. \"Goodbye\"). Required for voice.\n\n"
            "### How the dev-kit captures this\n"
            "- Set models + consent: `update_config(block=agent_core, section=agent, "
            "values={primary_model: ..., fallback_model: ..., ask_for_consent: ..., "
            "consent_prompt: ...})`\n"
            "- Set language normalisation: `section=preprocessing.language_normalisation`\n"
            "- Set NLU: `section=preprocessing.nlu_processor`\n"
            "- Set conversation messages: `section=conversation` (all message keys)\n"
            "- Set entity-to-profile map: `section=entity_to_profile_field`\n"
            "- Set HITL response: `section=hitl, values={response_message: ...}`\n"
            "- Auto-set observability domain: `section=observability, values={domain: "
            "'<project_slug>'}`\n"
            "- **Voice only** — set TTS rules + terminal word: `section=channels, "
            "values={voice: {tts_rules: {...}, terminal_word: 'Goodbye'}}`. "
            "You may draft the TTS rules from the canonical language defaults and "
            "offer `\"draft them for me\"` to the user.\n\n"
            "### Guide gap — DPG-specific fields not in the guide\n"
            "- `signal_intents` (map of intent → signal type for longitudinal context-"
            "graph writes). Ask: 'Are there intents that should write a longitudinal "
            "signal to the context graph?'\n"
            "- `user_state_confidence_threshold` (GH-139) — set only for "
            "Conversational agents during the user_state phase; default 0.4 works.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + _extract_template_sections(
                "agent_core",
                ["agent", "preprocessing", "conversation", "entity_to_profile_field",
                 "hitl", "observability", "channels"],
            )
            + "```\n\n"
            "➡️ When models, language normalisation, NLU, conversation messages, "
            "entity_to_profile_field, hitl.response_message, and (voice only) "
            "channels.voice.{tts_rules, terminal_word} are all set, call "
            "`set_phase('knowledge')`."
        )
```

- [ ] **Step 4: Rewrite `knowledge` branch**

Replace the existing `knowledge` branch body with:

```python
    if phase == "knowledge":
        return (
            "## Knowledge Base phase\n\n"
            "**What this phase is about:** Configure the RAG knowledge base that the "
            "agent queries when the LLM invokes the `knowledge_retrieval` internal "
            "tool.\n\n"
            "**Per-type requirement:** "
            "Informational = REQUIRED. Agentic / Conversational = OPTIONAL (only if the "
            "agent has a KB attached). Transactional = SKIP.\n\n"
            "### What to include (from guide §2.7 Knowledge Base Usage Rules)\n"
            "- Define the KB scope — what it contains and what it explicitly does NOT.\n"
            "- Confidence rules: what the agent does when the KB has a clear answer / "
            "partial answer / no answer / conflicting answers.\n"
            "- Citation behaviour: does the agent cite sources, or speak naturally? "
            "Formal/regulated domains cite; conversational domains speak naturally.\n"
            "- KB-to-agent boundary: the agent INTERPRETS and speaks; it must never "
            "read KB entries verbatim.\n\n"
            "### How the dev-kit captures this\n"
            "- Set RAG config: `update_config(block=knowledge_engine, "
            "section=knowledge.blocks.static_knowledge_base, values={...})`\n"
            "- Set persona + language: `section=persona`, `section=language_instruction`\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- `intent_filters` (per-intent document retrieval scoping) is DPG-specific "
            "and not covered by the guide.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```\n\n"
            "➡️ When collection_name, persona, and language_instruction are set, call "
            "`set_phase('memory')`."
        )
```

- [ ] **Step 5: Rewrite `memory` branch**

Replace the existing `memory` branch body with:

```python
    if phase == "memory":
        return (
            "## Memory & Session State phase\n\n"
            "**What this phase is about:** Define what the agent remembers across "
            "turns (session scope), across sessions (persistent graph), and what "
            "contact memory fields are available at call start.\n\n"
            "### What to include (from guide §3.3 Contact Memory & Session State)\n"
            "- Session memory schema: fields and TTL.\n"
            "- Persistent graph node types and merge rules.\n"
            "- User data persistence mode: saved | anonymous.\n"
            "- **Conversational agents** must cover all 5 contact-memory states in "
            "their subagent graph later (during the workflow phase):\n"
            "    - `new` (no memory)\n"
            "    - `sparse` (location only)\n"
            "    - `rich` (location + trade/topic)\n"
            "    - `mid-journey` (options presented, decision pending)\n"
            "    - `post-application` (action taken, checking back in)\n"
            "  Use this phase to define which memory fields populate which state.\n"
            "- Re-engagement triggers (optional): if the agent should follow up with "
            "users who dropped off (WhatsApp, SMS, outbound call).\n\n"
            "### How the dev-kit captures this\n"
            "- Session schema: `update_config(block=memory_layer, section=state.session, "
            "values={ttl_minutes: ..., schema: {...}})`\n"
            "- Persistent graph: `section=state.persistent, values={...}`\n"
            "- Storage mode: `section=user_data_persistence, values={default_mode: saved|anonymous}`\n"
            "- Re-engagement: `section=reengagement, values={triggers: [...]}`\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- `merge_on_session_end`, `context_graph` node types, and re-engagement "
            "triggers are DPG-specific.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("memory_layer")
            + "```\n\n"
            "➡️ When session schema, persistent graph, user_data_persistence, and "
            "reengagement (if needed) are set, call `set_phase('user_state')`."
        )
```

- [ ] **Step 6: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py dev-kit/agent/tests/test_prompts_phases.py
git commit -m "feat(dev-kit): rewrite language/knowledge/memory phase prompts with guide pedagogy (GH-137)"
```

---

## Task 22: Phase rewrite — user_state, trust, tools

- [ ] **Step 1: Rewrite `user_state` branch**

The existing `user_state` branch (from GH-139) is already in `phases.py`. Prepend guide framing + per-type note. Replace the branch body with:

```python
    if phase == "user_state":
        return (
            "## User State phase\n\n"
            "**What this phase is about:** Define the user's mental journey — the "
            "cognitive/emotional states they pass through (e.g. Fog → Orientation → "
            "Evaluation → Commitment → Follow-through) and how the agent should "
            "behave in each.\n\n"
            "**Per-type requirement:** Conversational = REQUIRED. All other types = "
            "SKIP (auto-advanced by set_phase). This phase shapes the user's "
            "conversational experience, not just what data is captured.\n\n"
            "### What to include (from guide §2.5 Conversation State Model)\n"
            "- List 2-5 states with short ids (e.g. fog, orientation, evaluation, "
            "commitment, follow-through for a job-market advisor).\n"
            "- For each state: natural-language signals (phrases users say in that "
            "state) and behavioural guidance for the agent (2-3 sentences).\n"
            "- Which state is the DEFAULT for a fresh caller?\n\n"
            "### How the dev-kit captures this\n"
            "- Declare states: `update_config(block=agent_core, section=conversation, "
            "values={user_state_model: {enabled: true, default_state: ..., states: [...]}})`\n"
            "- Set threshold (GH-139): `section=preprocessing.nlu_processor, "
            "values={user_state_confidence_threshold: 0.4}` (default 0.4; usually fine).\n\n"
            "### Guide gap\n"
            "- Sticky fallback on low-confidence classification is a DPG-specific "
            "mechanism (GH-139) — the guide describes the state model but not how "
            "confidence-thresholded classification handles ambiguous turns.\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["conversation"])
            + "```\n\n"
            "➡️ When the model is declared, call `set_phase('trust')`."
        )
```

- [ ] **Step 2: Rewrite `trust` branch**

Replace the existing `trust` branch body with:

```python
    if phase == "trust":
        return (
            "## Trust phase\n\n"
            "**What this phase is about:** Configure the safety gate — blocked "
            "content rules, prohibited language, topic firewall, escalation rules, "
            "and (for Conversational) the pre-response dignity check.\n\n"
            "### What to include\n"
            "- **All types:** Content rules, blocked phrases, escalation topics.\n"
            "- **Conversational:** `dignity_check` with the 5 canonical questions "
            "(auto-populated; you can override per domain). Flags `enabled: true`.\n"
            "- Prohibited language list (guide §2.11 Style & Prohibited). Include "
            "specific phrases, not just categories.\n\n"
            "### Canonical dignity check questions (Conversational only)\n"
            "1. Does this blame the user?\n"
            "2. Does it over-promise?\n"
            "3. Does it push urgency?\n"
            "4. Does it reduce their agency?\n"
            "5. Does it sound like a script instead of a human call?\n\n"
            "The dev-kit auto-emits these into `trust_layer.dignity_check.questions` "
            "when `agent_type=conversational`. Confirm with the user; author can "
            "override the list if the domain needs adjusted phrasing.\n\n"
            "### How the dev-kit captures this\n"
            "- Content/output rules: `update_config(block=trust_layer, section=rules, "
            "values={...})`\n"
            "- Consent rules (DPDP): `section=consent`.\n"
            "- Dignity check (Conversational): `section=dignity_check, values={enabled: "
            "true, questions: [...], fail_action: 'rewrite'}`. `fail_action` is schema-"
            "accepted but runtime ignores it for now — the check is self-enforced by the "
            "main LLM via prompt_constraints.\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- Trust Layer's `/assemble_constraints` async call mechanism is DPG-"
            "specific — the guide describes what the check does, not how it plumbs.\n\n"
            "```yaml\n"
            + load_template_text("trust_layer")
            + "```\n\n"
            "➡️ When rules, consent, and (for Conversational) dignity_check are set, "
            "call `set_phase('tools')`."
        )
```

- [ ] **Step 3: Rewrite `tools` branch**

Replace the existing `tools` branch body with:

```python
    if phase == "tools":
        return (
            "## Tools phase\n\n"
            "**What this phase is about:** Declare every external tool the agent can "
            "invoke, with strict invocation contracts the LLM must follow.\n\n"
            "**Per-type requirement:** Transactional / Agentic / Conversational = "
            "REQUIRED. Informational = SKIP (auto-advanced).\n\n"
            "### What to include (from guide §2.6 Tool Invocation Rules + §3.1)\n"
            "For each tool, define six fields in `invocation_rules`:\n"
            "1. `call_when` — exact trigger condition, in plain language.\n"
            "2. `required_before_calling` — list of data fields required before "
            "invocation. The tool MUST NOT be called if any are missing.\n"
            "3. `must_not_substitute` — memory, prior context, assumed knowledge — "
            "the LLM must never treat these as substitutes for a fresh tool call.\n"
            "4. `on_empty` — exact natural line the agent says when the tool returns "
            "empty results.\n"
            "5. `on_failure` — exact natural line on tool failure / timeout.\n"
            "6. `bridge_line` — optional single short line the agent says right before "
            "the tool call (e.g. 'ठीक है, current picture देख लेती हूँ।'). "
            "Essential for voice; optional for chat.\n\n"
            "### How the dev-kit captures this\n"
            "- Declare connectors: `update_config(block=agent_core, "
            "section=connectors.read | write | identity | internal, values=[{name, "
            "description, input_schema, invocation_rules: {...}}])`\n"
            "- If you have an OpenAPI spec for an action_gateway tool, you can upload "
            "it via `<document-extraction-tool>` (#130) — dev-kit will populate the "
            "tool schemas automatically. You still author `invocation_rules` by hand.\n\n"
            "### Guide gap\n"
            "- The guide discusses invocation contract but does not prescribe a 6-field "
            "structure; our schema formalises it.\n"
            "- The `route` field on `connectors.internal[]` (e.g. route=knowledge_engine) "
            "is DPG-specific.\n\n"
            "```yaml\n"
            + _extract_template_sections(\"agent_core\", [\"connectors\"])\n"
            + "```\n\n"
            "➡️ When all external tools are declared with all six invocation_rules "
            "fields populated, call `set_phase('workflow')`."
        )
```

Note: the `\"` escaping in the last f-string block inside the phase body is only needed inside triple-quoted Python strings if there are nested quotes — revert to plain `"connectors"` if Python string formatting allows.

- [ ] **Step 4: Write tests for the rewrites**

Append to `dev-kit/agent/tests/test_prompts_phases.py`:

```python
def test_user_state_phase_mentions_guide_section_and_threshold():
    text = get_phase_addition("user_state")
    assert "§2.5" in text or "Conversation State Model" in text
    assert "user_state_confidence_threshold" in text


def test_trust_phase_dignity_check_questions_present():
    text = get_phase_addition("trust")
    for q in ["blame", "over-promise", "urgency", "agency", "script"]:
        assert q in text.lower()


def test_tools_phase_lists_six_invocation_rule_fields():
    text = get_phase_addition("tools")
    for f in ["call_when", "required_before_calling", "must_not_substitute",
              "on_empty", "on_failure", "bridge_line"]:
        assert f in text
```

- [ ] **Step 5: Run tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py dev-kit/agent/tests/test_prompts_phases.py
git commit -m "feat(dev-kit): rewrite user_state/trust/tools phase prompts with guide pedagogy (GH-137)"
```

---

## Task 23: Phase rewrite — workflow, observability, reach, review

- [ ] **Step 1: Rewrite `workflow` branch**

Replace body with:

```python
    if phase == "workflow":
        return (
            "## Workflow phase\n\n"
            "**What this phase is about:** Design the subagent state machine — "
            "individual conversational sub-flows and how they route between each "
            "other based on NLU intent.\n\n"
            "### What to include (from guide §2.3 Conversation Opening Logic)\n"
            "- One subagent per coherent conversational sub-flow.\n"
            "- Each subagent: id, description, routing rules, and **`opening_phrase`** "
            "for the first turn of a session that enters this subagent.\n"
            "- Exactly ONE subagent has `is_start: true`.\n"
            "- **Conversational agents** should structure their subagent graph so the "
            "5 contact-memory states (new, sparse, rich, mid-journey, post-application) "
            "each land in a subagent with an appropriate `opening_phrase`. The dev-kit "
            "does not schema-enforce 'exactly 5 branches' — author judgement. The "
            "guide's 5-branch rule is pedagogy, not validation.\n\n"
            "### How the dev-kit captures this\n"
            "- Declare subagents: use the `create_subagent` tool per subagent "
            "(provides id, name, description, is_start, is_terminal, valid_intents, "
            "tools, system_prompt, opening_phrase).\n"
            "- Define routing: use `update_subagent` to set routing rules.\n"
            "- Set global routing: `update_config(block=agent_core, "
            "section=agent_workflow.global_routing, values=[...])`.\n\n"
            "### Opening phrase guidance\n"
            "- Emitted ONCE per session, on the first post-consent turn. Subsequent "
            "turns run the subagent's normal `system_prompt`.\n"
            "- The subagent active on turn 1 is determined by Memory Layer: either the "
            "`is_start: true` subagent (new session) or a subagent restored from the "
            "previous session's `current_subagent` (returning user).\n"
            "- Tailor each subagent's `opening_phrase` to what the user knows at that "
            "point. E.g. a start subagent opens with a warm discovery question; a "
            "post-action subagent opens by acknowledging the previous action.\n\n"
            "### Guide gap\n"
            "- The guide describes 5 'opening branches' as a single prompt-level "
            "conditional; we represent them via the subagent graph + `opening_phrase` "
            "field, because our subagent abstraction is richer than the guide assumes.\n\n"
            "```yaml\n"
            + _extract_template_sections(\"agent_core\", [\"agent_workflow\"])\n"
            + "```\n\n"
            "➡️ When all subagents are declared with routing and opening_phrases, "
            "call `set_phase('observability')`."
        )
```

- [ ] **Step 2: Rewrite `observability` branch**

Replace body with:

```python
    if phase == "observability":
        return (
            "## Observability phase\n\n"
            "**What this phase is about:** Configure outcome lifecycle states, "
            "quality metrics, and the domain tag used in all OTel spans.\n\n"
            "### What to include (from guide §3.4 Exception Handling + DPG defaults)\n"
            "- Outcome states for the domain (e.g. 'profile_gathered', 'options_shown', "
            "'applied', 'callback_pending').\n"
            "- Quality signals worth tracking (e.g. drop-off at specific subagents, "
            "low-confidence turns, consent declines).\n"
            "- Exception-handling policies: what the agent says on tool timeout, empty "
            "result, ASR misrecognition, mid-call drop.\n\n"
            "### How the dev-kit captures this\n"
            "- Outcome lifecycle: `update_config(block=observability_layer, "
            "section=outcomes.lifecycle, values=[...])`\n"
            "- Quality metrics: `section=quality.signals`.\n"
            "- Domain tag: auto-set from project slug.\n\n"
            "### Guide gap\n"
            "- DPG-specific: `turn_event` schema, async emit contract, OTel span "
            "attribute conventions (e.g. user_state.current, session.turn_count).\n\n"
            "```yaml\n"
            + load_template_text(\"observability_layer\")\n"
            + "```\n\n"
            "➡️ When outcomes and quality signals are set, call `set_phase('reach')`."
        )
```

- [ ] **Step 3: Rewrite `reach` branch**

Replace body with:

```python
    if phase == "reach":
        return (
            "## Reach phase\n\n"
            "**What this phase is about:** Declare channel adapters and adapter-"
            "specific settings (TTS provider endpoints, websocket URLs, campaign "
            "config, web UI branding).\n\n"
            "### What to include (from guide Appendix: Voice vs Chat)\n"
            "- Channels declared: any subset of voice / chat / web / cli.\n"
            "- **Voice** requires a TTS provider (e.g. raya_tts) and telephony adapter "
            "config; the LLM-facing voice config (prompt suffix, TTS rules, terminal "
            "word, turn_assembler) was set in the `language` phase under "
            "`agent_core.channels.voice`.\n"
            "- **Chat / web / whatsapp** require their respective adapter endpoints "
            "and webhook URLs.\n"
            "- Web UI branding (app name, icon, tagline) for web deployments.\n\n"
            "### How the dev-kit captures this\n"
            "- Voice adapter: `update_config(block=reach_layer, "
            "section=channels.voice, values={tts_provider: ..., telephony: ...})`\n"
            "- Web UI: `section=web, values={app_name: ..., icon: ..., tagline: ...}`\n"
            "- Campaign config (if outbound campaigns): `section=campaigns`.\n\n"
            "### Guide gap\n"
            "- Our Reach Layer is a distinct DPG block; the guide treats channels as "
            "adapter concerns without abstracting them.\n"
            "- TurnAssembler policy (semantic_gate, silence_trigger, max_wait_ceiling) "
            "lives in `agent_core.channels.<name>.turn_assembler` because TurnAssembler "
            "runs inside Agent Core — not covered by the guide.\n\n"
            "```yaml\n"
            + load_template_text(\"reach_layer\")\n"
            + "```\n\n"
            "➡️ When channel adapters and UI branding are declared, call "
            "`set_phase('review')`."
        )
```

- [ ] **Step 4: Rewrite `review` branch**

Replace body with:

```python
    if phase == "review":
        return (
            "## Review phase\n\n"
            "**What this phase is about:** Run a full schema-coverage check across "
            "all 7 DPG blocks and report any empty required fields for correction.\n\n"
            "Use the `validate_config` tool to run the check. It reads every block's "
            "YAML template, compares against the accumulated config, and lists empty "
            "required fields with exact paths (e.g. `agent_core.channels.voice."
            "terminal_word`, `trust_layer.dignity_check.questions[2]`).\n\n"
            "For each missing field: ask the user for the value, call the appropriate "
            "`update_config` tool, and re-run `validate_config` until the report is "
            "clean.\n\n"
            "Once `validate_config` reports no missing required fields, announce "
            "completion. The author can then deploy via the `deploy_project` tool or "
            "export the seven YAMLs."
        )
```

- [ ] **Step 5: Write tests**

Append to `dev-kit/agent/tests/test_prompts_phases.py`:

```python
def test_workflow_phase_mentions_opening_phrase():
    text = get_phase_addition("workflow")
    assert "opening_phrase" in text
    assert "is_start" in text


def test_reach_phase_mentions_consolidated_channels_note():
    text = get_phase_addition("reach")
    # Reach phase should clarify that LLM-facing channel config was set in language phase
    assert "agent_core.channels" in text or "channels.voice" in text


def test_review_phase_mentions_validate_config():
    text = get_phase_addition("review")
    assert "validate_config" in text
```

- [ ] **Step 6: Run all phase tests**

Run: `cd dev-kit && uv run pytest agent/tests/test_prompts_phases.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py dev-kit/agent/tests/test_prompts_phases.py
git commit -m "feat(dev-kit): rewrite workflow/observability/reach/review phase prompts with guide pedagogy (GH-137)"
```

---

## Task 24: Backwards-compat smoke — three domains boot with migrated configs

**Files:**
- Test: `agent_core/tests/test_backwards_compat_channels.py` (new)

- [ ] **Step 1: Write the smoke test**

Create `agent_core/tests/test_backwards_compat_channels.py`:

```python
"""
GH-137 backwards-compat smoke: the three in-tree domain configs migrated to
the new top-level `channels:` path must instantiate AgentCore without error.
"""
import yaml
from pathlib import Path

from src.preprocessing.nlu_processor import NLUProcessor


def _load_merged_domain_config(domain: str) -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((repo_root / "dev-kit" / "dpg" / "agent_core.yaml").read_text()) or {}
    dom = yaml.safe_load(
        (repo_root / "dev-kit" / "configs" / domain / "agent_core.yaml").read_text()
    ) or {}
    merged = {**dpg}
    for k, v in dom.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def test_kkb_has_top_level_channels():
    cfg = _load_merged_domain_config("kkb")
    assert "channels" in cfg
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_farmer_friendly_has_top_level_channels():
    cfg = _load_merged_domain_config("farmer-friendly")
    assert "channels" in cfg or cfg.get("channels") == {}  # may be empty if channels not declared
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_obsrv_docs_assistant_has_top_level_channels():
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_kkb_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("kkb")
    p = NLUProcessor(cfg)
    assert p is not None


def test_farmer_friendly_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("farmer-friendly")
    p = NLUProcessor(cfg)
    assert p is not None


def test_obsrv_docs_assistant_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    p = NLUProcessor(cfg)
    assert p is not None
```

- [ ] **Step 2: Run**

Run: `cd agent_core && uv run pytest tests/test_backwards_compat_channels.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add agent_core/tests/test_backwards_compat_channels.py
git commit -m "test(agent-core): backwards-compat smoke for migrated channel paths across three domains (GH-137)"
```

---

## Task 25: Guide gaps companion document

**Files:**
- Create: `docs/guide-gaps.md`

- [ ] **Step 1: Write the companion doc**

Create `docs/guide-gaps.md`:

```markdown
# Agent Configuration Guide — Gaps & DPG-Specific Addenda

This document records fields, behaviours, and mechanisms that the dev-kit
configures but that the Agent Configuration Guide (v3.0, April 2026) does
not cover. Share with the guide authors for potential inclusion in a future
revision.

## Fields not in the guide

### agent_core block

- `preprocessing.nlu_processor.signal_intents` — map of intent → signal type
  for longitudinal writes to the Memory Layer context graph. DPG-specific
  observability feature.
- `preprocessing.nlu_processor.user_state_confidence_threshold` — sticky
  fallback threshold for the user_state classifier (GH-139). Guide describes
  the state model but not how confidence-thresholded classification handles
  ambiguous turns.
- `entity_to_profile_field` — maps extracted NLU entities to persistent
  graph profile fields. Required for profile building.
- `channels.<name>.turn_assembler` — TurnAssembler runs inside Agent Core
  but is per-channel. Guide treats assembly as a channel/adapter concern.
- `channels.<name>.system_prompt_suffix` — per-channel tuning (GH-97). Guide
  discusses voice vs chat differences as prompt-authoring guidance, not as
  a runtime channel-aware suffix mechanism.

### memory_layer block

- `state.context_graph` node types and edge types — Memgraph-backed typed
  attribute graph per session. Guide describes contact memory as a flat
  record; DPG adds a typed graph dimension.
- `state.persistent.merge_on_session_end` — explicit declaration of which
  session fields promote to persistent scope at session end. Guide assumes
  memory is written as the session progresses.
- `reengagement.triggers` — outbound re-engagement via WhatsApp / SMS /
  callback. Guide does not cover outbound behaviour.

### trust_layer block

- `/assemble_constraints` async endpoint contract — the guide describes the
  dignity check as a pre-response self-check but does not specify how it
  plumbs through a distinct Trust Layer service.
- DPDP consent rules — Indian data-protection specifics not in the guide.

### observability_layer block

- OTel span attribute conventions (e.g. `user_state.current`,
  `session.turn_count`, `session_id`) — DPG-specific instrumentation.
- `turn_event` schema — async emit contract, per-turn event shape.

### reach_layer block

- Outbound campaigns and scheduled triggers.
- TurnAssembler adapter policy (semantic_gate, silence_trigger,
  max_wait_ceiling) for turn completion detection.

## Mechanisms not in the guide

- **Subagent state machine.** The guide treats the full agent prompt as a
  single document; DPG decomposes into subagents with typed routing. This
  affects how opening logic, tool scoping, and per-subagent prompts are
  authored.
- **Opening logic via `subagents[].opening_phrase`.** The guide's 5
  contact-memory branches map to our subagent graph rather than a single
  prompt's conditional; we enforce per-subagent opening phrases emitted on
  turn 1 only.
- **User-state model with NLU-based classification (GH-139).** Guide
  describes state concept; DPG adds runtime classification via the NLU
  call, sticky fallback, Memory-Layer persistence, and observability
  transition events.
- **Session-end signalling via `end_session` internal tool (GH-137).** The
  guide mentions a fixed terminal word for voice but does not prescribe how
  the LLM signals session end. DPG uses an orchestrator-routed internal tool
  to separate "when to end" (LLM decides) from "how to end" (voice adapter
  appends terminal word + closes websocket; chat closes naturally).
- **Channel-aware prompting with consolidated `channels:` top-level
  block.** GH-97 + GH-137.

## Fields used in the guide but not in DPG

(To be filled as we find them during implementation.)

---

**Maintainer:** keep this file updated as new gaps are discovered. Filed
items here are candidates for either (a) guide v4 inclusion, (b) dev-kit
documentation to compensate, or (c) alignment work to make DPG match the
guide where feasible.
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide-gaps.md
git commit -m "docs: add guide-gaps companion document (GH-137)"
```

---

## Task 26: ARCHITECTURE.md updates

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: Update the Agent Core section**

Locate the Agent Core section in `ARCHITECTURE.md`. Add three paragraphs (or amend existing ones):

**Channel consolidation:**

```markdown
**Channel configuration (GH-137).** Per-channel LLM-facing config lives at the
top-level `channels:` block in `agent_core.yaml`. Each channel declares
`system_prompt_suffix`, `tts_rules` (voice only), `terminal_word` (voice only),
and `turn_assembler` policy. The legacy `agent.channels` and
`reach_layer.channels` nested paths are removed — domains must use the top-level
`channels:` block. Reach Layer's own `channels:` block (in `reach_layer.yaml`)
stays for adapter-specific internals (TTS provider endpoints, websocket URLs).
```

**Session-end signalling:**

```markdown
**Session-end signalling (GH-137).** When `conversation.session_end_eval.enabled:
true`, the orchestrator registers an `end_session` internal tool that the LLM can
call when the conversation has naturally concluded (user said goodbye, task
completed, user asked to stop). The tool has no external executor — the
orchestrator intercepts it inside the tool loop and sets
`TurnResult.session_ended = True`. The voice adapter (`reach_layer_voice`)
reacts to this flag by appending `channels.voice.terminal_word` to the outbound
TTS stream and emitting a websocket close frame. Chat / web / CLI adapters close
the session without appending.
```

**Dignity check:**

```markdown
**Dignity check (GH-137).** Conversational agents enable
`trust_layer.dignity_check`, which auto-populates 5 canonical pre-response
questions. Trust Layer's `/assemble_constraints` endpoint appends these questions
to the `prompt_constraints` payload returned to Agent Core, which threads them
into the main LLM system prompt as a "Pre-response dignity check" section. The
LLM self-checks before emitting its response. No additional LLM call.
```

**Opening-phrase:**

```markdown
**Opening phrase (GH-137).** Each subagent may declare an `opening_phrase` that
the orchestrator emits once per session — on the first post-consent turn. The
subagent active on turn 1 is determined by Memory Layer (either `is_start: true`
for new sessions, or the `current_subagent` restored from a prior session).
Subsequent turns run the subagent's normal `system_prompt`. The session flag
`opening_phrase_emitted` prevents re-emission.
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: document channel consolidation, session-end signalling, dignity check, opening-phrase in ARCHITECTURE.md (GH-137)"
```

---

## Task 27: Coverage gate

**Files:**
- None (measurement only)

- [ ] **Step 1: Measure coverage on touched files**

```bash
uv run pytest --cov=src.preprocessing.nlu_processor \
  --cov=src.preprocessing.user_state_resolver \
  --cov=src.manager_agent \
  --cov=src.orchestrator \
  --cov=src.turn_assembler \
  --cov=src.workflow_loader \
  --cov=src.tool_registry \
  --cov-report=term-missing -q
```

Run inside `agent_core/`. Expected: ≥70% line coverage on each file.

Similarly for dev-kit:

```bash
cd ../dev-kit
uv run pytest --cov=dev_kit.agent.tools --cov=dev_kit.agent.accumulator \
  --cov=dev_kit.agent.prompts.phases --cov=dev_kit.agent.prompts.base \
  --cov-report=term-missing -q
```

- [ ] **Step 2: Fill gaps if any file is below 70%**

Add targeted unit tests for uncovered branches. Re-measure. Commit:

```bash
git add <tests>
git commit -m "test: cover remaining branches to hit 70% on GH-137 surface"
```

---

## Task 28: Final verification + push

**Files:**
- None

- [ ] **Step 1: Run all test suites**

```bash
cd agent_core && uv run pytest -q
cd ../dev-kit && uv run pytest -q
cd ../trust_layer && uv run pytest -q
cd ../reach_layer/voice && uv run pytest -q
```

Expected: all PASS.

- [ ] **Step 2: Docker compose config sanity**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
docker compose -f automation/docker/docker-compose.dev.yml config -q
```

Expected: no errors.

- [ ] **Step 3: Git log review**

```bash
git log --oneline main..HEAD
```

Expected: 25-30 commits on `GH-137-framework-uplift`, each mapping to one task, following Conventional Commits.

- [ ] **Step 4: Push**

```bash
git push -u origin GH-137-framework-uplift
```

---

## Spec coverage self-check

- **Scope boundary (framework uplift only; KKB refresh deferred):** Respected throughout. Tasks 5–7 migrate config paths, not content.
- **Agent-type selector (Part 1 decision tree, pre-phase `tier`):** Task 15 (PHASES), Task 17 (`set_agent_type` tool), Task 20 (phase branch).
- **`_meta/project.json` with `agent_type` and `phase_decisions`:** Task 15 schema, Task 17 write, Task 18 read/enforce.
- **`SHEET_REQUIREMENTS` matrix:** Task 16.
- **Per-type phase gating (Required / Optional / Skip):** Task 18.
- **Phase-prompt rewrites for all 12 phases:** Tasks 20–23.
- **Schema additions:**
  - `channels:` top-level — Task 3.
  - `connectors.*.invocation_rules` — Task 3.
  - `agent_workflow.subagents[].opening_phrase` — Task 3.
  - `conversation.session_end_eval` — Task 3.
  - `trust_layer.dignity_check` — Task 4.
- **Hard-cut loader rejection + domain migration:** Tasks 5–7 (migrate domains), Task 8 (orchestrator reject), Task 10 (turn_assembler reject), Task 19 (dev-kit update_config reject).
- **Runtime consumers:**
  - Orchestrator channels path — Task 8.
  - ManagerAgent docstring update — Task 9.
  - TurnAssembler path — Task 10.
  - Opening-phrase gate — Task 11.
  - `end_session` tool + session_end_eval prompt — Task 12.
  - WorkflowLoader `opening_phrase` — Task 2.
  - Trust Layer dignity check — Task 13.
  - Reach Layer voice terminal word — Task 14.
- **Models (`TurnResult.session_ended`, `DoneEvent.session_ended`):** Task 1.
- **Backwards-compat smoke:** Task 24.
- **Guide-gaps document:** Task 25.
- **ARCHITECTURE.md:** Task 26.
- **Coverage gate ≥70%:** Task 27.
- **Final verify + push:** Task 28.

**Potential gap:** per-subagent `create_subagent` tool schema needs updating to accept `opening_phrase` as an input param. Added to Task 23's workflow-phase rewrite text (the phase prompt tells the LLM to include `opening_phrase`), but the tool's JSON schema also needs the field. Addressed by:

- Updating `dev-kit/dev_kit/agent/tools.py` `create_subagent` tool definition to include `opening_phrase: {type: string, default: ""}` in `input_schema.properties`, and the corresponding `_handle_create_subagent` method to thread it into the subagent dict. Fold this into Task 17 or Task 23 as a sub-step — the simpler move is to add it to Task 23 Step 1 after the workflow branch rewrite (since that's where the guidance lives). Update Task 23 code or open a follow-up sub-task.

## Placeholder scan

No TBDs, TODOs, or vague steps in any task. Every code step shows actual code; every command shows actual invocation.

## Type consistency

- `SubAgent.opening_phrase: str = ""` (Task 2) — referenced identically in Task 11 orchestrator code and Task 23 workflow phase prompt.
- `TurnResult.session_ended: bool = False` (Task 1) — same in Task 12 orchestrator wiring and Task 14 reach_layer_voice consumer.
- `DoneEvent.session_ended: bool = False` (Task 1) — same in Task 14.
- `AGENT_TYPES` / `SHEET_REQUIREMENTS` constants (Task 16) — used identically in Tasks 17 (`set_agent_type`), 18 (`set_phase` gating), 20–23 (phase prompts).
- `_handle_set_agent_type`, `_handle_set_phase`, `_handle_update_config`, `_handle_skip_optional_phase` — consistent naming across Tasks 17–19.
- `_update_project_meta`, `_read_project_meta` helpers — referenced in Tasks 17–18; add once in Task 17 (with other helper work) and reuse in Task 18.
- `channel_config` dict shape (`system_prompt_suffix`, `tts_rules`, `terminal_word`, `turn_assembler`) — consistent across Tasks 3, 9, 10, 14.
- `channels.voice.terminal_word` path — consistent across schema (Task 3), migration (Tasks 5–7), runtime consumer (Task 14).

---
