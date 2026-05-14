# Dev-Kit Field Rules Catalogue

**Status:** Draft for implementation reference
**Date:** 2026-05-13
**Companion to:** [`2026-05-13-devkit-deterministic-wizard-design.md`](2026-05-13-devkit-deterministic-wizard-design.md)

## 1. Purpose & scope

The deterministic wizard design (companion doc) defines the *structure* of FIELD_RULES — categories, the `FieldRule` dataclass shape, the path syntax. This document defines the *content*: every field that appears in any block's domain YAML, what rules apply to it, and how it changes when `IntakeState` changes.

Implementers writing FIELD_RULES entries should treat this catalogue as the source of truth. Every domain-YAML field is listed exactly once; framework-only fields (lives in `dpg/<block>.yaml`) are listed in §7 for completeness with the `framework_default_only` allowlist marker.

### What this document is NOT

- Not the LLM prompt content — phase prompts are written separately.
- Not the runtime schema source — the runtime's `<block>/src/schema/config.py` is authoritative for types and constraints; this doc maps each schema field to its wizard rule.
- Not the deploy form spec — `category=deploy` fields are flagged here but the form is designed separately.

### Sources of truth

For each block, the catalogue assertions trace to these files (cited where non-obvious):

| Source | Location | What it tells us |
|---|---|---|
| Runtime Pydantic schema | `<block>/src/schema/config.py` (or `reach_layer/base/schema/config.py`) | Field path, type, required-ness, validators, default |
| Framework defaults YAML | `dev-kit/dpg/<block>.yaml` | Operational values that ship in every project |
| Reference domain YAML | `dev-kit/configs/kkb/<block>.yaml` | Working example of every domain-half field |
| Dev-kit mirror schema | `dev-kit/dev_kit/schemas/domain/<block>.py` | Today's chat-time strictness/lenience |
| Phase prompts (today) | `dev-kit/dev_kit/agent/prompts/phases.py` | Which phase asks the field in today's wizard |

When the runtime schema and the dev-kit mirror disagree on type/strictness, the **runtime schema wins** at deploy time (the pre-deploy dry-run uses it). Each such drift is called out under "Notes" in the relevant entry of §7.

## 2. Notation & conventions

### 2.1 Path syntax

Field paths are rooted at the block (without the block name prefix) and use dotted notation. List-of-objects entries use `[<key>=<value>]`:

- `agent.timeout_ms` — plain attribute walk in `agent_core`.
- `connectors.internal[name=knowledge_retrieval]` — the internal connector whose `name == "knowledge_retrieval"`.
- `agent_workflow.subagents[id=enquiry].system_prompt` — the system_prompt of the subagent with `id=="enquiry"`.

When cross-block references are needed in this doc, paths are prefixed with the block name and a dot: `agent_core.preprocessing.nlu_processor.intents`.

### 2.2 Categories (from FIELD_RULES design)

| Category | Meaning |
|---|---|
| `predetermined` | Value is set by an intake-state rule. Never asked. Recomputed when any field in `invalidated_by` changes. |
| `chat` | Asked in chat during the specified phase. May carry a `default` to pre-fill. Status tracked in `field_status.json`. |
| `deploy` | Captured at deploy time via the deploy form. Skeleton does not emit to domain YAML; framework default in dpg.yaml is used unless deploy overlay overrides. |
| `derived` | Computed by the renderer at write time from other fields (no user input, no status). |
| `framework_default_only` | Lives in `dpg/<block>.yaml`. Skeleton never writes it; allowlisted from the Coverage CI guard. |

Categories are mutually exclusive — each field appears in exactly one.

**Orthogonal flag: `deploy_overridable`** (boolean, added on top of `category`). When `true` on a `chat` field, the deploy form surfaces the field pre-filled from the domain YAML value and lets the operator override per-deploy. The override is applied via the existing deploy overlay (design §8 step 1). Today's canonical examples: `agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id`. The chat conversation writes the project's default; the deploy form swaps for specific environments without touching domain YAML.

### 2.3 `applies_if` and `invalidated_by` semantics

- **`applies_if`** is a Python-like expression on `IntakeState` (e.g., `has_kb`, `has_external_tools and is_multi_turn`, `"voice" in selected_channels`). When false, the field is not asked (chat) or not written (predetermined). Existing values clear to default.
- **`invalidated_by`** lists `IntakeState` fields whose mutation forces re-asking (chat) or recomputation (predetermined/derived). When any listed field changes, the rule fires the cascade described in §6.

### 2.4 IntakeState fields (recap)

The full intake state, defined in the companion design doc §4:

```python
@dataclass
class IntakeState:
    # Capabilities
    has_kb: bool
    has_external_tools: bool
    # Conversation pattern
    is_multi_turn: bool
    needs_persistent_user_data: bool
    is_companion_style: bool
    # Operational
    needs_consent: bool
    has_hitl: bool
    # Channels and languages
    selected_channels: list[Literal["web", "voice"]]
    default_language: str
    supported_languages: list[str]
    # Context (LLM-only)
    domain_description: str
    project_name: str
```

The CLI channel is deprecated; see design §4. Throughout this doc, `selected_channels` is treated as a subset of `{"web", "voice"}`.

### 2.5 Phases (recap)

