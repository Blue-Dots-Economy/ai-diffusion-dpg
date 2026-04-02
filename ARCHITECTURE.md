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
| Learning Layer | 8004 |
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

**Design spec:** KE RAG is a tool the LLM calls only when domain knowledge is needed. For `awaiting_consent` and `profile_building` workflow steps, KE is not in the tool list and is never called.

**Current implementation:** KE is called unconditionally on every turn. This wastes latency on consent/profile-building turns (which need no domain documents).

**Required fix:** KE should be added to the tool list only when `workflow_step = "ready"`. See GitHub issue.

### NLU conditional execution by workflow_step (known gap)

**Design spec:** NLU mode should vary by workflow_step:
- `awaiting_consent` → skip NLU entirely (Trust Layer phrase-match only)
- `profile_building` → entity extraction only (no intent classification)
- `ready` → full NLU (intent + entities + sentiment)

**Current implementation:** NLU always runs in full mode. Not yet conditioned on workflow_step.

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
- **Tier 3 — Live Tuning Dashboard:** A management UI that reads Learning Layer signals and patches YAML post-deployment. Not yet built.

---

## 3. DPG Blocks

### Agent Core ✅

Sole orchestrator and sole LLM caller. Stateless between turns.

**Responsibilities:**
- Read session state from Memory Layer at turn start.
- Input safety check via Trust Layer (mandatory).
- Language Normalisation (internal — `preprocessing/language_normaliser.py`).
- NLU (internal — `preprocessing/nlu_processor.py`). Early exit if intent unknown or confidence < threshold.
- Manager Agent routing: select system prompt and tool list based on `workflow_step`.
- Assemble retrieval context via Knowledge Engine (passes NLU results + session state in body).
- LLM call #1.
- Tool-use loop if `tool_use` block returned: route to Action Gateway → append `tool_result` → LLM call #2. Bounded by `max_tool_rounds`.
- Output safety check via Trust Layer (mandatory).
- Deliver response.
- Write state to Memory Layer (async, after response).
- Emit turn event to Learning Layer (async, after response).

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

Mandatory safety gate. Runs **twice per turn**: on input before LLM, on output before delivery. Never skipped.

**Current stub:** `BasicTrustLayer` in `trust_layer/src/guardrails.py`. Case-insensitive substring matching against config-defined phrase lists. No ML.

**Known gaps:**
- No ML-based semantic matching.
- `check_consent()` always returns `True` — no real DPDP consent flow.
- Fail-open when service unreachable (must be fail-closed in production).

**Config-driven rules (KKB):**
- Blocked input phrases: `bomb`, `weapon`, `kill`, `threat`, `violence`
- Escalation topics: `arrested`, `police case`, `court notice`, `FIR`, `jail`, `suicide`
- Blocked output phrases: `cannot help`, `as an AI I`, `guaranteed placement`, `100% job guarantee`
- Consent phrases / decline phrases: configured in `trust.consent_phrases` / `trust.decline_phrases`

**Key files:**
- `trust_layer/src/guardrails.py`
- `trust_layer/src/server.py` — FastAPI: `/check/input`, `/check/output`, `/check/consent`, `/health`

**Tests:** 39 tests, 100% coverage.

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

### Learning Layer 🟡

Async-only observability. Emits turn events after response delivery. Never in the response path.

**Current stub:** `ConsoleLogger` — logs all TurnEvent fields at INFO level as structured JSON.

**Planned production capabilities:** Audit log DB (DPDP Act compliance), quality scores, outcome tracking (job placement rate, drop-off taxonomy), Langfuse or custom eval backend. Drives Tier 3 Live Tuning Dashboard signals.

**Key files:**
- `learning_layer/src/console_logger.py`
- `learning_layer/src/server.py` — FastAPI: `/emit/turn`, `/emit/signal`, `/health`

**Tests:** 34 tests, ≥96% coverage.

---

## 4. Runtime Turn Sequence

