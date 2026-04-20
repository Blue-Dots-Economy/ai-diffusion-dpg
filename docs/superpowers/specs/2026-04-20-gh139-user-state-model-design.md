# Domain-Configurable User-State Model

**Issue:** #139
**Blocks:** #137 (KKB configs refresh)
**Status:** Design approved — ready for writing-plans

---

## Problem

The DPG runtime tracks **system state** — profile-fetched, post-application, do-not-call — via subagents, Memory Layer session scope, and NLU-driven transitions. This covers operational routing.

**Conversational**-type domains (per the agent-type taxonomy in `docs/Agent_Configuration_Guide_Main.pdf`) need a second, orthogonal dimension: **user mental state** — where the user is emotionally and cognitively *right now*. The KKB prompt doc (`docs/KKB Current Prompt.pdf`) and the new Agent Configuration Guide (April 2026, v3.0) both model this explicitly. Sheet D (Conversational) makes the state model mandatory and specifies observable triggers plus agent behaviours per state. Sheet C (Agentic) explicitly forbids it. Sheets A and B (Transactional / Informational) skip it entirely. KKB declares five states: Fog / Orientation / Evaluation / Commitment / Follow-through. Other Conversational domains declare different states.

User state is not rule-driven — it is inferred each turn from the user's message. The same user can cycle through states freely within a session. This is fundamentally different from system state and cannot be collapsed into the existing subagent DAG without distorting either abstraction.

## Design

### Architecture

A new conversation dimension, inferred by the existing NLU call and injected into the main agent call. Zero new services, zero new LLM calls, no new Memory Layer round-trips — rides on existing infrastructure.

Per-turn sequence (changes **bold**):

```
Reach Layer (input)
  → Orchestrator: read session state ← Memory Layer
      [includes previous user_state if declared]
  → Trust Layer /check/input
  → Language Normalisation
  → NLU Processor
      INPUT  + [declared states, signals, previous_state_id]    ← NEW
      OUTPUT + {user_state: {id, confidence}}                   ← NEW
  → Orchestrator: resolve current user_state                    ← NEW
      if conf ≥ threshold: transition to new state
      else:                stay sticky (previous state)
  → Manager Agent: build_system_prompt(..., user_state_guidance=...)  ← NEW param
  → LLM call → tool loop → LLM call
  → Trust Layer /check/output
  → Deliver response
  → [async] write session state including updated user_state    ← piggy-back
  → [async] emit user_state_transition event IF state changed   ← NEW
```

Module boundaries preserved: Agent Core remains the only orchestrator; NLU still returns one structured result; Memory Layer session scope grows by one object with no interface change; Trust Layer, Action Gateway, Knowledge Engine untouched.

### Config shape

New optional block under `conversation` in the agent_core YAML:

```yaml
conversation:
  user_state_model:
    enabled: false                # default off — Transactional/Informational/Agentic domains never touch this
    default_state: ""             # REQUIRED when enabled=true; must match an id below
    states:                       # REQUIRED when enabled=true; must be non-empty
      - id: ""                    # unique stable key
        signals: []               # list of source-language or English phrases (NLU hints)
        guidance: ""              # behaviour text injected into main LLM prompt when active

preprocessing:
  nlu_processor:
    user_state_confidence_threshold: 0.4   # NEW; independent of intent threshold
```