11 wizard phases per the new design: `tier`, `language`, `knowledge`, `memory`, `user_state`, `trust`, `tools`, `workflow`, `observability`, `reach`, `review`. The `tier` phase (today's "agent_type" intake) is now the IntakeState capture phase. Today's `overview` phase moves to the project-creation form.

## 3. Common fields (asked in every project)

These are fields with `category=chat` AND `applies_if=always` — every project regardless of IntakeState is asked about them. Detailed type/default information is in §7's per-block catalogue; this section is the inventory.

### 3.1 `agent_core` (always-asked)

Phase = `language` unless otherwise noted.

| Field | Notes |
|---|---|
| `agent.primary_model` | LLM model for the main loop. Mirror validator: model must match `provider`; cannot equal `fallback_model`. **`deploy_overridable=true`** — deploy form pre-fills from domain YAML and lets operator change per-deploy. |
| `agent.fallback_model` | Same constraints as `primary_model`. **`deploy_overridable=true`**. |
| `agent.provider` | `Literal["anthropic","openai"]`. Phase prompt asks the user this FIRST (changes the model choice domain). **`deploy_overridable=true`** — switching provider at deploy time also forces matching changes to the two model fields (deploy form enforces). |
| `conversation.blocked_message` | Required (mirror: min_length=1). Translation cascade. |
| `conversation.escalation_message` | Required. Re-phrased if `has_hitl`. |
| `conversation.output_blocked_message` | Required. |
| `conversation.unknown_intent_message` | Optional but typically set. |
| `conversation.unsupported_language_message` | Enumerates `supported_languages`. |
| `preprocessing.language_normalisation.enabled` | Default `true`; advanced. KKB sets `false` (GH-313 perf). |
| `preprocessing.language_normalisation.provider` | Optional per-helper override of `agent.provider`. |
| `preprocessing.language_normalisation.model` | Optional per-helper override. |
| `preprocessing.nlu_processor.provider` | Optional per-helper override. |
| `preprocessing.nlu_processor.model` | Optional per-helper override. |
| `preprocessing.nlu_processor.domain_instruction` | Domain-specific NLU classifier instructions. Invalidated by `domain_description`, `default_language`. |
| `preprocessing.nlu_processor.intents` | Required (mirror: min_length=1). The canonical intent set. See §5.3 for downstream invariants. |
| `preprocessing.nlu_processor.entities` | List of entity names NLU extracts. Co-evolves with `entity_to_profile_field` and `nlu_processor.signal_intents` when `needs_persistent_user_data=true`. |

Phase = `workflow`:

| Field | Notes |
|---|---|
| `agent_workflow.agent_system_prompt` | Top-level persona prompt seen on every turn. Domain-defining. |
| `agent_workflow.default_fallback_subagent_id` | Required (mirror: min_length=1). Must reference declared subagent. |
| `agent_workflow.subagents` (entire list-of-objects) | Required (mirror: min_length=1). Subagents subtree includes per-entry id/name/description/is_start/is_terminal/opening_phrase/valid_intents/system_prompt/routing/(tools, special_handler if gated). |
| `agent_workflow.global_intents` | Subset of `nlu_processor.intents`; disjoint with every subagent's `valid_intents`. |
| `agent_workflow.global_routing` | Each rule's `next_subagent_id` must reference declared subagent. |
| `agent_workflow.global_tools` | List of tool names. Must subset `connectors.*` names + MCP-namespaced tool names. Includes `"knowledge_retrieval"` iff `has_kb=true` (hard rule). |

Phase = `reach` / `language`:

| Field | Notes |
|---|---|
| `channels.web.system_prompt_suffix` | Web-specific prompt suffix. Web is always present. |
| `channels.web.turn_assembler.silence_trigger.silence_ms` | Web-side TurnAssembler config. |
| `channels.web.turn_assembler.max_wait_ceiling.max_wait_ms` | Web-side TurnAssembler config. |

### 3.2 `trust_layer` (always-asked)

Phase = `trust`:

| Field | Notes |
|---|---|
| `trust.policy_pack` | Active policy pack name. Must be a key in `trust.policy_packs` (cross-validator). Default = `f"{slug}_pack"`. |
| `trust.input_rules.blocked_phrases` | Per-language slang/profanity. |
| `trust.input_rules.blocked_input_message` | Refusal text. Translation cascade. |
| `trust.output_rules.blocked_phrases` | Output-side prohibited phrases. |
| `trust.output_rules.output_blocked_message` | Output refusal text. |
| `trust.policy_packs[name=*]` (open map) | At least one pack must exist if `policy_pack` is set. |
| `trust.policy_packs[name=*].guardrails[name=*]` (per-guardrail subtree) | Per guardrail: `severity`, `failure_mode`, `prompt_constraints`, `required_disclosures`, `refusal_template`. |

### 3.3 `reach_layer` (asked when `"web" in selected_channels`)

Phase = `reach`. `applies_if: "web" in selected_channels`. The reach_layer_web container is always deployed regardless of `selected_channels`, but for voice-only projects it runs in `routing_only` mode (no SPA, no chat UI — just health + ingest proxy to KE). The UI string fields below are only meaningful in `full` mode; the wizard skips them entirely when web is not selected. See §4.8 for the routing_only details.

| Field | Notes |
|---|---|
| `reach_layer.channels.web.ui.app_name` | Default = `project_name`. |
| `reach_layer.channels.web.ui.app_tagline` | Short tagline. |
| `reach_layer.channels.web.ui.app_icon` | Path or emoji. |
| `reach_layer.channels.web.ui.agent_avatar` | Path or initials. |
| `reach_layer.channels.web.ui.user_avatar` | Path or initials. |
| `reach_layer.channels.web.ui.setup_heading` | Pre-session heading. |
| `reach_layer.channels.web.ui.setup_subtitle` | Pre-session subtitle. |
| `reach_layer.channels.web.ui.user_id_placeholder` | Input placeholder. |
| `reach_layer.channels.web.ui.user_id_hint` | Helper text. |
| `reach_layer.channels.web.ui.start_btn_label` | Button label. |
| `reach_layer.channels.web.ui.new_session_msg` | Greeting for new sessions. |
| `reach_layer.channels.web.ui.returning_user_msg` | Greeting for returning users. |
| `reach_layer.channels.web.ui.sign_out_confirm` | Confirmation dialog text. |
| `reach_layer.channels.web.ui.switch_user_confirm` | Confirmation dialog text. |
| `reach_layer.channels.web.ui.delete_conversation_confirm` | Confirmation dialog text. |

All 15 are language-sensitive (invalidated by `default_language` and `supported_languages`). For voice-only projects (`selected_channels=["voice"]`), all 15 are `not_applicable` — the deploy wizard sets `REACH_LAYER_WEB_MODE=routing_only` and the container runs without a UI.

### 3.4 `observability_layer` (always-asked)

Phase = `observability`:

| Field | Notes |
|---|---|
| `observability.outcomes.lifecycle` (list-of-objects, `[state=X]`) | Required by mirror (min_length=1). Per entry: `state`, `trigger_tool` (optional), `trigger_condition` (reserved). Skeleton seeds `[{state: "started", trigger_tool: null}]`. |
| `observability.outcomes.metrics` (list-of-objects, `[name=X]`) | Optional. Per entry: `name`, `instrument`, `description`, `unit`, `attributes`. |

### 3.5 Blocks with no truly-always-asked chat fields

- **`knowledge_engine`** — every chat field is gated by `has_kb`.
- **`memory_layer`** — every chat field is gated by `is_multi_turn` or `needs_persistent_user_data`.
- **`action_gateway`** — every chat field is gated by `has_external_tools`.
- **`reach_layer`** — every chat field is gated by `"web" in selected_channels` or `"voice" in selected_channels`. A voice-only project asks no `reach_layer.channels.web.ui.*` strings (web runs in `routing_only` mode); a web-only project asks no voice fields.

All four blocks produce empty (or minimal: only derived `observability.domain`) domain YAMLs for projects whose relevant flag is false.

## 4. IntakeState-gated fields

This section is the **operational core** of this catalogue. For each `IntakeState` field, it lists exactly what happens to domain-YAML content when that flag is true vs false. Cross-references in this section use full block-prefixed paths.

Detailed per-field rules (defaults, types, sub-fields) live in §7's per-block catalogue. This section answers "what does the wizard *do* when intake changes?".

### 4.1 `has_kb`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `connectors.internal[name=knowledge_retrieval]` (full subtree: `name`, `route`, `description`, `input_schema`, `invocation_rules.*`) | Becomes the active KB connector. `name` and `route="knowledge_engine"` are predetermined; `description` and the six `invocation_rules` are chat. |
| agent_core | `agent_workflow.global_tools` | **MUST include** `"knowledge_retrieval"` (hard rule per phases.py:1006-1013). |
| agent_core | `preprocessing.nlu_processor.intents` | May add a KB-related intent (e.g., `lookup`, `faq`); `needs_re_asking` on the false→true transition. |
| knowledge_engine | `knowledge.blocks.static_knowledge_base.enabled` | Predetermined `true`. |
| knowledge_engine | `knowledge.blocks.static_knowledge_base.collection_name` | Predetermined: `f"{project_slug}_knowledge"`. |
| knowledge_engine | `knowledge.blocks.static_knowledge_base.default_doc_type` | Chat (default `"general"`). |
| knowledge_engine | `knowledge.blocks.static_knowledge_base.intent_filters` | Chat (open map: intent → list[doc_type]). Keys must be a subset of `agent_core.preprocessing.nlu_processor.intents`. |
| knowledge_engine | `knowledge.blocks.glossary.enabled` and `glossary.mappings` | Chat. Mappings are list-of-objects keyed by `canonical`. |
| reach_layer | `reach_layer.channels.web.ke_internal_url` | Becomes meaningful (Reach→KE direct call for `/ingest`). |
| compose | `knowledge_engine` service | Deployed. |
| phases | `knowledge` phase | Runs (PHASES `is_relevant=lambda s: s.has_kb`). |

**When false:**

- `agent_core.connectors.internal` does NOT include `knowledge_retrieval` (entry omitted entirely from the list).
- `agent_core.agent_workflow.global_tools` does NOT include `"knowledge_retrieval"`.
- All `knowledge_engine.knowledge.*` chat fields are `not_applicable` (cleared from accumulator).
- `knowledge_engine.knowledge.blocks.static_knowledge_base.enabled` predetermined `false` (but renderer suppresses write if equals dpg default).
- `knowledge` phase is auto-skipped.
- `knowledge_engine` service is omitted from compose; YAML still written with sentinel content (Agent Core's KE client no-ops gracefully).

### 4.2 `has_external_tools`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `connectors.read[name=*]`, `connectors.write[name=*]`, `connectors.identity[name=*]` (each entry's full subtree) | Chat. Each entry: `name`, `description`, `input_schema.{type,properties,required,additionalProperties}`, `invocation_rules.{call_when, required_before_calling, must_not_substitute, on_empty, on_failure, bridge_line, exception_no_call, ranking_order, presentation_limit, refinement_loop_max, safety.{never_present, never_speak}}`. |
| agent_core | `agent.max_tool_rounds` | Framework-default-only (3 in dpg.yaml). Not chat-asked. |
| agent_core | `agent_workflow.subagents[id=*].tools` | Chat per-subagent; may reference connector names. Empty list → use `global_tools`. |
| action_gateway | `tools` (the full list) | Chat. Each `tools[id=X]` entry has full ToolDefinition shape (see §7.4 for the per-entry contract). |
| compose | `action_gateway` service | Deployed. |
| phases | `tools` phase | Runs (`is_relevant=lambda s: s.has_external_tools`). |

**When false:**

- `agent_core.connectors.read`, `connectors.write`, `connectors.identity` all empty lists.
- `action_gateway.tools` empty list (matches Pydantic default → skeleton doesn't write).
- `agent_core.agent_workflow.global_tools` includes only `knowledge_retrieval` (if `has_kb=true`).
- All per-subagent `tools` lists empty.
- `tools` phase auto-skipped.
- `action_gateway` service omitted from compose.

### 4.3 `is_multi_turn`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `conversation.termination_message` | Chat (meaningful for multi-turn flow). |
| agent_core | `agent.termination_short_circuit.{enabled, confidence_threshold}` | Framework defaults stay (`true`, `0.7`); operational only — domain doesn't write. |
| agent_core | `agent_workflow.subagents` | Typically multiple non-terminal subagents. |
| agent_core | `agent_workflow.global_intents` | May include `termination_intent`. |
| agent_core | `agent_workflow.global_routing` | May include termination → ended subagent. |
| memory_layer | `state.session.ttl_minutes` | Chat (default 1440 = 24h; kkb uses 2880 = 48h). |
| memory_layer | `state.session.schema` (open map of `SessionFieldDefinition`) | Chat. Domain-specific session fields. Reserved names forbidden (see §7.3). |
| memory_layer | `state.persistent.merge_on_session_end[session_field=*]` | Chat. Rules promoting session field final values to graph node properties at session end. Requires `needs_persistent_user_data=true` AND `is_multi_turn=true`. |
| memory_layer | `reengagement.triggers[event=*]` | Chat (also conditional on `has_external_tools` or voice channel). |

**When false:**

- Single-shot bots collapse `agent_workflow.subagents` to a minimal start + terminal pair.
- `agent_workflow.global_intents` and `global_routing` likely empty.
- `conversation.termination_message` may be a single short ack.
- `agent.termination_short_circuit` largely irrelevant (no multi-turn flow to short-circuit).
- `state.session.*` chat fields are `not_applicable` (session memory unused).
- `state.persistent.merge_on_session_end` `not_applicable`.
- `reengagement.triggers` `not_applicable`.

### 4.4 `needs_persistent_user_data`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `entity_to_profile_field` (open map) | Chat. Bridges NLU entities → Memory Layer profile schema keys. |
| agent_core | `preprocessing.nlu_processor.entities` | Chat. Profile-bearing entities co-evolve with this. |
| agent_core | `preprocessing.nlu_processor.signal_intents` (open map: intent → signal_type) | Chat (advanced). Longitudinal signals feed context graph. |
| agent_core | `conversation.profile_complete_message` | Chat. |
| agent_core | `conversation.returning_user_greeting` | Chat. |
| memory_layer | `state.persistent` (whole subtree presence) | Predetermined `set: PersistentConfig(...) if needs_persistent_user_data else None`. |
| memory_layer | `state.persistent.backend` | Framework default `memgraph`. |
| memory_layer | `state.persistent.graph.user_node.label` | Chat (default `"User"`). |
| memory_layer | `state.persistent.graph.user_node.key` | Chat (default `"user_id"`). |
| memory_layer | `state.persistent.graph.subnodes` (open map of `SubnodeConfig`) | Chat. Named subnodes (UserProfile, JourneyHistory, etc.). Recursive: each may have `child` / `children` / `adhoc`. |
| memory_layer | `user_data_persistence.default_mode` | Predetermined: `set: "saved" if needs_persistent_user_data else "anonymous"`. |

**When false:**

- `agent_core.entity_to_profile_field` empty / not asked.
- `agent_core.preprocessing.nlu_processor.entities` minimal (transient only).
- `agent_core.preprocessing.nlu_processor.signal_intents` empty.
- `conversation.profile_complete_message` and `returning_user_greeting` not asked.
- `memory_layer.state.persistent` is `None` (entire subtree cleared from accumulator).
- `memory_layer.user_data_persistence.default_mode = "anonymous"` (predetermined).

### 4.5 `is_companion_style`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `conversation.user_state_model.enabled` | Predetermined `true`. |
| agent_core | `conversation.user_state_model.default_state` | Chat. Must be in `states[].id` (mirror validator). |
| agent_core | `conversation.user_state_model.states[id=*]` (full per-state subtree: `id`, `signals`, `guidance`) | Chat. List-of-objects keyed by `id`. Canonical kkb pattern: 5 states (fog, orientation, evaluation, commitment, follow_through). |
| agent_core | `preprocessing.nlu_processor.user_state_confidence_threshold` | Framework default `0.4` (GH-139). |
| agent_core | `agent_workflow.agent_system_prompt` | Often references `<user_state_guidance>` — `invalidated_by` includes this flag. |
| agent_core | `agent_workflow.subagents[id=*].system_prompt` | May reference user state — `invalidated_by` includes this flag. |
| trust_layer | `dignity_check.enabled` | Predetermined `true`. |
| trust_layer | `dignity_check.questions` | Predetermined: 5 canonical questions when true; `[]` when false. |
| trust_layer | `dignity_check.fail_action` | Framework default `rewrite`. |
| trust_layer | `trust.policy_pack` | `invalidated_by`. Companion bots typically need a dignity/emotional guardrail pack. |
| trust_layer | `trust.policy_packs[name=*]` open map and `guardrails` | `invalidated_by`. The set of guardrails differs (companion adds `dignity_harm`, `emotional_overreach`). |
| memory_layer | `state.session.schema` | `invalidated_by`. Companion bots often add `mental_state`, `loop_count` session fields. |
| memory_layer | `state.persistent.graph.subnodes` | `invalidated_by`. Companion bots typically have richer JourneyHistory / ContextGraph. |
| phases | `user_state` phase | Runs (`is_relevant=lambda s: s.is_companion_style`). |

**When false:**

- `conversation.user_state_model.enabled` predetermined `false`.
- `conversation.user_state_model.default_state` empty; `states[]` empty.
- `trust_layer.dignity_check.enabled` `false`; `questions=[]`.
- `user_state` phase auto-skipped.
- No state-related subagent prompt fragments.

### 4.6 `needs_consent`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `agent.ask_for_consent` | Predetermined `true`. |
| agent_core | `agent.consent_prompt` | Chat. Translation cascade on language change. |
| agent_core | `conversation.consent_message` | Chat (alternative to `agent.consent_prompt` — only one is typically used). |
| agent_core | `conversation.consent_decline_ack` | Chat. Spoken when user declines consent. |
| trust_layer | `trust.consent.consent_phrases` | Chat. Phrases that count as opt-in. |
| trust_layer | `trust.consent.decline_phrases` | Chat. Phrases that count as decline. |
| reach_layer | `reach_layer.channels.voice.recording.consent_purpose` | Chat (only when voice recording enabled). Ties to a Trust Layer consent grant. |

**When false:**

- `agent.ask_for_consent` predetermined `false`.
- `agent.consent_prompt`, `conversation.consent_message`, `conversation.consent_decline_ack` not asked.
- `trust.consent.consent_phrases` and `decline_phrases` `not_applicable` (cleared).

### 4.7 `has_hitl`

**When true:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `hitl.response_message` | Chat (required by mirror; min_length=1). HITL handoff acknowledgement. |
| agent_core | `conversation.escalation_message` | Chat. Re-phrased to refer to HITL queue. |
| agent_core | `agent_workflow.subagents[id=*].special_handler` | A subagent with `special_handler="hitl"` may exist. |
| trust_layer | `trust.input_rules.escalation_topics` | Chat. Topics that fire HITL handoff (matched by Trust Layer before LLM call). |
| trust_layer | `trust.hitl.holding_message` | Chat (default `"Please hold while I connect you to an agent."`). |
| trust_layer | `trust.hitl.queue_backend` | Deploy form (default `log`; production may pick `redis` or `webhook`). |
| trust_layer | `trust.hitl.notification_webhook` | Deploy form (conditional on `queue_backend ∈ {redis, webhook}`). |

**When false:**

- `agent_core.hitl.response_message` cleared or sentinel.
- `agent_core.conversation.escalation_message` is a generic non-HITL apology.
- No subagent has `special_handler="hitl"`.
- `trust.input_rules.escalation_topics` `not_applicable`.
- `trust.hitl.holding_message` `not_applicable`.
- `trust.hitl.queue_backend` reverts to dpg default; `notification_webhook` cleared.

### 4.8 `selected_channels`

`selected_channels` is a subset of `{"web", "voice"}`. The `reach_layer_web` container is **always deployed** regardless — it runs in one of two modes selected by the env var `REACH_LAYER_WEB_MODE`:

| `selected_channels` | `REACH_LAYER_WEB_MODE` | Web container behaviour |
|---|---|---|
| Includes `"web"` | `full` | Serves the React SPA, auth, chat endpoints, ingest proxy. All `reach_layer.channels.web.ui.*` strings are chat-asked. |
| `["voice"]` only | `routing_only` | Health endpoint + ingest proxy to KE only. No SPA, no auth, no chat UI. Web UI strings are `not_applicable`. |

The mode env var is injected by the deploy wizard's compose generator based on `selected_channels`; it is not in any block's domain YAML. See `reach_layer/web/server.py:1060-1067` for the runtime branch and `automation/docker/docker-compose.yml:347-349` for the env declaration.

**Voice present:**

| Block | Field | Effect |
|---|---|---|
| agent_core | `channels.voice` block (full subtree) | Chat — active. |
| agent_core | `channels.voice.system_prompt_suffix` | Chat. |
| agent_core | `channels.voice.tts_rules.{numbers, money, dates, time, phone, abbreviations, output_script, english_loanwords, email, named_entities}` | All asked. Per-language TTS rules. |
| agent_core | `channels.voice.terminal_word` | Chat. |
| agent_core | `channels.voice.turn_assembler.{semantic_gate, silence_trigger.silence_ms, max_wait_ceiling.max_wait_ms}` | Predetermined defaults seeded (silence_ms=600, max_wait_ms=8000); user may edit. |
| agent_core | `conversation.session_end_eval.enabled` | Predetermined `true`. Auto-injects `end_session` tool. |
| agent_core | `conversation.session_end_eval.prompt` | Chat. Multi-line prompt describing end-of-call signals. |
| agent_core | `conversation.session_end_eval.fail_action` | Framework default `none`. |
| agent_core | Per-tool `connectors.*[].invocation_rules.bridge_line` | Becomes essential (vs optional for chat-only). |
| reach_layer | `reach_layer.channels.voice.raya.stt_language`, `tts_language` | Predetermined from `default_language`. |
| reach_layer | `reach_layer.channels.voice.raya.voice_id` | Chat with auto-suggested default (`RAYA_VOICES[first where language == raya_language_of(default_language)]`). **`deploy_overridable=true`** — operator can swap voice at deploy time. |
| reach_layer | `reach_layer.channels.voice.agent_core.{fallback_phrase, barge_in_acknowledgement, timeout_ms}` | Chat. |
| reach_layer | `reach_layer.channels.voice.{filler_threshold_ms, filler_phrase, terminal_word}` | Chat. (GH-242 / GH-137: moved from agent_core to reach_layer.) |
| reach_layer | `reach_layer.channels.voice.{vobiz.*, raya.api_key, public_url, vad.*, recording.*}` | Deploy form. |
| compose | `reach_layer_voice` + `ngrok` services | Deployed. |

**Voice absent:**

- `agent_core.channels.voice` block omitted (framework default applies; not in domain YAML).
- All voice `tts_rules.*` and `terminal_word` not asked.
- `conversation.session_end_eval.enabled` predetermined `false`; `prompt` cleared.
- `reach_layer.channels.voice.*` all `not_applicable`.
- `reach_layer_voice` + `ngrok` services omitted.

**Web present (`"web" in selected_channels`):**

- `agent_core.channels.web.system_prompt_suffix` Chat.
- `agent_core.channels.web.turn_assembler.silence_trigger.silence_ms` Chat (default 0 — web is direct).
- `agent_core.channels.web.turn_assembler.max_wait_ceiling.max_wait_ms` Chat.
- `reach_layer.channels.web.ui.*` (15 strings): app_name, app_tagline, app_icon, agent_avatar, user_avatar, setup_heading, setup_subtitle, user_id_placeholder, user_id_hint, start_btn_label, new_session_msg, returning_user_msg, sign_out_confirm, switch_user_confirm, delete_conversation_confirm — all chat, all language-sensitive.
- `reach_layer.channels.web.ui.storage_key`, `theme_storage_key` derived from project slug.
- `reach_layer.channels.web.auth.*` deploy form (default `enabled: true` in dpg.yaml; kkb overrides to `false` for local dev).
- `reach_layer.channels.web.ke_internal_url` chat — applies only when `has_kb=true`.

**Web absent (`selected_channels=["voice"]`, voice-only deployment):**

- `agent_core.channels.web` block remains as framework default; `system_prompt_suffix` and `turn_assembler` fields are `not_applicable` (no user interaction with web).
- All 15 `reach_layer.channels.web.ui.*` strings are `not_applicable` (cleared from accumulator).
- `reach_layer.channels.web.auth.*` deploy form fields suppressed.
- Compose generator sets `REACH_LAYER_WEB_MODE=routing_only` for the `reach_layer_web` service.
- Container still deployed (provides health + ingest proxy for KE uploads even on voice-only projects).

### 4.9 `default_language`

When the default language changes:

| Block | Field | Effect |
|---|---|---|
| agent_core | `preprocessing.language_normalisation.default_language` | Predetermined recompute. |
| agent_core | All `conversation.*` message fields (~11 strings) | `needs_re_asking` — translation cascade. |
| agent_core | `agent.consent_prompt` | `needs_re_asking`. |
| agent_core | `hitl.response_message` | `needs_re_asking`. |
| agent_core | `channels.voice.system_prompt_suffix` | `needs_re_asking` (if voice present). |
| agent_core | `channels.voice.tts_rules.*` | `needs_re_asking` (if voice present) — script directive may need rewriting. |
| agent_core | `channels.voice.terminal_word` | `needs_re_asking` (if voice present). |
| agent_core | All `connectors.*[].invocation_rules.{on_empty, on_failure, bridge_line}` | `needs_re_asking` — spoken/displayed text. |
| agent_core | `preprocessing.nlu_processor.domain_instruction` | `needs_re_asking`. |
| agent_core | `agent_workflow.agent_system_prompt` | `needs_re_asking`. |
| agent_core | `agent_workflow.subagents[id=*].{opening_phrase, system_prompt, name, description}` | `needs_re_asking`. |
| agent_core | `conversation.user_state_model.states[id=*].{signals, guidance}` | `needs_re_asking` (if `is_companion_style`). |
| agent_core | `conversation.session_end_eval.prompt` | `needs_re_asking` (if voice present). |
| trust_layer | `trust.input_rules.blocked_input_message` | `needs_re_asking`. |
| trust_layer | `trust.output_rules.output_blocked_message` | `needs_re_asking`. |
| trust_layer | `trust.hitl.holding_message` | `needs_re_asking` (if `has_hitl`). |
| trust_layer | `trust.consent.consent_phrases` and `decline_phrases` | `needs_re_asking` (if `needs_consent`). |
| trust_layer | All `trust.policy_packs[name=*].guardrails[name=*].refusal_template` | `needs_re_asking`. |
| knowledge_engine | `knowledge.blocks.glossary.mappings` | `needs_re_asking` — colloquial terms are language-specific. |
| reach_layer | `reach_layer.channels.voice.raya.{stt_language, tts_language}` | Predetermined recompute. |
| reach_layer | `reach_layer.channels.voice.raya.voice_id` | Chat re-ask (default flips to new language's first voice). |
| reach_layer | `reach_layer.channels.voice.agent_core.{fallback_phrase, barge_in_acknowledgement}` | `needs_re_asking`. |
| reach_layer | `reach_layer.channels.voice.{filler_phrase, terminal_word}` | `needs_re_asking`. |
| reach_layer | All 9 `reach_layer.channels.web.ui.*` language-sensitive strings | `needs_re_asking`. |

### 4.10 `supported_languages`

When the list changes (add or remove):

- Includes everything `default_language` triggers (per-language translations multiplex).
- Plus:
  - `agent_core.preprocessing.language_normalisation.supported_languages` predetermined recompute (must contain `default_language`).
  - `agent_core.conversation.unsupported_language_message` `needs_re_asking` (enumerates the list).
  - `trust_layer.trust.input_rules.blocked_phrases` `needs_re_asking` (per-language slang/profanity may need adding).
  - `trust_layer.trust.output_rules.blocked_phrases` `needs_re_asking`.
  - All `trust.policy_packs[name=*].guardrails[name=*].prompt_constraints` and `required_disclosures` `needs_re_asking`.
  - `trust_layer.dignity_check.questions` `needs_re_asking` (translation pass; design choice — see ambiguity in §7.2).
  - All `reach_layer.channels.web.ui.*` multilingual strings `needs_re_asking` (LLM may format as `<en> · <hi>` etc.).

Note: voice is single-language regardless of `supported_languages` (phases.py:1215-1218). Voice fields are NOT invalidated by `supported_languages` — only by `default_language`.

### 4.11 `domain_description`

When changed (e.g., the user re-describes the project):

| Block | Field | Effect |
|---|---|---|
| agent_core | `preprocessing.nlu_processor.domain_instruction` | `needs_re_asking` — entirely domain-shaped. |
| agent_core | `preprocessing.nlu_processor.intents` | `needs_re_asking` — intent list derived from domain. |
| agent_core | `preprocessing.nlu_processor.entities` | `needs_re_asking`. |
| agent_core | `agent_workflow.agent_system_prompt` | `needs_re_asking` — persona references domain. |
| agent_core | `agent_workflow.subagents[id=*].system_prompt` and `.description` | `needs_re_asking`. |
| agent_core | `connectors.internal[name=knowledge_retrieval].description` | `needs_re_asking` (says what KB contains). |
| agent_core | `connectors.read[name=*].description` (for any tool whose description references domain) | `needs_re_asking`. |
| trust_layer | `trust.input_rules.{blocked_phrases, escalation_topics}` | `needs_re_asking`. |
| trust_layer | `trust.output_rules.blocked_phrases` | `needs_re_asking`. |
| trust_layer | All `trust.policy_packs[name=*].guardrails[name=*].{prompt_constraints, required_disclosures}` | `needs_re_asking`. |
| memory_layer | `state.session.schema` | `needs_re_asking`. |
| memory_layer | `state.persistent.graph.subnodes` | `needs_re_asking`. |
| observability_layer | `observability.outcomes.lifecycle` and `metrics` | `needs_re_asking`. |
| reach_layer | `reach_layer.channels.web.ui.{app_name, app_tagline, app_icon, agent_avatar, user_id_placeholder, user_id_hint, new_session_msg}` | `needs_re_asking`. |

### 4.12 `project_name`

When changed:

| Block | Field | Effect |
|---|---|---|
| agent_core | `observability.domain` | Derived = `slug(project_name)`. |
| agent_core | `agent_workflow.workflow_id` | Derived = `f"{project_slug}_workflow"`. |
| trust_layer | `observability.domain` | Derived = slug. |
| trust_layer | `trust.policy_pack` | `invalidated_by` — sensible default is `f"{slug}_pack"`. |
| trust_layer | `trust.policy_packs` outer key | `invalidated_by` (must match `policy_pack` per cross-validator). |
| knowledge_engine | `observability.domain` | Derived = slug. |
| knowledge_engine | `knowledge.blocks.static_knowledge_base.collection_name` | Predetermined = `f"{project_slug}_knowledge"` (when `has_kb`). |
| action_gateway | `observability.domain` | Derived = slug. |
| memory_layer | `observability.domain` | Derived = slug. |
| observability_layer | `observability.domain` | Derived = slug. |
| reach_layer | `reach_layer.common.observability.domain` | Derived = slug. |
| reach_layer | `reach_layer.channels.web.ui.storage_key` | Derived = `f"{slug}_user_id"`. |
| reach_layer | `reach_layer.channels.web.ui.theme_storage_key` | Derived = `f"{slug}_theme"`. |
| reach_layer | `reach_layer.channels.web.ui.{app_name, setup_heading, new_session_msg}` | `needs_re_asking` (default includes project name). |

## 5. Cross-field interactions

This section captures behaviours that span multiple FIELD_RULES entries or involve cross-block consistency. They are validated either by:

- **`applies_if` / `invalidated_by`** at the rule level (handled by the router automatically).
- **`validate_workflow_graph`** at workflow phase completion (single-block invariants on subagent graph).
- **`validate_cross_block_invariants`** at review phase completion / pre-deploy dry-run (cross-block invariants).

### 5.1 Slug derivation cascade

`intake_state.project_name → slug` (lowercased, sanitised, e.g., `"Kaam Ki Baat" → "kkb"`) drives:

- `<block>.observability.domain` in all 7 blocks (derived).
- `agent_core.agent_workflow.workflow_id` = `f"{slug}_workflow"` (derived).
- `knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name` = `f"{slug}_knowledge"` (predetermined, when `has_kb`).
- `reach_layer.channels.web.ui.storage_key` = `f"{slug}_user_id"` (derived).
- `reach_layer.channels.web.ui.theme_storage_key` = `f"{slug}_theme"` (derived).
- `trust_layer.trust.policy_pack` default = `f"{slug}_pack"` (chat default, `invalidated_by` on rename).

### 5.2 Translation cascade

Changing `default_language` or `supported_languages` ripples across many chat fields. See §4.9 and §4.10 for the exhaustive list per block. The single key invariant: every user-facing string is `invalidated_by` either `default_language` or both `default_language` and `supported_languages`.

Single-language fields (only sensitive to `default_language`): TTS rules, voice text fields, system prompts.
Multi-language fields (sensitive to `supported_languages` as well): NLU domain instruction, web UI strings (multilingual presentation), blocked-phrase lists, conversation messages.

### 5.3 NLU intents — cross-block invariant

`agent_core.preprocessing.nlu_processor.intents` is referenced by:

| Consumer field | Constraint |
|---|---|
| `agent_core.agent_workflow.global_intents` | Subset of `intents`; disjoint with every subagent's `valid_intents` (mirror validator). |
| `agent_core.agent_workflow.subagents[id=*].valid_intents` | Subset of `intents`; disjoint with `global_intents`. |
| `agent_core.agent_workflow.subagents[id=*].routing[].intent` | Each concrete intent (not `"*"` wildcard) must subset `intents`. |
| `agent_core.preprocessing.nlu_processor.signal_intents` keys | Subset of `intents` (open map). |
| `knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters` keys | Subset of `intents` (cross-block invariant per phases.py:548-555). |

Changing `intents` invalidates every consumer. The mirror validates the within-block subsets; `validate_cross_block_invariants` validates the KE invariant.

### 5.4 Connector / Tool name parity (agent_core ↔ action_gateway)

For every `agent_core.connectors.{read,write,identity}[name=X]` there MUST be a matching `action_gateway.tools[id=X]` (phases.py:872-873, 877-906). The cross-block constraints:

- `action_gateway.tools[id=X].id == agent_core.connectors.<cat>[name=X].name`.
- `action_gateway.tools[id=X].endpoints[*].params[name=Y]` where `source="agent"` must equal property names in `agent_core.connectors.<cat>[name=X].input_schema.properties`.
- `action_gateway.tools[id=X].category` ∈ `{read, write, identity}` must match which connector list the entry is in.

The wizard writes both blocks atomically in the tools phase. `validate_cross_block_invariants` verifies parity at review phase.

### 5.5 Workflow graph invariants

`agent_core.agent_workflow.subagents` must form a connected, terminating DAG. The mirror validators enforce:

- Exactly one subagent has `is_start=true`.
- Every `next_subagent_id` (in `subagents[*].routing` and `agent_workflow.global_routing`) references a declared subagent ID.
- `agent_workflow.default_fallback_subagent_id` references a declared subagent.
- Every `valid_intents` is a subset of `nlu_processor.intents`.
- `global_intents` and `subagents[*].valid_intents` are pairwise disjoint.
- Terminal subagents (`is_terminal=true`) have no routing.

`validate_workflow_graph` runs at workflow phase completion. Additional reachability check: every non-terminal subagent should be reachable from the start subagent (recommended; not yet implemented as of writing).

### 5.6 Consent flow — three-block coordination

`needs_consent=true` requires consistent writes across three blocks:

- `agent_core.agent.ask_for_consent = true` (predetermined).
- `agent_core.agent.consent_prompt` or `agent_core.conversation.consent_message` — non-empty.
- `agent_core.conversation.consent_decline_ack` — non-empty.
- `trust_layer.trust.consent.consent_phrases` — non-empty (phrases that count as opt-in).
- `trust_layer.trust.consent.decline_phrases` — non-empty.
- `reach_layer.channels.voice.recording.consent_purpose` — set (only when voice recording enabled).

If Trust has phrases but Agent Core doesn't ask (or vice versa), the user is never prompted — silent failure. `validate_cross_block_invariants` should check the trio is consistently set.

### 5.7 HITL coordination — two-block

`has_hitl=true` requires:

- `agent_core.hitl.response_message` — required (mirror).
- `agent_core.conversation.escalation_message` — re-phrased to refer to HITL queue.
- `trust_layer.trust.input_rules.escalation_topics` — non-empty.
- `trust_layer.trust.hitl.holding_message` — set.
- (Deploy) `trust_layer.trust.hitl.queue_backend` — visible in deploy form.

### 5.8 Voice-channel cross-block coordination

When `"voice" in selected_channels`:

- `agent_core.channels.voice` block present.
- `reach_layer.channels.voice` block present.
- `reach_layer.channels.voice.raya.tts_language` must match `agent_core.preprocessing.language_normalisation.default_language` (single-language voice).
- TTS rules in `agent_core.channels.voice.tts_rules.*` should align with `tts_language`.
- `conversation.session_end_eval.enabled = true` (predetermined).

`validate_cross_block_invariants` should check channel parity (`agent_core.channels.<X>` keys ↔ `reach_layer.channels.<X>` keys ↔ `selected_channels`).

### 5.9 Persistent state — entity ↔ profile bridge

When `needs_persistent_user_data=true`:

- `agent_core.preprocessing.nlu_processor.entities` lists the entity names NLU produces.
- `agent_core.entity_to_profile_field` maps each entity → profile field name in the memory graph.
- `memory_layer.state.persistent.graph.user_node` / `subnodes` schema must accommodate these field names.

The `entity_to_profile_field` map's keys must be a subset of `nlu_processor.entities` (logical invariant; not currently schema-validated).

### 5.10 Observability outcomes — cross-block

`observability_layer.observability.outcomes.lifecycle[state=X].trigger_tool` must reference either:

- An `action_gateway.tools[id=Y].id`, OR
- An `agent_core.connectors.*[name=Y].name`, OR
- `null` (for the entry state).

`validate_cross_block_invariants` should enforce this; runtime currently silently no-ops if the tool never runs (GH-115).

## 6. Mid-conversation transition matrix

Full matrix of every IntakeState change and its cascade. Each entry lists:

- **Predetermined fields recomputed** — `rule` re-evaluated; new value written to accumulator (or removed if equals dpg default).
- **Chat fields → `needs_re_asking`** — listed in `field_status.json`; the phase driver will re-ask in the next turn touching that phase.
- **Derived fields invalidated** — flagged for renderer recompute at write time.
- **Phase relevance change** — phase becomes relevant/skipped on next router walk.
- **Compose change** — service add/remove on next deploy.

The router lands the wizard in the **earliest affected phase** (lowest phase index with `needs_re_asking` fields).

### 6.1 `has_kb: false → true`

- **Predetermined:** `agent_core.connectors.internal[name=knowledge_retrieval].name` ← `"knowledge_retrieval"`; `.route` ← `"knowledge_engine"`; `.input_schema` ← canonical `{type: object, properties: {query: …}, required: [query]}`. `knowledge_engine.knowledge.blocks.static_knowledge_base.enabled` ← `true`; `.collection_name` ← slug-derived.
- **Chat `needs_re_asking`:**
  - `agent_core.connectors.internal[name=knowledge_retrieval].description`
  - `agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.{call_when, must_not_substitute, on_empty, on_failure, bridge_line, required_before_calling}`
  - `agent_core.agent_workflow.global_tools` (must add `"knowledge_retrieval"`)
  - `agent_core.preprocessing.nlu_processor.intents` (consider adding a KB-related intent)
  - `knowledge_engine.knowledge.blocks.static_knowledge_base.default_doc_type`
  - `knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters`
  - `knowledge_engine.knowledge.blocks.glossary.mappings`
- **Derived stale:** `agent_core.agent_workflow.global_tools` (if treated as derived from connector list).
- **Phases:** `knowledge` becomes relevant.
- **Compose:** `knowledge_engine` joins on next deploy.
- **Earliest affected phase:** `language` (because NLU intents may need updating before KB phase).

### 6.2 `has_kb: true → false`

- **Predetermined:** `agent_core.connectors.internal[name=knowledge_retrieval]` entry removed entirely. `knowledge_engine.knowledge.blocks.static_knowledge_base.enabled` ← `false`; `.collection_name` cleared.
- **Chat `needs_re_asking`:**
  - `agent_core.agent_workflow.global_tools` (drop `"knowledge_retrieval"`)
  - Any subagent.system_prompt that references KB content
- **Chat cleared (→ `not_applicable`):**
  - All `knowledge_engine.knowledge.*` chat fields
  - `reach_layer.channels.web.ke_internal_url`
- **Phases:** `knowledge` becomes skipped.
- **Compose:** `knowledge_engine` removed on next deploy.
- **Earliest affected phase:** `language` or `workflow` depending on what references it.

### 6.3 `has_external_tools: false → true`

- **Predetermined:** none directly (operator authors all connector entries).
- **Chat `needs_re_asking`:**
  - `agent_core.connectors.read`, `.write`, `.identity` (operator authors entries)
  - `agent_core.agent_workflow.global_tools` and/or each subagent's `tools`
  - `action_gateway.tools` (operator authors entries)
  - `observability_layer.observability.outcomes.lifecycle` (LLM may propose tool-triggered states)
- **Phases:** `tools` becomes relevant.
- **Compose:** `action_gateway` joins on next deploy.
- **Earliest affected phase:** `tools`.

### 6.4 `has_external_tools: true → false`

- **Predetermined:** none.
- **Chat `needs_re_asking`:**
  - Any subagent.system_prompt that references dropped tools
  - `observability_layer.observability.outcomes.lifecycle` (any `trigger_tool` references become invalid)
- **Chat cleared (→ `not_applicable`):**
  - All `agent_core.connectors.read[name=*]`, `connectors.write[name=*]`, `connectors.identity[name=*]`
  - `action_gateway.tools`
  - Per-subagent `tools` lists
- **Phases:** `tools` becomes skipped.
- **Compose:** `action_gateway` removed on next deploy.

### 6.5 `is_multi_turn: false → true`

- **Predetermined:** none directly.
- **Chat `needs_re_asking`:**
  - `agent_core.agent_workflow.subagents` (graph likely needs more than start+terminal)
  - `agent_core.agent_workflow.global_intents`
  - `agent_core.agent_workflow.global_routing`
  - `agent_core.conversation.termination_message`
  - `memory_layer.state.session.ttl_minutes`
  - `memory_layer.state.session.schema`
  - `memory_layer.state.persistent.merge_on_session_end` (if also `needs_persistent_user_data=true`)
  - `memory_layer.reengagement.triggers`
- **Earliest affected phase:** `memory` (or `workflow`).

### 6.6 `is_multi_turn: true → false`

- **Predetermined:** none.
- **Chat `needs_re_asking`:**
  - `agent_core.agent_workflow.subagents` (collapse to minimal pair)
  - `agent_core.conversation.termination_message` (may be a short ack)
- **Chat cleared (→ `not_applicable`):**
  - `agent_core.agent_workflow.global_intents` / `global_routing` likely empty
  - `memory_layer.state.session.ttl_minutes`, `schema`, `state.persistent.merge_on_session_end`, `reengagement.triggers`

### 6.7 `needs_persistent_user_data: false → true`

- **Predetermined:** `memory_layer.state.persistent` ← `PersistentConfig(...)` (full skeleton). `memory_layer.user_data_persistence.default_mode` ← `"saved"`.
- **Chat `needs_re_asking`:**
  - `agent_core.entity_to_profile_field`
  - `agent_core.preprocessing.nlu_processor.entities` (add profile-bearing entities)
  - `agent_core.preprocessing.nlu_processor.signal_intents`
  - `agent_core.conversation.profile_complete_message`, `returning_user_greeting`
  - `memory_layer.state.persistent.graph.user_node.label`
  - `memory_layer.state.persistent.graph.user_node.key`
  - `memory_layer.state.persistent.graph.subnodes`
  - `memory_layer.state.persistent.merge_on_session_end` (if also `is_multi_turn=true`)
- **Earliest affected phase:** `language` (entities) or `memory`.

### 6.8 `needs_persistent_user_data: true → false`

- **Predetermined:** `memory_layer.state.persistent` ← `None` (entire subtree cleared). `memory_layer.user_data_persistence.default_mode` ← `"anonymous"`.
- **Chat `needs_re_asking`:**
  - `agent_core.preprocessing.nlu_processor.entities` (strip profile entities)
- **Chat cleared (→ `not_applicable`):**
  - `agent_core.entity_to_profile_field`
  - `agent_core.preprocessing.nlu_processor.signal_intents`
  - `agent_core.conversation.profile_complete_message`, `returning_user_greeting`
  - `memory_layer.state.persistent.*` (entire subtree)

### 6.9 `is_companion_style: false → true`

- **Predetermined:** `agent_core.conversation.user_state_model.enabled` ← `true`. `trust_layer.dignity_check.enabled` ← `true`. `trust_layer.dignity_check.questions` ← canonical 5 questions.
- **Chat `needs_re_asking`:**
  - `agent_core.conversation.user_state_model.default_state`
  - `agent_core.conversation.user_state_model.states[id=*]` (author 2–5 states)
  - `agent_core.agent_workflow.agent_system_prompt` (likely needs `<user_state_guidance>` reference)
  - `agent_core.agent_workflow.subagents[id=*].system_prompt`
  - `trust_layer.trust.policy_pack` (may want a dignity/emotional pack)
  - `trust_layer.trust.policy_packs[name=*]` (open map; may add `dignity_harm`, `emotional_overreach`)
  - `memory_layer.state.session.schema` (may add `mental_state`)
  - `memory_layer.state.persistent.graph.subnodes` (may add richer journey nodes)
- **Phases:** `user_state` becomes relevant.

### 6.10 `is_companion_style: true → false`

- **Predetermined:** `agent_core.conversation.user_state_model.enabled` ← `false`. `trust_layer.dignity_check.enabled` ← `false`. `trust_layer.dignity_check.questions` ← `[]`.
- **Chat `needs_re_asking`:**
  - `agent_core.agent_workflow.agent_system_prompt` (drop state references)
  - `trust_layer.trust.policy_pack` and `policy_packs` (user may want to drop emotional guardrails)
  - `memory_layer.state.session.schema` and `state.persistent.graph.subnodes` (LLM may prune companion-specific fields)
- **Chat cleared (→ `not_applicable`):**
  - `agent_core.conversation.user_state_model.default_state`
  - `agent_core.conversation.user_state_model.states[*]`
- **Phases:** `user_state` becomes skipped.

### 6.11 `needs_consent: false → true`

- **Predetermined:** `agent_core.agent.ask_for_consent` ← `true`.
- **Chat `needs_re_asking`:**
  - `agent_core.agent.consent_prompt`
  - `agent_core.conversation.consent_message` (alt path)
  - `agent_core.conversation.consent_decline_ack`
  - `trust_layer.trust.consent.consent_phrases`
  - `trust_layer.trust.consent.decline_phrases`
  - `reach_layer.channels.voice.recording.consent_purpose` (if voice recording enabled)

### 6.12 `needs_consent: true → false`

- **Predetermined:** `agent_core.agent.ask_for_consent` ← `false`.
- **Chat cleared (→ `not_applicable`):**
  - `agent_core.agent.consent_prompt`
  - `agent_core.conversation.consent_message`, `consent_decline_ack`
  - `trust_layer.trust.consent.consent_phrases`, `decline_phrases`

### 6.13 `has_hitl: false → true`

- **Predetermined:** none.
- **Chat `needs_re_asking`:**
  - `agent_core.hitl.response_message`
  - `agent_core.conversation.escalation_message` (re-phrase)
  - Optionally a subagent with `special_handler="hitl"`
  - `trust_layer.trust.input_rules.escalation_topics`
  - `trust_layer.trust.hitl.holding_message`
- **Deploy:** `trust_layer.trust.hitl.queue_backend` (default `log`) and `notification_webhook` (conditional) surfaced in form.

### 6.14 `has_hitl: true → false`

- **Predetermined:** none.
- **Chat `needs_re_asking`:**
  - `agent_core.hitl.response_message` (clear or sentinel)
  - `agent_core.conversation.escalation_message` (generic apology)
- **Chat cleared (→ `not_applicable`):**
  - `trust_layer.trust.input_rules.escalation_topics`
  - `trust_layer.trust.hitl.holding_message`
- **Deploy:** `trust_layer.trust.hitl.queue_backend` reverts to dpg default; `notification_webhook` cleared.

### 6.15 `selected_channels`: add `voice`

- **Predetermined:** `agent_core.channels.voice.turn_assembler.silence_trigger.silence_ms` ← `600`. `agent_core.channels.voice.turn_assembler.max_wait_ceiling.max_wait_ms` ← `8000`. `agent_core.conversation.session_end_eval.enabled` ← `true`. `reach_layer.channels.voice.raya.stt_language` ← from `default_language`. `reach_layer.channels.voice.raya.tts_language` ← from `default_language`.
- **Chat `needs_re_asking`:**
  - `agent_core.channels.voice.system_prompt_suffix`
  - `agent_core.channels.voice.tts_rules.{numbers, money, dates, time, phone, abbreviations, output_script, english_loanwords, email, named_entities}`
  - `agent_core.channels.voice.terminal_word`
  - `agent_core.conversation.session_end_eval.prompt`
  - All `agent_core.connectors.*[].invocation_rules.bridge_line` (now essential, not optional)
  - `reach_layer.channels.voice.raya.voice_id` (default = first voice for language)
  - `reach_layer.channels.voice.agent_core.{fallback_phrase, barge_in_acknowledgement, timeout_ms}`
  - `reach_layer.channels.voice.{filler_threshold_ms, filler_phrase, terminal_word}`
- **Deploy:** All `reach_layer.channels.voice.{vobiz.*, raya.api_key, public_url, vad.*, recording.*}` surfaced in form.
- **Compose:** `reach_layer_voice` + `ngrok` services added.

### 6.16 `selected_channels`: remove `voice`

- **Predetermined:** `agent_core.conversation.session_end_eval.enabled` ← `false`; `prompt` cleared.
- **Chat cleared (→ `not_applicable`):**
  - All `agent_core.channels.voice.*` chat fields
  - All `reach_layer.channels.voice.*` chat fields
- **Deploy:** Voice deploy fields cleared from form view.
- **Compose:** `reach_layer_voice` + `ngrok` services removed.

### 6.17 `selected_channels`: add `web` (`["voice"] → ["web", "voice"]`)

Going from voice-only to multi-channel: web UI surfaces become relevant.

- **Predetermined:** none directly.
- **Chat `needs_re_asking` (flipped from `not_applicable` to `pending`):**
  - `agent_core.channels.web.system_prompt_suffix`
  - `agent_core.channels.web.turn_assembler.silence_trigger.silence_ms`
  - `agent_core.channels.web.turn_assembler.max_wait_ceiling.max_wait_ms`
  - All 15 `reach_layer.channels.web.ui.*` strings
- **Derived recomputed:** `reach_layer.channels.web.ui.storage_key`, `theme_storage_key` (already correct if `project_name` unchanged).
- **Deploy:** `reach_layer.channels.web.auth.*` deploy fields surfaced.
- **Compose:** `REACH_LAYER_WEB_MODE` flips from `routing_only` to `full` for the next deploy. Container is the same; mode env-var changes.
- **Earliest affected phase:** `reach`.

### 6.18 `selected_channels`: remove `web` (`["web", "voice"] → ["voice"]`)

Voice-only deployment with web container demoted to `routing_only` mode.

- **Predetermined:** none.
- **Chat cleared (→ `not_applicable`):**
  - All `agent_core.channels.web.*` chat fields
  - All 15 `reach_layer.channels.web.ui.*` strings
- **Deploy:** `reach_layer.channels.web.auth.*` form fields suppressed.
- **Compose:** `REACH_LAYER_WEB_MODE` flips to `routing_only` for the next deploy. Container still deployed (health + ingest proxy), no SPA/auth/chat.
- **Earliest affected phase:** none triggered (going backwards just clears).

Note: `selected_channels=[]` is invalid — at least one of `web` or `voice` must be selected (no chat-less deployments). The wizard enforces this at intake.

### 6.19 `default_language` change (e.g., `english → hindi`)

- **Predetermined:** `agent_core.preprocessing.language_normalisation.default_language` ← new value. `reach_layer.channels.voice.raya.stt_language` ← new lang code. `reach_layer.channels.voice.raya.tts_language` ← new lang code.
- **Chat `needs_re_asking`:** See §4.9 for the full per-block list.
- **Earliest affected phase:** `language`.

### 6.20 `supported_languages` add

- **Predetermined:** `agent_core.preprocessing.language_normalisation.supported_languages` ← extended list.
- **Chat `needs_re_asking`:** See §4.10 for the full per-block list.

### 6.21 `supported_languages` remove

- **Predetermined:** `agent_core.preprocessing.language_normalisation.supported_languages` ← shortened list.
- **Chat `needs_re_asking`:**
  - `agent_core.conversation.unsupported_language_message` (enumeration update)
  - Translations in removed language are dropped (no re-asking required for removal).

### 6.22 `domain_description` change

- **Predetermined:** none.
- **Chat `needs_re_asking`:** See §4.11 for the full per-block list (covers NLU, system prompts, KB description, observability outcomes, web UI).
- **Earliest affected phase:** `language`.

### 6.23 `project_name` change

- **Predetermined:** `knowledge_engine.knowledge.blocks.static_knowledge_base.collection_name` ← new slug. `trust_layer.trust.policy_pack` default = new slug pack name (chat re-ask).
- **Derived:** All 7 blocks' `observability.domain` recomputed. `agent_core.agent_workflow.workflow_id` recomputed. `reach_layer.channels.web.ui.storage_key`, `theme_storage_key` recomputed.
- **Chat `needs_re_asking`:**
  - `trust_layer.trust.policy_pack` (default suggests new slug)
  - `reach_layer.channels.web.ui.{app_name, setup_heading, new_session_msg}` (often reference project name)
- **Earliest affected phase:** `language` (slug cascades to multiple blocks).

## 7. Coverage matrix — per-block field catalogue

This section enumerates every field in every block's runtime `MergedConfig`. The aim is **completeness** — if a field is in the runtime schema, it appears here with its category. `framework_default_only` fields are listed for the allowlist; non-framework fields carry their FIELD_RULES disposition.

Format per block:

- **Domain-half summary** — counts (predetermined / chat / deploy / derived).
- **Field table** — every field path with its category, phase (if chat), `applies_if`, `invalidated_by`, defaults, and notes.
- **List-of-objects** — addressable instances and key attribute.
- **Mirror drift** — fields where the dev-kit mirror is stricter or laxer than the runtime schema.
- **Ambiguities** — fields where the audit found unresolved questions; implementers should pick one.

### 7.1 `agent_core`

Source: `agent_core/src/schema/config.py` (~700 lines). Top-level keys: `server`, `agent`, `conversation`, `connectors`, `preprocessing`, `entity_to_profile_field`, `hitl`, `agent_workflow`, `channels`, `reach_layer` (top-level inside agent_core.yaml — TurnAssembler fallback), inter-service clients (`ke_client`, `memory_client`, `trust_client`, `action_gateway_client`, `learning_client`), `observability`.

**Domain-half category counts** (approx):
- predetermined: ~10 (`agent.ask_for_consent`, `conversation.user_state_model.enabled`, `conversation.session_end_eval.enabled`, `conversation.user_state_model.default_state` seeding, `preprocessing.language_normalisation.default_language`, `preprocessing.language_normalisation.supported_languages`, `connectors.internal[name=knowledge_retrieval].{name,route,input_schema seed}`, voice TurnAssembler timing defaults)
- chat: ~70 (most of `conversation.*`, `connectors.read/write/identity[name=*].*`, `agent_workflow.subagents[id=*].*`, `agent_workflow.global_*`, NLU `intents`/`entities`/`domain_instruction`/`signal_intents`, voice `tts_rules.*`, web/voice TurnAssembler fields)
- deploy: ~5 (model name aliases for deploy-time overlay are mostly under `agent.*` and rely on the deploy form)
- derived: ~2 (`observability.domain`, `agent_workflow.workflow_id`)
- framework_default_only: ~30 (server, retry timing, features bits, recent_tool_exchanges, current_question, termination_short_circuit, language normalisation operational tuning, NLU thresholds, sentiment classes, inter-service client endpoints/timeouts/circuit-breakers)

**Field table (excerpt — full detail in the source audit at `/tmp/catalogue-audits/01-agent-core.md`):**

The catalogue here lists only domain-half fields (predetermined / chat / deploy / derived). For framework_default_only fields, see the allowlist below the table.

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `agent.primary_model` | chat (**deploy_overridable**) | language | always | — | (chat default; deploy form may override at deploy time) | Must match `provider`; not equal to `fallback_model`. Deploy form pre-fills from domain YAML; operator may swap per-deploy. |
| `agent.fallback_model` | chat (**deploy_overridable**) | language | always | — | (chat default) | Must match `provider`; not equal to `primary_model`. Deploy form pre-fills; operator may override. |
| `agent.provider` | chat (**deploy_overridable**) | language | always | — | `anthropic` | Phase prompt asks user first; flip invalidates models. Deploy form may swap provider per-deploy; UI enforces matching primary/fallback model swap. |
| `agent.consent_prompt` | chat | language | `needs_consent` | `needs_consent, default_language, supported_languages` | — | Translation-sensitive. |
| `agent.ask_for_consent` | predetermined | language | always | `needs_consent` | `set: needs_consent` | — |
| `agent.max_tool_rounds` | framework_default_only | — | always | — | 3 (dpg.yaml) | Not chat-asked. KKB's tightening to 2 (GH-206) will move to dpg.yaml or be revisited during the cleanup pass. |
| `conversation.blocked_message` | chat | language | always | `default_language, supported_languages` | (sentinel) | Required (mirror min_length=1). |
| `conversation.escalation_message` | chat | language | always | `default_language, supported_languages, has_hitl` | (sentinel) | Required. Re-phrased if `has_hitl`. |
| `conversation.output_blocked_message` | chat | language | always | `default_language, supported_languages` | (sentinel) | Required. |
| `conversation.unknown_intent_message` | chat | language | always | `default_language, supported_languages` | (sentinel) | — |
| `conversation.termination_message` | chat | language | `is_multi_turn` | `default_language, supported_languages` | — | Used by termination_short_circuit. |
| `conversation.consent_message` | chat | language | `needs_consent` | `needs_consent, default_language, supported_languages` | — | Alt path to `agent.consent_prompt`. |
| `conversation.consent_decline_ack` | chat | language | `needs_consent` | `needs_consent, default_language, supported_languages` | — | — |
| `conversation.unsupported_language_message` | chat | language | always | `default_language, supported_languages` | (sentinel) | Enumerates supported_languages. |
| `conversation.profile_complete_message` | chat | language | `needs_persistent_user_data` | `needs_persistent_user_data, default_language, supported_languages` | — | — |
| `conversation.returning_user_greeting` | chat | language | `needs_persistent_user_data` | `needs_persistent_user_data, default_language, supported_languages` | — | — |
| `conversation.user_state_model.enabled` | predetermined | user_state | `is_companion_style` | `is_companion_style` | `set: is_companion_style` | — |
| `conversation.user_state_model.default_state` | chat | user_state | `is_companion_style` | `is_companion_style` | — | Must be in `states[].id`. |
| `conversation.user_state_model.states[id=*]` (list-of-objects) | chat | user_state | `is_companion_style` | `is_companion_style, default_language` | — | Per-state `id, signals, guidance`. |
| `conversation.session_end_eval.enabled` | predetermined | language | `"voice" in selected_channels` | `selected_channels` | `set: "voice" in selected_channels` | Auto-injects end_session tool. |
| `conversation.session_end_eval.prompt` | chat | language | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `connectors.read[name=*]` (list-of-objects) | chat | tools | `has_external_tools` | `has_external_tools, default_language` | — | Per-entry: name/description/input_schema/invocation_rules. |
| `connectors.write[name=*]` (list-of-objects) | chat | tools | `has_external_tools` | `has_external_tools, default_language` | — | Same shape as read. Consent gate at runtime. |
| `connectors.identity[name=*]` (list-of-objects) | chat | tools | `has_external_tools` | `has_external_tools, default_language` | — | Same shape. Consent gate. |
| `connectors.internal[name=knowledge_retrieval]` | predetermined (structural) | knowledge | `has_kb` | `has_kb` | (skeleton seeds canonical entry) | name + route + input_schema are predetermined; description + invocation_rules are chat. |
| `connectors.internal[name=knowledge_retrieval].description` | chat | knowledge | `has_kb` | `has_kb, domain_description` | — | — |
| `connectors.internal[name=knowledge_retrieval].invocation_rules.{call_when,required_before_calling,must_not_substitute,on_empty,on_failure,bridge_line}` | chat | knowledge | `has_kb` | `has_kb, default_language` | — | Six fields phase prompt elicits. |
| `connectors.*[name=*].invocation_rules.{exception_no_call,ranking_order,presentation_limit,refinement_loop_max,safety.{never_present,never_speak}}` | chat (advanced) | tools / knowledge | `has_external_tools` or `has_kb` | inherits | — | GH-176 presentation contract. |
| `preprocessing.language_normalisation.enabled` | chat (advanced) | language | always | — | true | — |
| `preprocessing.language_normalisation.provider` | chat (advanced) | language | always | `agent.provider` | None (inherit) | — |
| `preprocessing.language_normalisation.model` | chat (advanced) | language | always | `preprocessing.language_normalisation.provider, agent.provider` | "" (inherit primary_model) | — |
| `preprocessing.language_normalisation.default_language` | predetermined | language | always | `default_language` | `set: default_language` | — |
| `preprocessing.language_normalisation.supported_languages` | predetermined | language | always | `supported_languages` | `set: supported_languages` | Must contain default_language. |
| `preprocessing.nlu_processor.provider` | chat (advanced) | language | always | `agent.provider` | None (inherit) | — |
| `preprocessing.nlu_processor.model` | chat (advanced) | language | always | `preprocessing.nlu_processor.provider, agent.provider` | "" (inherit) | — |
| `preprocessing.nlu_processor.domain_instruction` | chat | language | always | `domain_description, project_name, default_language` | — | Multi-paragraph NLU classifier instructions. |
| `preprocessing.nlu_processor.intents` | chat | language | always | `has_kb, has_external_tools, is_multi_turn, needs_consent, domain_description` | — | Required (mirror min_length=1). |
| `preprocessing.nlu_processor.entities` | chat | language | always | `domain_description, needs_persistent_user_data` | — | Co-domain with `entity_to_profile_field`. |
| `preprocessing.nlu_processor.signal_intents` (open map) | chat (advanced) | language | `needs_persistent_user_data` | `needs_persistent_user_data, preprocessing.nlu_processor.intents` | {} | Keys must subset `intents`. |
| `entity_to_profile_field` (open map) | chat | language or memory | `needs_persistent_user_data` | `needs_persistent_user_data, preprocessing.nlu_processor.entities` | {} | Bridges NLU entities → Memory profile. |
| `hitl.response_message` | chat | language or trust | `has_hitl` | `has_hitl, default_language, supported_languages` | — | Required (mirror min_length=1). |
| `agent_workflow.workflow_id` | derived | workflow | always | `project_name` | `compute: f"{project_slug}_workflow"` | — |
| `agent_workflow.version` | framework_default_only | workflow | always | — | `"1.0.0"` | Phase prompt seeds. |
| `agent_workflow.agent_system_prompt` | chat | workflow | always | `domain_description, default_language, supported_languages, is_companion_style` | — | Required (mirror min_length=1). Persona prompt. |
| `agent_workflow.global_intents` | chat | workflow | always | `preprocessing.nlu_processor.intents, is_multi_turn` | [] | Subset of `nlu_processor.intents`; disjoint with subagent valid_intents. |
| `agent_workflow.global_routing[]` (list, positional) | chat | workflow | always | `agent_workflow.global_intents, agent_workflow.subagents` | [] | Per rule: intent, next_subagent_id, condition/conditions, session_writes. |
| `agent_workflow.default_fallback_subagent_id` | chat | workflow | always | `agent_workflow.subagents` | — | Required. Must reference declared subagent. |
| `agent_workflow.global_tools` | chat | workflow | always | `has_kb, has_external_tools, connectors.read, connectors.internal` | [] | Must subset connector names + MCP tools. Includes `knowledge_retrieval` iff `has_kb`. |
| `agent_workflow.subagents[id=*]` (list-of-objects) | chat | workflow | always | `has_kb, has_external_tools, is_multi_turn, is_companion_style, needs_persistent_user_data, domain_description` | — | Required (min_length=1). Exactly one `is_start=true`. |
| `agent_workflow.subagents[id=*].{id,name,description,is_start,is_terminal,opening_phrase,system_prompt,valid_intents,routing,tools,special_handler,output_format}` | chat | workflow | (various; see §6) | (per-field — see source audit) | — | Per-subagent subtree. |
| `channels.web.system_prompt_suffix` | chat | language or reach | always | `default_language, supported_languages` | — | Web is always present. |
| `channels.web.turn_assembler.silence_trigger.silence_ms` | chat | reach | always | — | 0 | Web is direct mode. |
| `channels.web.turn_assembler.max_wait_ceiling.max_wait_ms` | chat | reach | always | — | (default) | — |
| `channels.voice.system_prompt_suffix` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `channels.voice.tts_rules.{numbers,money,dates,time,phone,abbreviations,output_script,english_loanwords,email,named_entities}` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | Per-language TTS rules. |
| `channels.voice.terminal_word` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `channels.voice.turn_assembler.semantic_gate` | chat | reach | `"voice" in selected_channels` | `selected_channels` | — | — |
| `channels.voice.turn_assembler.silence_trigger.silence_ms` | predetermined (default) | reach | `"voice" in selected_channels` | `selected_channels` | 600 | Voice canonical. |
| `channels.voice.turn_assembler.max_wait_ceiling.max_wait_ms` | predetermined (default) | reach | `"voice" in selected_channels` | `selected_channels` | 8000 | — |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug(project_name)` | — |

**Framework-default-only allowlist (agent_core):**

`server.host`, `server.port`, `agent.features.*` (prompt_cache, streaming, image_input), `agent.timeout_ms`, `agent.retry_attempts`, `agent.retry_backoff_seconds`, `agent.termination_short_circuit.{enabled, confidence_threshold}`, `agent.current_question.max_chars`, `agent.recent_tool_exchanges.{max_items, max_chars}`, `conversation.session_end_eval.fail_action`, `preprocessing.language_normalisation.{min_detection_tokens, transliteration, code_switching}`, `preprocessing.nlu_processor.{confidence_threshold, user_state_confidence_threshold, sentiment_classes, log_raw_response, log_raw_response_max_chars}`, `ke_client.{endpoint, timeout_ms}`, `memory_client.{endpoint, timeout_ms, read_timeout_ms, write_timeout_ms, circuit_breaker.*}`, `trust_client.{endpoint, timeout_ms, check_input.*, check_output.*, check_output_batch.*}`, `learning_client.{endpoint, timeout_ms}`, `action_gateway_client.{endpoint, timeout_ms}`, `reach_layer.turn_assembler` (top-level fallback), `observability.otel.*`.

**Mirror drift / ambiguities (agent_core):**

- Mirror `CurrentQuestionConfig` has `enabled: bool` vs runtime `max_chars: int = 500`. Mirror diverges.
- Mirror `RecentToolExchangesConfig` has `enabled` + `max_exchanges` vs runtime `max_items` + `max_chars`. Mirror diverges.
- Mirror enforces non-empty `conversation.blocked_message`, `escalation_message`, `output_blocked_message`, `hitl.response_message`; runtime accepts empty string. Mirror stricter — intended.
- `agent.max_tool_rounds`: design doc didn't list as canonical chat field; treated as chat under tools phase given KKB tightens it.
- `conversation.session_end_eval`: today's wizard places this in language phase; design could move to workflow phase since it interacts with the end_session tool. Implementers' call.

### 7.2 `trust_layer`

Source: `trust_layer/src/schema/config.py`. Top-level: `server`, `trust`, `dignity_check`, `observability`.

**Domain-half category counts:**
- predetermined: 2 (`dignity_check.enabled`, `dignity_check.questions`)
- chat: ~17 (`trust.policy_pack`, `trust.input_rules.*`, `trust.output_rules.*`, `trust.consent.*`, `trust.hitl.holding_message`, `trust.policy_packs[name=*].guardrails[name=*].*`)
- deploy: 2 (`trust.hitl.queue_backend`, `trust.hitl.notification_webhook`)
- derived: 1 (`observability.domain`)
- framework_default_only: ~5 (server, otel, consent_store.db_path, dignity_check.fail_action)

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `trust.policy_pack` | chat | trust | always | `is_companion_style, project_name` | `f"{slug}_pack"` | Must be a key in `policy_packs`. |
| `trust.input_rules.blocked_phrases` | chat | trust | always | `supported_languages, domain_description` | [] | Per-language slang/profanity. |
| `trust.input_rules.blocked_input_message` | chat | trust | always | `default_language` | "I can't help with that request." | Translation cascade. |
| `trust.input_rules.escalation_topics` | chat | trust | `has_hitl` | `has_hitl, domain_description` | [] | — |
| `trust.output_rules.blocked_phrases` | chat | trust | always | `supported_languages, domain_description` | [] | — |
| `trust.output_rules.output_blocked_message` | chat | trust | always | `default_language` | (sentinel) | — |
| `trust.policy_packs[name=*]` (open map) | chat | trust | always | `is_companion_style, project_name, domain_description` | {} | At least one pack required if `policy_pack` set. |
| `trust.policy_packs[name=*].guardrails[name=*]` (open map) | chat | trust | always | (inherits + `default_language, supported_languages`) | — | Per-guardrail: severity, failure_mode, prompt_constraints, required_disclosures, refusal_template. |
| `trust.consent.consent_phrases` | chat | trust | `needs_consent` | `needs_consent, default_language` | — | Phrases counting as opt-in. |
| `trust.consent.decline_phrases` | chat | trust | `needs_consent` | `needs_consent, default_language` | — | Phrases counting as decline. |
| `trust.hitl.holding_message` | chat | trust | `has_hitl` | `has_hitl, default_language, supported_languages` | "Please hold while I connect you to an agent." | — |
| `trust.hitl.queue_backend` | deploy | (form) | `has_hitl` | `has_hitl` | `log` (dpg default) | Deploy form. |
| `trust.hitl.notification_webhook` | deploy | (form) | `has_hitl AND queue_backend in {redis, webhook}` | `has_hitl, queue_backend` | None | Deploy form. |
| `dignity_check.enabled` | predetermined | trust | always | `is_companion_style` | `set: is_companion_style` | — |
| `dignity_check.questions` | predetermined | trust | always | `is_companion_style, supported_languages` | `set: _CANONICAL_DIGNITY_QUESTIONS if is_companion_style else []` | 5 canonical English questions; translation pass when supported_languages adds. |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug(project_name)` | — |

**Framework-default-only allowlist (trust_layer):**

`server.host`, `server.port`, `dignity_check.fail_action`, `trust.consent_store.db_path`, `observability.otel.*`.

**Mirror drift / ambiguities (trust_layer):**

- Runtime `trust.policy_packs` and `policy_packs[*].guardrails` use open-`dict[str, ...]` with no key-set validator. Coverage CI guard (design §5) should mandate at least one canonical pack/guardrail per project.
- Mirror cross-validator forbids `dignity_check.enabled=true` with `questions=[]`. The companion-style rule satisfies this; the rule's output `[]` for `is_companion_style=false` also satisfies (enabled=false in that case).
- Ambiguity: `dignity_check.questions` is predetermined English-canonical; `supported_languages` invalidation suggests a translation chat-re-ask. Design choice: either (a) keep predetermined English (translation done by Trust Layer at runtime), or (b) flip to chat once any non-English language is in supported_languages so LLM produces translated variants. The audit treats (b) as the conservative choice.

### 7.3 `reach_layer`

Source: `reach_layer/base/schema/config.py`. Top-level: `reach_layer.common`, `reach_layer.channels.{cli,web,voice}`. CLI is dev-only and being phased out (design §4) — never in domain YAML.

**Domain-half category counts:**
- predetermined: 2 (`channels.voice.raya.stt_language`, `channels.voice.raya.tts_language`)
- chat: ~25 (15 web UI strings + ~10 voice text fields)
- deploy: ~12 (web auth/cookies, voice vobiz/raya/public_url/vad/recording)
- derived: 3 (`common.observability.domain`, `web.ui.storage_key`, `web.ui.theme_storage_key`)
- framework_default_only: rest (server endpoints, transport defaults, store config, channel toggles)

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `reach_layer.common.observability.domain` | derived | observability | always | `project_name` | `compute: slug` | ⚠️ Path is `reach_layer.common.observability.domain` (NOT `reach_layer.observability.domain`). |
| `reach_layer.channels.web.ui.app_name` | chat | reach | `"web" in selected_channels` | `project_name, domain_description, default_language, supported_languages` | `project_name` | — |
| `reach_layer.channels.web.ui.app_tagline` | chat | reach | `"web" in selected_channels` | `domain_description, default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.app_icon` | chat | reach | `"web" in selected_channels` | `domain_description` | — | Path or emoji. |
| `reach_layer.channels.web.ui.agent_avatar` | chat | reach | `"web" in selected_channels` | `domain_description` | — | — |
| `reach_layer.channels.web.ui.user_avatar` | chat | reach | `"web" in selected_channels` | — | — | — |
| `reach_layer.channels.web.ui.setup_heading` | chat | reach | `"web" in selected_channels` | `project_name, default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.setup_subtitle` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.user_id_placeholder` | chat | reach | `"web" in selected_channels` | `domain_description, default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.user_id_hint` | chat | reach | `"web" in selected_channels` | `domain_description, default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.start_btn_label` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.new_session_msg` | chat | reach | `"web" in selected_channels` | `project_name, domain_description, default_language, supported_languages` | — | Often "Hello! I'm <project_name>…" |
| `reach_layer.channels.web.ui.returning_user_msg` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.sign_out_confirm` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.switch_user_confirm` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.delete_conversation_confirm` | chat | reach | `"web" in selected_channels` | `default_language, supported_languages` | — | — |
| `reach_layer.channels.web.ui.storage_key` | derived | reach | `"web" in selected_channels` | `project_name` | `compute: f"{slug}_user_id"` | — |
| `reach_layer.channels.web.ui.theme_storage_key` | derived | reach | `"web" in selected_channels` | `project_name` | `compute: f"{slug}_theme"` | — |
| `reach_layer.channels.web.ke_internal_url` | chat | reach | `has_kb` | `has_kb` | None | Used for Reach→KE direct ingest call. |
| `reach_layer.channels.web.auth.enabled` | deploy | (form) | always | — | true (dpg) | Google SSO toggle. KKB overrides false for dev. |
| `reach_layer.channels.web.auth.*` other fields | deploy | (form) | `auth.enabled` | — | (dpg defaults) | — |
| `reach_layer.channels.voice.raya.stt_language` | predetermined | reach | `"voice" in selected_channels` | `selected_channels, default_language` | `set: lang_code(default_language)` | — |
| `reach_layer.channels.voice.raya.tts_language` | predetermined | reach | `"voice" in selected_channels` | `selected_channels, default_language` | `set: lang_code(default_language)` | — |
| `reach_layer.channels.voice.raya.voice_id` | chat (**deploy_overridable**) | reach | `"voice" in selected_channels` | `selected_channels, default_language` | `RAYA_VOICES[first where language == raya_language_of(default_language)]` | Deploy form pre-fills from domain YAML; operator can audition and swap voices per-deploy. |
| `reach_layer.channels.voice.raya.api_key` | deploy | (form) | `"voice" in selected_channels` | — | (deploy secret) | — |
| `reach_layer.channels.voice.agent_core.fallback_phrase` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | Required when voice. |
| `reach_layer.channels.voice.agent_core.barge_in_acknowledgement` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `reach_layer.channels.voice.agent_core.timeout_ms` | chat | reach | `"voice" in selected_channels` | `selected_channels` | 15000 | — |
| `reach_layer.channels.voice.filler_threshold_ms` | chat | reach | `"voice" in selected_channels` | `selected_channels` | — | — |
| `reach_layer.channels.voice.filler_phrase` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `reach_layer.channels.voice.terminal_word` | chat | reach | `"voice" in selected_channels` | `selected_channels, default_language` | — | — |
| `reach_layer.channels.voice.public_url` | deploy | (form) | `"voice" in selected_channels` | — | (ngrok) | — |
| `reach_layer.channels.voice.vad.*` | deploy (advanced) | (form) | `"voice" in selected_channels` | `default_language` | — | Hindi/voice cadence overrides. |
| `reach_layer.channels.voice.vobiz.*` | deploy | (form) | `"voice" in selected_channels` | — | — | Telephony adapter. |
| `reach_layer.channels.voice.recording.source` | deploy | (form) | `"voice" in selected_channels` | — | disabled | — |
| `reach_layer.channels.voice.recording.consent_purpose` | chat | reach | `"voice" in selected_channels AND recording.source != "disabled"` | `needs_consent` | (from trust consent grants) | Ties to Trust Layer. |
| `reach_layer.channels.voice.recording.*` other fields | deploy | (form) | (as above) | — | — | — |

**Framework-default-only allowlist (reach_layer):**

`reach_layer.common.{agent_core_client.*, memory_layer_client.*, ke_client.*}` (all infra DNS), `reach_layer.common.observability.otel.*`, `reach_layer.channels.cli.*` (entire CLI block — dev-only), `reach_layer.channels.web.{store.*, cookie_secure, server.*, request_timeout_ms}` (operational; some deploy-overridable but not exposed today), `reach_layer.channels.voice.{enabled, transport, server.*, audio.*}` operational defaults.

**Compose-level env var (not a YAML field):**

- `REACH_LAYER_WEB_MODE` — `full` when `"web" in selected_channels`, else `routing_only`. Set by the compose generator (`automation/docker/docker-compose.yml:347-349`) based on intake state. Switches the `reach_layer_web` container between full SPA mode and ingest-proxy-only mode. Not part of FIELD_RULES — handled by the selective-deployment logic in design §8.

**Mirror drift / ambiguities (reach_layer):**

- No `list[<NamedObject>]` fields; channels modelled as fixed `ChannelsConfig{cli,web,voice}` attributes. The design's `[name=X]` path syntax is conceptual for channels but never used in actual paths.
- Multiple dpg/domain duplications in current KKB config (cookie_secure, store.backend, store.local.base_path, recording timeouts) — no-redundancy CI guard would strip.

### 7.4 `action_gateway`

Source: `action_gateway/src/schema/config.py`. Top-level: `server`, `tools`, `observability`. The chat surface is essentially the per-tool `ToolDefinition` shape — the wizard enforces this shape when the user adds a tool.

**Domain-half category counts:**
- predetermined: 0
- chat: 1 list (`tools`) with rich per-entry shape (REST or MCP)
- deploy: per-tool `auth.secret_env_var` value (env-var KEY is set in chat; VALUE collected at deploy)
- derived: 1 (`observability.domain`)
- framework_default_only: 6 (server.{host,port}, observability.otel.{collector_endpoint, sample_rate, export_interval_ms}, observability.domain initial default)

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `tools` | chat (list) | tools | `has_external_tools` | `has_external_tools` | [] | Mirror max_length=50. |
| `tools[id=X].id` | chat | tools | `has_external_tools` | — | — | Required. Pattern `^[a-z][a-z0-9_]*$`. Must equal an `agent_core.connectors.<cat>[name=X].name`. |
| `tools[id=X].type` | chat | tools | `has_external_tools` | — | `rest_api` | Enum: rest_api, mcp. |
| `tools[id=X].category` | chat | tools | `has_external_tools` | — | `read` | Enum: read, write, identity. write/identity require consent gate. |
| `tools[id=X].description` | chat | tools | `has_external_tools` | `default_language` | — | Mirror requires min_length=1; runtime accepts "". |
| (REST-only) `tools[id=X].base_url` | chat | tools | `has_external_tools AND type==rest_api` | — | — | Required when REST. |
| (REST-only) `tools[id=X].auth.type` | chat | tools | `has_external_tools AND type==rest_api` | — | — | Enum (no oauth2 in mirror). |
| (REST-only) `tools[id=X].auth.secret_env_var` | chat (key) / deploy (value) | tools | `has_external_tools AND type==rest_api` | — | — | Key name set in chat; value at deploy. |
| (REST-only) `tools[id=X].endpoints[name=Y]` | chat | tools | `has_external_tools AND type==rest_api` | — | — | Per-endpoint: path, method, params, response. |
| (REST-only) `tools[id=X].endpoints[name=Y].params[name=Z]` | chat | tools | `has_external_tools AND type==rest_api` | — | — | Per-param: source, type, required, default. |
| (REST-only) `tools[id=X].endpoints[name=Y].response.projection.*` | chat (advanced) | tools | `has_external_tools AND type==rest_api` | — | — | Reserved (GH-93). |
| (MCP-only) `tools[id=X].mcp_server_url` | chat | tools | `has_external_tools AND type==mcp` | — | — | Required when MCP. |
| (MCP-only) `tools[id=X].transport` | chat | tools | `has_external_tools AND type==mcp` | — | streamable_http | Mirror restricts to {sse, streamable_http}. |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug` | — |

**Framework-default-only allowlist (action_gateway):**

`server.host`, `server.port`, `observability.otel.*`.

**Mirror drift / ambiguities (action_gateway):**

- Runtime `AuthType` includes `oauth2`; mirror excludes. Wizard rejects oauth2 by design (no adapter).
- Runtime `transport: str`; mirror `McpTransport` restricts to {sse, streamable_http}. Wizard stricter.
- Mirror `response.projection: Optional[dict]` (looser) vs runtime `ProjectionConfig{list_key, fields}` (stricter). Pre-deploy dry-run catches malformed projections.

### 7.5 `knowledge_engine`

Source: `knowledge_engine/src/schema/config.py`. Top-level: `server`, `knowledge`, `observability`. The entire `knowledge.*` subtree is gated by `has_kb`.

**Domain-half category counts:**
- predetermined: 2 (`static_knowledge_base.enabled`, `collection_name`)
- chat: ~5 (`static_knowledge_base.{default_doc_type, intent_filters}`, `glossary.{enabled, mappings}`, possibly `multimodal_input_handler.enabled` — PoC)
- deploy: 0 today (embedding provider/model could be deploy-overridable)
- derived: 1 (`observability.domain`)
- framework_default_only: rest (server, retrieval tuning, chroma config, otel)

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `knowledge.blocks.glossary.enabled` | chat | knowledge | `has_kb` | `has_kb` | true | — |
| `knowledge.blocks.glossary.apply_to` | framework_default_only | — | `has_kb` | — | `[normalised_input, entities]` | KKB sets equal to default — redundant. |
| `knowledge.blocks.glossary.mappings` (list-of-objects, `[canonical=X]`) | chat | knowledge | `has_kb` | `has_kb, default_language, supported_languages` | [] | Each entry: `colloquial: list[str], canonical: str`. |
| `knowledge.blocks.static_knowledge_base.enabled` | predetermined | knowledge | always | `has_kb` | `set: has_kb` | — |
| `knowledge.blocks.static_knowledge_base.collection_name` | predetermined | knowledge | `has_kb` | `has_kb, project_name` | `set: f"{slug}_knowledge" if has_kb else None` | — |
| `knowledge.blocks.static_knowledge_base.default_doc_type` | chat | knowledge | `has_kb` | `has_kb, domain_description` | `"general"` | — |
| `knowledge.blocks.static_knowledge_base.intent_filters` (open map) | chat | knowledge | `has_kb` | `has_kb, agent_core.preprocessing.nlu_processor.intents` | — | Keys must subset `agent_core.preprocessing.nlu_processor.intents`. |
| `knowledge.blocks.static_knowledge_base.sources` (list-of-objects, `[path=X]`) | (not wizard-managed) | post-deploy | `has_kb` | — | — | Populated by IngestDocuments post-deploy. |
| `knowledge.blocks.multimodal_input_handler.enabled` | chat (PoC) | knowledge | `has_kb` | `has_kb` | false | Out of scope per design §11. |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug` | — |

**Framework-default-only allowlist (knowledge_engine):**

`server.host`, `server.port`, `knowledge.blocks.static_knowledge_base.{top_k, similarity_threshold, metadata_filters.*, chroma_persist_dir, embedding_provider, embedding_model}` (operational; `chroma_persist_dir` is the legacy quirk that should move to dpg.yaml from kkb), `knowledge.blocks.multimodal_input_handler.{image_model, …}`, `observability.otel.*`.

**Mirror drift / ambiguities (knowledge_engine):**

- Mirror `intent_filter_requires_mappings_when_enabled` validator rejects `use_intent_filter=true` with empty `intent_filters`. Skeleton must seed at least one entry when `has_kb` true.
- Today the wizard sets `observability.domain` via LLM `update_config` (phases.py:751); the new design has the renderer compute it derivedly.

### 7.6 `memory_layer`

Source: `memory_layer/src/schema/config.py`. Top-level: `server`, `redis`, `memgraph`, `observability`, `state`, `user_data_persistence`, `reengagement`.

**Domain-half category counts:**
- predetermined: 2 (`state.persistent` presence, `user_data_persistence.default_mode`)
- chat: ~10 (`state.session.{ttl_minutes, schema}`, `state.persistent.graph.{user_node.label, user_node.key, subnodes}`, `state.persistent.merge_on_session_end[session_field=*]`, `reengagement.triggers[event=*]`)
- deploy: 0 (redis/memgraph passwords could be exposed; not today)
- derived: 1 (`observability.domain`)
- framework_default_only: rest

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `state.session.ttl_minutes` | chat | memory | `is_multi_turn` | `is_multi_turn` | 1440 | Mirror: gt=0, le=10080. KKB uses 2880. |
| `state.session.schema` (open map) | chat | memory | `is_multi_turn` | `is_multi_turn, is_companion_style, domain_description` | {} | Per-entry: `type`, `values?`, `default?`. Reserved names forbidden (`user_id`, `journey_id`, etc.). |
| `state.persistent` | predetermined (structural) | memory | always | `needs_persistent_user_data` | `set: PersistentConfig(...) if needs_persistent_user_data else None` | — |
| `state.persistent.backend` | framework_default_only | — | `needs_persistent_user_data` | — | `memgraph` | KKB sets equal to default — redundant. |
| `state.persistent.graph.user_node.label` | chat | memory | `needs_persistent_user_data` | `needs_persistent_user_data` | `"User"` | — |
| `state.persistent.graph.user_node.key` | chat | memory | `needs_persistent_user_data` | `needs_persistent_user_data` | `"user_id"` | — |
| `state.persistent.graph.subnodes` (open map) | chat | memory | `needs_persistent_user_data` | `needs_persistent_user_data, is_companion_style` | {} | Recursive subnode tree. |
| `state.persistent.merge_on_session_end` (list-of-objects, `[session_field=X]`) | chat | memory | `needs_persistent_user_data AND is_multi_turn` | `needs_persistent_user_data, is_multi_turn` | [] | Per rule: `session_field`, `target`. |
| `user_data_persistence.default_mode` | predetermined | memory | always | `needs_persistent_user_data` | `set: "saved" if needs_persistent_user_data else "anonymous"` | — |
| `reengagement.triggers` (list-of-objects, `[event=X]`) | chat | memory | `is_multi_turn AND has_external_tools` | `is_multi_turn, selected_channels` | [] | Per trigger: `event, delay_hours, channel, message_template, loop_threshold, action`. Scheduler not wired (GH-168). |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug` | — |

**Framework-default-only allowlist (memory_layer):**

`server.host`, `server.port`, `redis.{host, port, db, password, socket_timeout_ms, socket_connect_timeout_ms}`, `memgraph.{uri, user, password, connection_timeout_s}`, `observability.otel.*`.

**Mirror drift / ambiguities (memory_layer):**

- Mirror `state.session.ttl_minutes`: `Field(..., gt=0, le=10080)` — required min_length style. Runtime defaults to 1440.
- Mirror `state.session.schema` reserved names enforced via `RESERVED_SESSION_FIELD_NAMES` frozenset (`memory_layer.py:47-61`).
- Mirror `state.persistent.graph.user_node.label` / `key` are required when `state.persistent` is present.
- Pydantic alias: YAML key `state.session.schema` ↔ Pydantic attribute `fields_schema` (populate_by_name=True). FIELD_RULES paths use YAML key.
- Ambiguity: `state.session.ttl_minutes` has a defensible default for single-shot bots (e.g., short like 30 min). Design choice: gate by `is_multi_turn` (current — single-shot bots have no session state at all) or allow short single-shot sessions. Audit recommends the gate.

### 7.7 `observability_layer`

Source: `observability_layer/src/schema/config.py`. Top-level: `server`, `observability` (which contains `domain`, `otel`, `outcomes`, `sli`, `audit`, `telemetry`).

**Domain-half category counts:**
- predetermined: 0
- chat: 2 (`outcomes.lifecycle`, `outcomes.metrics`)
- deploy: 0 today
- derived: 1 (`observability.domain`)
- framework_default_only: rest (server, otel, sli thresholds, audit/telemetry retention and PII lists)

| Path | Category | Phase | applies_if | invalidated_by | Default / rule | Notes |
|---|---|---|---|---|---|---|
| `observability.outcomes.lifecycle` (list-of-objects, `[state=X]`) | chat | observability | always | `domain_description, has_external_tools` | `[{state: "started", trigger_tool: null}]` | Mirror requires min_length=1. Each entry: `state`, `trigger_tool` (Optional), `trigger_condition` (reserved). |
| `observability.outcomes.metrics` (list-of-objects, `[name=X]`) | chat | observability | always | `domain_description` | [] | Per entry: `name`, `instrument` (counter/gauge/histogram), `description`, `unit`, `attributes`. |
| `observability.domain` | derived | observability | always | `project_name` | `compute: slug` | — |

**Framework-default-only allowlist (observability_layer):**

`server.host`, `server.port`, `observability.otel.*`, `observability.sli.turn_latency_p99_ms`, `observability.sli.trust_block_rate_max`, `observability.audit.retention_days`, `observability.audit.pii_fields_excluded`, `observability.telemetry.pii_fields_excluded`.

**Mirror drift / ambiguities (observability_layer):**

- Mirror `OutcomesConfig.lifecycle: Field(..., min_length=1)` requires at least one entry. Skeleton must seed `[{state: "started", trigger_tool: null}]`.
- Mirror `ObservabilitySection.outcomes: Optional[OutcomesConfig]` — the whole `outcomes` block can be omitted; if present, lifecycle must be non-empty. Wizard either writes a non-empty outcomes block or no outcomes at all.
- Several fields are declared-but-not-enforced at runtime (GH-104, GH-115, GH-160, GH-161): `audit.pii_fields_excluded`, `audit.retention_days`, `sli.*`, `telemetry.pii_fields_excluded`, `outcomes.lifecycle[*].trigger_condition`. They're framework_default_only until enforcement lands.
- `sli.turn_latency_p99_ms` could become deploy-overridable per `selected_channels` (voice tolerates 1500 vs 1200). Not exposed today.

## 8. Open ambiguities

Collected from per-block audits — call-outs implementers should decide on before encoding FIELD_RULES:

1. **`dignity_check.questions` translation strategy** — predetermined-canonical-English vs chat-on-multilingual. Recommendation: chat (re-ask on `supported_languages` change so LLM produces translated variants). Affects §7.2.
2. **`agent.max_tool_rounds` placement** — design doc didn't list as canonical chat; treated as chat in tools phase since KKB tightens it. Confirm. Affects §7.1.
3. **`state.session.ttl_minutes` for single-shot bots** — gated by `is_multi_turn` (current) or always asked with short default for single-shot. Recommendation: gate. Affects §7.6.
4. **`conversation.session_end_eval` phase placement** — today's language phase or workflow phase. Affects §7.1.
5. **`agent_workflow.subagents[id=*].routing[*]`** is positional / non-keyed; can't use `[name=X]` syntax. Treat the whole `routing` list as a single chat field, or define positional path syntax. Affects §7.1 and design §5 path syntax.
6. **`reach_layer.channels.voice.recording.consent_purpose`** — pulled from Trust Layer consent grants at the wizard layer, or set independently. Affects §7.3 and §5.6.
7. **Multimodal Input Handler in KE** — design §11 marks PoC; FIELD_RULES treats as framework_default_only or skipped. Affects §7.5.
8. **CI Coverage guard treatment of open-map fields** — at least one canonical entry per known consumer (`knowledge_retrieval` in `connectors.internal`, etc.). Affects design §5 and several §7 sections.

## 9. Sources

The catalogue assertions were collected by reading:

- All 7 runtime `MergedConfig` Pydantic schemas (`<block>/src/schema/config.py` or `reach_layer/base/schema/config.py`).
- All 7 framework default YAMLs (`dev-kit/dpg/<block>.yaml`).
- The KKB reference domain configs (`dev-kit/configs/kkb/<block>.yaml`).
- All 7 dev-kit mirror schemas (`dev-kit/dev_kit/schemas/domain/<block>.py`).
- The current wizard phase prompts (`dev-kit/dev_kit/agent/prompts/phases.py`).

Detailed per-block source audits live in `/tmp/catalogue-audits/{01-agent-core, 02-trust-reach, 03-ke-mem-ag-obs}.md` (transient; regenerable by re-running the audit subagents on the spec).
