# Dev-Kit Framework Uplift — Agent-Type Selector, Guide-Driven Phase Prompts, New Questionnaire Fields

**Issue:** #137 (framework uplift portion — KKB config refresh is follow-up sub-issue #137-child)
**Status:** Design approved — ready for writing-plans
**Branch:** `GH-137-framework-uplift`
**Depends on:** #141 (merged — user-state model)

---

## Problem

The KKB team has shared updated source documents that supersede prior inputs:
- `docs/KKB Current Prompt.pdf` — current KKB production prompt.
- `docs/Agent_Configuration_Guide_Main.pdf` — voice & chat agent configuration framework (v3.0, April 2026).
- `docs/Agent_Config_Sheets_ABCD.pdf` — one-page reference sheets per agent type (A Transactional / B Informational / C Agentic / D Conversational).

The guide introduces three structural ideas the existing dev-kit does not support:

1. A **4-type classification** (Transactional / Informational / Agentic / Conversational) via a 3-question decision tree that determines which configuration sections are Required / Optional / Skip.
2. **Building-block-level pedagogy** per agent type — the Configuration Agent should *teach* the author using the guide's framing, not just list schema keys.
3. **Structured configuration fields** the guide calls out but our YAML schemas don't yet express: tool invocation rigour, session-end signalling, dignity check, canonical TTS rules per language, fixed terminal word for voice, and opening phrases tied to subagent flow.

This spec rewrites the dev-kit Configuration Agent to be a guided walkthrough of the guide, adds an explicit agent-type selector driving per-phase behaviour, introduces the new questionnaire fields, and consolidates scattered channel configuration into a single top-level block. KKB's actual domain refresh against the new framework is a separate follow-up issue (#137-child).

## Scope

**In scope (this spec):**
- Dev-kit phase prompts rewritten using guide's Part 2 pedagogy.
- New `tier` pre-phase (slot 0) running the 3-question agent-type selector.
- `_meta/project.json` extended with `agent_type` and `phase_decisions`.
- Per-type phase gating via `SHEET_REQUIREMENTS` matrix: Required / Optional / Skip.
- New schema fields: `agent_core.channels.*` (top-level, consolidated), `agent_core.connectors.*.invocation_rules`, `agent_core.conversation.session_end_eval`, `agent_core.agent_workflow.subagents[].opening_phrase`, `trust_layer.dignity_check`.
- Runtime consumers updated: `ManagerAgent`, `orchestrator`, `turn_assembler`, `workflow_loader`, Trust Layer, `reach_layer_voice`.
- Three existing domains (`kkb`, `farmer-friendly`, `obsrv-docs-assistant`) migrated to the new YAML paths in this PR.
- Guide-gaps companion document `docs/guide-gaps.md`.

**Out of scope:**
- KKB domain-specific content refresh (new subagent graph, 5-state user-state model, prohibited-phrase lists, invocation rules for get_jobs / get_profile / apply, etc.) — #137-child.
- Document-extraction flow for OpenAPI → tool schemas — #130.
- Post-response dignity classifier — gated on production need.
- `session_end_eval.fail_action` runtime wiring — schema accepts, runtime ignores; deferred.

## Design

### Architecture & data flow

No new DPG modules; no new cross-module calls. Still 7 blocks, Agent Core still the only orchestrator. The changes land in three places:

- **Dev-kit** (`dev-kit/dev_kit/agent/` + `dev-kit/dev_kit/schemas/`) — new phase, rewritten phase prompts, new tools, schema extensions, path migrations.
- **Agent Core runtime** (`agent_core/src/`) — channel-config path migration, opening-phrase gate, `end_session` internal tool, `session_end_eval` system-prompt addendum, subagent `opening_phrase` field.
- **Trust Layer + Reach Layer voice** — dignity-check constraint assembly; flag-driven terminal-word append on session end.

Per-turn flow (changes **bold**):

```
Reach Layer (input)
  → Orchestrator: read session state ← Memory Layer
  → Consent gate (existing, unchanged)
  → Opening-phrase gate                    ← NEW — fires once per session
      if not session.opening_phrase_emitted and current_subagent.opening_phrase:
          emit opening_phrase, mark flag, return TurnResult
  → Trust /check/input
  → Language Normalisation
  → NLU
  → Trust /assemble_constraints            ← EXTENDED — appends dignity_check questions
  → Manager Agent: build_system_prompt(..., session_end_eval_prompt=...)   ← NEW kwarg
  → LLM call → tool loop
      if tool_use "end_session":           ← NEW internal tool
          set TurnResult.session_ended=True, suppress external execution
  → Trust /check/output
  → Deliver response to Reach Layer
      if session_ended and channel=voice:  ← NEW — in reach_layer_voice
          append channels.voice.terminal_word to TTS stream
          emit websocket close frame
  → [async] write session state
  → [async] emit obs events
```