Example KKB shape (lands with #137, not this issue):

```yaml
conversation:
  user_state_model:
    enabled: true
    default_state: fog
    states:
      - id: fog
        signals:
          - "कुछ समझ नहीं आ रहा"
          - "कोई काम है क्या?"
          - "vague or undirected request"
        guidance: |
          Orient gently. Surface 2–3 directions.
          Do not interrogate. Do not jump to detailed options.
          Bring the market into view first.
      - id: orientation
        signals: [...]
        guidance: |
          Show the real shape of the market. Explain what matters.
          Fill only the missing facts needed for the next useful answer.
      # evaluation, commitment, follow-through …

preprocessing:
  nlu_processor:
    user_state_confidence_threshold: 0.4
```

### Loader validation

Raised as `ConfigurationError` at startup:

| Rule | Violation |
|---|---|
| `enabled: true` | requires non-empty `default_state` AND non-empty `states` |
| `default_state` | must equal one of `states[].id` |
| `states[].id` | unique, non-empty string |
| `states[].guidance` | non-empty (empty guidance = no direction = pointless declaration) |
| `states[].signals` | may be empty, warning logged |
| `user_state_confidence_threshold` | float in `[0.0, 1.0]`; defaults to 0.4 if absent |

### NLU extension

**`agent_core/src/models.py`** — new dataclass, new optional field on `NLUResult`:

```python
@dataclass
class UserStateClassification:
    id: str
    confidence: float

@dataclass
class NLUResult:
    intent: str
    entities: dict
    sentiment: str
    confidence: float
    user_state: UserStateClassification | None = None
```

**`agent_core/src/preprocessing/nlu_processor.py`** — three changes:

1. `__init__` reads `conversation.user_state_model` and `preprocessing.nlu_processor.user_state_confidence_threshold`, caches declared states and threshold as instance fields.
2. `_NLU_SYSTEM_PROMPT_TEMPLATE` gets a conditional section, inserted only when `enabled=True`, that lists the declared states with their signals and one-line guidance summary and instructs the classifier to return a `user_state` field.
3. `process()` accepts a new kwarg `previous_user_state: str | None`. Parsing logic parallel to intent validation: extract `user_state`, validate `id` ∈ declared set, fall back to previous on invalid id or confidence below threshold, never raise.

The per-turn token cost for a 5-state KKB-like config is ~350 extra input tokens to NLU — Haiku handles this comfortably.

Failure modes all land on sticky fallback (return previous state, no event emitted):
- LLM omits `user_state` key → fallback.
- Returned id not in declared set → fallback + warning log.
- Confidence below threshold → fallback, no warning (normal sticky).
- NLU as a whole fails (existing path) → `_fallback_nlu_result()` sets `user_state=None`, downstream reuses previous.

### Orchestrator integration

Turn-flow touch points in `agent_core/src/orchestrator.py`:

1. On turn start, read previous state from session; if absent, fall back to config `default_state`. Always pass a real previous state to NLU.
2. Pass `previous_user_state=previous_state_id` to `nlu_processor.process()`.
3. Resolve the final state via a new helper `resolve_user_state()` (own module `src/preprocessing/user_state_resolver.py` for testability):

```python
def resolve_user_state(
    *,
    classification: UserStateClassification | None,
    previous: dict | None,
    config: dict,
    now: datetime,
) -> tuple[dict | None, bool]:
    """Returns (new_session_payload, transitioned)."""
```

Resolver rules:
- Disabled/absent model → `(None, False)`.
- First turn + enabled → payload initialised with `default_state`, `transitioned=False`.
- Same id as previous (sticky) → increment `turn_count`, keep timestamps and `previous_id`.
- Changed id → new payload, `previous_id` = old id, `turn_count = 1`, `updated_at = now`, `transitioned=True`.

4. Look up active guidance text (dict indexed by id, cached at AgentCore startup — never re-scanned per turn).
5. Pass `user_state_guidance` to `ManagerAgent.build_system_prompt()`.
6. After response delivery, existing async Memory Layer write gains `user_state` key in the session payload — no new round-trip.
7. Emit `user_state_transition` event to Observability Layer only when `transitioned=True`.

Session payload shape:

```python
{
  "id": "orientation",
  "confidence": 0.82,
  "updated_at": "2026-04-20T10:15:00Z",
  "previous_id": "fog",
  "turn_count": 1,
}
```

### ManagerAgent prompt assembly

**`agent_core/src/manager_agent.py`** — `build_system_prompt()` gains one kwarg:

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
    user_state_guidance: str | None = None,   # NEW
) -> str:
```

Render order (new piece between subagent prompt and guardrail constraints):

```
agent_system_prompt
context (channel + language)
resumption note
profile grounding
subagent_system_prompt
## Current user state guidance        ← NEW, only when non-empty
  {user_state_guidance}
