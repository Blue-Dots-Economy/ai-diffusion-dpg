# Observability Layer

> Status: 🟡 — OTel instrumentation functional; audit trail via Loki + Jaeger through OTel Collector; Grafana dashboards pending.

Async-only observability for the DPG framework. Receives turn events and feedback signals from Agent Core after each response is delivered. Never in the response path.

This module also ships the `dpg_telemetry` package — a shared OTel bootstrap library installed by all 7 DPG blocks.

---

## What this service does

**Critical constraint:** Agent Core emits events to this layer asynchronously, after the user response has already been returned. A slow or unavailable Observability Layer must never affect turn latency.

Each `POST /emit/turn` call triggers:

1. `OtelObservabilityLayer.emit_turn()` — extracts trace ID, token counts, and latency from the event, then calls `OutcomeTracker.process()`.
2. `OutcomeTracker.process()` — advances the lifecycle state machine based on which tools were called, and increments the matching OTel counters.
3. A structured `INFO` log entry is written.

The `ConsoleLogger` is a backward-compatible stub that logs an ASCII-art audit box to the Python logging system. It is not used by the server; `OtelObservabilityLayer` is the primary implementation for all new deployments.

---

## Folder structure

```
observability_layer/
├── main.py
├── pyproject.toml
├── config/
│   ├── dpg.yaml          # OTel collector endpoint, sample_rate, export_interval_ms, PII exclusions
│   └── domain.yaml       # Domain overrides (lifecycle states, metrics, SLI thresholds)
├── src/
│   ├── base.py                        # ObservabilityLayerBase ABC (emit_turn, emit_signal)
│   ├── otel_observability_layer.py    # OtelObservabilityLayer — primary implementation
│   ├── outcome_tracker.py             # OutcomeTracker — lifecycle state machine, OTel metric increments
│   ├── console_logger.py              # ConsoleLogger — backward-compatible stub
│   ├── server.py                      # FastAPI app (4 endpoints)
│   ├── schema/
│   │   ├── __init__.py
│   │   └── config.py                  # ObservabilityConfig — Pydantic v2 schema for full domain config
│   └── dpg_telemetry/                 # Shared OTel bootstrap package (installed by all 7 blocks)
│       ├── __init__.py                # Exports: init_otel(), get_tracer(), get_meter(), _reset_for_testing()
│       ├── bootstrap.py               # init_otel() — TracerProvider, MeterProvider, OTLP exporters, W3C propagator
│       ├── resource.py                # build_resource() — OTel Resource with service.name, dpg.block, dpg.domain
│       └── propagator.py             # configure_propagator() — W3C TraceContext + Baggage
└── tests/
    ├── test_schema.py                     (9 tests)
    ├── test_outcome_tracker.py            (10 tests)
    ├── test_console_logger.py             (20 tests)
    ├── test_otel_observability_layer.py   (6 tests)
    ├── test_dpg_telemetry.py              (10 tests)
    ├── test_server.py                     (16 tests)
    └── test_main.py                       (23 tests)
```

Total: 7 test modules, 94 tests.

---

## HTTP API

The service runs on port **8004**.

### `POST /emit/turn`

Records a complete turn event. Called once per turn, after the response has been returned to the user.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "turn_id": "turn-001",
  "trace_id": "abc123def456",
  "response_text": "Hubli mein electrician ke liye salary ₹15,000–₹28,000/month hai.",
  "tool_calls": ["onest_market_lookup"],
  "trust_input_result": { "passed": true, "action": "allow", "reason": null },
  "trust_output_result": { "passed": true, "action": "allow", "reason": null },
  "model_used": "claude-haiku-4-5-20251001",
  "intent": "job_search",
  "input_tokens": 342,
  "output_tokens": 87,
  "latency_ms": 1102,
  "timestamp_ms": 1700000000000
}
```

**Response:** `{ "status": "ok" }`

Always returns 200, even on internal errors — observability must never block.

---

### `POST /emit/signal`

Records an explicit or implicit feedback signal.

**Request:**
```json
{
  "signal_type": "task_completed",
  "data": { "session_id": "sess-abc123", "outcome": "placement" }
}
```

Signal types include: `drop_off`, `mismatch`, `escalation_requested`, `task_completed`.

**Response:** `{ "status": "ok" }`

Always returns 200, even on internal errors.

---

### `GET /validate-config`

Validates the loaded `ObservabilityConfig` against the Pydantic v2 schema and returns the domain name.

**Response:**
```json
{ "status": "ok", "domain": "kkb" }
```

---

### `GET /health`

```json
{ "status": "ok" }
```

---

## The `dpg_telemetry` package

`src/dpg_telemetry/` is a shared package installed as a dependency by all 7 DPG blocks. It provides a single initialisation call and typed accessors for the OTel tracer and meter.

### Usage in each block

Call `init_otel()` once at startup, before handling any requests:

```python
from dpg_telemetry import init_otel, get_tracer, get_meter

