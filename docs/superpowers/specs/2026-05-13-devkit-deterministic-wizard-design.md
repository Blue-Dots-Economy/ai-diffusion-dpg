# Dev-Kit Deterministic Wizard — Design

**Status:** Draft awaiting review
**Date:** 2026-05-13
**Supersedes (in spirit):** `docs/superpowers/specs/2026-05-13-devkit-config-generation-revamp-design.md`. That earlier doc proposed an LLM-with-skeleton model; this one is a more deterministic re-architecture using the same principles plus an explicit state machine.

## 1. Problem

The current dev-kit wizard is LLM-driven end to end. The LLM decides what to ask, when to advance phases, and what fields to write. This produces three classes of failure:

1. **Pre-deploy validation errors** — required fields the LLM forgot (`trust:` key missing, `input_schema` empty, `user_node` partial, etc.).
2. **Runtime crashes** — fields the dev-kit schema accepts but the runtime silently drops or requires (`tool_registry.py:155` silent-drop class of bugs). Today's validation only goes through dev-kit's own hand-written mirror schemas at `dev-kit/dev_kit/schemas/`; it never imports the runtime block's actual Pydantic class (e.g., `agent_core/src/schema/config.py`) to dry-run the generated YAML. Whenever the mirror drifts from the runtime schema, the wizard produces YAML that passes dev-kit validation but the runtime rejects at boot — and the user only finds out when the container fails to start.
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
│  ┌──────────────────┐    ┌─────────────────────────────────────┐   │
│  │  PHASE DRIVER    │◀──▶│             ROUTER                  │   │
│  │  - reads PHASES  │    │  - on update_intake → mark fields   │   │
│  │  - calls LLM     │    │    invalidated using FIELD_RULES    │   │
│  │  - parses tool   │    │  - decides next phase at end-of-    │   │
│  │    calls         │    │    turn                             │   │
│  └──────────────────┘    └─────────────────────────────────────┘   │
│         │                                                          │
│         ▼                                                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │           ACCUMULATOR  (current YAML state per block)        │  │
│  │  - holds skeleton + answered fields + invalidations          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│         │                                                          │
│         ▼ at deploy time                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  RENDERER  → applies derived fields + deploy overlay → YAML  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       docker-compose bind-mount
```

### Five new top-level constructs

| Component | Lives at | What it does |
|---|---|---|
| `IntakeState` | `dev-kit/dev_kit/agent/intake_state.py` | Typed dataclass; 12 fields captured in the intake phase (5 via project creation form, 7 binary flags via chat); persisted to `_meta/intake_state.json` |
| `FIELD_RULES` | `dev-kit/dev_kit/agent/field_rules/<block>.py` | Per-field rules: category, phase, default, invalidation triggers, applies_if expressions, derived-compute expressions |
| `PHASES` | `dev-kit/dev_kit/agent/phases_config.py` | Declarative phase definitions: id, label, prompt function, next-phase pointer, is_relevant predicate |
| Phase driver | `dev-kit/dev_kit/agent/phase_driver.py` | Single shared module that runs all phases; filters fields, builds prompts, calls LLM, processes tool calls |
| Router | `dev-kit/dev_kit/agent/router.py` | On `update_intake`: walks FIELD_RULES, marks invalidations, recomputes predetermined values. At end-of-turn: decides next phase |

### Framework defaults vs domain YAML — the split

Each runtime block reads its config from **two files** at startup, deep-merged in this order:

1. `dev-kit/dpg/<block>.yaml` — framework defaults. Identical for every project. Contains all operational tuning (timeouts, retry attempts, embedding provider, otel collector endpoint, server host/port, etc.) and the safe defaults for opt-in features (e.g., `dignity_check.enabled: false`).
2. `dev-kit/configs/<slug>/<block>.yaml` — domain-specific overrides. **Should be 100% domain-specific.** The skeleton writes here; the LLM writes here; nothing else.

**Anything that's identical across all projects belongs in `dpg/<block>.yaml`, NOT in the domain YAML.** The wizard does NOT touch `dpg/<block>.yaml` — it's framework state, edited only by framework maintainers.

Concrete evidence from `dev-kit/dpg/trust_layer.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8003
observability:
  otel:
    collector_endpoint: "http://otelcol:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
dignity_check:
  enabled: false        # opt-in; flipped to true in domain YAML for Conversational
  questions: []
  fail_action: "rewrite"
```

And from `dev-kit/dpg/agent_core.yaml` (abridged — the full file also sets `ask_for_consent`, `consent_prompt`, `features`, etc.):

```yaml
agent:
  timeout_ms: 10000
  retry_attempts: 2
  retry_backoff_seconds: [0, 0.5, 1.0]
  max_tool_rounds: 3
  provider: anthropic    # default; deploy overlay may override
  termination_short_circuit: {enabled: true, confidence_threshold: 0.7}
  recent_tool_exchanges: {max_items: 3, max_chars: 4000}
