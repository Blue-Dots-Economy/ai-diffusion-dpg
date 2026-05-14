# Dev-Kit — Domain Configuration Toolchain

The dev-kit is the **configuration toolchain** for the AI Composition Framework. It is not a runtime DPG block — it does not run during a conversation turn. Its purpose is to produce the YAML files that all 7 DPG blocks read at startup to configure themselves for a specific domain.

**Key principle:** deploying the framework to a new domain requires only a new folder under `dev-kit/configs/<domain>/`. No Python source code changes are needed.

---

## Three-Tier Configuration Model

```
Tier 1 — Configuration Agent        ✅ Implemented (FastAPI + React SPA)
Tier 2 — YAML Configuration         ✅ Canonical runtime source of truth
Tier 3 — Live Tuning Dashboard      ⏳ Not yet built
```

### Tier 1: Configuration Agent ✅

A fully implemented AI agent (powered by Claude) that interviews a domain expert through a structured conversation and generates the complete set of domain YAML files. The agent runs as a FastAPI server with a React + Vite SPA frontend.

**Conversation phases:** tier → overview → language → knowledge → memory → user_state → trust → tools → workflow → observability → reach → review (11 declarative phases, gated by `IntakeState`).

**Key capabilities:**
- Deterministic wizard: an `IntakeState` captured up front decides which phases run; `FIELD_RULES` decide each field's category; the router cascades intake changes through dependent fields.
- Stateless on-disk state model: every project's wizard state lives in `configs/<slug>/_meta/` (`intake_state.json`, `accumulator.json`, `field_status.json`, `current_phase.json`, `history.jsonl`, `deploy_settings.json`). No in-memory `ConversationEngine` or `ConfigAccumulator`.
- Per-block completion derived on demand from `field_status.json` via `block_status.block_completion_status` (`complete` / `incomplete`).
- Live YAML editing with CodeMirror-based ConfigEditor and validation.
- Workflow DAG visualisation with @xyflow (FlowGraph component).
- 8 canonical tools route every LLM mutation through Pydantic-validated handlers: `update_intake`, `update_config`, `add_subagent`, `update_subagent`, `remove_subagent`, `add_routing_rule`, `update_routing_rule`, `finalize_config`.
- Pre-deploy dry-run validates every block's merged config against the runtime block's own `MergedConfig` schema (baked into the dev-kit Docker image) before writing any YAML to disk.

**Run the Configuration Agent:**
```bash
cd dev-kit
uv run uvicorn dev_kit.agent.app:app --port 8080
# Open browser at http://localhost:8080
```

### Tier 2: YAML Configuration ✅

The canonical runtime source of truth. All 7 DPG blocks read these files once at startup via `dev-kit/loader.py`, which performs a deep-merge:

```
dev-kit/dpg/<block>.yaml               ← framework defaults
    +
dev-kit/configs/<domain>/<block>.yaml  ← domain overrides
    =
effective runtime config for the block
```

**Deep-merge rules:**
- Domain scalars override DPG scalars
- Domain dicts merge with DPG dicts (domain keys win on conflict)
- Domain lists replace DPG lists entirely

Domain values override framework defaults. Framework defaults provide safe starting values so domain configs only need to declare what differs.

### Tier 3: Live Tuning Dashboard ⏳

A web frontend that reads quality signals from the Observability Layer and allows operators to patch domain YAML values post-deployment (e.g., adjusting confidence thresholds, adding blocked phrases, updating persona text) without redeployment.

Status: not yet built. Frontend scaffolding exists at `dev-kit/frontend/`.

---

## Folder Structure

