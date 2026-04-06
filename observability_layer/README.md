# Observability Layer DPG

Asynchronous observability layer. Runs entirely out-of-band — never in the response path.

---

## What this service does

The Observability Layer receives turn metadata and feedback signals from Agent Core after each response is delivered. It records these events for audit, quality evaluation, and outcome tracking.

**Critical constraint:** Agent Core calls this layer asynchronously in a daemon thread, after the user response has already been returned. The Observability Layer must never be in the response path — a slow or unavailable Observability Layer must not affect turn latency.

The primary implementation is `OtelObservabilityLayer`, backed by OpenTelemetry instrumentation and the `OutcomeTracker` lifecycle state machine. A backward-compatible `ConsoleLogger` stub also exists.

### `dpg_telemetry` package

A shared OTel bootstrap package installed by all 7 DPG blocks (located in `src/dpg_telemetry/`). Exposes:
- `init_otel(service_name, config)` — called by every block at startup. Configures TracerProvider, MeterProvider, OTLP exporter, W3C propagator, and resource attributes.
- `get_tracer()` — returns the block's OTel tracer.
- `get_meter()` — returns the block's OTel meter.

### `OutcomeTracker`

Lifecycle state machine that tracks KKB conversation outcomes (placement, dropout, follow-through). Receives turn events and advances the outcome state based on session context and signal type.

### Block instrumentation

Each block self-instruments via `dpg_telemetry` and emits spans and metrics to an OTel Collector sidecar:

| Block | Key spans | Key metrics |
|---|---|---|
| `agent_core` | `orchestrator.turn`, `llm.call` | `llm.tokens`, `turn.latency_ms` |
| `trust_layer` | `trust.input_check`, `trust.output_check` | `trust.blocks` |
| `knowledge_engine` | `ke.prompt_assemble`, `ke.rag_retrieve` | `rag.retrieved_docs` |
| `memory_layer` | `memory.read`, `memory.write` | `memory.latency_ms` |
| `action_gateway` | `action.execute` | `action.calls` |
| `reach_layer` | `reach.inbound`, `reach.outbound` | `reach.sessions` |

---

## Folder structure

```
observability_layer/
├── main.py                         # Uvicorn entrypoint (port 8004)
├── pyproject.toml
├── config/
│   └── config.yaml                 # OTel config, outcome lifecycle, SLI thresholds, PII exclusions
├── src/
│   ├── dpg_telemetry/              # Shared OTel bootstrap package (installed by all 7 blocks)
│   │   ├── __init__.py
│   │   ├── bootstrap.py            # init_otel(), get_tracer(), get_meter()
│   │   └── ...
│   ├── schema/
│   │   └── config.py               # ObservabilityConfig — Pydantic v2 schema
│   ├── otel_observability_layer.py # OtelObservabilityLayer — primary implementation
│   ├── outcome_tracker.py          # OutcomeTracker lifecycle state machine
│   ├── console_logger.py           # ConsoleLogger — backward-compatible stub
│   └── server.py                   # FastAPI app (all endpoints)
└── tests/
    ├── test_otel_observability_layer.py
    ├── test_outcome_tracker.py
    └── test_server.py
```

---

## HTTP API

The service runs on port **8004**.

### `POST /emit/turn`

Records a complete turn event — called once per turn, after the response has been returned to the user. Routes to `OutcomeTracker`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "response_text": "Hubli mein electrician ke liye salary ₹15,000–₹28,000/month hai.",
  "tool_calls": ["onest_market_lookup"],
  "trust_input_result": { "passed": true, "action": "allow", "reason": null },
  "trust_output_result": { "passed": true, "action": "allow", "reason": null },
  "model_used": "claude-haiku-4-5-20251001",
  "input_tokens": 342,
  "output_tokens": 87,
  "latency_ms": 1243,
  "timestamp_ms": 1700000000000
}
```

**Response:** `{ "status": "ok" }`

### `POST /emit/signal`

Records an explicit or implicit feedback signal — called when the user gives a thumbs up/down, or when implicit signals (re-ask, escalation request) are detected.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "signal_type": "thumbs_up",
  "turn_reference": "sess-abc123:3",
  "metadata": {}
}
```

Signal types: `thumbs_up`, `thumbs_down`, `re_ask`, `escalation_requested`, `task_completed`

**Response:** `{ "status": "ok" }`

### `POST /validate-config`

Validates an `ObservabilityConfig` payload against the Pydantic v2 schema. Used during domain config authoring.

**Response:** `{ "valid": true }` or `{ "valid": false, "errors": [...] }`

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

---

## `ObservabilityConfig` schema

`src/schema/config.py` defines the full schema for domain-specific observability configuration (Pydantic v2):

- **Outcome lifecycle:** State machine definition for conversation outcomes (e.g. `placed`, `dropped_out`, `follow_through`).
- **Metric instrument types:** Gauge, counter, or histogram per metric name.
- **SLI thresholds:** Per-metric alert thresholds.
- **PII field exclusions:** Separate lists for telemetry (`user_id` allowed for dashboarding) vs. audit log (excluded for DPDP Act compliance).

---

## Configuration

| Key | Description |
|---|---|
| `server.port` | HTTP port (default: 8004) |
| `observability.otlp_endpoint` | OTLP/gRPC collector endpoint |
| `observability.log_level` | Python logging level (`INFO`, `DEBUG`, etc.) |
| `observability.outcome_lifecycle` | State machine config for outcome tracking |
| `observability.sli_thresholds` | Per-metric alert thresholds |
| `observability.pii_exclusions_telemetry` | PII fields excluded from OTel traces |
| `observability.pii_exclusions_audit` | PII fields excluded from audit log |

---

## Running the service

```bash
cd observability_layer
uv run uvicorn src.server:app --port 8004
```

---

## Running tests

```bash
cd observability_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected: ≥70% line coverage.

---

## Dependencies

```
fastapi            >= 0.110
uvicorn            >= 0.29
pydantic           >= 2.0
pyyaml             >= 6.0
opentelemetry-api  >= 1.20
opentelemetry-sdk  >= 1.20
opentelemetry-exporter-otlp >= 1.20
```

Requires Python 3.11+.

---

## Known gaps / planned production additions

- **No persistent audit DB.** Turn events are recorded via OTel spans only. A production deployment requires a persistent audit DB writer (PostgreSQL or BigQuery) for DPDP Act compliance.
- **No persistent outcome store.** `OutcomeTracker` state is in-process per session. A production deployment needs a durable outcome store.
- **Grafana dashboard provisioning** (`automation/docker/grafana/provisioning/`) — structure exists but dashboards are not fully provisioned.
- **`OutcomeTracker` placement.rate gauge** — ratio of placed/total sessions not yet computed.
- **`ConsoleLogger`** remains as a backward-compatible stub. `OtelObservabilityLayer` is the primary implementation for all new deployments.
