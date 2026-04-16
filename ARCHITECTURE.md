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
| Telephony Adapter | 8006 |
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

### Knowledge Engine — conditional call (resolved)

**Design spec:** KE RAG is a tool the LLM calls only when domain knowledge is needed. Subagents whose tool list does not include knowledge tools (e.g. `profile_building`) should never call KE.

**Implementation:** KE retrieval is now an internal LLM tool (`knowledge_retrieval`, connector type `internal`). The LLM invokes it only when the active subagent's tool list includes `knowledge_retrieval`. `ToolRegistry.get_route()` returns `"knowledge_engine"` for this tool, and `ManagerAgent` routes it to `_execute_knowledge_retrieval()` instead of the Action Gateway. Subagents without `knowledge_retrieval` in their tool list never trigger a KE call. Subagent tool lists are defined in `dev-kit/configs/<domain>/agent_core.yaml`.

### Fail-Closed Trust Layer

All Trust Layer endpoints return `block` / `deny` on internal error. The Agent Core's `TrustLayerHttpClient` is fail-closed. This was a known gap (formerly "Fail-Open Trust Layer") that has been resolved.

### Multimodal Input Handler disabled

`knowledge_engine/blocks/multimodal_input_handler.py` exists but is disabled via `enabled: false` in config. Placeholder for future image/audio input.

### Three-Tier config model: tools, not DPGs

The configuration toolchain is **not part of the runtime architecture**. It operates outside the deployed system:
- **Tier 1 — Configuration Agent:** ✅ Implemented. A FastAPI server with a React SPA frontend that interviews a domain expert through a structured conversation (8 phases) and generates all 7 domain YAML files. Lives in `dev-kit/dev_kit/agent/`.
- **Tier 2 — YAML Configuration:** The canonical runtime source of truth. Read by each DPG at startup. This is what the 7 DPGs consume.
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
- `agent_core/src/workflow_loader.py` — loads subagent graph from config at startup
- `agent_core/src/http_clients/` — HTTP adapters for all downstream blocks
- `agent_core/src/interfaces/` — abstract base classes for all block contracts
- `agent_core/src/servers/orchestration_server.py` — FastAPI: `/process_turn`, `/health`, `/internal/llm/call`

**Tests:** 414 tests, ≥70% line coverage.

**Known gaps:**
- HiTL escalation for output path not wired: `orchestrator.py` line 646 — output escalation TODO deferred to HiTL queue issue.

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
- `knowledge_engine/src/server.py` — FastAPI: `POST /retrieve`, `/health`

**Tests:** 117 tests, ≥70% line coverage.

---

### Memory Layer ✅

Manages state at three scopes. Agent Core reads at turn start and writes asynchronously after response.

**State scopes:**

| Scope | Backing Store | Status | Description |
|---|---|---|---|
| Turn/Session | Redis (RedisJSON, TTL) | ✅ | Profile: permanent for consent=true, TTL 4h for consent=false. Session: TTL 24h / 4h. |
| Context Graph | Memgraph | ✅ | Typed attribute graph per session (`Session` node → `Attribute` nodes via domain edge types). One query gives full LLM context. |
| Audit / Cross-session | SQLite (`audit_store`) | ✅ | Two purposes: (1) session lifecycle events with `consent_given` for DPDP compliance; (2) raw turn-by-turn conversation transcript (user_message + system_message + subagent_id + intent + model + latency_ms per turn). Never read back into LLM context. Distinct from OTel telemetry. Fully implemented. |

**Redis keys:**
- `profile:{phone_number}` — RedisJSON, user profile with all 5 entity layers
- `session:{session_id}` — RedisJSON, workflow_step, collection_round, turn history
- `user_sessions:{phone_number}` — Sorted Set, reverse index for session lookup by phone

**Memgraph edge types (KKB, from config):** `HAS_TRADE`, `HAS_LOCATION`, `HAS_EDUCATION_LEVEL`, `HAS_EXPERIENCE_YEARS`, `HAS_INCOME_URGENCY`, `HAS_COMMUTE_PREFERENCE`, `HAS_SALARY_EXPECTATION`, `HAS_SECTOR_PREFERENCE`, `HAS_TRAINING_PREFERENCE`, `HAS_GROWTH_HORIZON`, `HAS_LANGUAGE_PREFERENCE`.

**Public interface (5 methods + audit):** `context_bundle()`, `write()`, `flush_session()`, `get_active_sessions()`, `delete_user()`, plus audit write.

