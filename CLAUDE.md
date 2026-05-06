# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Single source of truth for design, architecture, and implementation status: [`ARCHITECTURE.md`](ARCHITECTURE.md)**
> Refer to ARCHITECTURE.md for current block responsibilities, design decisions (including deviations from original docs), runtime sequence, and what is ✅ complete / 🟡 stubbed / ⏳ pending.

---

## Commands

**Run the full system (Docker):**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd automation/docker
docker compose -f docker-compose.dev.yml up -d                    # all services except reach_layer
docker compose -f docker-compose.dev.yml run --rm reach_layer     # interactive CLI session
```
Ports: Agent Core `:8000`, Knowledge Engine `:8001`, Memory Layer `:8002`, Trust Layer `:8003`, Observability Layer `:8004`, Reach Layer Web `:8005`, Reach Layer Voice `:8006`, Action Gateway `:9999`.

**Run tests (per module):**
```bash
cd agent_core          # or knowledge_engine/, memory_layer/, etc.
uv run pytest                                          # all tests
uv run pytest tests/test_orchestrator.py              # single file
uv run pytest --cov=src --cov-report=term-missing     # with coverage
```

**KE document ingestion:**
```bash
cd knowledge_engine && uv run python scripts/ingest.py --config config/domain.yaml
```

**Config loading:** Each module deep-merges two YAML files at startup — `dev-kit/dpg/<module>.yaml` (framework defaults) overridden by `dev-kit/configs/<domain>/<module>.yaml` (domain values). Reference domain: `dev-kit/configs/kkb/`.

---

## Architecture

The framework assembles AI-powered voice/chat systems from **7 standardised DPG building blocks**, configured per-domain via a **Domain Configuration Kit** (YAML). The runtime blocks are fixed; all domain intelligence is external.

| Group | Blocks |
|---|---|
| Intelligence & Integration | Knowledge Engine, Action Gateway |
| Orchestration & Trust | Agent Core, Trust Layer |
| State & Memory | Memory Layer |
| Channels & Reach | Reach Layer |
| Learning & Observability | Observability Layer |

### Block responsibilities

**Agent Core** — turn-time orchestrator and sole LLM caller. Runs Language Normalisation and NLU internally, then builds the system prompt (`manager_agent.build_system_prompt()` — subagent prompt + Trust constraints + required disclosures; KE chunks enter via the `knowledge_retrieval` tool result, not via KE-side prompt assembly). Owns the tool execution loop (LLM → tool → LLM) and retry. Knowledge Engine is called only when the LLM invokes the `knowledge_retrieval` internal tool (subagents that do not include `knowledge_retrieval` in their tool list never trigger a KE call). Stateless between turns — any instance can handle any session. All LLM calls go through `agent_core/src/chat_provider/`. The package owns provider selection (Anthropic + OpenAI today; Azure/Ollama as follow-ups), neutral typing, retry/timeout, and OTel telemetry; the concrete provider files (`anthropic_provider.py`, `openai_provider.py`) are the only places that import their respective SDKs. Also exposes `POST /internal/llm/call` as a future LLM proxy (implemented, not yet wired).

**Knowledge Engine** — returns ranked retrieval chunks (does **not** assemble the final LLM prompt — Agent Core does). Receives NLU results and session state from Agent Core in the request body. Stateless on the retrieval path. Internal components: Glossary & Domain Vocabulary, Static Knowledge Base (ChromaDB semantic RAG), Multimodal Input Handler, and an SQLite **ingestion ledger** that tracks per-document state (queued / ingested / failed / `refreshed_at`) for documents fed by `scripts/ingest.py` or by Reach Layer's document-upload endpoint.

**Memory Layer** — manages state at three scopes: Turn/Session (Redis with RedisJSON, TTL), Context Graph (Memgraph — typed attribute nodes per session), and Persistent cross-session profile. Agent Core reads at turn start and writes asynchronously after response delivery.

**Trust Layer** — mandatory safety gate. Runs **twice per turn**: once on input before the LLM, once on output before delivery. Never skipped. Four sub-blocks: ContentBlock, GuardrailsBlock, ConsentBlock, HiTLBlock. Exposes endpoints: `/check/input`, `/assemble_constraints`, `/check/output`, `/consent/verify`, `/check/consent`, `/escalate`. Enforces content rules, output rules, consent rules (DPDP Act), escalation rules, and topic firewall.

**Action Gateway** — sole interface with external systems. Executes tool calls expressed by the LLM; the LLM never calls APIs directly. Returns normalised results to Agent Core. Write/identity connectors require Trust Layer consent before execution.

**Reach Layer** — normalises inbound channels (VOIP, WhatsApp, Web, Mobile SDK) and delivers responses. Manages outbound campaigns and cross-channel handoffs. Channel and assembly mode are independent: Voice is constrained to `session` mode (VAD-driven multi-segment input); CLI uses `direct` mode and does not need TurnAssembler; Web defaults to `direct` and can be configured for `session`. Also includes approved direct calls — see "Module interaction rules" below.

**Observability Layer** — async-only observability. Emits turn events after response delivery; never in the response path. Produces audit log, quality scores, feedback signals, and outcome tracking.

### Runtime turn sequence

```
Reach Layer (input)
  → Agent Core: read state ← Memory Layer
  → Agent Core: consent gate (if ask_for_consent: true in config)
  → Agent Core: NLU (internal) → early exit if low-confidence
  → Agent Core: input safety check → Trust Layer /check/input
  → Agent Core: Language Normalisation (internal)
  → Agent Core: POST /assemble_constraints → Trust Layer (if active_risks present)
  → Agent Core: Manager Agent selects subagent + tools, builds system prompt
  → Agent Core: LLM call #1
  → [tool_use] Agent Core routes by tool name:
        knowledge_retrieval → Knowledge Engine /retrieve (chunks)
        any other tool      → Action Gateway /execute
       → LLM call #2 with tool_result
  → Agent Core: output safety check → Trust Layer /check/output
  → Agent Core: deliver response → Reach Layer
  → [async] write state → Memory Layer
  → [async] emit events → Observability Layer
