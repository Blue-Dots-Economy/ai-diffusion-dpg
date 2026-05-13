# Dev-Kit Deterministic Wizard — Design

**Status:** Draft awaiting review
**Date:** 2026-05-13
**Supersedes (in spirit):** `docs/superpowers/specs/2026-05-13-devkit-config-generation-revamp-design.md`. That earlier doc proposed an LLM-with-skeleton model; this one is a more deterministic re-architecture using the same principles plus an explicit state machine.

## 1. Problem

The current dev-kit wizard is LLM-driven end to end. The LLM decides what to ask, when to advance phases, and what fields to write. This produces three classes of failure:

1. **Pre-deploy validation errors** — required fields the LLM forgot (`trust:` key missing, `input_schema` empty, `user_node` partial, etc.).
2. **Runtime crashes** — fields the dev-kit schema accepts but the runtime silently drops or requires (`tool_registry.py:155` silent-drop class of bugs).
3. **Brittle state changes** — when a user mid-conversation changes their mind ("actually we have a KB"), today's wizard has no model for figuring out which earlier phases are now invalidated. The LLM either misses the change entirely or re-asks everything.

Root cause: the LLM is asked to be both the conversationalist AND the state machine. We separate those.

## 2. Goal

Move the dev-kit to a **constrained-agent** architecture:

- LLM mediates natural-language conversation with the user.
- Python state machine owns routing, field invalidation, and phase transitions.
- A typed `IntakeState` captured upfront determines deterministic behaviour for every downstream phase.
- A unified `FIELD_RULES` dict (per block) is the source of truth for every field's category (`predetermined`, `chat`, `deploy`, `derived`), its phase, its default, its invalidation triggers.

Result: every project starts with a complete-by-default config; the LLM only writes domain-specific values via typed `update_config` calls; mid-conversation state changes route deterministically; pre-deploy validation and runtime crashes are eliminated by construction.

## 3. Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│                            DEV-KIT WIZARD                          │
│                                                                    │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────┐ │
│  │ INTAKE_STATE │   │  FIELD_RULES │   │      PHASES config      │ │
│  │  (typed)     │   │  per block   │   │  (declarative)          │ │
│  └──────┬───────┘   └──────┬───────┘   └──────────┬──────────────┘ │
│         │                  │                       │               │
│         │     ┌────────────┴───────────────────────┘               │
│         │     │                                                    │
│         ▼     ▼                                                    │
│  ┌──────────────────┐    ┌─────────────────────────────────────┐  │
│  │  PHASE DRIVER    │◀──▶│             ROUTER                  │  │
│  │  - reads PHASES  │    │  - on update_intake → mark fields   │  │
│  │  - calls LLM     │    │    invalidated using FIELD_RULES    │  │
│  │  - parses tool   │    │  - decides next phase at end-of-    │  │
│  │    calls         │    │    turn                             │  │
│  └──────────────────┘    └─────────────────────────────────────┘  │
│         │                                                          │
│         ▼                                                          │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │           ACCUMULATOR  (current YAML state per block)         │ │
│  │  - holds skeleton + answered fields + invalidations           │ │
│  └──────────────────────────────────────────────────────────────┘ │
│         │                                                          │
│         ▼ at deploy time                                           │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  RENDERER  → applies derived fields + deploy overlay → YAML  │ │
│  └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       docker-compose bind-mount
```

### Five new top-level constructs

| Component | Lives at | What it does |
|---|---|---|
| `IntakeState` | `dev-kit/dev_kit/agent/intake_state.py` | Typed dataclass; 11 fields captured in the intake phase; persisted to `_meta/intake_state.json` |
| `FIELD_RULES` | `dev-kit/dev_kit/agent/field_rules/<block>.py` | Per-field rules: category, phase, default, invalidation triggers, applies_if expressions, derived-compute expressions |
| `PHASES` | `dev-kit/dev_kit/agent/phases_config.py` | Declarative phase definitions: id, label, prompt function, next-phase pointer, is_relevant predicate |
| Phase driver | `dev-kit/dev_kit/agent/phase_driver.py` | Single shared module that runs all phases; filters fields, builds prompts, calls LLM, processes tool calls |
| Router | `dev-kit/dev_kit/agent/router.py` | On `update_intake`: walks FIELD_RULES, marks invalidations, recomputes predetermined values. At end-of-turn: decides next phase |

### Pydantic schemas — single source of truth used in four places

| Use | How |
|---|---|
| LLM sees the schema | Each phase's prompt inlines the Pydantic class source for the fields it's asking. Uses `_collect_referenced_models()` to walk the full transitive closure of nested classes (e.g., asking about `trust.input_rules.blocked_phrases` injects `TrustSection`, `InputRulesConfig`, etc.). |
| Skeleton construction | `build_skeleton()` reads Pydantic field defaults, applies `predetermined` rules, fills `chat` defaults — produces a valid-by-default YAML. |
| `update_config` validation | Every call goes through `validate_partial(block, partial)` — the Pydantic validator gates every write. |
| Pre-deploy dry-run | At write time, the renderer imports each runtime block's `MergedConfig` Pydantic class and calls `model_validate()` on the rendered YAML — catches anything the dev-kit schema accepted but the runtime would crash on. |

### Three things the system does NOT do

- **LLM does not call `set_phase`.** Today's `set_phase` tool is removed. The router decides phase transitions based on state.
- **LLM does not see `FIELD_RULES`.** It sees the prompt the phase driver builds from the rules. Effects of `update_intake` calls are summarised in the tool's return value.
- **LLM does not freely invent fields.** Every `update_config` and `update_intake` call is type-validated against the rule for that path. Invalid paths or values return an error to the LLM with the offending field path.

## 4. Intake state & the new intake phase

### Replacement for today's "tier" phase

Today's tier phase asks 4 yes/no questions to set `agent_type` only. The new intake phase asks all 11 routing-relevant values up front. This produces the typed `IntakeState` that every downstream phase reads from for branching decisions.

### `IntakeState` shape

```python
from dataclasses import dataclass
from typing import Literal