ke_client: {endpoint: "http://knowledge_engine:8001/retrieve", timeout_ms: 30000}
memory_client: {endpoint: "http://memory_layer:8002", timeout_ms: 3000}
```

None of these belong in a domain YAML. The skeleton will not emit them.

Existing configs sometimes carry fields that belong in the framework layer:

- **Truly redundant** — value equals the Pydantic default. Example: a project's `trust_layer.yaml` setting `trust.hitl.queue_backend: log` when `log` is already the Pydantic default. Pure noise; remove.
- **Operational override of a Pydantic default that ought to live in `dpg/<block>.yaml`** — value differs from the Pydantic default but is operational (same across projects). Example: `kkb/knowledge_engine.yaml` sets `chroma_persist_dir: /app/chroma_db`, which differs from the Pydantic default `./data/chroma_db` but is the Docker volume path used by every project. The fix is to move the override to `dpg/knowledge_engine.yaml` and remove it from domain YAML.

Both classes are legacy quirks from before the dpg/domain split was clean. Going forward, the skeleton will not emit any field whose value equals the corresponding dpg.yaml-merged-with-Pydantic default — a "no redundancy" rule enforced by CI. The operational-overrides class is handled by relocating those fields to `dpg/<block>.yaml` once and for all (separate cleanup; not in scope for the wizard itself).

### What `FIELD_RULES` covers

`FIELD_RULES` covers **only fields that legitimately appear in the domain YAML**:

- Predetermined fields whose value depends on intake state (e.g., `dignity_check.enabled` flips to true for Conversational — overrides the dpg.yaml default of false)
- Slug-derived fields (e.g., `observability.domain`, `static_knowledge_base.collection_name`)
- Chat fields the LLM writes (e.g., `trust.input_rules.blocked_phrases`, intents, subagents)
- Chat fields with `deploy_overridable=true` — written to domain YAML by chat but surfaced (pre-filled, editable) by the deploy form; the deploy overlay applies any operator change at write time. Examples: `agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id`. See the FIELD_RULES catalogue for the full list.
- Pure deploy fields (e.g., `trust.hitl.queue_backend` — no useful chat default; operational only, asked only on the deploy form)
- Derived fields (e.g., `agent_workflow.global_tools` computed from connectors)

Pure framework defaults like `agent.timeout_ms: 10000`, `ke_client.endpoint: "http://..."`, `knowledge_engine.server.port: 8001` are NOT in `FIELD_RULES`. They're not the wizard's concern — they live in `dpg/<block>.yaml`.

### Pydantic schemas — single source of truth used in four places

| Use | How |
|---|---|
| LLM sees the schema | Each phase's prompt inlines the Pydantic class source for the fields it's asking. Uses `_collect_referenced_models()` to walk the full transitive closure of nested classes (e.g., asking about `trust.input_rules.blocked_phrases` injects `TrustSection`, `InputRulesConfig`, etc.). |
| Skeleton construction | `build_skeleton()` reads Pydantic field defaults, applies `predetermined` rules, fills `chat` defaults — produces a valid-by-default YAML. |
| `update_config` validation | Every call goes through `validate_partial(block, partial)` — the Pydantic validator gates every write. |
| Pre-deploy dry-run | At write time, the renderer imports each runtime block's `MergedConfig` Pydantic class and calls `model_validate()` on the rendered YAML — catches anything the dev-kit schema accepted but the runtime would crash on. |

### Two Pydantic schema sets: dev-kit vs runtime

The codebase already maintains **two parallel Pydantic schema sets** today, and this design keeps both. They describe different things and run at different times.

**1. Dev-kit schemas** at `dev-kit/dev_kit/schemas/`:

- `dpg/<block>.py` — shape of the framework-default `dpg/<block>.yaml`
- `domain/<block>.py` — shape of the domain `configs/<slug>/<block>.yaml` (what `update_config` validates)

These are the **wizard's working schemas**. They power: LLM prompt schema injection, `build_skeleton`, `update_config` validation, and the `pydantic_class` lookup in `FIELD_RULES`. They are deliberately **lenient about mid-chat states** — for example, they may accept a sentinel like `"[NEEDS_TRANSLATION]"` or an empty list while the user is still filling in earlier phases.

**2. Runtime block schemas** owned by each block. Every block exposes a top-level `MergedConfig` Pydantic class at `<block>/src/schema/config.py`, except Reach Layer which lives at `reach_layer/base/schema/config.py`:

- `agent_core/src/schema/config.py` — `MergedConfig`
- `trust_layer/src/schema/config.py` — `MergedConfig`
- `knowledge_engine/src/schema/config.py` — `MergedConfig`
- `action_gateway/src/schema/config.py` — `MergedConfig`
- `memory_layer/src/schema/config.py` — `MergedConfig`
- `observability_layer/src/schema/config.py` — `MergedConfig`
- `reach_layer/base/schema/config.py` — `MergedConfig`

(Note: each block also has a `src/models.py`, but those hold HTTP request/response dataclasses, **not** the config schema. The config schema is always under `src/schema/config.py`.)

These define what each running service will accept at boot. They are **strict** — they validate the *fully-merged* config (`dpg/<block>.yaml` deep-merged with `configs/<slug>/<block>.yaml`) and reject sentinels.

**Which is used where:**

| Stage | Schema set used | Why |
|---|---|---|
| LLM prompt injection (every phase) | dev-kit `schemas/domain/<block>.py` | LLM only ever writes domain-specific values |
| `update_config` tool validation during chat | dev-kit `schemas/domain/<block>.py` | Permits in-progress sentinels |
| `build_skeleton()` reads field defaults | dev-kit `schemas/domain/<block>.py` | Domain-YAML shape only |
| Pre-deploy dry-run (`renderer.py` step 4) | **Runtime block's own** schema | Final hard gate: would the running service actually accept this? |

**Why two schema sets instead of one shared import:**

- **Different strictness levels.** The dev-kit chat schema may legitimately accept sentinels and partial states the LLM hasn't filled yet. The runtime schema is strict — every required field must be real.
- **Different shapes.** Dev-kit's `schemas/domain/` describes the *domain half* of the config. The runtime's schema describes the *merged result* (framework defaults + domain overrides). Sharing one class would force one of those to be wrong.
- **Direction of dependency.** Importing runtime schemas into the dev-kit at chat time would couple the wizard to every runtime block's internal model — a tight coupling we want only at deploy time, not at every keystroke.

**How the two stay in sync — CI guard:**

A test walks every Pydantic field in the runtime block's `MergedConfig` schema and asserts each field is either:

- represented in the dev-kit's `FIELD_RULES` (so the wizard knows how to fill it), or
- on a `framework_default_only` allowlist (so it's intentionally only in `dpg/<block>.yaml`)

This catches the case where a runtime block adds a new required field without telling the dev-kit. See §5 *CI guards* and §10 *Coverage gate*.

**Why the dry-run matters:**

The dry-run at deploy time is the bridge between the two schema sets. The dev-kit can be as lenient as it likes during chat; the dry-run still imports the runtime's own `MergedConfig` and refuses to deploy a YAML the runtime would crash on. So the wizard can never produce a config that breaks a running service — the runtime schema is the final source of truth at deploy time.

**Today vs new design (this is the gap):**

Today's validation pipeline (`dev-kit/dev_kit/schemas/validation.py`) only uses the dev-kit's mirror schemas: `validate_partial`, `validate_domain_section`, and `validate_dpg_block` all dispatch into `DOMAIN_SECTION_SCHEMAS` and `DPG_BLOCK_SCHEMAS`, both of which are populated entirely from `dev-kit/dev_kit/schemas/`. A `grep` for imports from `agent_core/src`, `trust_layer/src`, `knowledge_engine/src`, etc. inside the dev-kit returns zero results. The runtime block's own Pydantic classes are never loaded.

The new design adds the runtime-schema dry-run as a hard step in `render_all` (see §8 step 4). The dev-kit container has no access to runtime block source at runtime — its Docker image only `COPY`s `dev-kit/` and `automation/` (see `dev-kit/Dockerfile`). So a subprocess-into-the-block-directory approach doesn't work in production. Instead, the dry-run uses **schemas baked into the dev-kit image at Docker build time**.

**Why bake-in works here:**

Every runtime block's `src/schema/config.py` is **self-contained** — it only imports from `pydantic`, `enum`, `typing`, and `__future__`. No relative imports, no other block-internal modules. This makes each schema file safe to copy verbatim into the dev-kit image; once copied, the dev-kit can import it as a normal Python module.

**dev-kit Dockerfile additions:**

```dockerfile
# After "COPY dev-kit/ ." in dev-kit/Dockerfile, add:
COPY agent_core/src/schema/config.py           /app/dpg_runtime_schemas/agent_core/config.py
COPY trust_layer/src/schema/config.py          /app/dpg_runtime_schemas/trust_layer/config.py
COPY knowledge_engine/src/schema/config.py     /app/dpg_runtime_schemas/knowledge_engine/config.py
COPY action_gateway/src/schema/config.py       /app/dpg_runtime_schemas/action_gateway/config.py
COPY memory_layer/src/schema/config.py         /app/dpg_runtime_schemas/memory_layer/config.py
COPY observability_layer/src/schema/config.py  /app/dpg_runtime_schemas/observability_layer/config.py
COPY reach_layer/base/schema/config.py         /app/dpg_runtime_schemas/reach_layer/config.py
RUN find /app/dpg_runtime_schemas -type d -exec touch {}/__init__.py \;
```

The Docker build context is already the repo root (`../..` per `automation/docker/docker-compose.yml`), so the source paths above resolve correctly.

**Renderer code:**

```python
# dev-kit/dev_kit/agent/renderer.py
from dpg_runtime_schemas.agent_core.config          import MergedConfig as AgentCoreMergedConfig
from dpg_runtime_schemas.trust_layer.config         import MergedConfig as TrustLayerMergedConfig
from dpg_runtime_schemas.knowledge_engine.config    import MergedConfig as KnowledgeEngineMergedConfig
from dpg_runtime_schemas.action_gateway.config      import MergedConfig as ActionGatewayMergedConfig
from dpg_runtime_schemas.memory_layer.config        import MergedConfig as MemoryLayerMergedConfig
from dpg_runtime_schemas.observability_layer.config import MergedConfig as ObservabilityLayerMergedConfig
from dpg_runtime_schemas.reach_layer.config         import MergedConfig as ReachLayerMergedConfig