```

Two execution paths: `POST /process_turn` (sync JSON, used by web direct mode) and `POST /stream_turn` (SSE, used by CLI/voice session mode via TurnAssembler). Both run the same sequence.

### Module interaction rules

**Agent Core is the only turn-time orchestrator and the only LLM caller.** Every per-turn step (memory read → trust input → NLU → constraint assembly → prompt build → LLM → tool routing → trust output → deliver) runs inside Agent Core. The user only initiates a turn through Reach Layer → Agent Core.

Other blocks may call each other directly **only under the approved scopes listed below**. New cross-block calls require an architecture-level decision; do not introduce them ad hoc.

**Turn-time calls (initiated by Agent Core):**

| Caller | Callee | Purpose |
|---|---|---|
| Agent Core | Memory Layer | Read state at turn start; write state after response (async) |
| Agent Core | Trust Layer | Check input; check output; assemble constraints; consent verify |
| Agent Core | Knowledge Engine | `POST /retrieve` — ranked chunks, called only via the `knowledge_retrieval` internal tool |
| Agent Core | Action Gateway | Execute LLM-requested external tool calls |
| Agent Core | Observability Layer | Emit turn metadata (async) |
| Action Gateway | External systems | Only on instruction from Agent Core |

**Approved direct calls (production):**

| Caller | Callee | Purpose |
|---|---|---|
| Reach Layer (web) | Memory Layer | `GET /users/{user_id}/active-history` — restore chat history on session resume, before turn 1 |
| Reach Layer | Knowledge Engine | `POST /ingest` — user-uploaded documents go straight to KE for embedding + ledger update |

**Planned (post-PoC, design-approved):**

| Caller | Callee | Purpose |
|---|---|---|
| Action Gateway | Knowledge Engine, Memory Layer | Cache layer for tool results (#18) |

### Key design decisions

- **Config-driven runtime:** No model names, persona, tool definitions, guardrail rules, or routing logic are hardcoded. Everything comes from YAML loaded at startup. The Agent Core reads `agent.primary_model`, `agent.fallback_model`, `conversation.persona`, `connectors.*`, `trust.*`, `knowledge.*`.
- **Tool execution pattern:** LLM returns a `tool_use` block → Agent Core routes to Tool Registry → calls Action Gateway → appends `tool_result` → second LLM call. LLM sees only normalised results, never raw API responses.
- **Latency target:** 800–1200ms per turn (voice-first). One LLM call for most turns, two for tool turns.
- **Hard routing:** Escalation topics are enforced by Trust Layer before the LLM is called. LLM-driven routing handles everything else via tool selection.
- **Three-Tier config model:** Tier 1 (Configuration Agent) is implemented as a FastAPI + React app in `dev-kit/dev_kit/agent/`. Tier 2 (YAML) is the runtime source of truth. Tier 3 (Live Tuning Dashboard) is not yet built.

### PoC scope

Full implementations: **Agent Core** (818 tests — sync + async streaming + TurnAssembler + multi-provider chat_provider), **Knowledge Engine** (192 tests), **Memory Layer** (226 tests, Redis + Memgraph + SQLite), **Action Gateway** (173 tests — RestApiAdapter + McpAdapter), **Domain Configuration Kit** (365 tests).

Partial implementations (correct interface, some gaps): **Trust Layer** (138 tests — all 4 sub-blocks; HiTL log backend only, consent store in-process), **Reach Layer** (308 Python + 143 UI tests — CLI ✅, Web/React SPA ✅ (with `routing_only` mode for voice-only deployments), Voice/pipecat ✅), **Observability Layer** (101 tests — OTel functional; Grafana dashboards pending).

**Stub interfaces must exactly match the real interface** — they must be replaceable without changing Agent Core or other modules.

### Out of scope

ASR/TTS pipeline, model training, infrastructure provisioning, multi-tenancy, testing tooling, versioning/rollback.

---

## Development guidelines

1. **Agent Core is the only turn-time orchestrator.** Every per-turn step runs inside Agent Core, and Agent Core is the only block that calls the LLM. Other blocks may communicate directly only under the approved-direct-call scopes above (Reach→Memory session-restore, Reach→KE ingest, planned AG→KE/ML caching). Do not introduce new cross-block calls without updating this list.
2. **Agent Core is the only LLM caller.** All LLM interaction goes through a `ChatProviderBase` instance constructed via `build_chat_provider(agent_config)`.
3. **All external access goes through Action Gateway.** LLM expresses intent via tool definitions only.
4. **Trust Layer runs on every I/O pass.** Input before LLM, output before user. Never skip either.
5. **Agent Core is stateless.** All state lives in Memory Layer. Instances scale horizontally.
6. **Observability Layer is always async.** Never in the response path.
7. **Config drives all runtime behaviour.** No hardcoded domain values in Python source.
8. **Write connectors require consent.** Gate `write`/`identity` connectors via Trust Layer before execution.
9. **Keep blocks loosely coupled.** Call through the defined interface only; never reach into internals.
10. **Stubs honour the same interface as real implementations.** Signatures and return types must match.

---

## Coding standards

Detailed rules in `.claude/rules/`:

- [`base-class-pattern.md`](.claude/rules/base-class-pattern.md) — define ABC before any concrete implementation. preserve signatures and return types in all subclasses. expose public interface only; prefix internals with `_`. handle empty/None/missing-key/wrong-type explicitly
- [`error-handling.md`](.claude/rules/error-handling.md) — timeout + retry + structured errors on all external calls
- [`configuration-discipline.md`](.claude/rules/configuration-discipline.md) — no hardcoded domain values; read config once at startup
- [`testing-requirements.md`](.claude/rules/testing-requirements.md) — normal/edge/failure coverage; ≥70% line coverage on agent_core & knowledge_engine
- [`logging-observability.md`](.claude/rules/logging-observability.md) — structured logs with operation/status/error/latency_ms; no PII
- [`code-documentation.md`](.claude/rules/code-documentation.md) — Google-style docstrings on all public classes, methods, and functions
