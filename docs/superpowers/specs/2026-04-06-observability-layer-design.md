# Observability Layer Design

**Date:** 2026-04-06
**Issue:** #10
**Status:** Approved — ready for implementation

---

## Summary

Rename the Learning Layer to **Observability Layer** and redesign it as an OpenTelemetry-compliant observability system for the DPG framework. Every block self-instruments using a shared `dpg_telemetry` bootstrap package. Telemetry flows via OTLP/gRPC to an OTel Collector sidecar that fans out to Jaeger (traces), Prometheus (metrics), and Loki (logs). Domain-specific observability — outcome lifecycle, custom metrics, SLI thresholds — is defined in a config schema owned by the Observability Layer and implemented per-domain in YAML.

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DPG Runtime                              │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  agent_core  │  │ trust_layer  │  │knowledge_eng │  ...    │
│  │              │  │              │  │              │         │
│  │ init_otel()  │  │ init_otel()  │  │ init_otel()  │         │
│  │  (spans,     │  │  (spans,     │  │  (spans,     │         │
│  │  metrics,    │  │  metrics,    │  │  metrics,    │         │
│  │  logs)       │  │  logs)       │  │  logs)       │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         └────────────────┬┘──────────────────┘                 │
│                    OTLP/gRPC                                    │
│                          │                                      │
│              ┌───────────▼──────────┐                           │
│              │   OTel Collector     │  (Docker sidecar)         │
│              │   otelcol-contrib    │                           │
│              └───┬──────┬──────┬───┘                           │
│                  │      │      │                                │
│            Jaeger   Prom    Loki                                │
│           (16686)  (9090)  (3100)                               │
└─────────────────────────────────────────────────────────────────┘
```

### Observability Layer module responsibilities

1. **`dpg_telemetry` package** — installable Python package (`pip install -e observability_layer`). Exposes `init_otel(service_name, config)`. Every block calls this at startup. Configures TracerProvider, MeterProvider, LoggerProvider, OTLP exporters, W3C propagator, and resource attributes.

2. **Schema authority** — defines and validates `ObservabilityConfig` (Pydantic v2). DPG-level config sets standard defaults; domain-level config implements the full outcome schema. Invalid config raises at startup, never at runtime.

3. **Outcome Tracker** — runtime component that processes `TurnEvent` payloads against domain lifecycle config. Maps tool call results to lifecycle state transitions and increments OTel metrics. Runs async, never in response path.

4. **HTTP service (port 8004)** — `/health` + `/emit/turn` (backward-compatible, now routes to OutcomeTracker) + `/validate-config`.

---

## 2. Component Design

### `dpg_telemetry` package

```
observability_layer/src/dpg_telemetry/
├── __init__.py          # init_otel(), get_tracer(), get_meter(), get_logger()
├── bootstrap.py         # TracerProvider / MeterProvider / LoggerProvider setup
├── propagator.py        # W3C TraceContext + Baggage configuration
└── resource.py          # Resource attributes builder (service.name, dpg.block, dpg.domain)
```

Usage in every block:

```python
from dpg_telemetry import init_otel, get_tracer, get_meter

init_otel(service_name="trust_layer", config=config)

tracer = get_tracer(__name__)
meter  = get_meter(__name__)
```

`init_otel()` is idempotent — safe to call multiple times (only configures on first call).

### Block instrumentation

Each block wraps key operations in named spans with semantic attributes:

| Block | Key spans | Key metrics |
|---|---|---|
| `agent_core` | `orchestrator.turn`, `llm.call`, `nlu.classify`, `tool.execute` | `llm.tokens` (counter), `turn.latency_ms` (histogram) |
| `trust_layer` | `trust.input_check`, `trust.output_check` | `trust.blocks` (counter, by reason) |
| `knowledge_engine` | `ke.prompt_assemble`, `ke.rag_retrieve` | `rag.retrieved_docs` (histogram) |
| `memory_layer` | `memory.read`, `memory.write` | `memory.latency_ms` (histogram) |
| `action_gateway` | `action.execute` | `action.calls` (counter, by tool_name + status) |
| `reach_layer` | `reach.inbound`, `reach.outbound` | `reach.sessions` (counter, by channel) |

Span attributes follow OTel semantic conventions where they exist (`gen_ai.*` for LLM, `db.*` for Memory Layer) and `dpg.*` for DPG-specific attributes.

`orchestrator.turn` span attributes include: `session_id`, `turn_id`, `user_id`, `intent`, `dpg.domain`.

### `ObservabilityConfig` schema

Defined in `observability_layer/src/schema/config.py` (Pydantic v2). Two-layer config:

**DPG-level (`dev-kit/dpg/observability_layer.yaml`) — framework defaults:**

```yaml
server:
  host: "0.0.0.0"
  port: 8004