RUNTIME_SCHEMAS = {
    "agent_core":          AgentCoreMergedConfig,
    "trust_layer":         TrustLayerMergedConfig,
    "knowledge_engine":    KnowledgeEngineMergedConfig,
    "action_gateway":      ActionGatewayMergedConfig,
    "memory_layer":        MemoryLayerMergedConfig,
    "observability_layer": ObservabilityLayerMergedConfig,
    "reach_layer":         ReachLayerMergedConfig,
}

def runtime_validate(block: str, data: dict) -> None:
    """In-process validation against the runtime block's own MergedConfig.

    Raises pydantic.ValidationError on failure; the renderer surfaces this
    to the wizard so the user sees the offending field before deploy.
    """
    RUNTIME_SCHEMAS[block].model_validate(data)
```

In-process, sub-millisecond per block, no subprocesses, no network dependency.

**Why bake-in over the alternatives:**

| Alternative | Why we rejected it |
|---|---|
| Subprocess into the block's directory | Dev-kit container has no access to other blocks' source at runtime |
| Fetch schemas from `raw.githubusercontent.com` at deploy | Network dependency at deploy; token/rate-limit issues; version-skew risk between fetched ref and deployed image |
| Shared `dpg-schemas` package | Real refactor across 7 blocks; bigger blast radius than the bake-in |

**Build-time coordination is the guarantee.**

The release process already builds all images at the same `${GIT_SHA}` (per `automation/docker/docker-compose.yml`). When `agent_core/src/schema/config.py` changes, both the agent_core image and the dev-kit image are rebuilt; the schema baked into the dev-kit always matches the schema the deployed agent_core container will use. The bake-in only works because of this existing discipline.

**Mirror schemas are not affected.**

The dev-kit's mirror schemas at `dev-kit/dev_kit/schemas/domain/` are kept as-is — they continue to power LLM prompt injection, `update_config` validation during chat, skeleton defaults, and `pydantic_class` lookup. The bake-in adds a **separate** in-process source used only by the pre-deploy dry-run. The two schema sets remain decoupled (the mirror is intentionally lenient and shaped by FIELD_RULES + IntakeState; the runtime schema is strict and orthogonal). See "Two Pydantic schema sets" above.

**Pre-condition for bake-in.**

Every `<block>/src/schema/config.py` must stay self-contained — only `pydantic`, `enum`, `typing`, `__future__` imports. A future CI guard will enforce this at PR time (see §5). Until then, the project-level Claude rule at `.claude/rules/runtime-devkit-sync.md` documents the requirement.

This is the change that closes the "passes dev-kit, crashes runtime" failure mode #2 from §1.

### Three things the system does NOT do

- **LLM does not call `set_phase`.** Today's `set_phase` tool is removed. The router decides phase transitions based on state.
- **LLM does not see `FIELD_RULES`.** It sees the prompt the phase driver builds from the rules. Effects of `update_intake` calls are summarised in the tool's return value.
- **LLM does not freely invent fields.** Every `update_config` and `update_intake` call is type-validated against the rule for that path. Invalid paths or values return an error to the LLM with the offending field path.

## 4. Intake state & the new intake phase

### Replacement for today's "tier" phase

Today's tier phase asks up to 4 yes/no questions in a branching decision tree (typically 2–3 questions per user) to set `agent_type` (one of `transactional` / `informational` / `agentic` / `conversational`). **The new intake phase drops `agent_type` entirely** and replaces it with orthogonal capability + conversation-pattern flags. See "Why we removed `agent_type`" below for the rationale.

The new intake phase captures 12 typed fields up front. This produces the `IntakeState` that every downstream phase reads from for branching decisions.

### `IntakeState` shape

```python
from dataclasses import dataclass
from typing import Literal