AgentType = Literal["transactional", "informational", "automation", "conversational"]
Channel   = Literal["web", "voice"]

@dataclass
class IntakeState:
    # Routing decisions
    agent_type: AgentType
    has_kb: bool
    has_external_tools: bool
    needs_persistent_user_data: bool
    needs_consent: bool
    has_hitl: bool

    # Channels & languages
    selected_channels: list[Channel]
    default_language: str
    supported_languages: list[str]

    # Context (not used for routing — LLM context only)
    domain_description: str
    project_name: str

    # Bookkeeping
    completed: bool = False
    updated_at: str = ""
```

Note: `agentic` was renamed to `automation`. The four agent types remain conceptually unchanged.

### Why each field lives in intake

| Field | Why in intake | What it decides downstream |
|---|---|---|
| `agent_type` | Determines which phases run (user_state is conversational-only) and which behaviours toggle. Must be known before phase-skip routing. | `dignity_check.enabled`, `user_state_model.enabled`, phase gating |
| `has_kb` | Decides "ask the knowledge phase or skip it". Also drives whether the `knowledge_retrieval` connector exists in agent_core, which the workflow phase needs to know to wire subagents. | Deploy KE yes/no; `static_knowledge_base.enabled`; `connectors.internal.knowledge_retrieval`; whether knowledge phase runs; `agent_workflow.global_tools` inclusion |
| `has_external_tools` | The tools phase only runs if this is true; workflow phase needs to know which tools subagents can use. | Deploy AG yes/no; whether tools phase runs; `connectors.read/write/identity`; subagent tool wiring |
| `needs_persistent_user_data` | The memory phase asks different questions depending on this. Knowing upfront avoids the dead-end branch. | `memory_layer.state.persistent` populated or `null`; `user_data_persistence.default_mode` = saved or anonymous |
| `needs_consent` | The consent flow touches multiple phases (language asks consent_prompt; trust enables ConsentBlock; conversation messages add consent_message/consent_decline_ack). | `agent.ask_for_consent`; consent prompt and acknowledgement messages; Trust Layer ConsentBlock activation |
| `has_hitl` | Trust phase asks meaningful HITL questions vs leaves sentinels. Knowing upfront avoids dead questions. | `trust.hitl.holding_message` content vs sentinel; whether `escalation_topics` is asked; HITL queue backend exposure in deploy form |
| `selected_channels` | Cascades into many phases (language TTS rules only if voice; trust scope; reach configures active channels; deploy form needs right credentials). | Which `agent_core.channels.<x>` and `reach_layer.channels.<x>` entries exist; voice TTS rules asked or not; voice credentials required at deploy |
| `default_language` | The language phase needs this to know which language the conversation messages should default to. | `language_normalisation.default_language`; default language for all conversation messages |
| `supported_languages` | Many downstream phases multiply per-language (one conversation message per language, one TTS rule per language). Knowing the full list upfront avoids re-running phases on later additions. | `language_normalisation.supported_languages`; multiplexing of conversation messages and Trust messages; per-language TTS rules |
| `domain_description` | Used as LLM context in every downstream phase prompt so questions are phrased naturally for the user's domain. | LLM context only — no routing effect |
| `project_name` | Drives slug, observability domain, KE collection name, web UI app_name, filesystem path. | Slug derivation; default values for slug-derived fields |

### Single mutation point: `update_intake`

The LLM has exactly one tool to change intake values:

```python
class UpdateIntakeArgs(BaseModel):
    field: Literal[
        "agent_type", "has_kb", "has_external_tools",
        "needs_persistent_user_data", "needs_consent", "has_hitl",
        "selected_channels", "default_language", "supported_languages",
        "domain_description", "project_name",
    ]
    value: Any  # validated against the field's type in handler
