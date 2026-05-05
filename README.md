# AI Composition Framework

A modular framework for building AI-powered voice and chat systems from **7 standardised Digital Public Goods (DPG) building blocks**, configured entirely via YAML. The runtime blocks are fixed; all domain-specific intelligence — persona, knowledge, safety rules, connectors, intents — lives in a domain configuration kit. No source code changes are needed to deploy to a new domain.

Reference domain: **KKB (Kaam Ki Baat)** — a labour-market assistant for informal workers in India, helping users find trades, check market salaries, and apply to ONEST job postings.

---

## The 7 DPG Building Blocks

| Block | Group | Port | Status | Role |
|-------|-------|------|--------|------|
| **Agent Core** | Orchestration | 8000 | ✅ | Turn-time orchestrator + sole LLM caller. Language Normalisation, NLU, system-prompt assembly, tool-use loop, subagent routing. Stateless. |
| **Knowledge Engine** | Intelligence | 8001 | ✅ | Semantic RAG retrieval (ChromaDB) + glossary mapping + SQLite ingestion ledger. Returns ranked chunks; prompt assembly happens in Agent Core. |
| **Memory Layer** | State | 8002 | ✅ | Redis (session/profile) + Memgraph (context graph) + SQLite (audit). 3-scope state management. 10 HTTP endpoints. |
| **Observability Layer** | Learning | 8004 | ✅ | OTel instrumentation + Loki/Jaeger audit trail functional via shared `dpg_telemetry` package. OutcomeTracker. Grafana dashboards pending. |
| **Trust Layer** | Trust | 8003 | 🟡 | 4 sub-blocks: ContentBlock, GuardrailsBlock, ConsentBlock, HiTLBlock(todo). Fail-closed. 7 endpoints. |
| **Reach Layer** | Channels | 8005 | 🟡 | CLI (stdin/stdout) + Web adapter (port 8005). Outbound channels (voice) pending. |
| **Action Gateway** | Integration | 9999 | 🟡 | Mock ONEST API: market lookup + job apply. 10 fixture trades. No real connectors yet. |

---

## Configuration Model

All domain intelligence lives in YAML. The framework uses a **two-level configuration model**:

```
dev-kit/dpg/<block>.yaml               ← framework defaults (checked in, same across all domains)
dev-kit/configs/<domain>/<block>.yaml  ← domain overrides (one folder per deployment)
```

At startup, each block deep-merges these two files — domain values override framework defaults. To deploy to a new domain, create `dev-kit/configs/<new-domain>/` and populate one YAML file per block. No Python changes required.

The **Configuration Agent** (`dev-kit/dev_kit/agent/`) interviews a domain expert through a structured chat session and generates these YAML files automatically. See [dev-kit/README.md](dev-kit/README.md) for full details.

### What each domain YAML configures (KKB example)

| File | Key configuration |
|------|-------------------|
| `agent_core.yaml` | Primary/fallback models, 40+ intents, 20+ entity types, 10-subagent workflow graph, connectors, persona |
| `knowledge_engine.yaml` | 8 glossary mappings, 5 RAG source documents, similarity threshold, intent→doc_type filters |
| `memory_layer.yaml` | 24 UserProfile declared fields, graph edge types, session TTLs, reengagement triggers |
| `trust_layer.yaml` | Blocked phrases, escalation topics, 5 Policy Pack guardrails (GR-001–GR-005), consent phrases |
| `action_gateway.yaml` | ONEST API endpoints, timeouts |
| `reach_layer.yaml` | Agent Core URL, web adapter UI text |
| `observability_layer.yaml` | 4 lifecycle states, 3 custom metrics, SLI thresholds, PII field exclusions |

The framework defaults (`dev-kit/dpg/`) provide safe starting values for every field; domain overrides only need to specify what changes.

---

## Quick Start

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd automation/docker
docker compose -f docker-compose.dev.yml up -d                         # All services except reach_layer
docker compose -f docker-compose.dev.yml run --rm reach_layer          # Interactive CLI session
```

Ports: Agent Core `:8000`, Knowledge Engine `:8001`, Memory Layer `:8002`, Trust Layer `:8003`, Observability Layer `:8004`, Reach Layer web `:8005`, Action Gateway `:9999`.

---

## Running Tests

Tests live inside each module directory. Run per module:

```bash
cd agent_core          # or knowledge_engine/, memory_layer/, trust_layer/, etc.
uv run pytest                                          # all tests
uv run pytest tests/test_orchestrator.py              # single file
uv run pytest --cov=src --cov-report=term-missing     # with coverage
```

Target: ≥ 70% line coverage on `agent_core/` and `knowledge_engine/`.

---

## Further Reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — single source of truth: block responsibilities, runtime sequence, design decisions, implementation status
- [dev-kit/README.md](dev-kit/README.md) — configuration toolchain (Tier 1 agent + Tier 2 YAML) and how to add a new domain
- `agent_core/` — orchestrator, multi-provider chat_provider (Anthropic + OpenAI), NLU, tool-use loop (818 tests)
- `knowledge_engine/` — RAG retrieval, glossary, ingestion ledger (192 tests)
- `memory_layer/` — Redis session store + Memgraph context graph + SQLite audit (226 tests)
- `trust_layer/` — ContentBlock, GuardrailsBlock, ConsentBlock, HiTLBlock (138 tests)
- `observability_layer/` — OTel instrumentation via `dpg_telemetry` (101 tests)
- `reach_layer/` — CLI + web (React SPA) + voice (pipecat) channel adapters (308 Python + 143 UI tests)
- `action_gateway/` — generic RestApiAdapter + McpAdapter (173 tests)