observability:
  otel:
    collector_endpoint: "http://otelcol:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
  telemetry:
    pii_fields_excluded: ["user_message"]
  audit:
    pii_fields_excluded: ["user_message", "user_id"]
```

**Domain-level (`dev-kit/configs/kkb/observability_layer.yaml`) — KKB implementation:**

```yaml
observability:
  domain: "kkb"

  outcomes:
    lifecycle:
      - state: "enquiry"
        trigger_tool: null
      - state: "applied"
        trigger_tool: "onest_apply"
        trigger_condition: "result == 'success'"
      - state: "shortlisted"
        trigger_tool: "onest_status_check"
        trigger_condition: "status == 'shortlisted'"
      - state: "placed"
        trigger_tool: "onest_status_check"
        trigger_condition: "status == 'placed'"

    metrics:
      - name: "placement.rate"
        instrument: gauge
        description: "Percentage of sessions reaching placed state, computed by OutcomeTracker as placed_sessions / total_sessions"
        unit: "%"
      - name: "placement.applications"
        instrument: counter
        description: "Total job applications submitted"
      - name: "drop_off.by_stage"
        instrument: counter
        description: "Sessions that ended at each lifecycle stage"
        attributes: ["stage", "intent"]

  sli:
    turn_latency_p99_ms: 1200
    trust_block_rate_max: 0.05

  audit:
    retention_days: 90
```

### `ObservabilityLayerBase` ABC

```python
class ObservabilityLayerBase(ABC):

    @abstractmethod
    def emit_turn(self, event: TurnEvent) -> None:
        """Process a completed turn event. Must never block or raise."""

    @abstractmethod
    def emit_signal(self, signal_type: str, data: dict) -> None:
        """Process a discrete signal. Must never block or raise."""
```

Concrete implementation: `OtelObservabilityLayer` replaces `ConsoleLogger`.

---

## 3. Data Flow

### Full turn trace (end-to-end)

```
Reach Layer
  span: reach.inbound [trace_id=T1, span_id=S1]
  │  injects W3C traceparent header into HTTP request to Agent Core
  │
  ▼
Agent Core
  span: orchestrator.turn [T1, S2, parent=S1]
  │  attributes: session_id, turn_id, user_id, intent, dpg.domain
  │
  ├── Memory Layer (HTTP, traceparent propagated)
  │     span: memory.read [T1, S3, parent=S2]
  │
  ├── Trust Layer — input (HTTP, traceparent propagated)
  │     span: trust.input_check [T1, S4, parent=S2]
  │     attributes: trust.action, trust.reason
  │
  ├── Knowledge Engine (HTTP, traceparent propagated)
  │     span: ke.prompt_assemble [T1, S5, parent=S2]
  │       └── ke.rag_retrieve [T1, S6, parent=S5]
  │
  ├── LLM call
  │     span: llm.call [T1, S7, parent=S2]
  │     attributes: gen_ai.model, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens
  │
  ├── Action Gateway (if tool_use, HTTP, traceparent propagated)
  │     span: action.execute [T1, S8, parent=S2]
  │     attributes: dpg.tool_name, dpg.tool_status
  │
  ├── Trust Layer — output (HTTP, traceparent propagated)
  │     span: trust.output_check [T1, S9, parent=S2]
  │
  └── [async, after response delivered]
        emit_turn(TurnEvent{trace_id=T1, ...})
        → OutcomeTracker evaluates tool_calls against lifecycle config
        → increments OTel metrics (placement.applications, drop_off.by_stage)
        → attaches span events to T1
```

### TurnEvent schema change

One new field required on `TurnEvent` in `agent_core/src/models.py`:

```python
trace_id: str  # W3C trace ID for attaching outcome events to the distributed trace
```

If `trace_id` is absent or empty, `OutcomeTracker` emits metric increments without span attachment (graceful degradation, logs a warning).

### Metrics export

```
All blocks → OTLP/gRPC (port 4317) → OTel Collector
  Collector pipelines:
    traces  → Jaeger  (port 16686)
    metrics → Prometheus (port 9090)
    logs    → Loki (port 3100)
