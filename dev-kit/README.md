# Dev-Kit — Domain Configuration Toolchain

The dev-kit is the **configuration toolchain** for the AI Composition Framework. It is not a runtime DPG block — it does not run during a conversation turn. Its purpose is to produce the YAML files that all 7 DPG blocks read at startup to configure themselves for a specific domain.

**Key principle:** deploying the framework to a new domain requires only a new folder under `dev-kit/configs/<domain>/`. No Python source code changes are needed.

---

## Three-Tier Configuration Model

```
Tier 1 — Configuration Agent        (⏳ not yet built)
Tier 2 — YAML Configuration         (✅ canonical runtime source of truth)
Tier 3 — Live Tuning Dashboard      (⏳ not yet built)
```

### Tier 1: Configuration Agent

An AI interviewer that conducts a structured Q&A session with a domain expert and generates the complete set of domain YAML files. The agent asks about persona, intents, connectors, safety rules, knowledge sources, and success criteria, then writes the output into `dev-kit/configs/<domain>/`.

Status: placeholder only (`dev-kit/agent/`).

### Tier 2: YAML Configuration

The canonical runtime source of truth. All 7 DPG blocks read these files once at startup via `dev-kit/loader.py`, which performs a deep-merge:

```
dev-kit/dpg/<block>.yaml           ← framework defaults
    +
dev-kit/configs/<domain>/<block>.yaml  ← domain overrides
    =
effective runtime config for the block
```

Domain values override framework defaults. Framework defaults provide safe starting values so domain configs only need to declare what differs.

### Tier 3: Live Tuning Dashboard

A web frontend that reads quality signals from the Observability Layer and allows operators to patch domain YAML values post-deployment (e.g., adjusting confidence thresholds, adding blocked phrases, updating persona text) without redeployment.

Status: frontend placeholder (`dev-kit/frontend/`).

---

## Folder Structure

```
dev-kit/
├── dpg/                        # Framework defaults — one YAML per DPG block
│   ├── agent.yaml
│   ├── knowledge_engine.yaml
│   ├── memory_layer.yaml
│   ├── trust_layer.yaml
│   ├── action_gateway.yaml
│   ├── reach_layer.yaml
│   └── observability_layer.yaml
│
├── configs/                    # Domain-specific overrides
│   └── kkb/                    # Reference domain: Kaam Ki Baat (labour-market assistant)
│       ├── agent.yaml
│       ├── knowledge_engine.yaml
│       ├── memory_layer.yaml
│       ├── trust_layer.yaml
│       ├── action_gateway.yaml
│       ├── reach_layer.yaml
│       └── observability_layer.yaml
│
├── loader.py                   # Deep-merge: dpg/*.yaml + configs/<domain>/*.yaml
├── agent/                      # Tier 1: Configuration Agent (placeholder)
└── frontend/                   # Tier 3: Live Tuning Dashboard frontend
```

---

## YAML → DPG Mapping

Each YAML file configures one DPG block. The table below lists the key sections each file controls.

| YAML file | DPG block | What it controls |
|---|---|---|
| `agent.yaml` | Agent Core | Primary model, fallback model, intents, subagent routing graph, connector list, persona text, conversation rules |
| `knowledge_engine.yaml` | Knowledge Engine | Glossary mappings, RAG document sources, similarity threshold, top-k retrieval |
| `memory_layer.yaml` | Memory Layer | Profile graph relations (`profile_graph_relations`), session TTLs, graph schema |
| `trust_layer.yaml` | Trust Layer | Blocked phrases, escalation topics, consent phrases, Policy Packs, HiTL queue config |
| `action_gateway.yaml` | Action Gateway | Connector endpoints, authentication, timeouts, retry policy |
| `reach_layer.yaml` | Reach Layer | CLI prompts, Agent Core endpoint, channel adapter settings |
| `observability_layer.yaml` | Observability Layer | OTel collector endpoint, outcome lifecycle stages, SLI thresholds |

---

## Adding a New Domain

1. Create `dev-kit/configs/<new-domain>/`.
2. Add one YAML file per DPG block (copy from `dev-kit/configs/kkb/` as a starting point).
3. Override only the values that differ from the framework defaults in `dev-kit/dpg/`.
4. Point `DOMAIN` to `<new-domain>` in your environment or Docker compose file.
5. Restart services — each block will deep-merge and boot with the new config.

No Python source code changes are required.

---

## Config Loading Rule

Config is read **once at startup** via `dev-kit/loader.py`. It is never re-read inside request paths. If you change a YAML file, restart the affected service.

---

## Further Reading

- [ARCHITECTURE.md](../ARCHITECTURE.md) Section 6 — full config model specification
- [README.md](../README.md) — project overview and quick start
