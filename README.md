# AI Composition Framework

A modular framework for building AI-powered voice and chat systems from **7 standardised Digital Public Goods (DPG) building blocks**, configured entirely via YAML. The runtime blocks are fixed; all domain-specific intelligence — persona, knowledge, safety rules, connectors, intents — lives in a domain configuration kit. No source code changes are needed to deploy to a new domain.

---

## The 7 DPG Building Blocks

| Block | Role | Port | Status |
|---|---|---|---|
| **Agent Core** | Sole orchestrator and sole LLM caller. Runs Language Normalisation + NLU internally, owns the tool-use loop, retry, and fallback model switching. Stateless. | 8000 | ✅ |
| **Knowledge Engine** | Assembles the full LLM prompt. Receives NLU results + session state from Agent Core; performs semantic RAG retrieval (ChromaDB) and glossary mapping. Stateless. | 8001 | ✅ |
| **Memory Layer** | Manages state at Turn/Session scope (Redis + RedisJSON, TTL) and a typed Context Graph (Memgraph) for persistent cross-session user profiles. | 8002 | 🟡 |
| **Trust Layer** | Mandatory safety gate — runs twice per turn (input + output). Four sub-blocks: ContentBlock, GuardrailsBlock, ConsentBlock, HiTLBlock. | 8003 | 🟡 |
| **Observability Layer** | Async-only observability via OpenTelemetry (`dpg_telemetry` package). Emits turn events after response delivery; never in the response path. | 8004 | 🟡 |
| **Reach Layer** | Normalises inbound channels and delivers responses. Currently a CLI stdin/stdout stub; also includes a web channel adapter. | 8005 | 🟡 |
| **Action Gateway** | Sole interface with external systems. Executes tool calls expressed by the LLM; returns normalised results to Agent Core. | 9999 | 🟡 |

---

## Configuration Model

The framework uses a **two-level YAML configuration model**. The runtime blocks read config once at startup and never re-read inside request paths.

```
dev-kit/dpg/<block>.yaml          ← framework defaults (checked in)
dev-kit/configs/<domain>/<block>.yaml  ← domain overrides (one folder per deployment)
```

At startup, each block deep-merges these two files — domain values override framework defaults. **To deploy to a new domain, create `dev-kit/configs/<new-domain>/` and populate one YAML per block. No Python changes required.**

### Example — KKB (Kaam Ki Baat, labour-market assistant)

`dev-kit/configs/kkb/` configures the full system for a labour-market use case:

| YAML file | What it controls |
|---|---|
| `agent.yaml` | Primary/fallback models, intents, subagent routing graph, connector list, persona |
| `knowledge_engine.yaml` | Glossary mappings, RAG sources, similarity threshold |
| `memory_layer.yaml` | Profile graph relations (`profile_graph_relations`), session TTLs |
| `trust_layer.yaml` | Blocked phrases, escalation topics, consent phrases, Policy Packs |
| `action_gateway.yaml` | ONEST API endpoints, timeouts |
| `reach_layer.yaml` | CLI prompts, Agent Core endpoint |
| `observability_layer.yaml` | OTel collector endpoint, outcome lifecycle, SLI thresholds |

The framework defaults (`dev-kit/dpg/`) provide safe starting values for every field; domain overrides only need to specify what changes.

---

## Quick Start

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd automation/docker
docker compose -f docker-compose.dev.yml up -d        # start all services except reach_layer
docker compose -f docker-compose.dev.yml run --rm reach_layer   # interactive CLI session
```

Service ports: Agent Core `:8000`, Knowledge Engine `:8001`, Memory Layer `:8002`, Trust Layer `:8003`, Observability Layer `:8004`, Action Gateway `:9999`.

---

## Running Tests

Tests live inside each module directory. Run per module:

```bash
cd agent_core          # or knowledge_engine/, memory_layer/, trust_layer/, etc.
uv sync
uv run pytest                                          # all tests
uv run pytest tests/test_orchestrator.py              # single file
uv run pytest --cov=src --cov-report=term-missing     # with coverage
```

Target: ≥ 70% line coverage on `agent_core/` and `knowledge_engine/`.

---

## Further Reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — single source of truth: block responsibilities, runtime sequence, design decisions, implementation status
- [dev-kit/README.md](dev-kit/README.md) — configuration toolchain and how to add a new domain
- `agent_core/` — orchestrator, LLM wrapper, NLU, tool-use loop
- `knowledge_engine/` — RAG retrieval, glossary, prompt assembly
- `memory_layer/` — Redis session store + Memgraph context graph
- `trust_layer/` — ContentBlock, GuardrailsBlock, ConsentBlock, HiTLBlock
- `observability_layer/` — OTel instrumentation via `dpg_telemetry`
- `reach_layer/` — CLI + web channel adapter
- `action_gateway/` — mock ONEST connector