```
dev-kit/
├── dpg/                          # Framework defaults (7 YAML files, one per DPG)
│   ├── agent_core.yaml
│   ├── knowledge_engine.yaml
│   ├── memory_layer.yaml
│   ├── trust_layer.yaml
│   ├── action_gateway.yaml
│   ├── observability_layer.yaml
│   └── reach_layer.yaml
├── configs/
│   └── kkb/                      # KKB domain overrides (reference domain)
│       ├── agent_core.yaml       # Models, intents (40+), entities (20+), subagent graph (10 subagents), connectors
│       ├── knowledge_engine.yaml # Glossary (8 mappings), RAG sources (5 docs), intent filters
│       ├── memory_layer.yaml     # 24 UserProfile declared fields, journey schema, TTLs, merge rules, reengagement
│       ├── trust_layer.yaml      # 5 Policy Pack guardrails, blocked phrases, consent phrases
│       ├── action_gateway.yaml   # Connector endpoints, timeouts
│       ├── reach_layer.yaml      # Agent Core endpoint, UI text
│       └── observability_layer.yaml # 4 lifecycle states, 3 custom metrics, SLI thresholds
├── dev_kit/                      # Python package
│   ├── loader.py                 # Deep-merge loader (7 typed load functions + validate_all + build_all)
│   ├── schema.py                 # Pydantic v2 models for all 7 block configs
│   ├── schemas/                  # Per-block domain mirrors used by the wizard at chat time
│   └── agent/                    # Configuration Agent (Tier 1) — deterministic wizard
│       ├── app.py                # FastAPI server (REST endpoints)
│       ├── conversation.py       # Thin wrapper — chat_turn / get_history
│       ├── project_state.py     # BLOCKS + empty_accumulator / load_accumulator / save_accumulator
│       ├── block_status.py      # block_completion_status — derive complete/incomplete from field_status
│       ├── history.py            # history.jsonl append/read
│       ├── intake_state.py       # IntakeState dataclass + persistence
│       ├── field_rules/          # Per-block FIELD_RULES + AGGREGATED_FIELD_RULES registry
│       ├── phases_config.py      # 11 declarative phase definitions
│       ├── phase_prompts/        # One module per phase, each exports build()
│       ├── phase_driver.py       # Per-turn orchestrator + TOOL_HANDLERS
│       ├── tools.py              # 8 canonical tools (Pydantic-validated handlers)
│       ├── router.py             # on_intake_update / on_config_update / decide_next_phase
│       ├── skeleton.py           # build_skeleton — accumulator + field_status from FIELD_RULES
│       ├── path_ops.py           # set_path / get_path / clear_path with [name=X] syntax
│       ├── field_status.py       # field_status.json read/write
│       ├── derived_fields.py     # apply_derived_fields — slug-based renderer pass
│       ├── renderer.py           # render_all(project, dict, intake) + runtime_validate dry-run
│       └── deployer/             # Per-IntakeState selective compose generation
├── frontend/                     # React + Vite SPA
│   │                             #   Chat — conversation with the Configuration Agent
│   │                             #   Dashboard — project overview
│   │                             #   ConfigEditor — live YAML editing with CodeMirror
│   │                             #   FlowGraph — subagent DAG visualisation with @xyflow
│   │                             #   PhaseBar — phase progress indicator
├── tests/                        # Loader + schema tests
├── loader.py                     # CLI entry point
├── schema.py                     # Re-export of dev_kit/schema.py
└── pyproject.toml
```

---

## YAML → DPG Mapping

Each YAML file configures one DPG block. The table below lists the key sections each file controls.

| File | Configures | Key sections |
|------|-----------|--------------|
| `agent_core.yaml` | Agent Core | Models, intents (40+ for KKB), entity types (20+), subagent workflow graph (10 subagents for KKB), connectors, consent |
| `knowledge_engine.yaml` | Knowledge Engine | Glossary mappings, RAG sources, similarity threshold, top-k, intent→doc_type filters |
| `memory_layer.yaml` | Memory Layer | Session schema, graph node/edge types, TTLs, merge rules, reengagement triggers, 24 UserProfile declared fields (KKB) |
| `trust_layer.yaml` | Trust Layer | Blocked phrases, escalation topics, Policy Pack guardrails (5 for KKB: GR-001–GR-005), consent phrases |
| `action_gateway.yaml` | Action Gateway | Connector endpoints, authentication, timeouts, retry policy |
| `reach_layer.yaml` | Reach Layer | Agent Core URL, UI text for web adapter |
| `observability_layer.yaml` | Observability Layer | Lifecycle states, custom metrics, SLI thresholds, PII field exclusions |

---

## Adding a New Domain

1. Create `dev-kit/configs/<new-domain>/`.
2. Add one YAML file per DPG block (copy from `dev-kit/configs/kkb/` as a starting point).
3. Override only the values that differ from the framework defaults in `dev-kit/dpg/`.
4. Point `DOMAIN` to `<new-domain>` in your environment or Docker compose file.
5. Validate: `python -m dev_kit.loader validate --domain <new-domain>`
6. Build (write merged files): `python -m dev_kit.loader build --domain <new-domain> --output /tmp/merged/`
7. Restart services — each block will deep-merge and boot with the new config.

No Python source code changes are required.

---

## Config Loading Rule

Config is read **once at startup** via `dev-kit/loader.py`. It is never re-read inside request paths. If you change a YAML file, restart the affected service.

---

## Further Reading

- [ARCHITECTURE.md](../ARCHITECTURE.md) Section 6 — full config model specification
- [README.md](../README.md) — project overview and quick start
