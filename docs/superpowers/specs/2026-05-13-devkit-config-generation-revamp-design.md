# Dev-Kit Config Generation Revamp — Plan

## What we're doing

Move config generation from "LLM authors every field" to "skeleton seeds structural fields + LLM fills domain content + deploy form sets operational fields". The skeleton is decided by the **agent type** chosen in the tier phase and the **capability flags** captured alongside it.

**Pydantic schemas are the single source of truth** for what fields exist and what's valid. No hand-written YAML templates per tier. The skeleton is produced by a Python function (`build_skeleton`) that reads the Pydantic models and emits a valid starting config — when a Pydantic field is added, removed, or its default changes, the skeleton picks it up automatically.

## How agent type is decided

The **tier phase** stays. It asks 4 yes/no questions about the agent and classifies it into one of:

- `transactional` — handles transactions / writes (e.g., a bot that places orders)
- `informational` — answers questions from a knowledge base (e.g., FAQ / docs)
- `agentic` — multi-tool decision-making, no chit-chat
- `conversational` — long-running multi-turn conversation with memory of the user

Alongside the agent type, the wizard captures **three capability flags** (derived from the same answers, or as a single follow-up where ambiguous):

| Flag | Question | Used to |
|---|---|---|
| `has_kb` | Does the agent need a knowledge base? | Decide whether to deploy Knowledge Engine; whether to seed the `knowledge_retrieval` connector |
| `has_external_tools` | Does the agent call external APIs? | Decide whether to deploy Action Gateway; whether to expect tool API keys at deploy time |
| `needs_persistent_user_data` | Does the agent remember users across sessions? | Decide whether memory_layer ships with a persistent graph or stays session-only |

`agent_type` + the three flags are written to `_meta/project.json` immediately after the tier phase. Everything downstream reads from there.

## How the skeleton is built (the mechanism)

The skeleton is the output of a Python function — not a YAML template file. After tier + capabilities are set, the wizard runs:

```
build_skeleton(agent_type, capabilities, slug, channels)
  for each of the 7 blocks:
    1. instantiate the block's top-level Pydantic model with empty input
       → Pydantic applies every field's default
    2. override the few fields that depend on tier
       (e.g. dignity_check.enabled = (agent_type == "conversational"))
    3. fill in slug-derived values
       (collection_name, observability.domain, etc.)
    4. serialise back to YAML and write to dev-kit/configs/<slug>/<block>.yaml
  → returns dict { block_name: { ...valid config... } }
```

Because the function reads from the Pydantic models, the schema stays authoritative. Add a field with a default → next skeleton call emits it. Change a field name → the skeleton breaks at the type level, not in production. There is no per-tier YAML to keep in sync.

Pydantic schemas are used in four places, always the same models:

| Use case | How |
|---|---|
| Show the schema to the LLM | `_schema_source` walks the type graph (including nested classes — `HitlConfig`, `InputRulesConfig`, etc.) and inlines the Pydantic source into the chat phase prompt. |
| Build the skeleton | `build_skeleton` instantiates the same Pydantic models with defaults + tier overrides. |
| Validate `update_config` calls in chat | `validate_partial(block, data)` runs the Pydantic validator. |
| Validate the deploy-time overlay | After `_meta/deploy_settings.json` is merged into the YAML, the merged dict is re-validated through Pydantic before bind-mount. |

## How the schema is decided based on agent type

The skeleton differs per `agent_type`:

| Block | transactional | informational | agentic | conversational |
|---|---|---|---|---|
| agent_core | base workflow, no KB connector | base + `knowledge_retrieval` connector with valid `input_schema` | base + KB connector if `has_kb` | same as agentic, plus `user_state_model.enabled=true` with 5 states |
| knowledge_engine | disabled (sentinel file) | enabled, collection name from slug | enabled iff `has_kb` | enabled iff `has_kb` |
| memory_layer | session-only | session-only | persistent iff `needs_persistent_user_data` | persistent (default for conversational) |
| trust_layer | top-level `trust:` block with sentinel messages, `dignity_check.enabled=false` | same | same | same + `dignity_check.enabled=true` with canonical 5 questions |
| action_gateway | `tools:[]` placeholder, deployed only if `has_external_tools` | block disabled | tools from chat, deployed only if `has_external_tools` | same as agentic |
| reach_layer | web UI defaults; voice config only if voice selected | same | same | same |
| observability_layer | domain + one-state lifecycle | same | same | same |