```

The handler validates type, writes to `IntakeState`, walks `FIELD_RULES` to find affected fields, applies effects per category (predetermined → recompute; chat → mark `needs_re_asking`; derived → flag stale), persists to disk, returns a structured summary:

```json
{
  "ok": true,
  "field": "has_kb",
  "old_value": false,
  "new_value": true,
  "affected_count": 8,
  "earliest_affected_phase": "language"
}
```

The LLM uses this to write a natural-language acknowledgment for the user.

### Intake phase prompt style

The intake phase asks all 11 fields in a guided sequence. The LLM bundles related ones rather than asking one at a time. Example bundling:

- Turn 1: "What does the agent do? Who are the users?" → captures `project_name`, `domain_description`, derives `agent_type` (from the 4 classification questions)
- Turn 2: "Does the agent need a knowledge base or external APIs? Will it remember users across sessions? Does it handle PII or escalate to humans?" → captures `has_kb`, `has_external_tools`, `needs_persistent_user_data`, `needs_consent`, `has_hitl`
- Turn 3: "Which channels (web, voice, or both)? What languages?" → captures `selected_channels`, `default_language`, `supported_languages`

The driver enforces completeness — the intake phase is not complete until all 11 fields are set. The LLM has latitude on the bundling.

## 5. `FIELD_RULES` — per-field rules

### Structure

One Python module per block at `dev-kit/dev_kit/agent/field_rules/<block>.py`. Each exports a `FIELD_RULES: dict[str, FieldRule]` keyed by dotted field path (relative to the block root).

`FieldRule` dataclass:

```python
from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, Optional

Category = Literal["predetermined", "chat", "deploy", "derived"]

@dataclass(frozen=True)
class FieldRule:
    category:        Category
    # For predetermined: Python-expression string referencing intake state
    #   e.g. "set: ${needs_consent}", "set: (agent_type == 'conversational')"
    rule:            Optional[str] = None
    # For chat
    phase:           Optional[str] = None        # which phase asks this
    default:         Optional[Any] = None        # pre-fill value the LLM presents
    must_include:    Optional[list[Any]] = None  # required elements (lists)
    description:     Optional[str] = None        # short hint for the prompt
    applies_if:      Optional[str] = None        # gate expression on intake state
    invalidated_by:  list[str] = dc_field(default_factory=list)  # intake field names
    # For deploy
    advanced:        bool = False                # collapsible "advanced" section
    # For derived
    compute:         Optional[str] = None        # Python expression
    # For schema injection in prompts
    pydantic_class:  Optional[str] = None        # dotted path to owning Pydantic class
```

### Categories

| Category | What it means |
|---|---|
| `predetermined` | Set by an intake-state rule. Never asked. Value re-computed whenever any field in `invalidated_by` changes. |
| `chat` | Asked in chat, in a specific phase. Bot-builder sees the field, with `default` pre-filled if present, and can edit. **No "hidden defaults"** — every chat field surfaces to the user. |
| `deploy` | Captured by the deploy form (separate concern). May be marked `advanced` to live in a collapsible section. Skeleton seeds a default value into the YAML so the runtime always has a value present; deploy form overrides at deploy time. |
| `derived` | Computed from other fields by the renderer at write time. No status tracked; no user input. |

A field has exactly one category — categories are mutually exclusive.

### Example: trust_layer

```python
from dev_kit.agent.field_rules import FieldRule

_CANONICAL_DIGNITY_QUESTIONS = [
    "Does this blame the user?",
    "Does it over-promise?",
    "Does it push urgency?",
    "Does it reduce their agency?",
    "Does it sound like a script instead of a human call?",
]