Channel = Literal["web", "voice"]   # CLI channel intentionally excluded — see note below

@dataclass
class IntakeState:
    # ── Capabilities (what the agent has access to)
    has_kb: bool                          # → deploy KE, enable static_knowledge_base, add knowledge_retrieval connector
    has_external_tools: bool              # → deploy AG, ask Tools phase, connectors.read/write

    # ── Conversation pattern (how the agent talks to users)
    is_multi_turn: bool                   # multi-turn dialogue vs single-shot Q&A
                                          # → drives workflow phase complexity, session TTL relevance
    needs_persistent_user_data: bool      # remembers user across sessions
                                          # → memory_layer.state.persistent populated; default_mode=saved
    is_companion_style: bool              # emotionally-sensitive, dignity-aware long-running conversation
                                          # → dignity_check.enabled, user_state_model.enabled

    # ── Operational
    needs_consent: bool                   # → agent.ask_for_consent, consent flow in conversation messages
    has_hitl: bool                        # → meaningful trust HITL fields vs sentinels

    # ── Channels and languages
    selected_channels: list[Channel]      # → which channels.<x> entries; deploy form credentials
    default_language: str                 # → language_normalisation.default_language
    supported_languages: list[str]        # → multiplexing of messages and TTS rules

    # ── Context (LLM-only)
    domain_description: str               # used as LLM context in every phase prompt
    project_name: str                     # drives slug, observability domain, KE collection name

    # Bookkeeping
    completed: bool = False
    updated_at: str = ""