The skeleton satisfies every field the runtime needs to start. The LLM never authors structural plumbing — it only overrides specific values during chat (intents, blocked phrases, system prompts, etc.).

## What goes to chat vs what gets extracted out to the deploy window

Three sources for every final field value:

1. **🤖 Chat** — domain content authored by the LLM during the wizard (intents, blocked phrases, subagents, KB collection name)
2. **🚀 Deploy** — operational choices the user picks in the deploy form; stored in `_meta/deploy_settings.json`; applied as overlay at deploy time
3. **⚙️ Skeleton** — defaults seeded once when the tier + capabilities are set; the LLM may overwrite specific values in chat

### Extracted out of chat → into deploy form

These move from "LLM writes them into the YAML" to "user picks them in the deploy wizard form":

| Field | Why it moves |
|---|---|
| `agent.provider` | Switch provider per deploy without rerunning chat |
| `agent.primary_model` / `agent.fallback_model` | Provider-aware dropdown |
| LLM API keys (Anthropic / OpenAI) | Secrets, never in YAML |
| Tool API keys (per registered tool) | Secrets |
| Channel credentials (Google Client ID, Vobiz, Raya) | Secrets |
| Infra passwords (Memgraph, Redis, Grafana) | Secrets |
| `reach_layer.channels.voice.raya.voice_id` | Dropdown filtered by `tts_language` (which stays in chat) |
| `trust.hitl.queue_backend` | Operational choice (`log` / `redis` / `webhook`) |
| `agent.timeout_ms` / `retry_attempts` / `max_tool_rounds` / `retry_backoff_seconds` | Runtime tuning |
| `channels.<x>.turn_assembler.*_ms` per channel | Runtime tuning |
| Resource preset (low / medium / high) | Deploy-time |
| Deploy target (Docker / Kubernetes + kubeconfig) | Deploy-time |

### Stays in chat

Everything else that's domain-specific:

- Project metadata (name, description, use case)
- Workflow subagents, routing, system prompts, opening phrases
- NLU intents, entities, sentiment classes
- Default language + supported languages
- Conversation messages (blocked / escalation / unknown / consent — in the domain language)
- TTS rules (per language, only if voice channel)
- Blocked phrases and escalation topics
- Knowledge base settings (collection name, doc types, intent filters)
- Memory layer schema (session enums, persistent subgraph design)
- Observability outcome lifecycle states + metrics

## Selective deployment

Services that aren't needed are not in the generated docker-compose:

| Block | Deployed |
|---|---|
| agent_core | always |
| trust_layer | always (fail-closed) |
| memory_layer | always (session TTL needed even for stateless bots) |
| reach_layer (web) | always |
| reach_layer (voice) | only if voice channel selected |
| observability_layer | always (async, never blocks) |
| **knowledge_engine** | **only if `has_kb`** |
| **action_gateway** | **only if `has_external_tools`** |

The deploy wizard's compose generator reads the capability flags and omits the service blocks plus their `depends_on` lines. Agent_core's HTTP clients already degrade gracefully when those services are unreachable — no agent_core changes needed.

## Flow at deploy time

```
agent_type + capabilities (set in tier phase)
        │
        ▼
build_skeleton() reads Pydantic models, returns a       ← ⚙️ skeleton
valid starting config for all 7 blocks → written to        (from Pydantic)
dev-kit/configs/<slug>/*.yaml
        │
        ▼
LLM update_config calls during chat fill domain         ← 🤖 chat
content; structural plumbing is already in place.
Every call is validated against the same Pydantic
models so invalid keys are rejected immediately.
        │
        ▼
deploy form collects provider, models, voice_id,        ← 🚀 deploy
secrets, resource preset, target → stored in
_meta/deploy_settings.json
        │
        ▼
at deploy time, overlay is merged into the YAML,
re-validated through Pydantic, written to a temp
file and bind-mounted into containers
        │
        ▼
generated docker-compose omits knowledge_engine and
action_gateway when their capability flags are off
```

## How the LLM sees and writes on top of the skeleton