```
Reach Layer (input)
  │
  ▼
Agent Core: read state ← Memory Layer                     [async read at turn start]
  │
  ▼
Agent Core: input safety check → Trust Layer              [MANDATORY]
  │
  ▼ (blocked/escalated → short-circuit response)
Agent Core: Language Normalisation (internal)             [dialect, code-switching, transliteration]
  │
  ▼
Agent Core: NLU (internal)                                [intent, entities, confidence]
  │                                                       [design: skip/reduce by workflow_step — NOT YET DONE]
  ▼ (low confidence → early exit)
Agent Core: Manager Agent selects system prompt + tools   [driven by workflow_step]
  │
  ▼
Agent Core: LLM call #1 (ClaudeLLMWrapper)
  │
  ├─ [tool_use block returned]
  │    Agent Core: execute tool → Action Gateway
  │    Agent Core: LLM call #2 (with tool_result)
  │
  ▼
Agent Core: output safety check → Trust Layer             [MANDATORY]
  │
  ▼ (blocked → fallback response)
Agent Core: deliver response → Reach Layer
  │
  ├─ [async] write state → Memory Layer
  └─ [async] emit events → Learning Layer
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
| Agent Core | Learning Layer | Emit turn metadata (async, daemon thread) |
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
| Tier 3 — Live Tuning Dashboard | Management UI reading Learning Layer signals to patch YAML post-deployment | ⏳ Not yet built |

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
│       └── learning_layer.yaml   # log level, emit settings
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
| `evaluation` + `observability` | Learning Layer |
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
| FOG | `awaiting_consent` | Does not know what they want. Deliver market truth first. Never jump to options. |
| ORIENTATION | `profile_building` → `market_truth` | Collect profile (5 rounds). Then surface live ONEST data. |
| EVALUATION | `skill_evaluation` (Step A/B) | Compare options. Surface decision parameters. Never push one path. Honest trade-offs. |
| COMMITMENT | `apply_confirmation` | User decided. Remove friction. Consent mandatory before every action. |
| FOLLOW-THROUGH | `applied` | Did employer call? Did course start? Track outcome. Trust is built or broken here. |

### Workflow Step State Machine (profile-building phase)

| `workflow_step` | NLU Mode | System Prompt | Tools |
|---|---|---|---|
| `awaiting_consent` | Skip entirely | consent_prompt | none |
| `profile_building` | Entity extraction only | profile_prompt | none |
| `ready` | Full NLU | main_prompt | knowledge_search, onest_search, onest_apply |

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
| No profile / expired | `awaiting_consent` (FOG) |
| consent=false profile | `awaiting_consent` (re-ask on new session) |
| Partial profile (hard min missing) | Resume `profile_building` at saved `collection_round` |
| Full profile (hard min met) | `market_truth` (skip collection) |
| Active session within TTL | Resume at exact `workflow_step` saved in session |

---

## 8. Implementation Status

### By block

| Block | Status | Notes |
|---|---|---|
| Agent Core | ✅ | Orchestrator, LLM wrapper, preprocessing, tool-use loop, HTTP server. 177 tests, 90% coverage. |
| Knowledge Engine | ✅ | Glossary, ChromaDB RAG, HTTP server. 87 tests, ≥82% coverage. |
| Memory Layer | ✅ | Redis (session/profile) + Memgraph (context graph). HTTP server. |
| Trust Layer | 🟡 | Phrase-matching only. Consent always True. Fail-open. |
| Action Gateway | 🟡 | Hardcoded fixture data. No real ONEST API. |
| Reach Layer | 🟡 | CLI stdin/stdout only. |
| Learning Layer | 🟡 | Console logging only. |

### By feature

| Feature | Status | Notes |
|---|---|---|
| Language normalisation | ✅ | Dialect, code-switching, transliteration — in Agent Core |
| NLU (intent + entity) | ✅ | Intent classification, entity extraction, confidence — in Agent Core |
| NLU mode by workflow_step | ❌ | Always runs full NLU; should skip/reduce for consent/profile-building turns |
| Semantic RAG | ✅ | ChromaDB, multilingual embeddings, intent-based filtering |
| Glossary mapping | ✅ | Config-driven colloquial → canonical |
| LLM call with retry/fallback | ✅ | Exponential backoff, primary/fallback model switching |
| Tool-use loop | ✅ | Bounded by `max_tool_rounds`, consent tracking for write connectors |
| Manager Agent routing by workflow_step | ✅ | System prompt + tool list selected per step |
| KE conditional call (tool-only) | ❌ | KE called unconditionally; should only be called when `workflow_step = ready` |
| Session state (turn + session) | ✅ | Redis with TTL |
| Persistent profile store | ✅ | Redis RedisJSON |
| Context graph | ✅ | Memgraph typed attribute graph |
| Audit log / SQLite store | ⏳ | Design specifies SQLite for turn history/audit; not yet implemented |
| Input trust check | ✅ | Correct interface, stub logic |
| Output trust check | ✅ | Correct interface, stub logic |
| Consent check | 🟡 | Always returns True |
| ML-backed guardrails | ⏳ | Replace BasicTrustLayer with semantic matching |
| DPDP Act consent flow | ⏳ | Real `check_consent()` in Trust Layer |
| Fail-closed Trust Layer | ⏳ | Service-unreachable must block, not allow |
| Real ONEST connector | ⏳ | Replace MockActionGateway |
| WhatsApp/VOIP/Web channels | ⏳ | Replace CLIReachLayer |
| Audit log / eval pipeline | ⏳ | Replace ConsoleLogger with backend + eval service |
| Configuration Agent (Tier 1) | ⏳ | AI YAML generator tool for domain experts |
| Live Tuning Dashboard (Tier 3) | ⏳ | Dashboard reading Learning Layer signals |
| `/internal/llm/call` wiring | ⏳ | Endpoint implemented, no block calls it yet |
| Profile building 5-round flow | 🟡 | workflow_step state machine implemented; full 5-round collection partially complete |
| Multimodal input | 🟡 | Handler exists, disabled via config |
| Docker compose | ✅ | `automation/docker/docker-compose.dev.yml` |
| Helm charts | 🟡 | `automation/helm/` — structure exists, completeness unverified |

---

## 9. Stub Replacement Guide

Each stub implements the exact same abstract base class interface. Swapping requires **no changes to Agent Core or any other block**.

### Trust Layer

1. Implement `TrustLayerBase` (`agent_core/src/interfaces/trust_layer.py`): `check_input()`, `check_output()`, `check_consent()`.
2. Add ML/semantic matching for input/output checks.
3. Implement real DPDP consent flow in `check_consent()`.
4. Change fail-open to fail-closed in Agent Core's Trust Layer HTTP client.
5. Wire into `trust_layer/src/server.py`.

### Action Gateway

1. Implement `ActionGatewayBase` (`agent_core/src/interfaces/action_gateway.py`): `list_available_tools()`, `execute()`.
2. Replace fixture responses with real ONEST API calls. Add mandatory field validation for `onest_apply`.
3. Wire into `action_gateway/src/server.py`.

### Reach Layer

1. Implement `ReachLayerBase` (`agent_core/src/interfaces/reach_layer.py`): `receive()`, `deliver()`.
2. Implement per-channel adapter (WhatsApp webhook, VOIP SIP, etc.).
3. Wire into the reach layer entrypoint.

### Learning Layer

1. Implement `LearningLayerBase` (`agent_core/src/interfaces/learning_layer.py`): `emit_turn()`, `emit_signal()`.
2. Write to audit DB and eval service.
3. Wire into `learning_layer/src/server.py`.

---

## 10. Out of Scope

- ASR/TTS pipeline (speech-to-text, text-to-speech)
- Model training or fine-tuning
- Infrastructure provisioning or IaC
- Multi-tenancy and cost attribution
- Testing tooling beyond per-module pytest
- Versioning and rollback of domain configs