FIELD_RULES = {
    # PREDETERMINED — set by intake state
    "dignity_check.enabled": FieldRule(
        category="predetermined",
        rule="set: (agent_type == 'conversational')",
    ),
    "dignity_check.questions": FieldRule(
        category="predetermined",
        rule=f"set: {_CANONICAL_DIGNITY_QUESTIONS} if agent_type == 'conversational' else []",
    ),

    # CHAT — asked with optional default
    "trust.hitl.holding_message": FieldRule(
        category="chat",
        phase="trust",
        default="Please hold while I connect you to an agent.",
        description="Shown to the user while waiting for human handoff",
        invalidated_by=["has_hitl", "supported_languages"],
        applies_if="has_hitl",
        pydantic_class="HitlConfig",
    ),
    "trust.input_rules.blocked_phrases": FieldRule(
        category="chat",
        phase="trust",
        default=[],
        description="Strings that immediately block the user's message",
        pydantic_class="InputRulesConfig",
    ),
    "trust.input_rules.blocked_input_message": FieldRule(
        category="chat",
        phase="trust",
        default="I can't help with that request.",
        description="Reply shown when input is blocked",
        invalidated_by=["supported_languages"],
        pydantic_class="InputRulesConfig",
    ),

    # DEPLOY — captured by deploy form
    "trust.hitl.queue_backend": FieldRule(
        category="deploy",
        advanced=True,
        default="log",
    ),
}
```

### Where the categories drive behaviour

| Consumer | Operation |
|---|---|
| `build_skeleton()` | For every field where `category == "predetermined"`: run `rule` against current `IntakeState`. For `chat` and `deploy`: write `default` if present. For `derived`: skip. |
| Phase driver | For phase X: filter rules to `category == "chat" AND phase == X AND (applies_if is None OR applies_if(state) is True)`. Of those, take fields with status `pending` or `needs_re_asking`. Render in the phase prompt. |
| Router on `update_intake(F, V)` | For every rule where `F in rule.invalidated_by`: if `predetermined`, re-run rule and write to accumulator; if `chat`, mark status `needs_re_asking`; if `derived`, flag for renderer recompute. |
| Renderer | At write time: for every `category == "derived"` rule, run `compute` and write to YAML. |

### CI guard

A test asserts: every Pydantic field in every runtime block's `MergedConfig` schema has a corresponding entry in `FIELD_RULES` (or is explicitly listed in a `not_exposed_intentionally` allowlist). This prevents new Pydantic fields being added later without a rule.

## 6. `PHASES` config & phase driver

### Declarative phase definitions

`dev-kit/dev_kit/agent/phases_config.py`:

```python
from dataclasses import dataclass
from typing import Callable, Optional
from dev_kit.agent.phase_prompts import (
    tier, language, knowledge, memory, user_state, trust,
    tools, workflow, observability, reach, review,
)

@dataclass(frozen=True)
class PhaseDefinition:
    id:                 str
    label:              str
    prompt_fn:          Callable        # the per-phase build() function
    next_default:       Optional[str]
    is_relevant:        Optional[Callable[[IntakeState], bool]] = None
    on_complete:        Optional[Callable[[Accumulator], None]] = None

PHASES = {
    "tier":           PhaseDefinition("tier", "Intake", tier.build, "language"),
    "language":       PhaseDefinition("language", "Language & NLU", language.build, "knowledge"),
    "knowledge":      PhaseDefinition("knowledge", "Knowledge base", knowledge.build, "memory",
                                       is_relevant=lambda s: s.has_kb),
    "memory":         PhaseDefinition("memory", "Memory & sessions", memory.build, "user_state"),
    "user_state":     PhaseDefinition("user_state", "User state", user_state.build, "trust",
                                       is_relevant=lambda s: s.agent_type == "conversational"),
    "trust":          PhaseDefinition("trust", "Trust & safety", trust.build, "tools"),
    "tools":          PhaseDefinition("tools", "External tools", tools.build, "workflow",
                                       is_relevant=lambda s: s.has_external_tools),
    "workflow":       PhaseDefinition("workflow", "Workflow", workflow.build, "observability",
                                       on_complete=validate_workflow_graph),
    "observability":  PhaseDefinition("observability", "Observability", observability.build, "reach"),
    "reach":          PhaseDefinition("reach", "Channels", reach.build, "review"),
    "review":         PhaseDefinition("review", "Review", review.build, None,
                                       on_complete=validate_cross_block_invariants),
}
```

### Per-phase prompt modules

One Python module per phase under `dev-kit/dev_kit/agent/phase_prompts/<phase>.py`. Each exports a single `build()` function returning the prompt string. Example:

```python
# dev-kit/dev_kit/agent/phase_prompts/knowledge.py
def build(
    pending_fields: list[FieldRule],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: IntakeState,
) -> str:
    fields_section = _render_fields(pending_fields)
    return f"""# Phase: Knowledge base

You are now configuring the agent's knowledge base. The user has confirmed
`has_kb=true`.

The KB collection name defaults to `{intake_state.project_name}_kb`; the user
can override. doc_types are domain-specific labels used to filter retrieval.
intent_filters map NLU intents to doc_types — keys must match the intents
declared in the language phase (visible below).

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{pydantic_schemas}
```

## Already-set values you can reference

{cross_phase_refs}

When all fields are answered, the router advances to memory. Do NOT call set_phase.
"""
```

Per-phase modules are 50-100 lines each. Per-phase logic stays in the phase's own file; nothing global to update when prompts change.

### Why Python functions over Markdown templates

Three reasons we rejected Markdown:
- **Loading complexity** — MD files require bundling as package data; Python imports are immediate.
- **Template engine needed** — schema source code contains curly braces; `str.format()` would mis-parse it. Avoiding Jinja2 keeps the dependency surface tight.
- **Type checks** — Python functions have IDE/mypy support; placeholder renames fail at compile time, not at runtime.

### Phase driver — the single shared module

`dev-kit/dev_kit/agent/phase_driver.py`, ~200 lines:

```python
def run_turn(user_message: str, project_slug: str) -> str:
    intake_state = load_intake_state(project_slug)
    accumulator = load_accumulator(project_slug)
    field_status = load_field_status(project_slug)
    current_phase = load_current_phase(project_slug)
    phase_def = PHASES[current_phase]

    # 1. Filter pending/needs_re_asking fields for THIS phase
    pending_fields = collect_pending_fields(
        phase_id=current_phase,
        intake_state=intake_state,
        field_status=field_status,
    )

    # 2. Resolve Pydantic class closure for those fields
    pydantic_schemas = render_pydantic_classes(pending_fields)

    # 3. Build the prompt
    prompt = phase_def.prompt_fn(
        pending_fields=pending_fields,
        pydantic_schemas=pydantic_schemas,
        cross_phase_refs=cross_phase_references(accumulator),
        intake_state=intake_state,
    )

    # 4. Call LLM
    response, tool_calls = llm.invoke(system=prompt, user=user_message)

    # 5. Process tool calls
    for call in tool_calls:
        if call.name == "update_intake":
            on_intake_update(call.args, intake_state, accumulator, field_status)
        elif call.name == "update_config":
            on_config_update(call.args, accumulator, field_status)
        # ... other tools

    # 6. End-of-turn: maybe transition
    next_phase = router.decide_next_phase(current_phase, intake_state, accumulator, field_status)
    if next_phase != current_phase:
        save_current_phase(project_slug, next_phase)
        if phase_def.on_complete:
            phase_def.on_complete(accumulator)

    return response