```
1. Wizard starts → tier + overview phases run
   User answers tier questions → agent_type set
   User answers overview questions → capabilities set

2. set_capabilities tool fires
   ↓
   build_skeleton(agent_type, capabilities, slug, channels)
   ↓
   skeleton is written into the in-memory accumulator
   AND serialised to disk: dev-kit/configs/<slug>/*.yaml

3. From this point on, the accumulator is the source of truth.
   It already holds a valid config for all 7 blocks.

4. Every subsequent phase prompt includes:
   - accumulator.summary()        ← LLM sees current config state
   - schema source for that phase ← LLM sees valid field shape
   - phase-specific instructions

5. User chats. When the LLM decides a value should change, it calls:
     update_config(block, section, values={...})
   ↓
   validate_partial(block, partial)  ← Pydantic gate
   ↓
   if valid → accumulator deep-merges values on top of existing state
   ↓
   renderer writes the new state to dev-kit/configs/<slug>/<block>.yaml

6. Final YAML on disk = skeleton + every LLM overwrite, each validated.
```

The LLM doesn't know "this field came from the skeleton" vs "this came from a previous turn" — it just sees the current config state. When the user requests a change, the LLM overwrites whatever value is there now via `update_config`. Skeleton sentinels (e.g. `blocked_input_message: "I can't help with that request."`) stay if untouched and get replaced when the user explicitly asks for a different value or the LLM translates them in the language phase. Deep-merge means only fields the LLM names actually change — everything else the skeleton seeded is preserved.

## Runtime safety: how we handle "config was missed or wrong"

**The skeleton alone cannot run a working agent.** Some fields *must* be filled by the chat phase before deploy is meaningful:

- `agent_workflow.subagents` — runtime requires at least one subagent with `is_start=true`. The skeleton seeds a placeholder `greeting` subagent (empty system prompt) so the workflow validator passes, but the LLM fills the real subagents during the workflow phase.
- `preprocessing.nlu_processor.intents` — skeleton has `["unknown"]` only; LLM appends domain intents in the language phase.
- `agent_workflow.default_fallback_subagent_id` — skeleton sets it to the placeholder greeting subagent; LLM updates it once real subagents exist.

So even with a valid skeleton, the wizard must gate deploy on the workflow phase being complete. That gate plus four layers of defence catches everything else:

| Layer | What it catches | Where it lives |
|---|---|---|
| 1. **Pydantic field validation on every `update_config`** | wrong key names, wrong types, single-field constraints (`min_length`, `pattern`, etc.) | `validate_partial(block, data)` in `dev-kit/dev_kit/schemas/validation.py` |
| 2. **Dev-kit cross-field model_validators** | invariants like `policy_pack_must_be_declared`, `exactly_one_start_subagent`, `default_must_be_in_states`, `voice_id_matches_language` | `@model_validator` on the Pydantic classes in `dev-kit/dev_kit/schemas/domain/*.py` |
| 3. **Pre-deploy dry-run against runtime schemas** | fields the dev-kit schema accepts but the runtime crashes on (the `input_schema` silent-drop class of bugs) | new step in `_run_docker_deploy`: import each runtime block's `MergedConfig` class from `agent_core/src/schema/config.py`, `trust_layer/src/schema/config.py`, etc., and call `MergedConfig.model_validate(merged_yaml)` on the patched YAML *before* bind-mount. Any failure aborts the deploy with the exact field path. |
| 4. **Loud runtime errors** | the rare case something slips through 1–3 | runtime services raise `ConfigurationError` with field path + reason instead of silently dropping (e.g. `agent_core/src/tool_registry.py:155` must raise when an internal connector has empty `input_schema`, not skip it) |

The first two are schema strengthening — adding/tightening Pydantic validators in the dev-kit. The third is the key insight: we already have the runtime's own Pydantic schemas in this repo (each block has its own `schema/config.py`), so the dev-kit can run them as a final pre-flight check. If the runtime would crash on this YAML, we know before `docker compose up`.

The fourth layer is small, targeted runtime changes — replace every silent-drop / silent-default code path with a loud error. The blast radius is small (a handful of `if c.get("name") and c.get("input_schema")` patterns across the seven blocks).

With all four layers in place, the answer to "can a runtime error happen?" is: only if a runtime block has a startup precondition that's not represented in its own Pydantic schema. Those are findable and fixable on a per-block basis — and once moved into the schema, layer 3 catches them automatically.

## End state

- Every project starts day 1 with a complete, valid YAML across all 7 blocks
- The LLM only writes domain content; required structural fields can never be missing
- Re-deploying with a different provider/model is a dropdown change in the deploy form, no chat rerun
- A no-KB / no-tool agent deploys 5 services instead of 7
- Validation errors before deploy: eliminated by construction
- Runtime errors at service start: eliminated by construction
