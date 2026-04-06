# AI Diffusion DPG — Architecture & Implementation Status

> **Single source of truth** for system design, block responsibilities, runtime behaviour, and implementation status.
> Status legend: ✅ Complete · 🟡 Stubbed (correct interface, lightweight behaviour) · ⏳ Pending · ❌ Known gap

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Design Decisions & Changes from Original Spec](#2-design-decisions--changes-from-original-spec)
3. [DPG Blocks](#3-dpg-blocks)
4. [Runtime Turn Sequence](#4-runtime-turn-sequence)
5. [Module Interaction Rules](#5-module-interaction-rules)
6. [Configuration Architecture](#6-configuration-architecture)
7. [KKB Domain — User Journey Model](#7-kkb-domain--user-journey-model)
8. [Implementation Status](#8-implementation-status)
9. [Stub Replacement Guide](#9-stub-replacement-guide)
10. [Out of Scope](#10-out-of-scope)

---

## 1. System Overview

The framework assembles AI-powered voice/chat systems from **7 standardised DPG building blocks** configured per-domain via a **Domain Configuration Kit** (YAML). Runtime block boundaries are fixed; all domain intelligence is external (config-driven).

The reference domain is **KKB (Kaam Ki Baat)** — a labour-market assistant helping informal workers in India find trades, check market salaries, and apply to ONEST job postings. Entry point: dial 5226.

### Ports

| Block | Port |
|---|---|
| Agent Core | 8000 |
| Knowledge Engine | 8001 |
| Memory Layer | 8002 |
| Trust Layer | 8003 |
| Observability Layer | 8004 |
| Action Gateway | 9999 |

---

## 2. Design Decisions & Changes from Original Spec

### NLU moved from Knowledge Engine → Agent Core

**Original design:** NLU (intent classification, entity extraction) was inside Knowledge Engine.

**Current implementation:** NLU runs entirely inside Agent Core (`preprocessing/nlu_processor.py`) before KE is called. NLU results are passed to KE in the request body.

**Why:** NLU drives early-exit decisions (low-confidence bail-out) and is coupled to Language Normalisation sequencing — both Agent Core responsibilities. Moving it inward keeps KE stateless and retrieval-focused.

### Language Normalisation is also in Agent Core

Language normalisation (dialect detection, code-switching, transliteration) runs in Agent Core (`preprocessing/language_normaliser.py`) using a haiku model override, before NLU.

### Memory Layer: Redis + Memgraph (not in-process dict)

**Original design:** Memory Layer planned as in-process dict stub.

**Current implementation:** Redis (session/profile store, RedisJSON) + Memgraph (context graph — typed attribute nodes per session). SQLite was specified in the design doc for audit/cross-session data; current implementation uses Memgraph for the persistent store instead.

**Memgraph context graph:** Each session is a `Session` node connected to `Attribute` nodes via typed relationship edges (e.g., `[:HAS_TRADE]`, `[:HAS_LOCATION]`). Edge types come from config (`profile_collection.profile_graph_relations`), never hardcoded. One graph query gives the LLM its complete context — no conversation history needed.

### Knowledge Engine — conditional call (known gap)

**Design spec:** KE RAG is a tool the LLM calls only when domain knowledge is needed. Subagents whose tool list does not include knowledge tools (e.g. `profile_building`) should never call KE.

**Current implementation:** KE is called unconditionally on every turn, wasting latency on profile-building and other non-retrieval subagent turns.

**Required fix:** KE should only be called when the active subagent's tool list includes knowledge tools. Subagent tool lists are defined in `dev-kit/configs/<domain>/agent_core.yaml`.

### Fail-Open Trust Layer (known gap)

Current Trust Layer returns "allow" when the service is unreachable. Production must be fail-closed (default block on service failure).

### `/internal/llm/call` endpoint implemented but not wired

Agent Core exposes `POST /internal/llm/call` as an LLM proxy for future blocks. Implemented in `servers/orchestration_server.py` but no block calls it yet.

### Multimodal Input Handler disabled

`knowledge_engine/blocks/multimodal_input_handler.py` exists but is disabled via `enabled: false` in config. Placeholder for future image/audio input.

### Three-Tier config model: tools, not DPGs

The configuration toolchain is **not part of the runtime architecture**. It operates outside the deployed system:
- **Tier 1 — Configuration Agent:** An AI interviewer that turns a domain expert's natural-language answers into structured YAML. Not yet built.
- **Tier 2 — YAML Configuration:** The canonical runtime source of truth. Read by Agent Core at startup. This is what the 7 DPGs consume.
- **Tier 3 — Live Tuning Dashboard:** A management UI that reads Observability Layer signals and patches YAML post-deployment. Not yet built.

---

## 3. DPG Blocks

### Agent Core ✅

Sole orchestrator and sole LLM caller. Stateless between turns.

**Responsibilities:**
- Read session state from Memory Layer at turn start.
- Consent gate: if `ask_for_consent: true` in config and `user_storage_mode` not yet set, deliver scripted consent prompt (turn 1) or evaluate response via Trust Layer `/consent/verify` and write `user_storage_mode` to Memory Layer (turn 2).
- Input safety check via Trust Layer (mandatory) — passes `active_risks` from NLU when available.
- Language Normalisation (internal — `preprocessing/language_normaliser.py`).
- NLU (internal — `preprocessing/nlu_processor.py`). Outputs intent, entities, confidence, and optional `active_risks`. Early exit if confidence < threshold.
- Pre-LLM guardrail assembly via Trust Layer `/assemble_constraints` — returns prompt constraints, required disclosures, and action gates when active risks are present.
- Manager Agent routing: select active subagent and tool list based on `current_subagent_id` + NLU intent, following routing rules defined in `dev-kit/configs/<domain>/agent_core.yaml`.
- Assemble retrieval context via Knowledge Engine (passes NLU results + session state in body).
- LLM call #1 — system prompt = subagent prompt + guardrail constraints + required disclosures.
- Tool-use loop if `tool_use` block returned: route to Action Gateway → append `tool_result` → LLM call #2. Bounded by `max_tool_rounds`. Tool list filtered by `action_gates`.
- Output safety check via Trust Layer (mandatory).
- Deliver response.
- Write state to Memory Layer (async, after response) — includes `current_subagent_id` and `user_storage_mode`.
- Emit turn event to Observability Layer (async, after response). Block/escalate turns also emit — observability never skipped.

**Key files:**
- `agent_core/src/orchestrator.py` — main turn handler
- `agent_core/src/manager_agent.py` — system prompt + tool selection, tool-use loop
- `agent_core/src/llm_wrapper/claude_wrapper.py` — only file that imports `anthropic`
- `agent_core/src/preprocessing/language_normaliser.py`
- `agent_core/src/preprocessing/nlu_processor.py`
- `agent_core/src/tool_registry.py`
- `agent_core/src/http_clients/` — HTTP adapters for all downstream blocks
- `agent_core/src/interfaces/` — abstract base classes for all block contracts
- `agent_core/src/servers/orchestration_server.py` — FastAPI: `/process_turn`, `/health`, `/internal/llm/call`

**Tests:** 177 tests, 90% line coverage.

---

### Knowledge Engine ✅

Assembles retrieval context for the LLM prompt. Receives NLU results and session state in the request body. Does **not** call Memory Layer or Agent Core. Stateless.

**Internal blocks:**

| Block | Status | Description |
|---|---|---|
| Glossary & Domain Vocabulary | ✅ | Maps colloquial/dialect terms to canonical concepts (e.g., "kaam chahiye" → `market_truth_query`). Config-driven. |
| Static Knowledge Base | ✅ | ChromaDB semantic RAG. `paraphrase-multilingual-MiniLM-L12-v2` embeddings. Top-3 chunks, 0.65 similarity threshold. Intent-based doc-type filtering. |
| Multimodal Input Handler | 🟡 | Disabled via config (`enabled: false`). Placeholder for future image/audio. |

**Data:** `knowledge_engine/data/chroma_db/` — pre-computed vector store from 5 source documents (labour_schemes.pdf, trade_descriptions.pdf, training_institutes.csv, bridge_income_options.pdf, onest_market_truth.csv).

**Key files:**
- `knowledge_engine/src/engine.py`
- `knowledge_engine/src/blocks/glossary.py`
- `knowledge_engine/src/blocks/static_knowledge_base.py`
- `knowledge_engine/src/servers/server.py` — FastAPI: `/assemble_prompt`, `/health`

**Tests:** 87 tests, ≥82% line coverage.

---

### Memory Layer ✅

Manages state at three scopes. Agent Core reads at turn start and writes asynchronously after response.

**State scopes:**

| Scope | Backing Store | Description |
|---|---|---|
| Turn/Session | Redis (RedisJSON, TTL) | Profile: permanent for consent=true, TTL 4h for consent=false. Session: TTL 24h / 4h. |
| Context Graph | Memgraph | Typed attribute graph per session (`Session` node → `Attribute` nodes via domain edge types). One query gives full LLM context. |
| Audit / Cross-session | (design: SQLite) | Turn history written for compliance only; never read back into LLM context. Not yet implemented. |

**Redis keys:**
- `profile:{phone_number}` — RedisJSON, user profile with all 5 entity layers
- `session:{session_id}` — RedisJSON, workflow_step, collection_round, turn history
- `user_sessions:{phone_number}` — Sorted Set, reverse index for session lookup by phone

**Memgraph edge types (KKB, from config):** `HAS_TRADE`, `HAS_LOCATION`, `HAS_EDUCATION_LEVEL`, `HAS_EXPERIENCE_YEARS`, `HAS_INCOME_URGENCY`, `HAS_COMMUTE_PREFERENCE`, `HAS_SALARY_EXPECTATION`, `HAS_SECTOR_PREFERENCE`, `HAS_TRAINING_PREFERENCE`, `HAS_GROWTH_HORIZON`, `HAS_LANGUAGE_PREFERENCE`.

**Key files:**
- `memory_layer/src/memory_layer.py` — 5-method public interface: `context_bundle()`, `write()`, `flush_session()`, `get_active_sessions()`, `delete_user()`
- `memory_layer/src/session_store.py` — RedisSessionStore
- `memory_layer/src/graph_user_store.py`, `graph_journey_store.py`, `graph_context_store.py`
- `memory_layer/src/server.py` — FastAPI: `/session/read`, `/session/write`, `/profile/{session_id}`, `/session/{session_id}`, `/health`

---

### Trust Layer 🟡

Mandatory safety gate. Stateless. Runs on every turn — never skipped. Structured as four internal sub-blocks.

**Internal sub-blocks:**

| Sub-block | File | Responsibility |
|---|---|---|
| ContentBlock | `blocks/content.py` | Phrase-match input/output blocking and escalation routing. Receives `active_risks` from NLU. |
| GuardrailsBlock | `blocks/guardrails.py` | Pre-LLM constraint assembly. Maps active risks → Policy Pack → prompt constraints, disclosures, action gates. |
| ConsentBlock | `blocks/consent.py` | Evaluates user message against consent/decline phrases. Stateless — Agent Core owns flag management. |
| HiTLBlock | `blocks/hitl.py` | Escalation queue. Returns `holding_message` and `ticket_id`. Queue backend configurable (log → Redis/webhook). |

**Endpoints:**

| Endpoint | When called | Purpose |
|---|---|---|
| `POST /check/input` | Pre-LLM | Phrase-match + risk-signal input check. Returns `allow`, `block`, or `escalate`. |
| `POST /assemble_constraints` | Pre-LLM, after input passes | Returns guardrail control artifacts for system prompt injection. |
| `POST /check/output` | Post-LLM | Output phrase-match and guardrail contract check. |
| `POST /consent/verify` | Turn 2 of fresh session | Evaluates user response against consent phrases. Returns `granted: bool`. |
| `POST /check/consent` | Before write/identity tool execution | Verifies connector-level consent. Fail-closed. |
| `POST /escalate` | When input returns `"escalate"` | Queues HiTL escalation, returns holding message. |
| `GET /health` | Liveness probe | — |

**Fail-closed:** All endpoints return `block` / `deny` on internal error. Agent Core's HTTP client enforces this — never fail-open.

**Known gaps:**
- No ML-based semantic matching (ContentBlock uses phrase-match only).
- HiTL queue: `log` backend only. `redis` and `webhook` backends reserved — tracked in GH issue "feat(trust-layer): implement production HiTL escalation queue".
- HiTL queue: output-check escalation (`trust_output.action == "escalate"`) does not yet call `self._trust.escalate(...)` — deferred to HiTL queue issue.
- `check_consent`: SQLite consent store writes consent when `verify_consent` returns True. Cross-session consent persistence is in-process only; a shared consent store is needed for multi-instance deployments.

**Key files:**
- `trust_layer/src/trust_layer.py` — TrustLayer orchestrator
- `trust_layer/src/blocks/content.py`, `guardrails.py`, `consent.py`, `hitl.py`
- `trust_layer/src/server.py` — FastAPI: all endpoints above
- `trust_layer/src/models.py` — all Pydantic request/response types

**Tests:** 39 tests, 100% coverage (ContentBlock only; new sub-blocks require new test suites).

---

### Action Gateway 🟡

Sole interface with external systems. Executes tool calls expressed by the LLM. LLM never calls APIs directly. Write/identity connectors require Trust Layer consent before execution.

**Current stub:** `MockActionGateway` calls `mock_server.py` which returns hardcoded fixture data.

**Available tools (KKB domain):**

| Tool | Type | Description |
|---|---|---|
| `onest_market_lookup` | read | Returns trade, salary range, market signal, top employers. Fixture: 3 trades (electrician, welder, fitter). |
| `onest_apply` | write | Submits job application. Requires Trust Layer consent. Currently returns `applied: true` for all requests. |

**Key files:**
- `action_gateway/src/mock_gateway.py`
- `action_gateway/src/mock_server.py` — FastAPI mock ONEST API on port 9999

---

### Reach Layer 🟡

Normalises inbound channels and delivers responses.

**Current stub:** `CLIReachLayer` — reads stdin, writes stdout. Single session ID per process. Blocking HTTP POST to Agent Core `/process_turn`.

**Planned production channels:** WhatsApp (Gupshup/Twilio), VOIP (Exotel/Twilio, inbound 5226), Web (WebSocket), Mobile SDK. Outbound campaigns: re-engagement, alerts, follow-through.

**Key files:**
- `reach_layer/src/cli_reach.py`

---

### Observability Layer 🟡

Async-only observability. Emits turn events after response delivery. Never in the response path.
All 7 blocks self-instrument via the shared `dpg_telemetry` package (installed from `observability_layer/`).
Telemetry flows via OTLP/gRPC to an OTel Collector sidecar.

**`dpg_telemetry` package:** Exposes `init_otel(service_name, config)`, `get_tracer()`, `get_meter()`.
Every block calls `init_otel()` at startup. Configures TracerProvider, MeterProvider, OTLP exporter,
W3C propagator, and resource attributes from config.

**Block instrumentation:**

| Block | Key spans | Key metrics |
|---|---|---|
| `agent_core` | `orchestrator.turn`, `llm.call` | `llm.tokens`, `turn.latency_ms` |
| `trust_layer` | `trust.input_check`, `trust.output_check` | `trust.blocks` |
| `knowledge_engine` | `ke.prompt_assemble`, `ke.rag_retrieve` | `rag.retrieved_docs` |
| `memory_layer` | `memory.read`, `memory.write` | `memory.latency_ms` |
| `action_gateway` | `action.execute` | `action.calls` |
| `reach_layer` | `reach.inbound`, `reach.outbound` | `reach.sessions` |

**Domain config schema:** `ObservabilityConfig` (Pydantic v2) defines the full outcome lifecycle,
metric instrument types, SLI thresholds, and PII field exclusions (separate lists for telemetry
vs. audit log — `user_id` allowed in traces for dashboarding, excluded from audit for DPDP Act compliance).

**HTTP service (port 8004):** `/emit/turn` (backward-compatible; routes to `OutcomeTracker`),
`/emit/signal`, `/validate-config`, `/health`.

**Current stub:** `OtelObservabilityLayer` with `OutcomeTracker` — functional OTel instrumentation,
no persistent audit DB yet.

**Planned production additions:** Audit log DB (DPDP Act), persistent outcome store, Grafana dashboards.

**Key files:**
- `observability_layer/src/dpg_telemetry/` — shared bootstrap package
- `observability_layer/src/schema/config.py` — `ObservabilityConfig` schema
- `observability_layer/src/outcome_tracker.py` — lifecycle state machine
- `observability_layer/src/otel_observability_layer.py` — `OtelObservabilityLayer`
- `observability_layer/src/server.py` — FastAPI: `/emit/turn`, `/emit/signal`, `/validate-config`, `/health`

**Tests:** ≥70% coverage.

---

## 4. Runtime Turn Sequence

```
Reach Layer (input)
  │
  ▼
Agent Core: read state ← Memory Layer                     [session state, current_subagent_id, user_storage_mode]
  │
  ▼
Agent Core: consent gate                                  [only if ask_for_consent: true in dpg config]
  │  user_storage_mode=None, no prior turns → return consent prompt (no LLM)
  │  user_storage_mode=None, prior turn exists → POST /consent/verify → write user_storage_mode → continue
  │  user_storage_mode set → skip
  ▼
Agent Core: NLU (internal)                                [intent, entities, confidence, active_risks (optional)]
  │
  ▼ (low confidence → early exit)
Agent Core: POST /check/input → Trust Layer               [MANDATORY — passes active_risks]
  │
  ▼ (block → TurnResponse(blocked_input_message))
    (escalate → POST /escalate → TurnResponse(holding_message))
  ▼ (allow → continue)
Agent Core: Language Normalisation (internal)             [dialect, code-switching, transliteration]
  │
  ▼
Agent Core: POST /assemble_constraints → Trust Layer      [if active_risks present]
  │  returns: prompt_constraints, required_disclosures, action_gates, refusal_templates
  ▼
Agent Core: Manager Agent selects subagent + tools        [current_subagent_id + NLU intent → routing rules in config]
  │  system_prompt = subagent_prompt + guardrail_constraints + required_disclosures
  │  tool list filtered by action_gates
  ▼
Agent Core: LLM call #1 (ClaudeLLMWrapper)
  │
  ├─ [tool_use block returned]
  │    Agent Core: execute tool → Action Gateway
  │    Agent Core: LLM call #2 (with tool_result)
  │
  ▼
Agent Core: POST /check/output → Trust Layer              [MANDATORY]
  │
  ▼ (block → TurnResponse(output_blocked_message))
Agent Core: deliver response → Reach Layer
  │
  ├─ [async] write state → Memory Layer                   [current_subagent_id, user_storage_mode, session data]
  └─ [async] emit TurnEvent → Observability Layer         [all turns including blocked/escalated; carries trace_id]
```

**Latency target:** 800–1200ms per turn (voice-first).
- One LLM call for most turns; two for tool turns.

---

## 5. Module Interaction Rules

Only Agent Core initiates calls to other blocks. No other cross-module calls exist.

| Caller | Callee | Purpose |
|---|---|---|
| Agent Core | Memory Layer | Read state at turn start; write state after response (async) |
| Agent Core | Trust Layer | Check input (before LLM); check output (before user) |
| Agent Core | Knowledge Engine | Assemble retrieval context (NLU results + session state in body) |
| Agent Core | Action Gateway | Execute LLM-requested tool calls |
| Agent Core | Observability Layer | Emit turn metadata (async, daemon thread) |
| Reach Layer | Agent Core | POST /process_turn (blocking) |
| Action Gateway | External systems | Only on instruction from Agent Core |

**No other cross-module calls are permitted.**

---

## 6. Configuration Architecture

### Three-Tier Config Model (overview)

| Tier | What it is | Status |
|---|---|---|
| Tier 1 — Configuration Agent | AI interviewer that generates YAML from domain expert's natural language | ⏳ Not yet built |
| Tier 2 — YAML Configuration | Canonical runtime source of truth. Read by Agent Core at startup. | ✅ |
| Tier 3 — Live Tuning Dashboard | Management UI reading Observability Layer signals to patch YAML post-deployment | ⏳ Not yet built |

These tiers are **configuration tooling — not runtime DPGs**. The 7 DPGs remain the architecture.

### Two-level YAML model (runtime)

```
dev-kit/
├── dpg/                          # Framework defaults (same across all domains)
│   ├── agent_core.yaml
│   ├── knowledge_engine.yaml
│   └── ...
├── configs/
│   └── kkb/                      # KKB domain overrides
│       ├── agent_core.yaml       # primary_model, fallback_model, intents, connectors
│       ├── knowledge_engine.yaml # glossary mappings, RAG sources, intent filters
│       ├── memory_layer.yaml     # graph schema (profile_graph_relations), merge rules
│       ├── trust_layer.yaml      # blocked phrases, escalation topics, consent phrases
│       ├── action_gateway.yaml   # connector endpoints, timeout
│       ├── reach_layer.yaml      # CLI prompts, Agent Core endpoint
│       └── observability_layer.yaml # OTel config, outcome lifecycle, SLI thresholds
└── loader.py                     # Deep-merge: dpg/*.yaml overridden by configs/<domain>/*.yaml
```

### YAML section → DPG mapping

| YAML Section | DPG Configured |
|---|---|
| `agent` | Agent Core |
| `channels` | Reach Layer |
| `knowledge` | Knowledge Engine |
| `connectors` | Action Gateway |
| `conversation` | Agent Core + Knowledge Engine |
| `trust` | Trust Layer |
| `observability` | Observability Layer |
| `state` | Memory Layer |

### Rule: nothing domain-specific may be hardcoded

Model names, persona text, tool definitions, guardrail rules, intent definitions, connector endpoints, TTLs, thresholds, and graph edge types must all come from YAML. Config is read once at startup. Never re-read inside request paths.

---

## 7. KKB Domain — User Journey Model

This section describes the KKB-specific conversation design implemented in the domain config. It is not part of the DPG framework itself — a different domain would configure a different journey.

### User Personas

| Persona | Profile | Primary Constraint |
|---|---|---|
| ITI Graduate ★ | 19–24, trade-certified, first job seeker | Distance + skill confidence |
| Women Returning to Work | 26–38, career gap 2–8 years | Hours + distance + family approval |
| Daily Wage Labourer | 30–45, informal, no fixed employer | Immediacy + daily income certainty |
| AI-Displaced Worker | 35–50, formal sector, job eliminated | Income continuity + dignity |
| Person with Disability | Any age, accessibility needs | Role accessibility + remote options |

★ Primary persona. Others appear at decision-tree branch points.

### Five Mental States (Journey State Machine)

A caller is always in one of five states. Detecting the correct state is the system's primary intelligence task.

| State | `current_mental_state` | System Behaviour |
|---|---|---|
| FOG | `profile_building` (start) | Does not know what they want. Deliver market truth first. Never jump to options. |
| ORIENTATION | `profile_building` → `market_truth` | Collect profile. Then surface live ONEST data. |
| EVALUATION | `skill_check` → `evaluation` | Compare options. Surface decision parameters. Never push one path. Honest trade-offs. |
| COMMITMENT | `commitment` | User decided. Remove friction. Consent mandatory before every action. |
| FOLLOW-THROUGH | `follow_through` | Did employer call? Did course start? Track outcome. Trust is built or broken here. |

### Subagent Graph (KKB)

Conversation flow is defined as a directed graph of subagents in `dev-kit/configs/kkb/agent_core.yaml`. Each subagent has its own system prompt, tool list, valid intents, and routing rules. The orchestrator tracks `current_subagent_id` in session state and advances it on each turn based on NLU intent + routing conditions.

| Subagent ID | Entry Condition | Tools | Terminal |
|---|---|---|---|
| `profile_building` | Session start (first subagent) | none | No |
| `market_truth` | Profile hard minimums met | `onest_market_lookup` | No |
| `skill_check` | User engaged with market truth | `onest_market_lookup` | No |
| `evaluation` | Skill assessed | `onest_market_lookup` | No |
| `commitment` | User ready to apply | `onest_apply` | No |
| `follow_through` | Application submitted | none | No |
| `counsellor_request` | `counsellor_request` global intent | none | Yes |
| `capture_dropoff` | User drops off | none | Yes |
| `ended` | `termination_intent` global intent | none | Yes |
| `clarification` | Fallback (unrecognised input) | none | No |

**Consent** is handled by the orchestrator before the subagent graph is entered — not by any subagent. See Section 4 Runtime Turn Sequence.

### Profile Collection (5 rounds)

| Round | Fields collected | Layer |
|---|---|---|
| 1 | name, age, gender | Identity |
| 2 | disability_status, location | Identity |
| 3 | trade_or_stream, iti_pass_year, iti_institute | Capability |
| 4 | max_distance_km, salary_expectation_min, preferred_shift | Constraint |
| 5 | target_roles, open_to_relocation | Aspiration |

**Hard minimum fields** (required for ONEST query): `capability.trade_or_stream` + `identity.location`. If missing after Round 5, one grace turn asks only those fields. System proceeds regardless of answer.

**Journey layer fields** (never asked, system-tracked): `session_count`, `current_mental_state`, `last_market_truth_delivered`, `options_presented`, `actions_taken`, `outcomes_tracked`, `drop_off_reason`.

### Returning User Entry Points

| Profile state on return | Entry point |
|---|---|
| No profile / expired | Consent gate (if `ask_for_consent: true`), then `profile_building` |
| `user_storage_mode: anonymous` | Consent gate re-runs (new session, `user_storage_mode` cleared), then `profile_building` |
| Partial profile (hard min missing) | Resume `profile_building` at saved `collection_round` |
| Full profile (hard min met) | `market_truth` (skip collection) |
| Active session within TTL | Resume at `current_subagent_id` saved in session |

---

## 8. Implementation Status

### By block

| Block | Status | Notes |
|---|---|---|
| Agent Core | ✅ | Orchestrator, LLM wrapper, preprocessing, tool-use loop, HTTP server. 177 tests, 90% coverage. |
| Knowledge Engine | ✅ | Glossary, ChromaDB RAG, HTTP server. 87 tests, ≥82% coverage. |
| Memory Layer | ✅ | Redis (session/profile) + Memgraph (context graph). HTTP server. |
| Trust Layer | 🟡 | ContentBlock (phrase-match) implemented. GuardrailsBlock, ConsentBlock, HiTLBlock pending. Fail-open (must be fail-closed). |
| Action Gateway | 🟡 | Hardcoded fixture data. No real ONEST API. |
| Reach Layer | 🟡 | CLI stdin/stdout only. |
| Observability Layer | 🟡 | OTel instrumentation across all blocks. OutcomeTracker with KKB lifecycle config. No persistent audit DB yet. |

### By feature

| Feature | Status | Notes |
|---|---|---|
| Language normalisation | ✅ | Dialect, code-switching, transliteration — in Agent Core |
| NLU (intent + entity) | ✅ | Intent classification, entity extraction, confidence — in Agent Core |
| NLU active_risks output | ⏳ | `active_risks: list[str] \| None` field to be added to NLUResult |
| Subagent-based routing | ✅ | current_subagent_id tracked; routing rules driven by config graph |
| Semantic RAG | ✅ | ChromaDB, multilingual embeddings, intent-based filtering |
| Glossary mapping | ✅ | Config-driven colloquial → canonical |
| LLM call with retry/fallback | ✅ | Exponential backoff, primary/fallback model switching |
| Tool-use loop | ✅ | Bounded by `max_tool_rounds`, action_gates from Trust Layer applied |
| KE conditional call (tool-only) | ❌ | KE called unconditionally; should only be called when subagent tool list includes knowledge tools |
| Session state (turn + session) | ✅ | Redis with TTL |
| Persistent profile store | ✅ | Redis RedisJSON |
| Context graph | ✅ | Memgraph typed attribute graph |
| Audit log / SQLite store | ⏳ | Design specifies SQLite for turn history/audit; not yet implemented |
| Input trust check (ContentBlock) | ✅ | Phrase-match implemented |
| Output trust check (ContentBlock) | ✅ | Phrase-match implemented |
| GuardrailsBlock + /assemble_constraints | ⏳ | Pre-LLM constraint assembly; Risk Taxonomy + Policy Pack from config |
| ConsentBlock + /consent/verify | ⏳ | DPDP consent phrase evaluation |
| HiTLBlock + /escalate | ⏳ | Escalation queue with holding_message |
| Orchestrator consent gate | ⏳ | user_storage_mode flag logic; replaces greeting subagent |
| Fail-closed Trust Layer | ⏳ | All endpoints and AC HTTP client must block on error, not allow |
| Real ONEST connector | ⏳ | Replace MockActionGateway |
| WhatsApp/VOIP/Web channels | ⏳ | Replace CLIReachLayer |
| Audit log / eval pipeline | ⏳ | Persistent audit DB + eval service in Observability Layer |
| Configuration Agent (Tier 1) | ⏳ | AI YAML generator tool for domain experts |
| Live Tuning Dashboard (Tier 3) | ⏳ | Dashboard reading Observability Layer signals |
| `/internal/llm/call` wiring | ⏳ | Endpoint implemented, no block calls it yet |
| Profile building subagent flow | 🟡 | Subagent graph implemented; full profile collection partially complete |
| Multimodal input | 🟡 | Handler exists, disabled via config |
| Docker compose | ✅ | `automation/docker/docker-compose.dev.yml` |
| Helm charts | 🟡 | `automation/helm/` — structure exists, completeness unverified |

---

## 9. Stub Replacement Guide

Each stub implements the exact same abstract base class interface. Swapping requires **no changes to Agent Core or any other block**.

### Trust Layer

1. Implement `GuardrailsBlock` (`trust_layer/src/blocks/guardrails.py`): loads Policy Pack from config, maps `active_risks` → prompt constraints + disclosures + action gates. Wire into `POST /assemble_constraints`.
2. Implement `ConsentBlock` (`trust_layer/src/blocks/consent.py`): phrase-match user message against `consent_phrases` / `decline_phrases` from config. Wire into `POST /consent/verify`.
3. Implement `HiTLBlock` (`trust_layer/src/blocks/hitl.py`): write escalation record to queue backend (start with log, add Redis/webhook via config). Wire into `POST /escalate`.
4. Implement orchestrator consent gate in `agent_core/src/orchestrator.py`: `user_storage_mode` flag logic, call `/consent/verify` on turn 2, write flags to Memory Layer. Remove `greeting` subagent.
5. Add `active_risks: list[str] | None` to `NLUResult` in `agent_core/src/preprocessing/nlu_processor.py`.
6. Add `assemble_constraints()`, `verify_consent()`, `escalate()` to `agent_core/src/http_clients/trust_layer_client.py`.
7. Change all Trust Layer HTTP error handlers in Agent Core from fail-open to **fail-closed**.
8. Update `trust_layer/src/server.py` with all new endpoints and wire all sub-blocks through `TrustLayer` orchestrator in `trust_layer/src/trust_layer.py`.

### Action Gateway

1. Implement `ActionGatewayBase` (`agent_core/src/interfaces/action_gateway.py`): `list_available_tools()`, `execute()`.
2. Replace fixture responses with real ONEST API calls. Add mandatory field validation for `onest_apply`.
3. Wire into `action_gateway/src/server.py`.

### Reach Layer

1. Implement `ReachLayerBase` (`agent_core/src/interfaces/reach_layer.py`): `receive()`, `deliver()`.
2. Implement per-channel adapter (WhatsApp webhook, VOIP SIP, etc.).
3. Wire into the reach layer entrypoint.

### Observability Layer

1. Implement persistent audit DB writer in `observability_layer/src/audit_store.py` implementing `AuditStoreBase`.
2. Wire into `OtelObservabilityLayer.emit_turn()` — write PII-excluded fields to audit DB asynchronously.
3. Implement Grafana dashboard provisioning in `automation/docker/grafana/provisioning/`.
4. Implement `OutcomeTracker` placement.rate gauge computation (ratio of placed/total sessions).

---

## 10. Out of Scope

- ASR/TTS pipeline (speech-to-text, text-to-speech)
- Model training or fine-tuning
- Infrastructure provisioning or IaC
- Multi-tenancy and cost attribution
- Testing tooling beyond per-module pytest
- Versioning and rollback of domain configs