```

12 typed fields. No `agent_type` enum.

**CLI channel deprecation.** The runtime today still has a `cli` channel in `ChannelsConfig` (`agent_core/src/schema/config.py:599-606`, `reach_layer/base/schema/config.py:346-353`), but it is dev-only — never user-selectable — and is being phased out. This design supports only `web` and `voice`. The skeleton continues to emit the framework-default `cli` block (so existing tests and developer CLI workflows keep working) but `selected_channels` never includes `cli`, no FIELD_RULES entry exists for it, and the project creation form does not offer it as a choice.

### Why we removed `agent_type`

Today's framework uses a 4-way `agent_type` classification (`transactional` / `informational` / `agentic` / `conversational`). Each project picks one. The wizard uses it to gate phase skipping (`user_state` is conversational-only) and toggle features (`dignity_check.enabled`). However the boundaries between the 4 categories are leaky in practice:

- **`transactional`** is defined as "takes an action — API call / form submission" — that's just `has_external_tools=true`. It says nothing about whether the workflow has a clear end state or is single vs multi-turn. A transactional bot may have a KB; a transactional bot may run for 20 turns gathering inputs.
- **`informational`** is defined as "answers questions from a defined knowledge source" — but real informational bots also call external tools (kkb is technically informational AND has the ONEST job lookup). The boundary is fuzzy.
- **`agentic` / `automation`** is "multi-step decision-making with tools" — but multi-step is true for almost any agent except trivial single-tool calls.
- **`conversational`** bundles three independent attributes (multi-turn + persistent-user-data + emotional-dignity-aware) into one label. A career counsellor and a mental-health companion are both `conversational` today but need different behaviour.

Picking one bucket forces the user to fit their bot into a pre-defined shape that may not match what they're actually building. The new design captures the same dimensions as **orthogonal flags**, so any combination is expressible:

| Today's `agent_type` | What it actually encoded | Replaced by |
|---|---|---|
| `transactional` | `has_external_tools=true` | `has_external_tools` |
| `informational` | `has_kb=true` AND typically `has_external_tools=false` | `has_kb` (and `has_external_tools` separately) |
| `agentic` / `automation` | `has_external_tools=true` AND multi-step workflow | `has_external_tools=true`, `is_multi_turn=true` |
| `conversational` | multi-turn + persistent + emotional | `is_multi_turn=true`, `needs_persistent_user_data=true`, `is_companion_style=true` |

Behaviours that today key off `agent_type`:

| Old check | New check |
|---|---|
| `dignity_check.enabled = (agent_type == "conversational")` | `dignity_check.enabled = is_companion_style` |
| `user_state_model.enabled = (agent_type == "conversational")` | `user_state_model.enabled = is_companion_style` |
| `user_state` phase relevant only when `agent_type == "conversational"` | relevant only when `is_companion_style=true` |
| `needs_persistent_user_data` auto-true for conversational | user answers the flag directly; no auto-derivation needed |
| Workflow phase prompt framing (transactional vs automation vs conversational) | derived from `(has_external_tools, is_multi_turn)` combination |

Every behaviour that read `agent_type` has a clean flag-based replacement. The flags are stricter (no fuzzy buckets) and more expressive (any combination is valid).

### Why each field lives in intake

| Field | Why in intake | What it decides downstream |
|---|---|---|
| `has_kb` | Decides "ask the knowledge phase or skip it". Drives whether the `knowledge_retrieval` connector exists, which the workflow phase needs to know to wire subagents. | Deploy KE yes/no; `static_knowledge_base.enabled`; `connectors.internal[name=knowledge_retrieval]`; whether knowledge phase runs; `agent_workflow.global_tools` inclusion |
| `has_external_tools` | The tools phase only runs if this is true; workflow phase needs to know which tools subagents can use. | Deploy AG yes/no; whether tools phase runs; `connectors.read/write/identity`; subagent tool wiring |
| `is_multi_turn` | Affects workflow phase prompt framing (single happy path vs multi-step orchestration) and session TTL relevance. Single-shot Q&A bots don't need session state at all. | Workflow phase prompt framing; subagent count expectations; session TTL setting |
| `needs_persistent_user_data` | The memory phase asks different questions depending on this. Knowing upfront avoids the dead-end branch. | `memory_layer.state.persistent` populated or `null`; `user_data_persistence.default_mode` = saved or anonymous |
| `is_companion_style` | Captures the "dignity-aware long-running conversation" bundle (the meaningful part of today's `conversational` type). Drives dignity check and user state model — both conversational-bot-specific. | `dignity_check.enabled`; `user_state_model.enabled`; `user_state` phase relevance |
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
        "has_kb", "has_external_tools",
        "is_multi_turn", "needs_persistent_user_data", "is_companion_style",
        "needs_consent", "has_hitl",
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

### What's captured before chat (project creation form)

5 of the 12 fields don't fit a yes/no chat conversation — they're better as form inputs. The project creation form (already part of the dev-kit UI today, extended) captures these before chat starts:

| Field | Form input |
|---|---|
| `project_name` | text |
| `domain_description` | textarea (1–2 sentences) |
| `selected_channels` | multi-select checkboxes: web, voice |
| `default_language` | dropdown (english, hindi, etc.) |
| `supported_languages` | multi-select picker |

When the form is submitted, the dev-kit calls `update_intake(field=..., value=...)` server-side for each of these 5 fields. Chat starts with them already populated. `IntakeState.completed` stays false because the 7 binary flags haven't been captured yet.

**Replaces today's `overview` phase.** Today the wizard has 12 phases — the first one (`overview`, defined in `dev-kit/dev_kit/agent/prompts/phases.py`) captures problem statement, target users, languages, channels, and knowledge domain via chat. In the new design that content moves to the project creation form (the 5 fields above), so the chat starts with this context already on disk and the `overview` phase is removed. The new design has 11 chat phases: the project form replaces phase 1; phases 2–12 become the 11 entries in `PHASES`.

### Chat intake — 4 yes/no turns

The chat intake phase asks the remaining 7 binary flags in **4 turns**. Each turn has a clear theme. Follow-up questions in turn 3 are conditional on the answer to `is_multi_turn`.

**Turn 1 — Knowledge.** Single yes/no.

> "Does your agent need to answer questions from a knowledge base (reference docs / FAQ / domain content)?"

→ captures `has_kb`

**Turn 2 — External tools.** Single yes/no.

> "Does your agent need to call external APIs or services? (Looking up jobs, placing orders, fetching weather, etc.)"

→ captures `has_external_tools`

**Turn 3 — Conversation style.** Yes/no plus 2 conditional follow-ups.

> "Is this a multi-turn back-and-forth conversation, or single Q&A?"

→ captures `is_multi_turn`. If yes, the LLM follows up in the same turn:

> "Two quick follow-ups since it's multi-turn:
>  1. Should it remember users across sessions (pick up where they left off next time)?
>  2. Is this a sensitive companion bot (mental health, distressed users, vulnerable populations)?"

→ captures `needs_persistent_user_data` and `is_companion_style`. If `is_multi_turn` was no, both auto-set to false and the follow-ups are skipped.

**Turn 4 — Operational sensitivity.** Two yes/no in one turn.

> "Two more:
>  1. Does the agent collect personal information (name, location, ID — anything covered by privacy rules)?
>  2. Should it be able to escalate to a human agent when needed?"

→ captures `needs_consent` and `has_hitl`

Each LLM response that contains the user's binary answer maps to one or more `update_intake` calls. Driver enforces completeness — intake phase is not complete until all 7 binary flags are captured. The driver advances to the language phase automatically when complete.

Total chat turns to complete intake: **4**. Total binary answers: 5 (single-shot bot) or 7 (multi-turn bot).

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
    category:           Category
    # For predetermined: Python-expression string referencing intake state
    #   e.g. "set: ${needs_consent}", "set: is_companion_style"
    rule:               Optional[str] = None
    # For chat
    phase:              Optional[str] = None        # which phase asks this
    default:            Optional[Any] = None        # pre-fill value the LLM presents
    must_include:       Optional[list[Any]] = None  # required elements (lists)
    description:        Optional[str] = None        # short hint for the prompt
    applies_if:         Optional[str] = None        # gate expression on intake state
    invalidated_by:     list[str] = dc_field(default_factory=list)  # intake field names
    # For deploy and deploy-overridable chat
    advanced:           bool = False                # collapsible "advanced" section in deploy form
    deploy_overridable: bool = False                # chat field also surfaced (editable) by deploy form
    # For derived
    compute:            Optional[str] = None        # Python expression
    # For schema injection in prompts
    pydantic_class:     Optional[str] = None        # dotted path to owning Pydantic class
```