init_otel(service_name="agent_core", config=config)   # config is the merged YAML dict
tracer = get_tracer(__name__)
meter  = get_meter(__name__)
```

### What `init_otel()` does

1. Thread-safe idempotency guard — runs only once per process even if called multiple times.
2. Creates an OTel `Resource` with attributes: `service.name`, `dpg.block`, `dpg.domain`, `service.version`.
3. Configures a `TracerProvider` with:
   - OTLP gRPC exporter pointing at `observability.otel.collector_endpoint`
   - `BatchSpanProcessor`
   - `ParentBased(TraceIdRatioBased(sample_rate))` sampler
4. Configures a `MeterProvider` with:
   - OTLP gRPC exporter
   - `PeriodicExportingMetricReader` (interval from `export_interval_ms`)
5. Sets W3C TraceContext + Baggage as the global propagator.
6. Never raises — logs to stderr if the OTLP exporter is unreachable.

### Block instrumentation

Each block self-instruments and emits spans and metrics to the OTel Collector:

| Block | Key spans | Key metrics |
|-------|-----------|-------------|
| agent_core | `orchestrator.turn`, `llm.call` | `llm.tokens`, `turn.latency_ms` |
| trust_layer | `trust.input_check`, `trust.output_check` | `trust.blocks` |
| knowledge_engine | `ke.prompt_assemble`, `ke.rag_retrieve` | `rag.retrieved_docs` |
| memory_layer | `memory.read`, `memory.write` | `memory.latency_ms` |
| action_gateway | `action.execute` | `action.calls` |
| reach_layer | `reach.inbound`, `reach.outbound` | `reach.sessions` |

---

## OtelObservabilityLayer and OutcomeTracker

### OtelObservabilityLayer

The primary `ObservabilityLayerBase` implementation. Used by the server for all production paths.

- `emit_turn(event)` — extracts `trace_id`, `session_id`, token counts, and latency; calls `OutcomeTracker.process(event)`; writes a structured `INFO` log.
- `emit_signal(signal)` — writes a structured `INFO` log with `signal_type` and `data`.
- Never raises — all exceptions are caught and logged as `ERROR`.

### OutcomeTracker

A lifecycle state machine that maps tool invocations to conversation outcome states.

- Reads `lifecycle[]` from `ObservabilityConfig` at startup. Each state has a `trigger_tool` (the tool name whose invocation transitions into that state).
- `process(event)` — for each tool call in the turn event, finds the matching lifecycle state by `trigger_tool` and increments the corresponding OTel counter.
- Creates OTel instruments (counter, gauge, histogram) at startup from the `metrics[]` config section.
- `trigger_condition` field in the config: reserved for future conditional logic; currently ignored (any invocation of `trigger_tool` transitions the state).

---

## Configuration

| Key | Description |
|-----|-------------|
| `observability.otel.collector_endpoint` | OTLP gRPC collector URL (default: `http://localhost:4317`) |
| `observability.otel.sample_rate` | Trace sampling rate 0.0–1.0 (default: `1.0`) |
| `observability.otel.export_interval_ms` | Metric export interval (default: `5000`) |
| `observability.outcomes.lifecycle[]` | Ordered lifecycle states: `state`, `trigger_tool`, `trigger_condition` (reserved) |
| `observability.outcomes.metrics[]` | OTel instruments: `name`, `instrument` (`counter`/`gauge`/`histogram`), `description`, `unit`, `attributes[]` |
| `observability.sli.turn_latency_p99_ms` | P99 latency SLI threshold (default: `1200`) |
| `observability.sli.trust_block_rate_max` | Max acceptable trust block rate (default: `0.05`) |
| `observability.audit.retention_days` | Audit log retention period (default: `90`) |
| `observability.audit.pii_fields_excluded` | PII fields excluded from audit log — stricter; excludes `user_id` (DPDP Act compliance) |
| `observability.telemetry.pii_fields_excluded` | PII fields excluded from OTel traces — less strict; allows `user_id` for dashboards |

Config is loaded once at startup by deep-merging `config/dpg.yaml` (framework defaults) with `config/domain.yaml` (domain overrides).

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

---

## Dependencies

```
fastapi                                          >= 0.110
uvicorn[standard]                                >= 0.29
pydantic                                         >= 2.0
pyyaml                                           >= 6.0
python-dotenv                                    >= 1.0.0
opentelemetry-api                                >= 1.20
opentelemetry-sdk                                >= 1.20
opentelemetry-exporter-otlp-proto-grpc           >= 1.20
```

Requires Python 3.11+.

---

## Known gaps

- **PII field filtering not enforced in code.** The `observability.audit.pii_fields_excluded` and `observability.telemetry.pii_fields_excluded` config fields define what DPGs must not include in span attributes and log fields for DPDP Act compliance, but enforcement at each DPG's instrumentation point is not yet applied.
- **`OutcomeTracker.trigger_condition` not evaluated.** The `trigger_condition` field is parsed from config but currently ignored; any invocation of `trigger_tool` transitions the state.
- **Grafana dashboard provisioning.** The provisioning directory structure exists under `automation/docker/grafana/provisioning/` but dashboards are not yet provisioned.

### Audit trail

Two separate audit systems exist and serve different purposes — both are needed:

- **OTel → Loki/Jaeger** (this layer): structured observability telemetry — spans, metrics, and traces. Each DPG self-instruments via `dpg_telemetry`, emitting data to the OTel Collector over OTLP/gRPC. The Collector forwards logs to Loki and traces to Jaeger.
- **SQLiteAuditStore in `memory_layer/`**: raw conversation transcript — every turn's user message and agent response, plus subagent_id, intent, model, and latency_ms per turn (`turn_audit` table). Also records session lifecycle events (start/end/escalate) with `consent_given` for DPDP Act compliance (`session_audit` table). Accessed via `GET /audit/sessions/{session_id}/history` and `GET /users/{user_id}/active-history`.

The SQLite store is the conversation transcript log used for DPDP audit and conversation replay. It is not replaced by the OTel pipeline, which holds instrumentation telemetry rather than raw message content.
