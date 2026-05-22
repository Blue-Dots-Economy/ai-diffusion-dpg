# Blue Dots Economy Domain Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new domain configuration `blue-dots-economy` under `dev-kit/configs/` that wires the existing 7 DPG runtime blocks into a voice-first job-seeker agent powered by four real REST APIs (`fetch_profile`, `fetch_jobs`, `update_profile`, `apply_job`) — no Knowledge Engine retrieval, no mocks, two subagents under the Manager Agent.

**Architecture:** Configuration-only. No Python code changes. Files mirror the structure of `dev-kit/configs/kkb/` but replace domain content. KKB stays untouched. KE service may be omitted from compose for this domain (the `knowledge_retrieval` internal tool is not registered in either subagent's tool list, so KE is never hit).

**Tech Stack:** YAML, deep-merged with `dev-kit/dpg/*.yaml` framework defaults at startup. Reference spec: [docs/superpowers/specs/2026-05-20-blue-dots-economy-config-design.md](../specs/2026-05-20-blue-dots-economy-config-design.md).

**No commits.** User wants to test the configs before any commit on `feat/blue-dots-economy-domain-config`.

---

## File Structure

All files under `dev-kit/configs/blue-dots-economy/`:

| File | Responsibility | Approx size |
|---|---|---|
| `action_gateway.yaml` | 4 REST tool definitions + shared bearer auth + projections | ~150 lines |
| `agent_core.yaml` | Persona, languages, voice TTS rules, NLU intents, 2 subagents, Manager Agent routing rule | ~400 lines |
| `memory_layer.yaml` | `User` node + `SeekerProfile` and `JobSearch` subnodes, declared fields, journey history | ~80 lines |
| `trust_layer.yaml` | KKB policy pack copied + English consent text + Gujarati phrases added + dignity check | ~140 lines |
| `observability_layer.yaml` | `blue_dot` domain + outcome lifecycle (applied/shortlisted/placed) + drop-off metrics | ~50 lines |
| `reach_layer.yaml` | Voice channel only, English-default voice id, English/Hindi/Gujarati STT/TTS, no web | ~50 lines |
| `knowledge_engine.yaml` | Minimal — no documents, no glossary, KE never hit by agent | ~20 lines |

---

## Task 1: action_gateway.yaml — 4 tool definitions

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/action_gateway.yaml`

- [ ] **Step 1: Author file**

Tool catalogue:

1. `fetch_profile` — GET `/api/v1/seeker/profile/{phone}` on `http://65.2.66.144:2742`, bearer auth via `BLUE_DOTS_API_KEY`, category `read`, `{phone}` substituted by adapter. Projection: `item_id`, `name`, `trade`, `preferred_city`, `experience_years`, `open_to_remote` from `item_state.*`.
2. `fetch_jobs` — GET `/api/v1/network/item/fetch` on same host, bearer auth, category `read`. Static params: `item_network=blue_dot`, `item_domain=provider`, `item_type=job_posting_1.0`, `limit=100`, `offset=0`. Agent param: `item_state.role` (capitalised). Projection list_key `items`, fields `item_id`, `role`, `company`, `city`, `salary_range`, `employment_type`. City filter is client-side in the LLM prompt.
3. `update_profile` — PATCH `/api/v1/item/{profile_item_id}` on same host, bearer auth, category `write`. `{profile_item_id}` substituted from session memory. Body: single `item_state` object; agent supplies a subset of `name`, `skills`, `preferred_city`, `age`, `experience_years`, `open_to_remote`. Adapter wraps into `{"item_state": {...}}`.
4. `apply_job` — POST `/api/v1/action/perform`, bearer auth, category `write`. Body assembled from session memory + one LLM-supplied `job_item_id`. Static `cover_note=""`, `resume_url=""`.

All four share `auth: {type: bearer, secret_env: BLUE_DOTS_API_KEY}`. `health_check.enabled: false` on all (the host has no `/health` endpoint we control). `timeout_ms`: 3000 for read tools, 5000 for write tools.

`observability.domain: "blue_dot"` at the end of the file.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/action_gateway.yaml'))"`
Expected: no exception.

---

## Task 2: agent_core.yaml — persona, NLU, two subagents

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/agent_core.yaml`

- [ ] **Step 1: Author file**

Sections (mirroring KKB structure):

- `agent.provider` / `primary_model` / `fallback_model`: copy KKB's current pair (openai + `gpt-4.1-mini-2025-04-14`).
- `agent.ask_for_consent: true`, `consent_prompt` in English: "Hello! Welcome to Blue Dots. Can I save your profile and submit job applications on your behalf during this call?"
- `agent.max_tool_rounds: 2` (matches the longest legitimate chain: `update_profile` → `apply_job`).
- `channels.voice.system_prompt_suffix`: English-default phone-call rules — at most 2 short sentences per reply, no markdown, no emoji. Add tts_rules section adapted for English numerals + Hindi/Gujarati transliteration guidance.
- `channels.voice.terminal_word: "Thank you"`.
- `channels.voice.turn_assembler.silence_trigger.silence_ms: 800` (English speakers pause less than older Hindi speakers).
- `channels.web` and `channels.cli`: minimal stubs with empty `system_prompt_suffix` and the framework-default assembler.
- `conversation.blocked_message`, `escalation_message`, `output_blocked_message`, `unknown_intent_message`, `termination_message`, `unsupported_language_message`, `consent_decline_ack`, `returning_user_greeting`: all in English.
- `conversation.user_state_model.enabled: false` (out of scope for this simple linear flow).
- `conversation.session_end_eval.enabled: true` with English wake words: "thank you", "bye", "that's all", plus Hindi "shukriya", "alvida".
- `connectors.read` declares `fetch_profile` and `fetch_jobs` with `input_schema` matching the action_gateway params and rich `invocation_rules` (when to call, what's required, on_empty, on_failure messages in English).
- `connectors.write` declares `update_profile` and `apply_job` similarly with `invocation_rules.required_before_calling` enforcing the design rules (`profile_item_id` required for both; `job_item_id` required for apply; profile must have been updated before apply).
- `connectors.internal: []` — no `knowledge_retrieval` registration.
- `preprocessing.language_normalisation.enabled: false` (LLM mirrors language), `default_language: english`, `supported_languages: [english, hindi, gujarati]`.
- `preprocessing.nlu_processor` with English-domain instruction, intents `[profile_answer, evaluate_option, apply_now, language_switch_request, counsellor_request, termination_intent, any_input, unknown]`, entities `[name, age, location, trade, language_preference, years_experience, open_to_remote, skills, monthly_in_hand]`.
- `entity_to_profile_field` mapping for those entities.
- `hitl.response_message`: English text about a counsellor callback.
- `agent_workflow.workflow_id: blue_dots_journey`, `version: "1.0.0"`.
- `agent_workflow.global_tools: [fetch_profile, fetch_jobs, update_profile, apply_job]`.
- `agent_workflow.agent_system_prompt`: English persona "You are Blue Dots Agent — a warm, conversational voice guide for job seekers in India. Mirror the caller's language: English, Hindi, or Gujarati. Tool order matters: `fetch_profile` once at session start; then ask for trade and preferred city if missing; then `fetch_jobs` filtered locally by city; let the user pick; ensure `name` is captured (required); call `update_profile` once before any `apply_job`; finally `apply_job` and confirm."
- `agent_workflow.subagents` — two subagents:
  - `profile_intake` (is_start: true, is_terminal: false) — tools `[fetch_profile]`. System prompt: greet, take consent, fetch profile, ask gaps (trade, preferred_city). One question per turn. Routing: when both `trade` and `preferred_city` are present in session → `job_match_apply`; otherwise stay; on `termination_intent` → `ended`.
  - `job_match_apply` (is_start: false, is_terminal: false) — tools `[fetch_jobs, update_profile, apply_job]`. System prompt covers fetch → filter-by-city → read top 1-3 → ask pick → ensure name → optional age/experience/open_to_remote → update → apply → confirm `application_id`. Routing: on `termination_intent` → `ended`; on `explore_more` → `profile_intake`; otherwise stay.
  - `ended` (is_terminal: true) — final goodbye.
  - `clarification` (default fallback) — generic re-prompt.
- `reach_layer.turn_assembler.semantic_gate.enabled: true`, `confidence_threshold: 0.75`.
- `observability.domain: "blue_dot"`.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/agent_core.yaml'))"`
Expected: no exception.

---

## Task 3: memory_layer.yaml — seeker state shape

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/memory_layer.yaml`

- [ ] **Step 1: Author file**

Mirror KKB's structure exactly with these substitutions:

- `state.session.ttl_minutes: 2880` (unchanged).
- `state.session.schema`: replace KKB's enums with these blue-dots fields:
  - `consent_given: {type: bool, default: false}`
  - `profile_item_id: {type: string, default: ""}`
  - `trade: {type: string, default: ""}`
  - `preferred_city: {type: string, default: ""}`
  - `last_jobs: {type: list, default: []}` (compact dicts)
  - `selected_job_item_id: {type: string, default: ""}`
- `state.persistent.backend: memgraph` and `state.persistent.graph.user_node`: `label: User`, `key: user_id` (set by Reach Layer Voice to caller's E.164 phone).
- `subnodes.SeekerProfile`:
  - `rel: HAS_PROFILE`
  - `declared_fields: [name, phone, profile_item_id, trade, preferred_city, age, experience_years, open_to_remote, skills]`
  - `adhoc: {label: UserAttribute, rel: HAS_ATTRIBUTE, fields: [key, value, raw, turn, journey_id]}`
- `subnodes.JourneyHistory`: grouping, child `Journey` with fields `[journey_id, started_at, ended_at, end_reason]` and children:
  - `JobOffered` (`rel: OFFERED`) with `[item_id, role, company, city, salary_range, employment_type]`
  - `Application` (`rel: APPLIED`) with `[application_id, target_item_id, applied_at, status]`
  - `DropOff` with `[node, reason, timestamp]`
- `merge_on_session_end`: promote `selected_job_item_id` to `Journey.selected_job` and `last_jobs` to `JobOffered` edges.
- `user_data_persistence.default_mode: saved`.
- `reengagement.triggers: []` (out of scope).
- `observability.domain: "blue_dot"`.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/memory_layer.yaml'))"`
Expected: no exception.

---

## Task 4: trust_layer.yaml — KKB rules + English consent

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/trust_layer.yaml`

- [ ] **Step 1: Author file**

Copy KKB's `trust_layer.yaml` and change:

- `trust.policy_pack: "blue_dots_advisory_jobs"`.
- `trust.input_rules.blocked_phrases`: same as KKB (bomb/weapon/kill/threat/violence) — domain-agnostic.
- `trust.input_rules.escalation_topics`: same as KKB (suicide/arrested/police case/FIR/jail).
- `trust.input_rules.blocked_input_message`: English — "Sorry, I can't help with that topic."
- `trust.output_rules.blocked_phrases`: keep the English subset ("guaranteed placement", "100% job guarantee", "as an AI, I"); drop all Hindi promotional phrases.
- `trust.output_rules.output_blocked_message`: English.
- `trust.policy_packs`: rename `kkb_advisory_jobs` → `blue_dots_advisory_jobs`. Translate refusal_template fields to English. Otherwise identical.
- `trust.consent.consent_phrases`: keep KKB's set (Hindi/Romanised) and add Gujarati `["હા", "જી હા", "બરાબર", "ઓકે"]`.
- `trust.consent.decline_phrases`: similar expansion to Gujarati `["ના", "નહિ", "મારે નથી"]`.
- `trust.hitl.queue_backend: "log"`, `holding_message` in English.
- `dignity_check.enabled: true`, 6 questions copied verbatim from KKB (they are domain-agnostic).
- `observability.domain: "blue_dot"`.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/trust_layer.yaml'))"`
Expected: no exception.

---

## Task 5: observability_layer.yaml — outcome lifecycle

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/observability_layer.yaml`

- [ ] **Step 1: Author file**

- `observability.domain: "blue_dot"`.
- `observability.outcomes.lifecycle`:
  - `enquiry` (no trigger_tool) — initial state
  - `profile_fetched` — trigger_tool `fetch_profile`
  - `profile_updated` — trigger_tool `update_profile`
  - `applied` — trigger_tool `apply_job`, trigger_condition `result == 'success'`
- `observability.outcomes.metrics`:
  - `blue_dots.applications` (counter) with attributes `[trade, city]`
  - `blue_dots.drop_off.by_stage` (counter) with attributes `[stage, intent]` — stages: `pre-consent`, `pre-trade`, `pre-city`, `pre-pick`, `pre-name`, `pre-apply`
- `observability.sli.turn_latency_p99_ms: 1200`, `trust_block_rate_max: 0.05`.
- `observability.audit.retention_days: 90`.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/observability_layer.yaml'))"`
Expected: no exception.

---

## Task 6: reach_layer.yaml — voice only

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/reach_layer.yaml`

- [ ] **Step 1: Author file**

- `reach_layer.common.observability.domain: "blue_dot"`.
- `reach_layer.channels.cli`: minimal — `prompt: "You: "`, `agent_prefix: "Agent: "`. (Even though only voice will be enabled at compose level, leaving cli config harmless.)
- `reach_layer.channels.web.auth.enabled: false`. UI strings: app_name `"Blue Dots Agent"`, English subtitles. Even though web container can be excluded from compose, having defaults avoids a partial-config error if someone enables it.
- `reach_layer.channels.voice`:
  - `vad.stop_secs: 0.7` (English speakers pause less than Hindi older speakers).
  - `raya.stt_language: "en"`, `tts_language: "en"`, `voice_id: ""` (leave empty so framework default English voice is used; user can set later).
  - `agent_core.timeout_ms: 15000`.
  - `agent_core.fallback_phrase: "Sorry, I didn't catch that. Could you repeat?"`
  - `agent_core.barge_in_acknowledgement: "One moment."`
  - `filler_threshold_ms: 1500`, `filler_phrase: "One second"`.
  - `terminal_word: "Thank you"`.
  - `recording`: source `pipeline`, consent_purpose `storage`, min_duration_ms 500, caller_id_hash_salt placeholder (32+ chars) — same shape as KKB, English-context salt string. `start_on_connect: true`.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/reach_layer.yaml'))"`
Expected: no exception.

---

## Task 7: knowledge_engine.yaml — minimal placeholder

**Files:**
- Create: `dev-kit/configs/blue-dots-economy/knowledge_engine.yaml`

- [ ] **Step 1: Author file**

- `knowledge.blocks.glossary.enabled: false`, `mappings: []`, `apply_to: []`.
- `knowledge.blocks.static_knowledge_base.enabled: false`, `collection_name: blue_dots_knowledge`, `chroma_persist_dir: /app/chroma_db`, `sources: []`, `intent_filters: {}`.
- `knowledge.blocks.multimodal_input_handler.image_model: claude-haiku-4-5-20251001`.
- `observability.domain: "blue_dot"`.

KE service is configured but the `knowledge_retrieval` tool is not in either subagent's tool list (set in Task 2), so KE is never called at runtime. The service may be omitted from docker-compose for this domain.

- [ ] **Step 2: Verify file parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('dev-kit/configs/blue-dots-economy/knowledge_engine.yaml'))"`
Expected: no exception.