### Categories

| Category | What it means |
|---|---|
| `predetermined` | Set by an intake-state rule. Never asked. Value re-computed whenever any field in `invalidated_by` changes. |
| `chat` | Asked in chat, in a specific phase. Bot-builder sees the field, with `default` pre-filled if present, and can edit. **No "hidden defaults"** — every chat field surfaces to the user. |
| `deploy` | Captured by the deploy form (separate concern). May be marked `advanced` to live in a collapsible section. The runtime gets its baseline from `dpg/<block>.yaml`; deploy form (if used) overrides via the deploy-time overlay. Skeleton does NOT write to the domain YAML for these fields. |
| `derived` | Computed from other fields by the renderer at write time. No status tracked; no user input. |

**Orthogonal flag — `deploy_overridable`:** a `chat` field may set `deploy_overridable=True` to indicate the deploy form should also surface it, pre-filled from the domain YAML value, with operator edit capability. The chat conversation writes the *project default*; the deploy form swaps for *specific environments* (dev/staging/prod). The override applies via the existing deploy overlay (§8 step 1) and never modifies the domain YAML on disk. Canonical uses: `agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id`. Pure `deploy` fields (e.g., `trust.hitl.queue_backend`) are NOT chat-asked at all and have no pre-fill; the deploy form is the only surface.

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
        rule="set: is_companion_style",
    ),
    "dignity_check.questions": FieldRule(
        category="predetermined",
        rule=f"set: {_CANONICAL_DIGNITY_QUESTIONS} if is_companion_style else []",
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

    # DEPLOY — overridable per-deployment via the deploy form.
    # No `default` here: "log" is already the runtime default from dpg.yaml
    # / Pydantic. Writing default="log" in domain YAML would be redundant.
    "trust.hitl.queue_backend": FieldRule(
        category="deploy",
        advanced=True,
    ),
}
```

**Notes on the example:**

- `pydantic_class` values reference exports from the dev-kit's `schemas/domain/<block>.py`. Top-level orchestration classes use the `*Section` suffix (e.g., `TrustSection`); nested classes use `*Config` (e.g., `HitlConfig`, `InputRulesConfig`). Implementers should grep the dev-kit's domain module to confirm the exact name.
- The `dignity_check.questions` rule returns `[]` when `is_companion_style=false`, which equals the framework default. The skeleton's "only write if differs from default" guard (§8) then writes nothing — by design.

### Where the categories drive behaviour

| Consumer | Operation |
|---|---|
| `build_skeleton()` | For every field where `category == "predetermined"`: run `rule` against current `IntakeState`; write the resulting value to the domain YAML **only if it differs from the dpg.yaml / Pydantic default**. For `chat`: write `default` if present (this seeds an English sentinel the LLM later overrides). For `deploy`: write nothing to the domain YAML — the deploy overlay applies at deploy time. For `derived`: skip. |
| Phase driver | For phase X: filter rules to `category == "chat" AND phase == X AND (applies_if is None OR applies_if(state) is True)`. Of those, take fields with status `pending` or `needs_re_asking`. Render in the phase prompt. |
| Router on `update_intake(F, V)` | For every rule where `F in rule.invalidated_by`: if `predetermined`, re-run rule and write to accumulator (or remove the override if value equals dpg default); if `chat`, mark status `needs_re_asking`; if `derived`, flag for renderer recompute. |
| Renderer | At write time: for every `category == "derived"` rule, run `compute` and write to YAML. |

### Path syntax (including list-of-objects)

Field paths in `FIELD_RULES` keys, `field_status.json` keys, and tool arguments use dotted notation rooted at the block. Most paths are plain dotted attribute walks (`trust.hitl.holding_message`, `agent.timeout_ms`).

Some runtime fields are **lists of named objects** rather than mappings. The most common case is `agent_core.connectors.internal`, which is `list[InternalConnectorDef]` (`agent_core/src/schema/config.py:389`) — each entry has a `name` attribute and is addressed by that name. For these paths the syntax is `<list_attr>[name=<value>]`:

- `connectors.internal[name=knowledge_retrieval]` — the internal connector whose `name=="knowledge_retrieval"`
- `connectors.read[name=lookup_jobs]` — the read connector named `lookup_jobs`
- `agent_workflow.subagents[id=intake]` — list-of-objects keyed by `id`

The resolver:

- **Reading** a `[name=X]` segment finds the matching list element by attribute. Missing match returns `None`.
- **Writing** a `[name=X]` segment finds-or-appends — if no element matches, a new element is appended with the given key.
- **Clearing** a `[name=X]` segment removes the matching element from the list.

A small `path_ops.py` helper (~80 lines) implements `get_path`, `set_path`, `clear_path` with this syntax. `FIELD_RULES` keys and `field_status.json` keys both use the same syntax so the two stay aligned.

### CI guards (future hardening)

The dev-kit has no CI guards today; the pre-deploy dry-run (§3, §8) is the primary safety net for runtime-schema drift. Three guards are planned as follow-up hardening — each makes drift surface at PR time rather than deploy time:

1. **Self-contained schema** — every `<block>/src/schema/config.py` may only import from `pydantic`, `enum`, `typing`, and `__future__`. The bake-in approach (§3) depends on this; any other import would fail at dev-kit Docker build. CI flags the violation at PR time. Until this guard exists, the rule is enforced by code review and the project-level Claude rule at `.claude/rules/runtime-devkit-sync.md`.

2. **Coverage** — every Pydantic field in every runtime block's `MergedConfig` schema either has a corresponding entry in `FIELD_RULES` OR is explicitly listed in a `framework_default_only` allowlist (operational fields that live in dpg.yaml and are never project-specific). Prevents new Pydantic fields being added later without an explicit decision. For list-of-objects fields, the guard checks that at least one canonical `[name=X]` entry is declared per known consumer (e.g., `knowledge_retrieval` in `connectors.internal`).

3. **No redundancy** — for every generated domain YAML in `dev-kit/configs/<slug>/<block>.yaml`, no field's value equals the corresponding dpg.yaml / Pydantic default. If equal, the entry is redundant and must be removed. Catches legacy quirks (`queue_backend: log` duplicating the Pydantic default; operational fields like `chroma_persist_dir` that should move to dpg.yaml) and prevents new ones.

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
                                       is_relevant=lambda s: s.is_companion_style),
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
  intake_state.json       # the 12 intake fields + completed flag
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
  "agent_core.connectors.internal[name=knowledge_retrieval]": "not_applicable",
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

**Setup:** User builds a bot with `has_kb=false`, `has_external_tools=true`, `is_multi_turn=true` (a multi-turn API-calling bot). Wizard ran intake → language → memory → trust → tools → workflow. Currently in **workflow** defining first subagent.

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
     - `agent_core.connectors.internal[name=knowledge_retrieval]` (predetermined) → skeleton structure written
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
    - Router walks `memory` (complete), `user_state` (irrelevant — `is_companion_style=false`, skip), `trust` (complete), `tools` (relevant — `has_external_tools=true`, but already complete), `workflow` (has incomplete fields).
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
    """Walk FIELD_RULES, produce a domain-specific-only accumulator + initial field statuses.

    Writes ONLY domain-specific values to the accumulator. Framework defaults that live
    in dpg/<block>.yaml are never written here — they merge in at runtime. The
    no-redundancy CI guard enforces this.
    """
    accumulator = {block: {} for block in BLOCKS}
    field_status: dict[str, str] = {}

    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.applies_if and not eval_expr(rule.applies_if, intake_state):
            if rule.category == "chat":
                field_status[path] = "not_applicable"
            continue

        if rule.category == "predetermined":
            value = eval_rule(rule.rule, intake_state)
            # Only write if it differs from the framework default. Skeleton never
            # duplicates dpg.yaml values into the domain YAML.
            if value != get_framework_default(path):
                set_path(accumulator, path, value)

        elif rule.category == "chat":
            if rule.default is not None:
                set_path(accumulator, path, rule.default)
            field_status[path] = "pending"

        elif rule.category == "deploy":
            # Deploy fields are NOT written to the domain YAML. The deploy overlay
            # applies them at deploy time; the dpg.yaml provides the baseline.
            pass

        elif rule.category == "derived":
            pass  # renderer computes at write time

    return accumulator, field_status
```