```

### Slimmed tool surface

Today's `tools.py` has 20 tools. The new design needs 6 core tools plus 2 utilities:

| Tool | Purpose | Caller |
|---|---|---|
| `update_intake(field, value)` | Mutate `IntakeState` | LLM, intake phase only |
| `update_config(block, section, values)` | Mutate accumulator (Pydantic-validated) | LLM, any phase |
| `add_subagent(definition)` | Add a subagent to workflow | LLM, workflow phase |
| `update_subagent(id, fields)` | Modify a subagent | LLM, workflow phase |
| `add_routing_rule(from, intent, to, condition?)` | Add routing rule | LLM, workflow phase |
| `add_tool(spec)` | Add an action_gateway tool | LLM, tools phase |
| `parse_openapi_spec(spec)` | Utility — parse uploaded OpenAPI | LLM, tools phase |
| `discover_mcp_tools(server_url)` | Utility — list MCP server tools | LLM, tools phase |

Tools removed: `set_phase`, `skip_optional_phase`, `set_agent_type`, `set_project_meta`, `set_reach_channels`, `set_response_transformation`, `declare_azure_storage`, `rollback_to_checkpoint`, `finalize_config`, `set_agent_core_connector`, `update_routing_rule`, `remove_subagent`, plus internal helpers. Most are subsumed by `update_intake` and `update_config`; phase transitions are owned by the router.

## 7. Backtracking & state mutations

### State storage

Three files per project under `dev-kit/configs/<slug>/_meta/`:

```
_meta/
  intake_state.json       # the 11 intake fields + completed flag
  field_status.json       # status per category=chat field
  current_phase.json      # which phase the wizard is in