### Agent-type selector (pre-phase `tier`)

`PHASES` becomes 12 entries:

```
tier → overview → language → knowledge → memory → user_state → trust → tools → workflow → observability → reach → review
```

`tier` phase runs Guide Part 1's 3-question decision tree as a short conversational exchange. On completion, a new tool `set_agent_type(type)` writes `_meta/project.json.agent_type ∈ {transactional, informational, agentic, conversational}` and advances to `overview`.

`_meta/project.json` schema:

```json
{
  "slug": "kkb",
  "name": "काम की बात",
  "agent_type": "conversational",
  "phase_decisions": {
    "<phase>": {"status": "answered | skipped_by_user | not_applicable_for_type | deferred",
                 "timestamp": "..."}
  }
}
```

### Per-type phase gating

`SHEET_REQUIREMENTS` matrix in `dev-kit/dev_kit/agent/prompts/base.py` declares Required / Optional / Skip per (phase, type):

| phase | transactional | informational | agentic | conversational |
|---|---|---|---|---|
| overview | required | required | required | required |
| language | required | required | required | required |
| knowledge | skip | required | optional | optional |
| memory | required | required | required | required |
| user_state | skip | skip | skip | required |
| trust | required | required | required | required |
| tools | required | skip | required | required |
| workflow | required | required | required | required |
| observability | required | required | required | required |
| reach | required | required | required | required |
| review | required | required | required | required |

`set_phase` tool consults this matrix + `_meta.phase_decisions`:
- **Required** → strict. `set_phase` rejects advance past a Required phase that has not been visited.
- **Optional** → user decides. Phase prompt asks "Apply this phase?" and records `answered` or `skipped_by_user`. Persisted so reloads don't re-ask.
- **Skip** → phase prompt announces "Not applicable for your agent type" and auto-advances; records `not_applicable_for_type`.

### Phase-prompt rewrite — guide-driven pedagogy

Every phase's `get_phase_addition` branch is rewritten to mirror the guide's Part 2 § format:

```
## <Phase name>

**What this phase is about:** <from guide §2.X intro>
**Why it matters:** <from guide>
**Per-type requirement:** <required / optional / skip for <type>>

### What to include
- <bullets from guide §2.X "What to include">

### Template
<guide template pasted>

### How the dev-kit captures this
- update_config with block=..., section=..., keys=...
- <per-phase specifics>

### Type-specific expectations (<type>)
<injected from Sheet A/B/C/D>

### Validation
- <required schema fields for this phase>

When complete, call set_phase('<next>').
```

Per-phase guide mapping:

| Phase | Guide sections |
|---|---|
| tier | Part 1 decision tree |
| overview | — (dev-kit use-case gathering; adds type-confirmation recap) |
| language | §2.10 Language & TTS, §2.11 Style & Prohibited |
| knowledge | §2.7 Knowledge Base Usage Rules |
| memory | §3.3 Contact Memory & Session State |
| user_state | §2.5 Conversation State Model |
| trust | §2.11 prohibited, §2.12 Emotional & Dignity |
| tools | §2.6 Tool Invocation, §3.1 Tool Configuration |
| workflow | §2.3 Conversation Opening Logic |
| observability | §3.4 Exception Handling |
| reach | Appendix: Voice vs Chat |
| review | — (runs schema-coverage validator) |