Output is a domain-specific accumulator that, when deep-merged with `dpg/<block>.yaml`, validates against every block's runtime Pydantic schema (enforced by CI test) and has every required field present with either a real value or a sentinel.

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

    # 4. Pre-deploy dry-run — validate through runtime's own schemas baked
    # into the dev-kit image at Docker build time (see §3 "How the dry-run runs").
    for block, data in overlaid.items():
        runtime_validate(block, data)   # in-process: RUNTIME_SCHEMAS[block].model_validate(data)

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

### Post-deployment reconfiguration

Scenario: a user deploys an agent today with `has_kb=false, selected_channels=["voice"]`. A week later they want to add a knowledge base and switch on the web channel. **The current design supports this without conceptual changes** — the same machinery that handles mid-conversation backtracking handles post-deploy edits.

**What already works (by construction):**

| Concern | How it's handled today |
|---|---|
| Loading existing state | `IntakeState`, `accumulator`, `field_status`, `current_phase` are all persisted to `_meta/`. Re-opening the project deserialises them; the wizard starts in `current_phase = "review"` with everything previously answered marked `answered`. |
| User changes intake mid-session | `update_intake(field="has_kb", value=True)` triggers the same FIELD_RULES walk described in §7. Affected fields get `needs_re_asking`; router lands the wizard in the earliest affected phase (`language` for intents, then `knowledge` for KE config). |
| Selective service add | The compose generator at deploy time reads the updated `intake_state` and now includes `knowledge_engine`. New service starts on next `docker compose up`. |
| Selective service remove | Same generator omits the no-longer-needed service. The user re-runs deploy. |
| Schema validation across the new shape | Dry-run revalidates the full merged config against runtime schemas before bind-mount. |

**What needs explicit follow-up work (out of scope for this spec, listed for awareness):**

1. **UI affordance to re-open a deployed project.** The dev-kit web UI today has a project list; "edit & redeploy" needs to be a first-class action with a clear "your changes will trigger a redeploy" warning.
2. **Data-preserving service removal.** Removing `memory_layer` (hypothetically) would orphan Redis volumes. The compose flow needs an explicit data-retention policy — most likely: never auto-remove data-bearing services without confirmation; offer "stop but preserve volumes" as the default.
3. **Adding voice to a previously web-only deploy.** Requires new credentials (voice provider, ngrok auth) at deploy form. The form already supports conditional credential capture based on `selected_channels`, so this is mostly a UX question of when to prompt.
4. **Live reconfigure (no redeploy).** Out of scope. Today's design requires redeploy for any config change. Live config reload is a future enhancement separate from this design.

The architectural invariant that makes post-deploy reconfiguration cheap: **the wizard is stateless across deploys; all wizard memory is on disk in `_meta/`.** A re-opened session sees the exact same state the first deploy used, so the only new code is the "open existing project" entry point and the redeploy UX.

### Decision logging

Every dev-kit decision that changes state, skips work, or branches the flow emits a structured log entry. This makes "why did the wizard do X?" answerable from logs alone, without re-running the session.

Logs follow the project-wide `.claude/rules/logging-observability.md` format — every entry carries `operation`, `status`, and (where applicable) `latency_ms` and `error`. Additional context fields are listed below per decision point.