```

Plus the existing accumulator persisted as `<block>.yaml` files (unchanged).

### `field_status.json` — every chat field gets one entry

```json
{
  "agent_core.preprocessing.nlu_processor.intents": "answered",
  "agent_core.preprocessing.nlu_processor.entities": "answered",
  "agent_core.conversation.blocked_message": "answered",
  "knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name": "not_applicable",
  "agent_core.connectors.internal.knowledge_retrieval": "not_applicable",
  "trust_layer.trust.hitl.holding_message": "answered",
  "...": "..."
}
```

Status values: `pending` | `answered` | `needs_re_asking` | `not_applicable`. The `not_applicable` state is set when the field's `applies_if` expression evaluates false for the current intake state. Predetermined / derived / deploy fields are NOT tracked here — their state is implied elsewhere.

### `on_intake_update` — what happens when intake changes

```python
def on_intake_update(args, intake_state, accumulator, field_status):
    field, new_value = args.field, args.value
    old_value = getattr(intake_state, field)
    if old_value == new_value:
        return {"ok": True, "noop": True}

    # 1. Mutate intake
    setattr(intake_state, field, new_value)
    intake_state.updated_at = now_iso()
    save_intake_state(intake_state)

    # 2. Walk FIELD_RULES; collect affected fields
    affected = [
        (path, rule)
        for path, rule in AGGREGATED_FIELD_RULES.items()
        if field in rule.invalidated_by
    ]

    # 3. Apply effects per category
    earliest_affected_phase = None
    for path, rule in affected:
        if rule.category == "predetermined":
            new_val = eval_rule(rule.rule, intake_state)
            accumulator.set_path(path, new_val)

        elif rule.category == "chat":
            if rule.applies_if and not eval_expr(rule.applies_if, intake_state):
                accumulator.clear_path(path)
                field_status[path] = "not_applicable"
            else:
                if rule.default is not None and field_status.get(path) == "not_applicable":
                    accumulator.set_path(path, rule.default)
                field_status[path] = "needs_re_asking"
                earliest_affected_phase = _earlier_of(earliest_affected_phase, rule.phase)

        elif rule.category == "derived":
            field_status[path] = "derived_stale"   # in-memory only

    save_field_status(field_status)
    save_accumulator(accumulator)

    return {
        "ok": True,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "affected_count": len(affected),
        "earliest_affected_phase": earliest_affected_phase,
    }
```

### End-of-turn router

```python
def decide_next_phase(current_phase, intake_state, accumulator, field_status):
    # 1. Has an earlier phase been invalidated this turn?
    invalidated_phase = _earliest_phase_with_needs_re_asking(field_status)
    if invalidated_phase and _phase_index(invalidated_phase) < _phase_index(current_phase):
        return invalidated_phase

    # 2. Is current phase complete?
    if is_phase_complete(current_phase, intake_state, accumulator, field_status):
        return _next_relevant_phase(current_phase, intake_state)

    # 3. Stay put
    return current_phase