---

## Task 8: Schema dry-run verification (no commit)

- [ ] **Step 1: Verify the seven domain configs deep-merge cleanly with framework defaults**

Run the existing module config loaders against each block's merged config. For agent_core:

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/agent_core
uv run python -c "
from src.config.loader import load_config
import os
os.environ['DOMAIN'] = 'blue-dots-economy'
cfg = load_config()
print('agent.primary_model:', cfg.agent.primary_model)
print('subagents:', [s.id for s in cfg.agent_workflow.subagents])
print('global_tools:', cfg.agent_workflow.global_tools)
"
```

Expected:
- No `ValidationError` from Pydantic.
- Prints two subagent ids `profile_intake` and `job_match_apply` (plus `ended` and `clarification`).
- Prints global_tools list of 4.

Repeat the same pattern for `action_gateway`, `memory_layer`, `trust_layer`, `observability_layer`, `knowledge_engine`, `reach_layer`. Each module's `config/loader.py` should load without error.

- [ ] **Step 2: Verify Pydantic model loads end-to-end** (best-effort — may be skipped if loader path varies)

If the loader path differs per module, fall back to: `cd <module>; uv run python -m src.config.loader` — most modules expose this hook.

- [ ] **Step 3: Hand off to user for live test**

Tell the user the configs are ready under `dev-kit/configs/blue-dots-economy/` and provide the test command:

```bash
export ANTHROPIC_API_KEY=...      # or OPENAI_API_KEY=...
export BLUE_DOTS_API_KEY=<bearer token from the curl examples>
cd automation/docker
DOMAIN=blue-dots-economy docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml run --rm reach_layer
```

**Do not commit. Wait for the user to test and confirm.**

---

## Self-review

1. **Spec coverage** — every spec section has a task:
   - Slug/domain (§1) — Task 1–7 (`observability.domain: "blue_dot"` in each file)
   - Two subagents + Manager rule (§2) — Task 2
   - 4 tools (§3) — Task 1 (definitions) + Task 2 (`connectors.read`/`connectors.write` invocation rules)
   - Memory shape (§4) — Task 3
   - Trust + DPDP consent (§5) — Task 4
   - No KE (§6) — Task 7 + tool registration in Task 2
   - Observability outcomes (§7) — Task 5
   - Voice-only reach (§8) — Task 6
   - File layout (§9) — Tasks 1–7
2. **Placeholder scan** — none. Task 6 has a hard-coded English voice_id of `""` (relying on framework default); flagged as not a placeholder but an intentional default.
3. **Type consistency** — `profile_item_id` and `selected_job_item_id` are used consistently across Tasks 1, 2, 3. `BLUE_DOTS_API_KEY` env var name appears once (Task 1).
4. **No commits per user instruction** — implementation steps explicitly omit `git add` / `git commit` lines; Task 8 hands the branch over for the user to test before deciding when to commit.