| Decision point | Where | Required log fields (in addition to operation/status) |
|---|---|---|
| Intake field updated | `on_intake_update` handler | `field`, `old_value`, `new_value`, `affected_count`, `earliest_affected_phase` |
| Config field updated | `on_config_update` handler | `block`, `section`, `paths_written`, `validation_errors` (empty list on success) |
| Field marked `needs_re_asking` | Router on intake change | `path`, `triggered_by` (the intake field name), `reason` (`applies_if_changed` / `default_stale` / `chat_field_invalidated`) |
| Predetermined field recomputed | Router on intake change | `path`, `triggered_by`, `old_value`, `new_value` |
| Phase transition (forward) | End-of-turn router | `from_phase`, `to_phase`, `reason="phase_complete"` |
| Phase transition (backtrack) | End-of-turn router | `from_phase`, `to_phase`, `reason="invalidated"`, `triggered_by` (intake field that caused backtrack) |
| Phase skipped via `is_relevant` | `_next_relevant_phase` | `skipped_phase`, `reason` (e.g., `"has_external_tools=false"`) |
| Skeleton field written | `build_skeleton` | `path`, `category`, `value_kind` (`predetermined_value` / `chat_default` / `derived`) |
| Skeleton field skipped (equals framework default) | `build_skeleton` | `path`, `reason="equals_dpg_default"` |
| Renderer derived-field computation | `render_all` step 2 | `path`, `computed_value` |
| Pre-deploy dry-run per block | `runtime_validate` | `block`, `status`, `latency_ms`, `validation_errors` (full Pydantic error tree on failure) |
| LLM call per turn | Phase driver | `phase`, `model`, `latency_ms`, `input_tokens`, `output_tokens`, `tool_calls` (count + names) |
| Tool call rejected (invalid path / type) | Tool dispatcher | `tool`, `args`, `error`, `error_type` |
| Selective service include / exclude | Compose generator | `service`, `included` (bool), `reason` (e.g., `"has_kb=true"`, `"voice not in selected_channels"`) |

**Never logged:**

- User-typed chat content (PII risk — covered by `.claude/rules/logging-observability.md`)
- LLM-generated user-facing replies
- Secret values from the deploy form (API keys, voice credentials, ngrok auth)

**Log levels:**

- `INFO` — normal decisions (phase transitions, fields updated, skeleton writes).
- `WARNING` — anything that surfaces a deferred error to the user (validation failures the user must resolve, phases invalidated by intake change).
- `ERROR` — dry-run failure, LLM tool-call validation failure, renderer exception.

A future Grafana dashboard can pivot off these to answer: average turns per project, % of sessions with backtracks, which fields trigger the most invalidations, dry-run pass rate by block. Out of scope for this design; the logging surface is what enables the dashboard.

## 9. Files & line counts

| Today | New design |
|---|---|
| `prompts/phases.py` (1334 lines, all phases as concatenated strings) | `phase_prompts/<phase>.py` × 11 files (~50-100 lines each) + `phases_config.py` (~200 lines) |
| `prompts/base.py` (`build_system_prompt`) | Replaced by `phase_driver.py` (~200 lines) |
| `tools.py` (20 tools, 1527 lines) | Trimmed to 8 tools, ~300 lines |
| `accumulator.py` (config dict + ConfigStatus enum) | Kept; add `field_status` dict + helpers |
| `schemas/domain/*.py` (Pydantic models) | Kept; lightly tightened where the runtime schema requires |
| `conversation.py` (turn handler) | Kept; updated to call `phase_driver.run_turn()` |
| `renderer.py` (YAML writer) | Kept; add derived-field pass + runtime dry-run |
| `deployer/compose.py` | Updated for intake-driven service inclusion |
| **New**: `intake_state.py` (~100 lines) | |
| **New**: `field_rules/*.py` × 7 files (~700-900 lines total) | |
| **New**: `router.py` (~200 lines) | |
| **New**: `skeleton.py` (~100 lines) | |
| **New**: `path_ops.py` (~80 lines) | |
| `dev-kit/Dockerfile` | +8 lines: COPY each block's `src/schema/config.py` into `/app/dpg_runtime_schemas/<block>/`; create `__init__.py` files. Zero changes to any runtime block. |

Roughly: ~+2300 lines new (phase prompts + new modules), ~-2900 lines deleted (today's `phases.py` + most of today's `tools.py`). Net is close to neutral; the gain is structural, not size — every file is smaller, single-purpose, and individually testable.

### Migration of existing projects

**Deferred.** The current dev-kit is not yet stable enough for production projects, so the design treats migration of pre-existing project configs as an out-of-scope, future concern. The new wizard will be developed alongside a fresh set of test projects; once stable, a separate spec will define a reverse-engineering migration that maps legacy YAML + `_meta/project.json` (where present) onto the new `IntakeState`. Until then, projects authored under the old wizard can be re-created from scratch in the new wizard.

## 10. Testing approach

### Per-block tests

- **`test_field_rules_<block>.py`**: every Pydantic field in the runtime `MergedConfig` schema has a corresponding `FIELD_RULES` entry (or is in the `not_exposed_intentionally` allowlist).
- **`test_skeleton_validates.py`**: for every meaningful combination of `(has_kb, has_external_tools, is_multi_turn, needs_persistent_user_data, is_companion_style)`, the skeleton output validates against the runtime's Pydantic schemas.
- **`test_intake_updates.py`**: changing each intake field marks the documented affected fields as `needs_re_asking` / re-runs predetermined rules / flags derived fields.

### End-to-end tests

- **`test_wizard_flow.py`**: simulates a full conversation through the wizard for each canonical intake combination (single-shot KB-only, transactional multi-turn with tools, conversational companion, etc.). Asserts the final YAML matches a golden file for a fixed input transcript.
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

- **Earlier (now superseded) plan**: `docs/superpowers/specs/2026-05-13-devkit-config-generation-revamp-design.md` describes the LLM-with-skeleton model. This document extends it with the deterministic state machine.