**Key files:**
- `memory_layer/src/memory_layer.py` — public interface
- `memory_layer/src/session_store.py` — RedisSessionStore
- `memory_layer/src/graph_user_store.py`, `graph_journey_store.py`, `graph_context_store.py`
- `memory_layer/src/audit_store.py` — SQLite audit log (fully implemented)
- `memory_layer/src/server.py` — FastAPI: 10 endpoints including `/context_bundle`, `/write`, `/flush_session`, `/audit`, `/users/{user_id}/active-history`, `/profile/{session_id}`, `/session/{session_id}`, `/health`

**Tests:** 200 tests.

---

### Trust Layer 🟡

Mandatory safety gate. Stateless. Runs on every turn — never skipped. Structured as four internal sub-blocks.

**Internal sub-blocks:**

| Sub-block | File | Status | Responsibility |
|---|---|---|---|
| ContentBlock | `blocks/content.py` | ✅ | Phrase-match input/output blocking and escalation routing. Receives `active_risks` from NLU. |
| GuardrailsBlock | `blocks/guardrails.py` | ✅ | Pre-LLM constraint assembly. Maps active risks → Policy Pack → prompt constraints, disclosures, action gates. |
| ConsentBlock | `blocks/consent.py` | ✅ | Evaluates user message against consent/decline phrases. Stateless — Agent Core owns flag management. |
| HiTLBlock | `blocks/hitl.py` | ⏳ | Escalation queue. Returns `holding_message` and `ticket_id`. Queue backend configurable (log → Redis/webhook). |

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

**Tests:** 39 tests, 100% coverage (ContentBlock). GuardrailsBlock/ConsentBlock/HiTLBlock coverage pending.

---

### Action Gateway ✅

Sole interface with external systems. Executes tool calls expressed by the LLM. LLM never calls APIs directly. Write/identity connectors require Trust Layer consent before execution.

**Architecture:** Generic adapter framework. Each external tool is described in `action_gateway.yaml` under `tools:[]`; the gateway instantiates the correct adapter at startup based on `type`. Agent Core fetches the full tool-definition list at startup via `GET /tools` and includes it in every LLM request.

**Adapter types:**

| Type | Class | Status | Description |
|---|---|---|---|
| `rest_api` | `RestApiAdapter` | ✅ | Calls external HTTP APIs. Supports `api_key`, `bearer`, and `none` auth. Params sourced from `agent` (LLM-supplied) or `static` (config). |
| `mcp` | `McpAdapter` | ✅ | Connects to MCP servers via the Model Context Protocol. Tool definitions fetched from server at startup. |
| `database` | — | ⏳ | Reserved |
| `file_upload` | — | ⏳ | Reserved |
| `gRPC` | — | ⏳ | Reserved |
| `GraphQL` | — | ⏳ | Reserved |

**Adding a new tool:** Add a `tools[]` entry in `action_gateway.yaml` (type, category, auth, endpoints, params) and restart. No code changes required.

**Adding a new adapter type:** Implement `ToolAdapter` ABC (`src/adapters/base.py`) and register the class in `ADAPTER_TYPES` in `src/registry/adapter_factory.py`.

**Endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /tools` | Returns tool definitions in Anthropic tool-use format. Agent Core fetches this at startup. |
| `POST /execute` | Executes a single tool call. Never raises — returns `success: false` with structured error on failure. |
| `GET /health` | Liveness probe. |

**Key files:**
- `action_gateway/src/server.py` — FastAPI: `GET /tools`, `POST /execute`, `GET /health`
- `action_gateway/src/adapters/base.py` — `ToolAdapter` ABC
- `action_gateway/src/adapters/rest_api.py` — `RestApiAdapter`
- `action_gateway/src/adapters/mcp.py` — `McpAdapter`
- `action_gateway/src/registry/adapter_registry.py` — `AdapterRegistry`: holds all instantiated adapters
- `action_gateway/src/registry/adapter_factory.py` — `AdapterFactory`: instantiates adapters from YAML config
- `action_gateway/src/models.py` — Pydantic request/response types

**Tests:** 124 tests.

---

### Reach Layer 🟡

Normalises inbound channels and delivers responses.

**Current implementation:** CLI adapter (`CLIReachLayer`) reads stdin, writes stdout. Web adapter (`server.py`) serves a single-page chat UI on port 8005 via `POST /chat`.

**Approved exception:** The web adapter's session-restore feature calls Memory Layer `GET /users/{user_id}/active-history` directly before the first turn. This is a scoped exception for the dev/demo web adapter only — all other state access routes through Agent Core.

**Channel implementation status:**

| Channel | Status | Notes |
|---------|--------|-------|
| CLI (stdin/stdout) | ✅ | `CLIReachLayer` — dev/test REPL |
| Web UI | ✅ | FastAPI + single-page chat UI on port 8005; session restore via `GET /user-history/{user_id}` |
| Telephony (VoBiz/Exotel) | 🟡 | `telephony_adapter/` — WebSocket media stream, STT (Raya), TTS (Raya), Agent Core integration; port 8006. `POST /campaign` for outbound. Build from repo root: `docker build -f telephony_adapter/Dockerfile -t telephony_adapter .` |
| Voice / VOIP | ⏳ | Production SIP/PSTN integration (Exotel inbound 5226) — pending |
| WhatsApp | ⏳ | Gupshup/Twilio webhook — pending |
| Mobile SDK | ⏳ | Pending |
| Outbound campaigns | ⏳ | Re-engagement, alerts, follow-through — pending |

**Key files:**
- `reach_layer/src/cli_reach.py` — CLI stdin/stdout adapter
- `reach_layer/server.py` — FastAPI web adapter; `POST /chat`, `GET /user-history/{user_id}`, port 8005

**Tests:** 125+ tests.

---

### Observability Layer ✅

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

**Primary implementation:** `OtelObservabilityLayer` with `OutcomeTracker` — functional OTel instrumentation. Audit trail = Loki (logs) + Jaeger (traces) via OTel Collector; no separate audit DB needed. DPDP PII exclusions enforced at DPG instrumentation layer via `observability.audit.pii_fields_excluded` and `observability.telemetry.pii_fields_excluded` config fields. `ConsoleLogger` is a backward-compatible PoC stub, not the primary implementation.

**Planned production additions:** Grafana dashboard provisioning, persistent outcome store.

**Key files:**
- `observability_layer/src/dpg_telemetry/` — shared bootstrap package (`init_otel`, `get_tracer`, `get_meter`)
- `observability_layer/src/schema/config.py` — `ObservabilityConfig` Pydantic v2 schema
- `observability_layer/src/outcome_tracker.py` — lifecycle state machine
- `observability_layer/src/otel_observability_layer.py` — `OtelObservabilityLayer` (primary implementation)
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

> **Approved exception — Reach Layer web channel:** The web channel's session-restore feature (`GET /user-history/{user_id}` in `reach_layer/server.py`) makes a direct call to the Memory Layer to load chat history before the first turn. This is a deliberate, scoped exception for the dev/demo web adapter only. All other Reach Layer → Memory Layer calls are still prohibited. Future production channel adapters must route state retrieval through Agent Core.

> **Planned exception — Action Gateway caching (#18):** When caching is implemented, Action Gateway will call Knowledge Engine and Memory Layer to read/write cached tool results. This is not yet implemented. For now, Action Gateway only talks to external APIs and MCP servers.

---

## 6. Configuration Architecture

### Three-Tier Config Model (overview)

| Tier | What it is | Status |
|---|---|---|
| Tier 1 — Configuration Agent | AI interviewer that generates YAML from domain expert's natural language. FastAPI server + React SPA frontend in `dev-kit/dev_kit/agent/`. | ✅ Implemented |
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

> **Note — External tool definitions:** Tool definitions for external connectors (name, description, parameters, auth, endpoints) live in `action_gateway.yaml` under `tools:[]`, not in `agent_core.yaml`. Agent Core fetches the assembled tool list from Action Gateway at startup via `GET /tools` and injects it into the LLM request. This keeps tool schema ownership with Action Gateway and removes the need to duplicate connector config in `agent_core.yaml`.

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
| Agent Core | ✅ | Orchestrator, LLM wrapper, preprocessing, tool-use loop, 10-subagent workflow. 414 tests, ≥70% coverage. |
| Knowledge Engine | ✅ | Glossary, ChromaDB RAG, HTTP server (`POST /retrieve`). 117 tests, ≥70% coverage. |
| Memory Layer | ✅ | Redis (session/profile) + Memgraph (context graph) + SQLite (audit). 10 HTTP endpoints. 200 tests. |
| Trust Layer | 🟡 | All 4 sub-blocks implemented. Fail-closed. HiTL: log backend only. Consent: in-process SQLite. |
| Action Gateway | ✅ | Generic adapter framework (RestApiAdapter + McpAdapter). Config-driven via `tools:[]` in `action_gateway.yaml`. Agent Core fetches tool defs at startup via `GET /tools`. 124 tests. |
| Reach Layer | 🟡 | CLI + Web (port 8005). Web calls Memory Layer for session restore (approved exception). Telephony Adapter (port 8006): WebSocket media stream, STT/TTS via Raya, 48 tests, 92% coverage. |
| Observability Layer | 🟡 | OTel instrumentation functional. Audit = Loki+Jaeger via OTel Collector. Grafana dashboards pending. |

### By feature

| Feature | Status | Notes |
|---|---|---|
| Language normalisation | ✅ | Dialect, code-switching, transliteration — in Agent Core |
| NLU (intent + entity) | ✅ | Intent classification, entity extraction, confidence — in Agent Core |
| NLU active_risks output | ✅ | `active_risks: list[str] \| None` field added to NLUResult |
| Subagent-based routing | ✅ | current_subagent_id tracked; routing rules driven by config graph |
| Semantic RAG | ✅ | ChromaDB, multilingual embeddings, intent-based filtering |
| Glossary mapping | ✅ | Config-driven colloquial → canonical |
| LLM call with retry/fallback | ✅ | Exponential backoff, primary/fallback model switching |
| Tool-use loop | ✅ | Bounded by `max_tool_rounds`, action_gates from Trust Layer applied |
| KE conditional call (tool-only) | ✅ | `knowledge_retrieval` internal tool; LLM decides when to call KE; subagents without `knowledge_retrieval` never trigger KE |
| Session state (turn + session) | ✅ | Redis with TTL |
| Persistent profile store | ✅ | Redis RedisJSON |
| Context graph | ✅ | Memgraph typed attribute graph |
| Audit log / SQLite store | ✅ | SQLiteAuditStore fully implemented — session lifecycle events (DPDP consent) + raw turn-by-turn conversation transcript |
| Input trust check (ContentBlock) | ✅ | Phrase-match implemented |
| Output trust check (ContentBlock) | ✅ | Phrase-match implemented |
| GuardrailsBlock + /assemble_constraints | ✅ | GuardrailsBlock implemented; Policy Pack from config; /assemble_constraints endpoint live |
| ConsentBlock + /consent/verify | ✅ | ConsentBlock implemented; phrase evaluation from config; consent_store SQLite (in-process only) |
| HiTLBlock + /escalate | 🟡 | HiTLBlock implemented as log backend only; redis/webhook backends reserved |
| Orchestrator consent gate | ✅ | Consent gate implemented in orchestrator; user_storage_mode flag logic active |
| Fail-closed Trust Layer | ✅ | All endpoints and AC HTTP client are fail-closed (resolved) |
| Reach Layer web adapter | ✅ | Web UI + POST /chat + session restore via Memory Layer (approved exception) |
| Action Gateway adapter framework | ✅ | RestApiAdapter + McpAdapter; config-driven via `tools:[]` |
| Real ONEST connector | ⏳ | Add ONEST tool entry to `action_gateway.yaml` once live API is available |
| Telephony adapter (VoBiz/Exotel) | 🟡 | `telephony_adapter/` — WebSocket media stream, STT/TTS, Agent Core integration, outbound campaign endpoint |
| WhatsApp/Mobile channels | ⏳ | Replace CLIReachLayer for production |
| Grafana dashboard provisioning | ⏳ | `automation/docker/grafana/provisioning/` not yet implemented |
| Configuration Agent (Tier 1) | ✅ | FastAPI + React SPA; conversation-driven YAML generation for all 7 DPGs |
| Live Tuning Dashboard (Tier 3) | ⏳ | Dashboard reading Observability Layer signals |
| Profile building subagent flow | ✅ | Subagent graph implemented; full profile collection partially complete |
| Multimodal input | 🟡 | Handler exists, disabled via config |
| Docker compose | ✅ | `automation/docker/docker-compose.dev.yml` |
| Helm charts | 🟡 | `automation/helm/` — structure exists, completeness unverified |

---

## 9. Stub Replacement Guide

Each stub implements the exact same abstract base class interface. Swapping requires **no changes to Agent Core or any other block**.

### Action Gateway

The adapter framework is production-ready. To connect a new external API:

1. Add a `tools[]` entry to `dev-kit/configs/<domain>/action_gateway.yaml` with `type: rest_api` (or `mcp`), auth config, endpoint URLs, and parameter definitions.
2. Restart — `AdapterFactory` instantiates the adapter from config. No code changes required.
3. To support a new adapter type (e.g. `database`, `gRPC`), implement `ToolAdapter` ABC in `action_gateway/src/adapters/` and register the class in `ADAPTER_TYPES` in `action_gateway/src/registry/adapter_factory.py`.

### Reach Layer

1. Implement `ReachLayerBase` (`agent_core/src/interfaces/reach_layer.py`): `receive()`, `deliver()`.
2. Implement per-channel adapter (WhatsApp webhook, VOIP SIP, etc.).
3. Wire into the reach layer entrypoint.

### Observability Layer

1. Implement Grafana dashboard provisioning in `automation/docker/grafana/provisioning/`.
2. Implement `OutcomeTracker` placement.rate gauge computation (ratio of placed/total sessions).

---

## 10. Out of Scope

- ASR/TTS pipeline (speech-to-text, text-to-speech)
- Model training or fine-tuning
- Infrastructure provisioning or IaC
- Multi-tenancy and cost attribution
- Testing tooling beyond per-module pytest
- Versioning and rollback of domain configs