**Schema coverage guarantee:** per-phase completion checklists are advisory, not gates. Review phase (slot 11) runs the sole hard schema-coverage validator reading all seven block templates and listing empty required fields. Field provenance in the phase prompts is soft guidance: LLM-drafted + confirmed, user-provided, user-edited draft, or document-extracted (OpenAPI via #130).

**Guide-gap annotations:** phase prompts mark fields with no guide coverage (e.g. `memory_layer.state.context_graph.node_types`, `preprocessing.nlu_processor.signal_intents`, `reengagement.triggers`, `preprocessing.nlu_processor.user_state_confidence_threshold`) as `[No guide coverage — DPG-specific]`. Contributes to `docs/guide-gaps.md`.

### Schema changes

**`dev-kit/dev_kit/schemas/agent_core.yaml`:**

New top-level `channels:` block replaces `agent.channels` (remove) and agent_core's `reach_layer.channels` (remove). Per-channel LLM-facing config centralises here.

```yaml
channels:
  voice:
    system_prompt_suffix: ""           # existing GH-97 field, migrated
    tts_rules:                          # canonical rules, auto-populated per default_language;
      numbers: ""                       # rendered by dev-kit into system_prompt_suffix at authoring time
      money: ""                         # (not at runtime) — source of truth for the canonical rule set
      dates: ""
      time: ""
      phone: ""
      abbreviations: ""
      output_script: ""
      english_loanwords: ""
    terminal_word: ""                   # required when voice declared; consumed by reach_layer_voice
    turn_assembler:                     # migrated from reach_layer.channels.voice.turn_assembler
      semantic_gate: {enabled: false, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 400}
      max_wait_ceiling: {max_wait_ms: 8000}
  chat:
    system_prompt_suffix: ""
    tts_rules: null
    terminal_word: null
    turn_assembler: {...}
  web: {...}
  cli: {...}
```

Extend every connector with `invocation_rules`:

```yaml
connectors:
  read:
    - name: ""
      description: ""
      input_schema: {...}
      invocation_rules:                 # NEW — six sub-fields per tool
        call_when: ""
        required_before_calling: []
        must_not_substitute: ""
        on_empty: ""
        on_failure: ""
        bridge_line: ""
```

Extend every subagent with `opening_phrase`:

```yaml
agent_workflow:
  subagents:
    - id: ""
      # ... existing fields ...
      opening_phrase: ""                # NEW — emitted on first turn only; may be empty
```

Add `conversation.session_end_eval`:

```yaml
conversation:
  # ... existing fields ...
  session_end_eval:
    enabled: false
    prompt: ""                          # appended to main system prompt when enabled
    fail_action: "none"                 # schema-accepted; runtime ignores in this PR
```

**`end_session` internal tool — runtime-registered, not authored in YAML.** When `session_end_eval.enabled: true`, the orchestrator registers `end_session` into the tool registry at startup with route `orchestrator` (handled inside Agent Core, not routed externally). Domain authors never declare it in YAML. Tool schema (canonical, injected into the LLM tool list):

```yaml
# Runtime-registered when session_end_eval.enabled: true — do NOT add this to agent_core.yaml
- name: end_session
  route: orchestrator
  description: >
    Call when the conversation has naturally concluded (user said goodbye,
    task completed, user asked to stop). Emits the session-end signal to
    runtime; still include your natural final response text alongside this
    tool call.
  input_schema:
    type: object
    properties:
      reason:
        type: string
        enum: ["user_goodbye", "task_complete", "user_requested_stop", "other"]
    required: ["reason"]
```

ManagerAgent's tool loop must intercept `end_session` calls before they reach Action Gateway or any external executor — the tool has no external side effect; its only purpose is to set `TurnResult.session_ended = True`.

**`dev-kit/dev_kit/schemas/trust_layer.yaml`:**

```yaml
dignity_check:                           # auto-populated for Conversational agents
  enabled: false
  questions:
    - "Does this blame the user?"
    - "Does it over-promise?"
    - "Does it push urgency?"
    - "Does it reduce their agency?"
    - "Does it sound like a script instead of a human call?"
  fail_action: "rewrite"                 # schema-accepted; runtime ignores in this PR
```

**`dev-kit/dev_kit/schemas/reach_layer.yaml`:** no structural changes. Adapter-specific per-channel settings (TTS provider, websocket URLs) stay here.

**Validation rules (loader + workflow_loader):**
- `agent.channels` or `reach_layer.channels` present in agent_core.yaml → `ConfigurationError` with migration instruction.
- `channels.voice.terminal_word` required (non-empty) when voice channel declared.
- Connector `invocation_rules.call_when` + `required_before_calling` required for Agentic / Conversational types.
- `trust_layer.dignity_check.enabled: true` required for Conversational.
- `agent_workflow.subagents[].opening_phrase` warned (not errored) when empty on `is_start: true` subagent of a Conversational project.

### Runtime changes

**`agent_core/src/orchestrator.py`:**

1. **Channel-config path hard cut.** Read `config["channels"][channel]`. Raise `ConfigurationError` on old paths.

2. **Opening-phrase gate** — fires after the consent gate resolves and before Trust input check. Uses a new session flag `opening_phrase_emitted`:

```python
if not bundle.session.get("opening_phrase_emitted", False):
    current_sa_id = bundle.session.get("current_subagent") or self._workflow.start_subagent_id
    current_sa = self._workflow.get_subagent(current_sa_id)
    opening = (current_sa.opening_phrase or "").strip()
    if opening:
        self._write_memory_sync(session_id, user_id, "session", "current_subagent", current_sa_id)
        self._write_memory_sync(session_id, user_id, "session", "opening_phrase_emitted", True)
        return TurnResult(
            session_id=session_id, turn_id=turn_id,
            response_text=opening,
            latency_ms=int((time.time() - start) * 1000),
        )
    self._write_memory_sync(session_id, user_id, "session", "opening_phrase_emitted", True)
```

Turn-0 precedence: memory read → consent gate → opening-phrase gate → normal turn.

3. **`end_session` internal tool handling.** On detecting a call to `end_session` in the LLM response, set `TurnResult.session_ended = True` (or `DoneEvent.session_ended = True` in the streaming path), suppress external execution, emit the LLM's text response normally.

4. **`session_end_eval` prompt injection.** When `config.conversation.session_end_eval.enabled=true`, pass `session_end_eval.prompt` as a new kwarg to `ManagerAgent.build_system_prompt`.

**`agent_core/src/manager_agent.py`:**

1. Read `channels.*` top-level (no signature change — orchestrator passes `channel_config`).

2. **TTS rules not injected at runtime.** `channels.voice.tts_rules` is the canonical source rendered by dev-kit into `channels.voice.system_prompt_suffix` at authoring time. Runtime just reads the suffix (existing GH-97 behaviour).

3. **New `session_end_eval_prompt` kwarg.** Rendered as a section before the channel suffix when non-empty.

**`agent_core/src/turn_assembler.py`:** path migration to `config["channels"][channel]["turn_assembler"]`.

**`agent_core/src/workflow_loader.py`:** `SubAgent.opening_phrase: str = ""`; `AgentWorkflow.start_subagent_id` property; `AgentWorkflow.get_subagent(id)` (or verify existing); validator warns on empty `opening_phrase` + `is_start: true` + Conversational type.

**`agent_core/src/models.py`:**

```python
@dataclass
class TurnResult:
    # … existing fields …
    session_ended: bool = False

@dataclass
class DoneEvent:
    # … existing fields …
    session_ended: bool = False
```

**Trust Layer:**

Load `trust_layer.yaml → dignity_check` at startup. `/assemble_constraints` appends the 5 questions to `prompt_constraints` under a "Pre-response dignity check" heading when `enabled: true`. Existing prohibited-phrase output check unchanged. No new LLM calls; dignity is a pre-response self-check enforced via the main LLM's system prompt.

**`reach_layer_voice`:**

On receiving a `TurnResult` / `DoneEvent` with `session_ended=True`:
1. Append `channels.voice.terminal_word` to the outbound TTS stream as the final utterance.
2. Wait for TTS of that utterance to complete.
3. Emit websocket close frame to telephony adapter.

Empty `terminal_word` + `session_ended=True` → close without appending, log warning.

### Backwards compatibility

Hard cut. Existing three in-tree domains migrated in this PR. Out-of-tree domains must migrate `agent.channels` → `channels`, remove `reach_layer.channels` from their agent_core.yaml, and move `turn_assembler` under the new path. Loader emits a clear error with the migration path.

## Testing

**Dev-kit:**
- `PHASES` contains `tier` at index 0 and `user_state` between `memory` and `trust`; length 12.
- `set_agent_type` writes project meta; rejects unknown types.
- `set_phase` honours `SHEET_REQUIREMENTS`: auto-skips `skip`, blocks on missing `required`, records `skipped_by_user` / `answered`.
- `update_config` rejects writes to removed paths with migration error.
- Each phase branch non-empty for each type; `tier` branch returns decision-tree text; `overview` sequence mentions all 12 phases.
- Config-writer round-trips a sample Conversational project populating all new fields.

**Agent Core:**
- `manager_agent`: `build_system_prompt` reads `channels.*` top-level; renders `session_end_eval_prompt` section; terminal word absent from assembled prompt.
- `orchestrator`: turn-0 sequencing (consent before opening); `opening_phrase_emitted` flag persists; empty opener sets flag and proceeds; `end_session` tool sets `session_ended=True` and suppresses external execution; disabled eval = flag stays False.
- `workflow_loader`: `opening_phrase` accepted; `start_subagent_id` resolves; validator warns on empty opener + Conversational.
- `turn_assembler`: reads new path; errors on old.
- `models`: `TurnResult.session_ended` and `DoneEvent.session_ended` default `False`.

**Trust Layer:**
- `/assemble_constraints` with `dignity_check.enabled=true` returns the 5 questions in `prompt_constraints`.
- Disabled / missing block → unchanged constraints.
- Prohibited-phrase checks unchanged.

**Reach Layer voice:**
- `TtsStreamer` with `session_ended=True` + non-empty `terminal_word` appends it as final utterance.
- `session_ended=True` + empty terminal_word → close, warning log, no append.
- `session_ended=False` → bypass.
- Integration: full turn exercising `end_session` tool → flag → append → close.

**Backwards-compat smoke:** all three in-tree domains load with migrated configs; no regressions in existing behaviour tests.

**Coverage target:** ≥70% line coverage on each touched file per `.claude/rules/testing-requirements.md`.

## Rollout

**Single PR for the framework uplift.** Schema changes, phase-prompt rewrites, and runtime path migrations are tightly coupled — the hard-cut loader rejection requires all three to land atomically.

**Recommended commit ordering** (for reviewer sanity; not hard dependency):

1. Models: `TurnResult.session_ended`, `DoneEvent.session_ended`, `SubAgent.opening_phrase`.
2. Schema templates (`dev-kit/dev_kit/schemas/*.yaml`) with new fields and removed old paths.
3. Loader updates: reject old paths with clear error. Three existing domains migrated in the same commit range.
4. `ManagerAgent`: `channels.*` top-level read, `session_end_eval_prompt` kwarg.
5. `Orchestrator`: opening-phrase gate, `end_session` tool handling, channel path migration, session_end_eval plumbing.
6. `TurnAssembler` path migration.
7. `WorkflowLoader` + `AgentWorkflow` extensions.
8. Trust Layer dignity-check constraint assembly.
9. `reach_layer_voice`: terminal-word append + telephony close on flag.
10. Dev-kit: `PHASES` with `tier`, `SHEET_REQUIREMENTS`, `set_agent_type` tool, `set_phase` gating, `update_config` path rejections.
11. Dev-kit: phase-prompt rewrites (all 12 branches).
12. Tests in each commit range (TDD).
13. `ARCHITECTURE.md` updates; `docs/guide-gaps.md` companion.

**Follow-up issues filed with this PR:**

- **#137-child — KKB config refresh from prompt doc.** Translates KKB's 5-state model, subagent opening phrases, prohibited phrases, TTS rules, dignity check enablement, and tool invocation rules from `docs/KKB Current Prompt.pdf` into the new YAML shape. Depends on this PR landing.
- **`session_end_eval.fail_action` runtime wiring.** Schema-accepted today; runtime ignored. Future issue if programmatic enforcement ever needed.

## Risks

- **Path migration is a breaking change.** Out-of-tree domains error on startup. Mitigation: clear error messages with fix-up paths; release notes; all in-tree domains migrated in the PR.
- **Phase-prompt rewrite is large surface.** Subtle LLM behaviour regressions possible. Mitigation: end-to-end conversational tests per type; add one per type post-rewrite.
- **`end_session` tool adoption depends on LLM calling it reliably.** Under-calling = sessions never close cleanly; over-calling = premature termination. Mitigation: `session_end_eval` is opt-in per domain, default off; KKB enables it in the follow-up refresh PR with a tuned eval prompt.

## Acceptance criteria

- `_meta/project.json` supports `agent_type` and `phase_decisions`.
- `set_agent_type` and `set_phase` honour the `SHEET_REQUIREMENTS` matrix.
- All 12 phase-prompt branches rewritten using guide pedagogy; each injects per-type content.
- Channel config consolidated under `channels.*` top-level in `agent_core.yaml`; old paths rejected at load time.
- Per-tool `invocation_rules` schema in place; validator enforces required sub-fields for Agentic / Conversational.
- `session_end_eval` block and `end_session` internal tool wired end-to-end: LLM call → tool detection → `TurnResult.session_ended` → voice adapter appends terminal word → websocket close.
- `trust_layer.dignity_check` block plumbed through `/assemble_constraints`; no new LLM calls.
- Subagent `opening_phrase` field emits on the first post-consent turn of a session; re-entries skip.
- All three in-tree domains migrated and booting.
- `ARCHITECTURE.md` updated; `docs/guide-gaps.md` populated during phase-prompt rewrite.
- Test coverage ≥70% on every touched file.