guardrail_constraints
channel suffix (GH-97)
```

Orthogonal to channel-aware prompting (#97). Channel suffix still lands last.

### Observability

Three surfaces, three purposes:

**(a) OTel span attributes** on the existing `agent_core.process_turn` span — operational telemetry, written inline (no new span):

```
user_state.enabled: true
user_state.previous: "fog"
user_state.current: "orientation"
user_state.transitioned: true
user_state.confidence: 0.82
user_state.turn_count: 1
```

**(b) Observability Layer event** — product telemetry, async post-delivery, only on actual transition:

```python
{
  "event_type": "user_state_transition",
  "session_id": "...",
  "turn_index": 5,
  "timestamp": "2026-04-20T10:15:00Z",
  "from_state": "fog",
  "to_state": "orientation",
  "confidence": 0.82,
  "trigger_intent": "pay_disappointment",
  "turns_in_previous_state": 3,
}
```

Uses the existing turn-event fan-out (audit log / quality pipeline / dashboards).

**(c) Structured log** — every turn, per `.claude/rules/logging-observability.md`:

```python
logger.info("user_state.resolved", extra={
    "operation": "orchestrator.resolve_user_state",
    "status": "success",
    "transitioned": transitioned,
    "state_id": new_id,
    "previous_state_id": previous_id,
    "confidence": conf,
    "latency_ms": 0,
})
```

Metrics (e.g. `user_state_transitions_total{from,to}`) are intentionally deferred to a follow-up issue — best designed once production data exists.

### Dev-kit questionnaire

New dedicated phase `user_state`, slotted between `memory` and `trust` in the 10-phase flow. Gated on **agent type = Conversational** once #137's agent-type selector lands (per the 3-question selector in Part 1 of the Main Guide; Sheet C explicitly forbids a state model for Agentic). Until then, the phase is always visited with an "answer `skip` if not applicable" hint.

Changes:

- `dev-kit/dev_kit/agent/prompts/phases.py` — new branch in `get_phase_addition("user_state")` that elicits state ids, signals, guidance, and default state, then writes to `conversation.user_state_model`.
- Phase sequence in the `overview` branch updated to include `user_state` after `memory`.
- `dev-kit/dev_kit/agent/accumulator.py` — accepts `conversation.user_state_model` updates; pass-through to YAML writer.
- `set_phase` tool recognises `user_state` and enforces ordering.

### Backwards compatibility

All existing domains are unaffected:
- `conversation.user_state_model.enabled` defaults to `false`.
- `user_state_confidence_threshold` defaults to `0.4` if absent.
- `build_system_prompt(..., user_state_guidance=None)` renders no section.
- Existing KKB config, `farmer-friendly`, `obsrv-docs-assistant` continue to boot without validation errors or behavioural change.
- Existing tests run unchanged.

### Interaction with in-flight work

- **#97 channel-aware prompting** — orthogonal. Concat order: agent prompt → context → subagent prompt → user_state_guidance → guardrail constraints → channel suffix. No conflict; PR rebases after both merge.
- **#137 KKB config refresh** — blocked by #139. Once #139 merges, #137 declares the 5 KKB states in `dev-kit/configs/kkb/agent_core.yaml`.

## Testing

**Unit — `nlu_processor.py`:**

| Case | Expected |
|---|---|
| Model disabled (`enabled=false`) | `result.user_state is None`, prompt has no state section |
| Valid classification ≥ threshold | `UserStateClassification(id=valid_id, confidence=0.82)` |
| Confidence below threshold | Sticky: returns `previous_user_state`, no warning |
| Returned id not in declared set | Sticky fallback + warning log |
| `user_state` key missing from JSON | Sticky fallback + warning |
| LLM failure path | `NLUResult.user_state = None`, no crash |

**Unit — `resolve_user_state`:**

| Case | Expected |
|---|---|
| Disabled model | `(None, False)` |
| First turn + enabled | payload with `default_state`, `transitioned=False` |
| Same id as previous | `turn_count` incremented, timestamps unchanged, `transitioned=False` |
| Changed id | new payload, `previous_id` set, `turn_count=1`, `transitioned=True` |

**Unit — `manager_agent.build_system_prompt`:**

- `user_state_guidance=None` or `""` → section not rendered.
- `user_state_guidance="..."` → section appears between subagent prompt and guardrail constraints with `## Current user state guidance` heading.

**Integration — `process_turn` / `stream_turn`:**

- KKB-like enabled config exercises NLU extension, resolver, manager-agent injection, memory write, and event emission in one turn.
- Transition event appears in observability fake exactly once per actual change, never on sticky.
- OTel span attributes populated on the turn span.

**Backwards compatibility:**

- Existing KKB tests (without `user_state_model`) run unchanged and green.
- `farmer-friendly`, `obsrv-docs-assistant` configs boot without validation errors.

**Config loader validation:**

- Missing `default_state` when `enabled=true` → `ConfigurationError` at startup.
- `default_state` not in declared ids → `ConfigurationError`.
- Duplicate state ids → `ConfigurationError`.
- Empty `guidance` for any state → `ConfigurationError`.
- Threshold outside `[0, 1]` → `ConfigurationError`.

**Dev-kit:**

- `set_phase("user_state")` visits the new phase.
- `update_config` with `conversation.user_state_model` round-trips to YAML.
- Validator rejects malformed declarations before accumulator accepts them.

**Coverage target:** ≥70% line coverage on touched files, per `.claude/rules/testing-requirements.md`.

## Rollout

Single PR, cohesive surface. Implementation order:

1. Schema + loader validation (`dev-kit/dpg/agent_core.yaml`, loader).
2. Models + NLU processor extension.
3. Orchestrator resolver + ManagerAgent injection.
4. Observability wiring (span attrs + Obs Layer event + structured log).
5. Dev-kit phase + accumulator.
6. Tests in lockstep (TDD).
7. `ARCHITECTURE.md` paragraph under Agent Core describing the orthogonal user-state dimension.
8. Minimal 2-state example in an existing domain config so the feature is self-documenting. Full 5-state KKB refresh lands with #137.

## Out of scope

- Hard routing between user states (user state is inferred, not gated).
- Cross-session user-state persistence (session scope only).
- Power-user template vars like `{user_state.id}` or `{user_state.turn_count}` (deferred; automatic injection is sufficient for v1).
- Metrics dashboard (deferred; needs production data).
- Agent-type selector + type-based phase gating (delivered as part of #137).
- KKB's actual 5-state declarations (delivered as part of #137).
- Strict-override opening logic, pre-response dignity check, rigorous tool-trigger schema, TTS rules checklist, fixed terminal word (all delivered as part of #137 — orthogonal to user-state).

## Acceptance criteria

- All runtime changes land behind `enabled=false` default.
- KKB, farmer-friendly, obsrv-docs-assistant boot unchanged with existing configs.
- Test coverage ≥70% on touched files.
- Integration test proves: transition event emitted exactly once per real change; OTel span attributes populated; structured log emitted per turn.
- Loader validation catches every schema misuse at startup with a clear error message.
- `ARCHITECTURE.md` updated.
- Minimal example in a domain config demonstrates the feature end-to-end.