```

Collector config: `automation/docker/otelcol/otelcol-config.yaml`.

### PII separation

| Sink | `user_id` | `user_message` |
|---|---|---|
| OTel traces / metrics | Included (dashboarding) | Excluded |
| Audit log | Excluded (DPDP Act) | Excluded |

Controlled via separate `pii_fields_excluded` lists in the config schema (see above).

---

## 4. Error Handling

- `init_otel()` failure → logs to stderr, block continues. A misconfigured Collector must not prevent startup.
- OTel SDK export failures → SDK internal retry queue handles transient Collector unavailability. Never propagates to block.
- `OutcomeTracker.process()` runs in a daemon thread. All exceptions caught, logged as structured errors, dropped. Never propagates to caller.
- `ObservabilityConfig` schema validation → fail-fast at startup only, never at request time.
- Missing `trace_id` in `TurnEvent` → graceful degradation: metrics emitted, span attachment skipped, warning logged.
- Collector unreachable at startup → block logs a warning and continues. Telemetry is best-effort; system availability is not coupled to observability infrastructure.

---

## 5. Testing

| Layer | What | Approach |
|---|---|---|
| `dpg_telemetry.init_otel` | TracerProvider, MeterProvider, LoggerProvider configured correctly | `InMemorySpanExporter` + `InMemoryMetricReader` |
| Block instrumentation | Spans emitted with correct names and attributes | `InMemorySpanExporter` per block unit tests |
| `ObservabilityConfig` schema | Valid / invalid / partial domain YAML | Pydantic validation, no I/O |
| `OutcomeTracker` | Lifecycle transitions, metric increments, missing trace_id degradation | Mocked `MeterProvider`, fixture `TurnEvent`s |
| Integration | Full turn produces connected trace across all blocks | Docker Compose test with real Collector |

Coverage target: ≥70% line coverage on `observability_layer/src/`.

---

## 6. Docker Compose additions

New services in `automation/docker/docker-compose.dev.yml`:

| Service | Image | Port |
|---|---|---|
| `otelcol` | `otel/opentelemetry-collector-contrib` | 4317 (OTLP gRPC), 4318 (OTLP HTTP) |
| `jaeger` | `jaegertracing/all-in-one` | 16686 (UI) |
| `prometheus` | `prom/prometheus` | 9090 |
| `loki` | `grafana/loki` | 3100 |
| `grafana` | `grafana/grafana` | 3000 |

---

## 7. File changes summary

| Path | Change |
|---|---|
| `learning_layer/` → `observability_layer/` | Rename directory |
| `observability_layer/src/dpg_telemetry/` | New package — bootstrap, propagator, resource |
| `observability_layer/src/schema/config.py` | New — `ObservabilityConfig` Pydantic model |
| `observability_layer/src/outcome_tracker.py` | New — lifecycle state machine + OTel metric emitter |
| `observability_layer/src/otel_observability_layer.py` | New — `OtelObservabilityLayer` replacing `ConsoleLogger` |
| `observability_layer/src/server.py` | Update — wire `OtelObservabilityLayer`, add `/validate-config` |
| `agent_core/src/models.py` | Add `trace_id: str` to `TurnEvent` |
| `agent_core/src/http_clients/learning_layer.py` | Update — pass `trace_id`, rename references |
| `agent_core/src/orchestrator.py` | Add `init_otel()` call at startup; instrument turn, LLM, NLU, tool spans |
| `trust_layer/src/` | Add `init_otel()` + span instrumentation |
| `knowledge_engine/src/` | Add `init_otel()` + span instrumentation |
| `memory_layer/src/` | Add `init_otel()` + span instrumentation |
| `action_gateway/src/` | Add `init_otel()` + span instrumentation |
| `reach_layer/src/` | Add `init_otel()` + span instrumentation |
| `dev-kit/dpg/observability_layer.yaml` | New — framework defaults (replaces `learning_layer.yaml`) |
| `dev-kit/configs/kkb/observability_layer.yaml` | New — KKB domain config (outcome lifecycle, metrics, SLI) |
| `automation/docker/otelcol/otelcol-config.yaml` | New — Collector pipeline config |
| `automation/docker/docker-compose.dev.yml` | Add otelcol, jaeger, prometheus, loki, grafana |
| `ARCHITECTURE.md` | Update block name, responsibilities, turn sequence |
| All `pyproject.toml` files | Add `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` |

---

## 8. Out of scope

- Grafana dashboard definitions (`.json` provisioning files)
- Alert manager rules
- Production Collector configuration (TLS, authentication)
- ASR/TTS trace instrumentation
- Multi-tenancy trace isolation