```

`_next_relevant_phase` walks `PHASES` in order, skipping phases where `is_relevant(intake_state)` is False or where the phase has no chat fields.

`is_phase_complete` returns true iff every field with `category=="chat" AND phase==X AND applies_if(intake_state)` has status `answered`.

### Worked example — mid-conversation KB addition

**Setup:** User builds a transactional bot, intake captured `has_kb=false`. Wizard ran intake → language → memory → trust → tools → workflow. Currently in **workflow** defining first subagent.

**Turn N — user says "Actually wait — we do have a small FAQ doc the bot should reference."**

1. LLM detects intent → calls `update_intake(field="has_kb", value=True)`.
2. Tool handler:
   - `IntakeState.has_kb = True`
   - Walks FIELD_RULES; affected fields:
     - `agent_core.preprocessing.nlu_processor.intents` (chat) → `needs_re_asking`
     - `agent_core.preprocessing.nlu_processor.entities` (chat) → `needs_re_asking`
     - `knowledge_engine.knowledge.blocks.static_knowledge_base.enabled` (predetermined) → re-run rule → True
     - `knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name` (predetermined) → re-run → slug-derived value
     - `knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters` (chat) → was `not_applicable`, now `needs_re_asking`
     - `knowledge_engine.knowledge.blocks.static_knowledge_base.default_doc_type` (chat) → `not_applicable` → `needs_re_asking`
     - `agent_core.connectors.internal.knowledge_retrieval` (predetermined) → skeleton structure written
     - `agent_core.agent_workflow.global_tools` (derived) → `derived_stale`
   - Returns `{ "earliest_affected_phase": "language", "affected_count": 8 }`.
3. LLM writes user-facing reply: "Got it — adding a knowledge base. I'll need to revisit a couple of earlier questions to get the KB wired up properly."
4. End-of-turn router:
   - Earliest phase with `needs_re_asking` field = `language`.
   - `_phase_index("language") < _phase_index("workflow")` → switch to `language`.
   - Save `current_phase = "language"`.

**Turn N+1 — user sends next message (could be any reply, e.g., "okay, what do you need?")**

1. Driver loads `current_phase = "language"`.
2. Filters fields: 2 in language phase have status `needs_re_asking`:
   - `nlu_processor.intents`
   - `nlu_processor.entities`
3. Driver calls `language.build(...)` with these 2 fields.
4. Prompt template renders:

   ```
   ## Fields to capture this phase

   - `nlu_processor.intents` (list[str]) — Domain intents the NLU recognises.
     CURRENT VALUE (needs review because `has_kb` just changed to true):
     ["unknown", "order_status", "shipping_question"]. Consider adding an intent
     that triggers the new knowledge retrieval (e.g. "lookup", "faq").
   - `nlu_processor.entities` (list[str]) — Entities extracted by NLU.
     CURRENT VALUE: ["order_id", "city"]. Review if any new entity is implied
     by the KB content.
   ```

5. LLM receives this + user's message; asks: "With the KB you just added, I should add an intent for it. How about `faq_lookup`?"
6. User confirms.
7. LLM calls `update_config(block="agent_core", section="preprocessing.nlu_processor", values={"intents": [...new list...]})`.
8. Tool validates against Pydantic, merges, sets status `answered`.
9. End-of-turn router: still 1 field re-asking in language phase → stay put. Next turn covers entities.
10. After both answered, language phase complete. Router walks forward:
    - `knowledge` is relevant (has_kb=true) and has `needs_re_asking` → go there.
    - User answers `default_doc_type` and `intent_filters` for the new intent.
    - Knowledge phase complete.
    - Router walks `memory` (complete), `user_state` (irrelevant — transactional, skip), `trust` (complete), `tools` (irrelevant — has_external_tools=false, skip), `workflow` (has incomplete fields).
11. User is back where they were in workflow.

Re-asking happens **passively** — the driver naturally filters fields each turn based on status. There's no explicit "re-ask this list now" loop. The LLM never sees "needs_re_asking" or "answered" — those are internal status flags. It sees the prompt the driver built, which lists exactly the fields that need attention this turn.

### Three guardrails

1. **Pydantic validation on `update_config`** — every typed call is gated.
2. **`is_phase_complete` requires all relevant `chat` fields answered** — deploy is blocked otherwise.
3. **Pre-deploy dry-run** — at deploy time the patched YAML is validated through the runtime's own Pydantic schemas before bind-mount.

## 8. Skeleton, renderer, selective deployment

### `build_skeleton()`

Pure function at `dev-kit/dev_kit/agent/skeleton.py`. Runs when intake completes (or any time intake materially changes — idempotent for already-set values).

```python
def build_skeleton(intake_state: IntakeState) -> tuple[dict[str, dict], dict[str, str]]:
    """Walk FIELD_RULES, produce valid-by-default accumulator + initial field statuses."""
    accumulator = {block: {} for block in BLOCKS}
    field_status: dict[str, str] = {}

    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.applies_if and not eval_expr(rule.applies_if, intake_state):
            if rule.category == "chat":
                field_status[path] = "not_applicable"
            continue

        if rule.category == "predetermined":
            value = eval_rule(rule.rule, intake_state)
            set_path(accumulator, path, value)

        elif rule.category == "chat":
            if rule.default is not None:
                set_path(accumulator, path, rule.default)
            field_status[path] = "pending"

        elif rule.category == "deploy":
            if rule.default is not None:
                set_path(accumulator, path, rule.default)

        elif rule.category == "derived":
            pass  # renderer computes at write time

    return accumulator, field_status
```

Output is a complete-by-default config dict that validates against every block's runtime Pydantic schema (enforced by CI test), has every required field present with either a real value or a sentinel, and lists every chat field's initial status.

### Renderer

`dev-kit/dev_kit/agent/renderer.py`. Adds two passes to the existing implementation:

```python
def render_all(project_path, accumulator, intake_state):
    # 1. Apply deploy overlay (provider/model/voice_id/runtime tuning from deploy_settings.json)
    overlaid = apply_deploy_overlay(accumulator, load_deploy_settings(project_path))

    # 2. Compute derived fields
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category == "derived":
            value = eval_compute(rule.compute, overlaid, intake_state)
            set_path(overlaid, path, value)

    # 3. Validate against Pydantic + dev-kit cross-block invariants
    for block, data in overlaid.items():
        validate_partial(block, data)
    validate_cross_block_invariants(overlaid)

    # 4. Pre-deploy dry-run — validate through runtime's own schemas
    for block, data in overlaid.items():
        runtime_validate(block, data)   # imports runtime's MergedConfig, calls model_validate

    # 5. Write YAML files
    for block, data in overlaid.items():
        write_yaml(project_path / f"{block}.yaml", data)
```

Step 4 is the key correctness guarantee — if the runtime would crash on this YAML, the dev-kit knows before mounting.

### Selective deployment

The compose generator reads `intake_state` and:

- **Always include**: agent_core, trust_layer, memory_layer, reach_layer_web, observability_layer, redis, memgraph, jaeger, loki, prometheus, grafana, otel_collector
- **Include if `voice in selected_channels`**: reach_layer_voice, ngrok
- **Include if `has_kb`**: knowledge_engine
- **Include if `has_external_tools`**: action_gateway

For omitted services:
- Their `depends_on` references are stripped from other services in the compose file.
- Their config YAML file is still written with sentinel content — agent_core's HTTP clients gracefully no-op if KE/AG aren't reachable, so no behavioural change for the running services.

A no-KB / no-tools poem bot deploys **9 services** instead of **12**.

## 9. Migration & backward compatibility

### Files & line counts

| Today | New design |
|---|---|
| `prompts/phases.py` (~1400 lines, all phases as concatenated strings) | `phase_prompts/<phase>.py` × 11 files (~50-100 lines each) + `phases_config.py` (~200 lines) |
| `prompts/base.py` (`build_system_prompt`) | Replaced by `phase_driver.py` (~200 lines) |
| `tools.py` (20 tools, ~1000 lines) | Trimmed to 8 tools, ~300 lines |
| `accumulator.py` (config dict + ConfigStatus enum) | Kept; add `field_status` dict + helpers |
| `schemas/domain/*.py` (Pydantic models) | Kept; lightly tightened where the audit doc flagged gaps |
| `conversation.py` (turn handler) | Kept; updated to call `phase_driver.run_turn()` |
| `renderer.py` (YAML writer) | Kept; add derived-field pass + runtime dry-run |
| `deployer/compose.py` | Updated for intake-driven service inclusion |
| **New**: `intake_state.py` (~100 lines) | |
| **New**: `field_rules/*.py` × 7 files (~700-900 lines total) | |
| **New**: `router.py` (~200 lines) | |
| **New**: `skeleton.py` (~100 lines) | |

Net: ~+1700 lines new, ~-2500 lines deleted/rewritten, net **–800 lines** with much higher determinism and testability.

### Existing projects

Existing projects under `dev-kit/configs/<slug>/` were authored without intake or field_status. When the wizard opens an existing project:

1. **If `_meta/intake_state.json` is missing**, reverse-engineer intake values from the existing YAML files (e.g., `has_kb = (knowledge_engine.knowledge.blocks.static_knowledge_base.enabled == True)`). Write the result to `intake_state.json` as if intake just completed.
2. **If `_meta/field_status.json` is missing**, mark every chat field as `answered` (best effort — trusts what's on disk). User can edit individual fields through the YAML editor or re-open phases to revise.
3. Existing projects load with `current_phase = "review"` and the user can selectively re-enter a phase to modify.

This gives existing projects a smooth upgrade path without forcing a regenerate.

## 10. Testing approach

### Per-block tests

- **`test_field_rules_<block>.py`**: every Pydantic field in the runtime `MergedConfig` schema has a corresponding `FIELD_RULES` entry (or is in the `not_exposed_intentionally` allowlist).
- **`test_skeleton_validates.py`**: for every combination of `(agent_type, has_kb, has_external_tools, ...)`, the skeleton output validates against the runtime's Pydantic schemas.
- **`test_intake_updates.py`**: changing each intake field marks the documented affected fields as `needs_re_asking` / re-runs predetermined rules / flags derived fields.

### End-to-end tests

- **`test_wizard_flow.py`**: simulates a full conversation through the wizard for each `agent_type`. Asserts the final YAML matches a golden file for a fixed input transcript.
- **`test_backtracking.py`**: simulates the "user changes their mind mid-conversation" cases and asserts the router lands in the correct phase.

### Coverage gate

CI fails if a new Pydantic field is added without a corresponding `FIELD_RULES` entry or allowlist entry.

## 11. Open questions / future work

1. **Per-language sentinels**: should the skeleton seed English defaults for conversation messages, with the language phase translating them, or should rules support per-language defaults? Decision deferred to implementation.
2. **`validate_workflow_graph` and `validate_cross_block_invariants` hooks**: list of invariants to enforce in these hooks is in the existing audit doc at `docs/superpowers/specs/2026-05-13-runtime-schema-prompt-audit.md`.
3. **LLM token cost**: per-phase Pydantic schema injection is smaller than today's "show all 7 blocks" pattern. Should be a net token reduction.
4. **Voice ID preview audio**: deploy form needs to support voice sample preview MP3s — covered in the dev-kit UI revamp design, not this design.
5. **Multimodal Input Handler**: KE PoC block, default disabled. Out of scope for this design.

## 12. Cross-references

- **Schema gaps and prompt issues per block**: `docs/superpowers/specs/2026-05-13-runtime-schema-prompt-audit.md` enumerates field-level issues and concrete fixes.
- **Deploy-time overlay & UI revamp**: `docs/superpowers/specs/2026-05-12-devkit-ui-revamp-design.md`.
- **Earlier (now superseded) plan**: `docs/superpowers/specs/2026-05-13-devkit-config-generation-revamp-design.md` describes the LLM-with-skeleton model. This document extends it with the deterministic state machine.
