# Observability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Learning Layer to Observability Layer and build a full OpenTelemetry-compliant observability system with distributed tracing, metrics, and config-driven outcome tracking across all 7 DPG blocks.

**Architecture:** All 7 blocks self-instrument using a shared `dpg_telemetry` bootstrap package (installed from `observability_layer/`). Telemetry flows via OTLP/gRPC to an OTel Collector sidecar that fans out to Jaeger (traces), Prometheus (metrics), and Loki (logs). Domain-specific outcome tracking is defined in a Pydantic v2-validated config schema owned by the Observability Layer, implemented per domain in YAML.

**Tech Stack:** opentelemetry-sdk>=1.20, opentelemetry-exporter-otlp-proto-grpc>=1.20, opentelemetry-instrumentation-fastapi, opentelemetry-instrumentation-httpx, Pydantic v2, FastAPI, uv

**Spec:** `docs/superpowers/specs/2026-04-06-observability-layer-design.md`

---

## File Map

### New / Renamed

| Path | What |
|---|---|
| `learning_layer/` → `observability_layer/` | Directory rename |
| `observability_layer/src/dpg_telemetry/__init__.py` | Public API: `init_otel`, `get_tracer`, `get_meter` |
| `observability_layer/src/dpg_telemetry/bootstrap.py` | TracerProvider + MeterProvider setup |
| `observability_layer/src/dpg_telemetry/resource.py` | OTel Resource attributes builder |
| `observability_layer/src/dpg_telemetry/propagator.py` | W3C TraceContext + Baggage propagator |
| `observability_layer/src/schema/__init__.py` | Empty |
| `observability_layer/src/schema/config.py` | `ObservabilityConfig` Pydantic v2 model |
| `observability_layer/src/base.py` | `ObservabilityLayerBase` ABC |
| `observability_layer/src/otel_observability_layer.py` | `OtelObservabilityLayer` concrete impl |
| `observability_layer/src/outcome_tracker.py` | Lifecycle state machine + OTel metric emitter |
| `automation/docker/otelcol/otelcol-config.yaml` | OTel Collector pipeline config |
| `dev-kit/dpg/observability_layer.yaml` | Framework defaults (replaces `learning_layer.yaml`) |
| `dev-kit/configs/kkb/observability_layer.yaml` | KKB outcome lifecycle, metrics, SLI |

### Modified

| Path | What changes |
|---|---|
| `observability_layer/pyproject.toml` | Name, OTel deps, package discovery |
| `observability_layer/main.py` | Import `OtelObservabilityLayer`, domain config key |
| `observability_layer/src/server.py` | Wire `OtelObservabilityLayer`, add `/validate-config` |
| `observability_layer/config/dpg.yaml` | Rename sections |
| `agent_core/src/models.py` | Add `trace_id: str` to `TurnEvent` |
| `agent_core/src/http_clients/learning_layer.py` | Pass `trace_id` in serialisation |
| `agent_core/src/orchestrator.py` | `init_otel()` at startup + `orchestrator.turn` span |
| `agent_core/pyproject.toml` | Add dpg_telemetry dep |
| `trust_layer/src/server.py` | `init_otel()` + span instrumentation |
| `trust_layer/pyproject.toml` | Add dpg_telemetry dep |
| `knowledge_engine/src/server.py` | `init_otel()` + span instrumentation |
| `knowledge_engine/pyproject.toml` | Add dpg_telemetry dep |
| `memory_layer/src/server.py` | `init_otel()` + span instrumentation |
| `memory_layer/pyproject.toml` | Add dpg_telemetry dep |
| `action_gateway/src/mock_server.py` | `init_otel()` + span instrumentation |
| `action_gateway/pyproject.toml` | Add dpg_telemetry dep |
| `reach_layer/src/web_reach.py` | `init_otel()` + span instrumentation |
| `reach_layer/pyproject.toml` | Add dpg_telemetry dep |
| `automation/docker/docker-compose.dev.yml` | Add otelcol, jaeger, prometheus, loki, grafana |
| `ARCHITECTURE.md` | Rename Learning Layer → Observability Layer throughout |

---

## Task 1: Rename `learning_layer` → `observability_layer`

**Files:**
- Rename: `learning_layer/` → `observability_layer/`
- Modify: `observability_layer/pyproject.toml`
- Modify: `observability_layer/config/dpg.yaml`

- [ ] **Step 1: Git rename the directory**

```bash
cd /path/to/repo
git mv learning_layer observability_layer
```

- [ ] **Step 2: Update `observability_layer/pyproject.toml`**

Replace the entire file:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "observability-layer"
version = "0.1.0"
description = "Observability Layer — OpenTelemetry-compliant observability DPG block"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-grpc>=1.20",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-mock>=3.0",
    "opentelemetry-sdk>=1.20",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src"]

[tool.coverage.run]
source = ["src"]
omit = [
    "*/tests/*",
    "*/__init__.py",
]

[tool.coverage.report]
fail_under = 70
show_missing = true
```

- [ ] **Step 3: Update `observability_layer/config/dpg.yaml`**

```yaml
# observability_layer/config/dpg.yaml — DPG framework defaults.

server:
  host: "0.0.0.0"
  port: 8004

observability:
  otel:
    collector_endpoint: "http://localhost:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
  telemetry:
    pii_fields_excluded:
      - "user_message"
  audit:
    pii_fields_excluded:
      - "user_message"
      - "user_id"
    retention_days: 90
```

- [ ] **Step 4: Sync dependencies**

```bash
cd observability_layer
uv sync
```

Expected: resolves without error.

- [ ] **Step 5: Verify existing tests still pass**

```bash
cd observability_layer
uv run pytest tests/ -v
```

Expected: all 34 existing tests pass (console_logger + server tests).

- [ ] **Step 6: Commit**

```bash
git add observability_layer/
git commit -m "refactor: rename learning_layer → observability_layer, add OTel deps"
```

---

## Task 2: Create `dpg_telemetry` package

**Files:**
- Create: `observability_layer/src/dpg_telemetry/__init__.py`
- Create: `observability_layer/src/dpg_telemetry/resource.py`
- Create: `observability_layer/src/dpg_telemetry/propagator.py`
- Create: `observability_layer/src/dpg_telemetry/bootstrap.py`
- Create: `observability_layer/tests/test_dpg_telemetry.py`

- [ ] **Step 1: Write the failing tests**

Create `observability_layer/tests/test_dpg_telemetry.py`:

```python
"""Tests for the dpg_telemetry bootstrap package."""
from unittest.mock import patch, MagicMock
import pytest

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


def test_build_resource_sets_service_name():
    from dpg_telemetry.resource import build_resource
    resource = build_resource("trust_layer", {"observability": {"domain": "kkb"}})
    attrs = resource.attributes
    assert attrs["service.name"] == "trust_layer"
    assert attrs["dpg.block"] == "trust_layer"
    assert attrs["dpg.domain"] == "kkb"


def test_build_resource_defaults_domain_to_unknown():
    from dpg_telemetry.resource import build_resource
    resource = build_resource("agent_core", {})
    assert resource.attributes["dpg.domain"] == "unknown"


def test_configure_propagator_sets_w3c():
    from dpg_telemetry.propagator import configure_propagator
    from opentelemetry.propagate import get_global_textmap
    configure_propagator()
    propagator = get_global_textmap()
    assert propagator is not None


def test_init_otel_does_not_raise_on_missing_collector():
    """init_otel must not raise even when the collector is unreachable."""
    from dpg_telemetry import init_otel
    # Use a port that is definitely not listening
    config = {"observability": {"otel": {"collector_endpoint": "http://localhost:19999"}, "domain": "test"}}
    # Should not raise
    init_otel("test_service", config)


def test_init_otel_is_idempotent():
    """Calling init_otel twice must not raise or reconfigure."""
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    config = {"observability": {"otel": {"collector_endpoint": "http://localhost:4317"}, "domain": "test"}}
    init_otel("svc", config)
    init_otel("svc", config)  # second call — must be a no-op, no error


def test_get_tracer_returns_tracer():
    from dpg_telemetry import get_tracer
    tracer = get_tracer("my.module")
    assert tracer is not None


def test_get_meter_returns_meter():
    from dpg_telemetry import get_meter
    meter = get_meter("my.module")
    assert meter is not None


def test_init_otel_none_config_does_not_raise():
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    init_otel("svc", {})  # empty config should use defaults, not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd observability_layer
uv run pytest tests/test_dpg_telemetry.py -v
```

Expected: `ImportError` — `dpg_telemetry` does not exist yet.

- [ ] **Step 3: Create `observability_layer/src/dpg_telemetry/resource.py`**

```python
"""
observability_layer/src/dpg_telemetry/resource.py

Builds the OTel Resource for a DPG block service.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

from opentelemetry.sdk.resources import Resource


def build_resource(service_name: str, config: dict) -> Resource:
    """Build an OTel Resource with DPG-standard attributes.

    Args:
        service_name: The block's service name (e.g. "trust_layer").
        config: Full merged config dict for the service.

    Returns:
        Resource with service.name, dpg.block, dpg.domain, and service.version.
    """
    obs_cfg = config.get("observability", {}) if config else {}
    domain = obs_cfg.get("domain", "unknown")
    return Resource.create({
        "service.name": service_name,
        "dpg.block": service_name,
        "dpg.domain": domain,
        "service.version": "0.1.0",
    })
```

- [ ] **Step 4: Create `observability_layer/src/dpg_telemetry/propagator.py`**

```python
"""
observability_layer/src/dpg_telemetry/propagator.py

Configures the global W3C TraceContext + Baggage propagator.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def configure_propagator() -> None:
    """Set the global OTel propagator to W3C TraceContext + Baggage.

    Must be called once at service startup. Safe to call multiple times
    (each call replaces the previous propagator).
    """
    set_global_textmap(
        CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ])
    )
```

- [ ] **Step 5: Create `observability_layer/src/dpg_telemetry/bootstrap.py`**

```python
"""
observability_layer/src/dpg_telemetry/bootstrap.py

OTel SDK bootstrap — configures TracerProvider and MeterProvider.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

import logging
import threading

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from dpg_telemetry.propagator import configure_propagator
from dpg_telemetry.resource import build_resource

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialised = False


def init_otel(service_name: str, config: dict) -> None:
    """Configure OTel SDK for a DPG block. Idempotent — safe to call multiple times.

    Configures TracerProvider with OTLP gRPC export and ratio-based sampling,
    MeterProvider with periodic OTLP export, and W3C propagator. Failure never
    raises — a misconfigured Collector must not prevent service startup.

    Args:
        service_name: Service name for Resource attributes (e.g. "trust_layer").
        config: Full merged config dict. Reads observability.otel section.
    """
    global _initialised
    with _lock:
        if _initialised:
            return
        try:
            obs_cfg = (config or {}).get("observability", {})
            otel_cfg = obs_cfg.get("otel", {})
            endpoint = otel_cfg.get("collector_endpoint", "http://localhost:4317")
            sample_rate = float(otel_cfg.get("sample_rate", 1.0))
            export_interval_ms = int(otel_cfg.get("export_interval_ms", 5000))

            resource = build_resource(service_name, config or {})

            # TracerProvider
            tracer_provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(TraceIdRatioBased(sample_rate)),
            )
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, insecure=True)
                )
            )
            trace.set_tracer_provider(tracer_provider)

            # MeterProvider
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True),
                export_interval_millis=export_interval_ms,
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader],
            )
            metrics.set_meter_provider(meter_provider)

            configure_propagator()

            _initialised = True
            logger.info(
                "dpg_telemetry.init",
                extra={
                    "operation": "dpg_telemetry.init_otel",
                    "status": "success",
                    "service_name": service_name,
                    "endpoint": endpoint,
                    "sample_rate": sample_rate,
                },
            )

        except Exception as e:
            logger.error(
                "dpg_telemetry.init_error",
                extra={
                    "operation": "dpg_telemetry.init_otel",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            # Never raise — observability must not prevent service startup


def reset_for_testing() -> None:
    """Reset global OTel state. For use in tests only."""
    global _initialised
    with _lock:
        _initialised = False
        trace.set_tracer_provider(trace.ProxyTracerProvider())
        metrics.set_meter_provider(metrics.ProxyMeterProvider())
```

- [ ] **Step 6: Create `observability_layer/src/dpg_telemetry/__init__.py`**

```python
"""
observability_layer/src/dpg_telemetry/__init__.py

Public API for the dpg_telemetry bootstrap package.

Every DPG block imports this package to initialise OTel SDK and obtain
framework-standard tracers, meters, and loggers.

Usage:
    from dpg_telemetry import init_otel, get_tracer, get_meter

    init_otel(service_name="trust_layer", config=config)
    tracer = get_tracer(__name__)
    meter  = get_meter(__name__)
"""

from __future__ import annotations

from opentelemetry import metrics, trace

from dpg_telemetry.bootstrap import init_otel as _bootstrap_init
from dpg_telemetry.bootstrap import reset_for_testing as _bootstrap_reset


def init_otel(service_name: str, config: dict) -> None:
    """Initialise OTel SDK for a DPG block. Idempotent. Never raises.

    Args:
        service_name: Block service name (e.g. "agent_core").
        config: Full merged config dict.
    """
    _bootstrap_init(service_name, config)


def get_tracer(name: str):
    """Return an OTel Tracer for the given instrumentation scope.

    Args:
        name: Instrumentation scope name, typically ``__name__``.

    Returns:
        opentelemetry.trace.Tracer instance.
    """
    return trace.get_tracer(name)


def get_meter(name: str):
    """Return an OTel Meter for the given instrumentation scope.

    Args:
        name: Instrumentation scope name, typically ``__name__``.

    Returns:
        opentelemetry.metrics.Meter instance.
    """
    return metrics.get_meter(name)


def _reset_for_testing() -> None:
    """Reset OTel global state between tests. Do not call in production."""
    _bootstrap_reset()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd observability_layer
uv run pytest tests/test_dpg_telemetry.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 8: Commit**

```bash
git add observability_layer/src/dpg_telemetry/ observability_layer/tests/test_dpg_telemetry.py
git commit -m "feat(observability): add dpg_telemetry bootstrap package with OTel SDK setup"
```

---

## Task 3: Create `ObservabilityConfig` schema

**Files:**
- Create: `observability_layer/src/schema/__init__.py`
- Create: `observability_layer/src/schema/config.py`
- Create: `observability_layer/tests/test_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `observability_layer/tests/test_schema.py`:

```python
"""Tests for ObservabilityConfig Pydantic schema."""
import pytest
from pydantic import ValidationError

from schema.config import (
    ObservabilityConfig,
    InstrumentType,
    LifecycleState,
    MetricDefinition,
)


def test_from_config_full_kkb_config():
    config = {
        "observability": {
            "domain": "kkb",
            "otel": {
                "collector_endpoint": "http://otelcol:4317",
                "sample_rate": 0.5,
                "export_interval_ms": 3000,
            },
            "outcomes": {
                "lifecycle": [
                    {"state": "enquiry", "trigger_tool": None},
                    {"state": "applied", "trigger_tool": "onest_apply", "trigger_condition": "result == 'success'"},
                ],
                "metrics": [
                    {"name": "placement.applications", "instrument": "counter", "description": "Applications submitted"},
                    {"name": "placement.rate", "instrument": "gauge", "description": "Placement rate", "unit": "%"},
                ],
            },
            "sli": {"turn_latency_p99_ms": 1200, "trust_block_rate_max": 0.05},
            "audit": {"retention_days": 90},
        }
    }
    cfg = ObservabilityConfig.from_config(config)
    assert cfg.domain == "kkb"
    assert cfg.otel.collector_endpoint == "http://otelcol:4317"
    assert cfg.otel.sample_rate == 0.5
    assert len(cfg.outcomes.lifecycle) == 2
    assert cfg.outcomes.lifecycle[1].trigger_tool == "onest_apply"
    assert len(cfg.outcomes.metrics) == 2
    assert cfg.outcomes.metrics[0].instrument == InstrumentType.counter
    assert cfg.sli.turn_latency_p99_ms == 1200
    assert cfg.audit.retention_days == 90


def test_from_config_empty_uses_defaults():
    cfg = ObservabilityConfig.from_config({})
    assert cfg.domain == "unknown"
    assert cfg.otel.collector_endpoint == "http://localhost:4317"
    assert cfg.otel.sample_rate == 1.0
    assert cfg.otel.export_interval_ms == 5000
    assert cfg.outcomes.lifecycle == []
    assert cfg.outcomes.metrics == []
    assert cfg.sli.turn_latency_p99_ms == 1200
    assert cfg.audit.retention_days == 90


def test_invalid_instrument_type_raises():
    with pytest.raises(ValidationError):
        MetricDefinition(
            name="foo",
            instrument="not_valid",  # type: ignore
            description="test",
        )


def test_lifecycle_state_optional_fields():
    state = LifecycleState(state="enquiry")
    assert state.trigger_tool is None
    assert state.trigger_condition is None


def test_pii_fields_excluded_defaults():
    cfg = ObservabilityConfig.from_config({})
    assert "user_message" in cfg.audit.pii_fields_excluded
    assert "user_id" in cfg.audit.pii_fields_excluded
    assert "user_message" in cfg.telemetry.pii_fields_excluded
    assert "user_id" not in cfg.telemetry.pii_fields_excluded


def test_from_config_with_none_raises():
    with pytest.raises((TypeError, AttributeError)):
        ObservabilityConfig.from_config(None)  # type: ignore
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd observability_layer
uv run pytest tests/test_schema.py -v
```

Expected: `ImportError` — `schema.config` does not exist yet.

- [ ] **Step 3: Create `observability_layer/src/schema/__init__.py`**

```python
"""Schema package for the Observability Layer DPG block."""
```

- [ ] **Step 4: Create `observability_layer/src/schema/config.py`**

```python
"""
observability_layer/src/schema/config.py

ObservabilityConfig — the domain config schema for the Observability Layer.

Domain implementors (e.g. KKB) fill in this schema via YAML. The framework
validates it at startup via Pydantic v2. Invalid config raises at startup,
never at runtime.

Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InstrumentType(str, Enum):
    """OTel metric instrument type."""
    counter = "counter"
    gauge = "gauge"
    histogram = "histogram"


class LifecycleState(BaseModel):
    """One state in the domain outcome lifecycle state machine.

    Args:
        state: State name (e.g. "applied", "placed").
        trigger_tool: Tool name whose execution transitions into this state.
            None for the initial state.
        trigger_condition: Optional condition expression evaluated against
            the tool call result (e.g. "result == 'success'"). None means
            any invocation of trigger_tool transitions to this state.
    """
    state: str
    trigger_tool: Optional[str] = None
    trigger_condition: Optional[str] = None


class MetricDefinition(BaseModel):
    """A domain-defined OTel metric instrument.

    Args:
        name: Metric name (e.g. "placement.applications").
        instrument: OTel instrument type: counter, gauge, or histogram.
        description: Human-readable description.
        unit: Optional UCUM unit string (e.g. "%", "ms").
        attributes: Attribute keys recorded on this metric.
    """
    name: str
    instrument: InstrumentType
    description: str
    unit: str = ""
    attributes: list[str] = Field(default_factory=list)


class OutcomesConfig(BaseModel):
    """Domain-specific outcome lifecycle and metrics configuration."""
    lifecycle: list[LifecycleState] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)


class SLIConfig(BaseModel):
    """Service Level Indicator thresholds for alerting."""
    turn_latency_p99_ms: int = 1200
    trust_block_rate_max: float = 0.05


class AuditConfig(BaseModel):
    """Audit log configuration. Fields listed in pii_fields_excluded are
    never written to the audit log (DPDP Act compliance)."""
    retention_days: int = 90
    pii_fields_excluded: list[str] = Field(
        default_factory=lambda: ["user_message", "user_id"]
    )


class TelemetryConfig(BaseModel):
    """OTel telemetry PII configuration. user_id is allowed in traces
    for dashboarding but excluded from the audit log."""
    pii_fields_excluded: list[str] = Field(
        default_factory=lambda: ["user_message"]
    )


class OtelConfig(BaseModel):
    """OTel exporter and sampling configuration."""
    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = 1.0
    export_interval_ms: int = 5000


class ObservabilityConfig(BaseModel):
    """Full observability configuration.

    Validated at service startup. Any domain implementing this schema
    must provide an ``observability`` section in their domain YAML.

    Args:
        domain: Domain identifier (e.g. "kkb").
        otel: OTel exporter + sampling settings.
        outcomes: Domain outcome lifecycle and metric definitions.
        sli: SLI thresholds.
        audit: Audit log PII exclusions and retention.
        telemetry: Telemetry PII exclusions (less strict than audit).
    """
    domain: str = "unknown"
    otel: OtelConfig = Field(default_factory=OtelConfig)
    outcomes: OutcomesConfig = Field(default_factory=OutcomesConfig)
    sli: SLIConfig = Field(default_factory=SLIConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    @classmethod
    def from_config(cls, config: dict) -> "ObservabilityConfig":
        """Parse the observability section of a merged config dict.

        Args:
            config: Full merged config dict (dpg + domain YAMLs merged).

        Returns:
            Validated ObservabilityConfig instance.

        Raises:
            pydantic.ValidationError: If the observability section is malformed.
        """
        obs = config.get("observability", {})
        return cls.model_validate(obs)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd observability_layer
uv run pytest tests/test_schema.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add observability_layer/src/schema/ observability_layer/tests/test_schema.py
git commit -m "feat(observability): add ObservabilityConfig Pydantic v2 schema"
```

---

## Task 4: Create `ObservabilityLayerBase` ABC

**Files:**
- Create: `observability_layer/src/base.py`

- [ ] **Step 1: Create `observability_layer/src/base.py`**

```python
"""
observability_layer/src/base.py

Abstract base class for the Observability Layer DPG block.

All implementations must honour the contract: never block, never raise.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ObservabilityLayerBase(ABC):
    """Abstract base class for the Observability Layer.

    Implementations must be non-blocking. Any I/O must happen in a
    background thread or queue inside the implementation — never in these calls.
    """

    @abstractmethod
    def emit_turn(self, event: Any) -> None:
        """Process a completed turn event for observability.

        Accepts a TurnEvent dataclass or a plain dict with equivalent fields.
        Must never block or raise.

        Args:
            event: TurnEvent dataclass or dict. None is silently ignored.
        """

    @abstractmethod
    def emit_signal(self, signal_type: str, data: dict) -> None:
        """Process a discrete signal event (e.g. drop_off, mismatch).

        Must never block or raise.

        Args:
            signal_type: Label for the signal. None is silently ignored.
            data: Arbitrary key-value context dict.
        """
```

- [ ] **Step 2: Verify no new test failures in existing suite**

```bash
cd observability_layer
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add observability_layer/src/base.py
git commit -m "feat(observability): add ObservabilityLayerBase ABC"
```

---

## Task 5: Create `OutcomeTracker`

**Files:**
- Create: `observability_layer/src/outcome_tracker.py`
- Create: `observability_layer/tests/test_outcome_tracker.py`

- [ ] **Step 1: Write the failing tests**

Create `observability_layer/tests/test_outcome_tracker.py`:

```python
"""Tests for OutcomeTracker — lifecycle state machine and OTel metric emitter."""
from unittest.mock import MagicMock, patch, call
import pytest

from schema.config import (
    ObservabilityConfig,
    InstrumentType,
    LifecycleState,
    MetricDefinition,
    OutcomesConfig,
)
from outcome_tracker import OutcomeTracker


def _make_config(lifecycle=None, metrics=None):
    """Build a minimal ObservabilityConfig for testing."""
    cfg = ObservabilityConfig()
    if lifecycle:
        cfg.outcomes.lifecycle = lifecycle
    if metrics:
        cfg.outcomes.metrics = metrics
    return cfg


def _make_event(tool_calls=None, intent="market_truth", session_id="s1"):
    return {
        "tool_calls": tool_calls or [],
        "intent": intent,
        "session_id": session_id,
        "trace_id": "abc123",
    }


def test_process_increments_counter_on_matching_tool():
    counter = MagicMock()
    meter = MagicMock()
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()
    meter.create_histogram.return_value = MagicMock()

    config = _make_config(
        lifecycle=[
            LifecycleState(state="applied", trigger_tool="onest_apply", trigger_condition=None),
        ],
        metrics=[
            MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps"),
        ],
    )
    tracker = OutcomeTracker(config, meter)

    event = _make_event(tool_calls=[{"tool_name": "onest_apply", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)

    counter.add.assert_called_once()
    call_kwargs = counter.add.call_args
    assert call_kwargs[0][0] == 1  # increment by 1


def test_process_no_increment_on_non_matching_tool():
    counter = MagicMock()
    meter = MagicMock()
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()

    config = _make_config(
        lifecycle=[
            LifecycleState(state="applied", trigger_tool="onest_apply"),
        ],
        metrics=[
            MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps"),
        ],
    )
    tracker = OutcomeTracker(config, meter)

    event = _make_event(tool_calls=[{"tool_name": "other_tool", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)

    counter.add.assert_not_called()


def test_process_with_none_event_does_not_raise():
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    config = _make_config()
    tracker = OutcomeTracker(config, meter)
    tracker.process(None)  # must not raise


def test_process_with_empty_tool_calls_does_not_raise():
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    config = _make_config()
    tracker = OutcomeTracker(config, meter)
    tracker.process(_make_event(tool_calls=[]))


def test_process_exception_does_not_propagate():
    meter = MagicMock()
    counter = MagicMock()
    counter.add.side_effect = RuntimeError("otel failure")
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()

    config = _make_config(
        lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
        metrics=[MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps")],
    )
    tracker = OutcomeTracker(config, meter)
    event = _make_event(tool_calls=[{"tool_name": "onest_apply", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)  # must not raise despite counter.add raising


def test_no_metrics_config_process_is_noop():
    meter = MagicMock()
    config = _make_config(lifecycle=[], metrics=[])
    tracker = OutcomeTracker(config, meter)
    tracker.process(_make_event(tool_calls=[{"tool_name": "any_tool", "tool_use_id": "t1", "input_params": {}}]))
    meter.create_counter.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd observability_layer
uv run pytest tests/test_outcome_tracker.py -v
```

Expected: `ImportError` — `outcome_tracker` does not exist yet.

- [ ] **Step 3: Create `observability_layer/src/outcome_tracker.py`**

```python
"""
observability_layer/src/outcome_tracker.py

OutcomeTracker — maps TurnEvent tool calls to domain lifecycle state
transitions and increments the corresponding OTel metric instruments.

Runs in the Observability Layer's emit_turn path. Must never raise.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

import logging
from typing import Any

from schema.config import InstrumentType, ObservabilityConfig

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Maps incoming TurnEvent tool calls to OTel metric increments.

    At construction the OTel metric instruments are created from the
    domain config. At runtime, ``process()`` evaluates each tool call
    in the event against the lifecycle trigger rules and increments
    the matching counters.

    Args:
        config: Validated ObservabilityConfig containing lifecycle and metrics.
        meter: OTel Meter instance used to create and update instruments.
    """

    def __init__(self, config: ObservabilityConfig, meter: Any) -> None:
        if config is None:
            raise ValueError("config must not be None")
        if meter is None:
            raise ValueError("meter must not be None")

        self._lifecycle = config.outcomes.lifecycle
        self._counters: dict = {}
        self._gauges: dict = {}
        self._histograms: dict = {}

        for m in config.outcomes.metrics:
            try:
                if m.instrument == InstrumentType.counter:
                    self._counters[m.name] = meter.create_counter(
                        name=m.name,
                        description=m.description,
                        unit=m.unit,
                    )
                elif m.instrument == InstrumentType.gauge:
                    self._gauges[m.name] = meter.create_gauge(
                        name=m.name,
                        description=m.description,
                        unit=m.unit,
                    )
                elif m.instrument == InstrumentType.histogram:
                    self._histograms[m.name] = meter.create_histogram(
                        name=m.name,
                        description=m.description,
                        unit=m.unit,
                    )
            except Exception as e:
                logger.error(
                    "outcome_tracker.instrument_create_error",
                    extra={
                        "operation": "outcome_tracker.init",
                        "status": "failure",
                        "metric_name": m.name,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    def process(self, event: Any) -> None:
        """Evaluate tool calls in a TurnEvent against lifecycle trigger rules.

        Increments OTel counters for matching tool calls. Accepts a TurnEvent
        dataclass or plain dict. Silently ignores None. Never raises.

        Args:
            event: TurnEvent dataclass or dict with tool_calls, intent, session_id.
        """
        if event is None:
            return

        try:
            def _get(key: str, default: Any = None) -> Any:
                if isinstance(event, dict):
                    return event.get(key, default)
                return getattr(event, key, default)

            tool_calls = _get("tool_calls", []) or []
            intent = _get("intent", "")
            session_id = _get("session_id", "")

            for tc in tool_calls:
                tool_name = (
                    tc.get("tool_name", "") if isinstance(tc, dict)
                    else getattr(tc, "tool_name", "")
                )
                self._evaluate_tool_call(tool_name, intent, session_id)

        except Exception as e:
            logger.error(
                "outcome_tracker.process_error",
                extra={
                    "operation": "outcome_tracker.process",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_tool_call(
        self,
        tool_name: str,
        intent: str,
        session_id: str,
    ) -> None:
        """Check tool_name against lifecycle rules and increment matching metrics."""
        for state_def in self._lifecycle:
            if state_def.trigger_tool and state_def.trigger_tool == tool_name:
                attrs = {"intent": intent, "state": state_def.state}
                try:
                    for counter in self._counters.values():
                        counter.add(1, attrs)
                except Exception as e:
                    logger.error(
                        "outcome_tracker.increment_error",
                        extra={
                            "operation": "outcome_tracker._evaluate_tool_call",
                            "status": "failure",
                            "tool_name": tool_name,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                break
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd observability_layer
uv run pytest tests/test_outcome_tracker.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add observability_layer/src/outcome_tracker.py observability_layer/tests/test_outcome_tracker.py
git commit -m "feat(observability): add OutcomeTracker — lifecycle state machine and OTel metric emitter"
```

---

## Task 6: Create `OtelObservabilityLayer` and update `server.py` + `main.py`

**Files:**
- Create: `observability_layer/src/otel_observability_layer.py`
- Modify: `observability_layer/src/server.py`
- Modify: `observability_layer/main.py`
- Create: `observability_layer/tests/test_otel_observability_layer.py`

- [ ] **Step 1: Write the failing tests**

Create `observability_layer/tests/test_otel_observability_layer.py`:

```python
"""Tests for OtelObservabilityLayer."""
from unittest.mock import MagicMock, patch
import pytest


def _make_event(tool_calls=None):
    return {
        "session_id": "s1",
        "turn_id": "t1",
        "response_text": "hello",
        "tool_calls": tool_calls or [],
        "trust_input_result": {"passed": True, "action": "allow", "reason": None},
        "trust_output_result": {"passed": True, "action": "allow", "reason": None},
        "model_used": "claude-haiku",
        "intent": "market_truth",
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 800,
        "timestamp_ms": 1700000000000,
        "trace_id": "abc123",
    }


def test_emit_turn_with_valid_event_does_not_raise():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({"observability": {}})
        layer.emit_turn(_make_event())


def test_emit_turn_none_is_silently_ignored():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_turn(None)  # must not raise


def test_emit_signal_with_valid_type_does_not_raise():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_signal("drop_off", {"stage": "profile_building"})


def test_emit_signal_none_type_is_silently_ignored():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_signal(None, {})  # must not raise


def test_init_with_none_config_raises_value_error():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        with pytest.raises(ValueError, match="config"):
            OtelObservabilityLayer(None)


def test_inherits_from_base():
    from base import ObservabilityLayerBase
    from otel_observability_layer import OtelObservabilityLayer
    assert issubclass(OtelObservabilityLayer, ObservabilityLayerBase)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd observability_layer
uv run pytest tests/test_otel_observability_layer.py -v
```

Expected: `ImportError` — `otel_observability_layer` does not exist yet.

- [ ] **Step 3: Create `observability_layer/src/otel_observability_layer.py`**

```python
"""
observability_layer/src/otel_observability_layer.py

OtelObservabilityLayer — concrete implementation of ObservabilityLayerBase.

Replaces ConsoleLogger. Processes TurnEvents through the OutcomeTracker
(which maps tool calls to OTel metric increments) and emits structured logs.
Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from base import ObservabilityLayerBase
from dpg_telemetry import get_meter, get_tracer
from outcome_tracker import OutcomeTracker
from schema.config import ObservabilityConfig

logger = logging.getLogger(__name__)


class OtelObservabilityLayer(ObservabilityLayerBase):
    """Observability Layer implementation backed by OpenTelemetry.

    Processes incoming TurnEvents via OutcomeTracker to increment
    domain-defined OTel metrics. Emits structured logs for all signals.
    Never blocks or raises.

    Args:
        config: Full merged config dict. Reads observability section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._obs_config = ObservabilityConfig.from_config(config)
        self._tracer = get_tracer(__name__)
        self._meter = get_meter(__name__)
        self._outcome_tracker = OutcomeTracker(self._obs_config, self._meter)

        logger.info(
            "observability_layer.init",
            extra={
                "operation": "otel_observability_layer.init",
                "status": "success",
                "domain": self._obs_config.domain,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — implements ObservabilityLayerBase
    # ------------------------------------------------------------------

    def emit_turn(self, event: Any) -> None:
        """Process a completed turn event.

        Passes the event to OutcomeTracker for metric updates and logs
        structured turn metadata. Never blocks or raises.

        Args:
            event: TurnEvent dataclass or dict. None is silently ignored.
        """
        if event is None:
            return

        start = time.time()
        try:
            self._outcome_tracker.process(event)

            def _get(key: str, default: Any = None) -> Any:
                if isinstance(event, dict):
                    return event.get(key, default)
                return getattr(event, key, default)

            logger.info(
                "observability_layer.turn_event",
                extra={
                    "operation": "otel_observability_layer.emit_turn",
                    "status": "success",
                    "session_id": _get("session_id", ""),
                    "turn_id": _get("turn_id", ""),
                    "model_used": _get("model_used", ""),
                    "input_tokens": _get("input_tokens", 0),
                    "output_tokens": _get("output_tokens", 0),
                    "latency_ms": _get("latency_ms", 0),
                    "emit_latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "observability_layer.emit_turn_error",
                extra={
                    "operation": "otel_observability_layer.emit_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """Process a discrete signal event. Never blocks or raises.

        Args:
            signal_type: Signal label (e.g. "drop_off"). None is silently ignored.
            data: Arbitrary context dict.
        """
        if signal_type is None:
            return

        try:
            logger.info(
                "observability_layer.signal_event",
                extra={
                    "operation": "otel_observability_layer.emit_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "data": data or {},
                },
            )

        except Exception as e:
            logger.error(
                "observability_layer.emit_signal_error",
                extra={
                    "operation": "otel_observability_layer.emit_signal",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
```

- [ ] **Step 4: Update `observability_layer/src/server.py`**

Replace the entire file:

```python
"""
observability_layer/src/server.py

FastAPI server wrapping OtelObservabilityLayer.
Port: 8004

Exposes:
  POST /emit/turn      — process a TurnEvent (outcome tracking + metrics)
  POST /emit/signal    — process a discrete signal event
  GET  /validate-config — validate the loaded domain config (returns domain name)
  GET  /health         — liveness probe

Belongs to the Observability Layer DPG block.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from otel_observability_layer import OtelObservabilityLayer
from schema.config import ObservabilityConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ToolCallSchema(BaseModel):
    tool_name: str
    tool_use_id: str
    input_params: dict[str, Any]


class TrustCheckResultSchema(BaseModel):
    passed: bool
    action: str
    reason: Optional[str] = None


class TurnEventRequest(BaseModel):
    session_id: str
    turn_id: str = ""
    trace_id: str = ""
    response_text: str
    tool_calls: List[ToolCallSchema] = []
    trust_input_result: TrustCheckResultSchema
    trust_output_result: TrustCheckResultSchema
    model_used: str
    intent: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    timestamp_ms: int = 0


class SignalRequest(BaseModel):
    signal_type: str
    data: dict[str, Any] = {}


class StatusResponse(BaseModel):
    status: str


class ConfigValidationResponse(BaseModel):
    status: str
    domain: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(observability: OtelObservabilityLayer, obs_config: ObservabilityConfig) -> FastAPI:
    """Create the FastAPI application wired to OtelObservabilityLayer.

    Args:
        observability: Pre-constructed OtelObservabilityLayer instance.
        obs_config: Validated ObservabilityConfig for this domain.

    Returns:
        Configured FastAPI application.
    """
    if observability is None:
        raise ValueError("observability must not be None")
    if obs_config is None:
        raise ValueError("obs_config must not be None")

    app = FastAPI(
        title="Observability Layer Service",
        description="OpenTelemetry-compliant observability DPG block.",
        version="0.1.0",
    )

    @app.post("/emit/turn")
    def emit_turn(request: TurnEventRequest) -> StatusResponse:
        """Process a turn event — outcome tracking and OTel metrics."""
        start = time.time()
        try:
            event_dict = {
                "session_id": request.session_id,
                "turn_id": request.turn_id,
                "trace_id": request.trace_id,
                "response_text": request.response_text,
                "tool_calls": [
                    {
                        "tool_name": tc.tool_name,
                        "tool_use_id": tc.tool_use_id,
                        "input_params": tc.input_params,
                    }
                    for tc in request.tool_calls
                ],
                "trust_input_result": {
                    "passed": request.trust_input_result.passed,
                    "action": request.trust_input_result.action,
                    "reason": request.trust_input_result.reason,
                },
                "trust_output_result": {
                    "passed": request.trust_output_result.passed,
                    "action": request.trust_output_result.action,
                    "reason": request.trust_output_result.reason,
                },
                "model_used": request.model_used,
                "intent": request.intent,
                "input_tokens": request.input_tokens,
                "output_tokens": request.output_tokens,
                "latency_ms": request.latency_ms,
                "timestamp_ms": request.timestamp_ms,
            }
            observability.emit_turn(event_dict)
            logger.info(
                "observability_server.emit_turn",
                extra={
                    "operation": "server.emit_turn",
                    "status": "success",
                    "session_id": request.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "observability_server.emit_turn_error",
                extra={
                    "operation": "server.emit_turn",
                    "status": "failure",
                    "session_id": request.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        return StatusResponse(status="ok")

    @app.post("/emit/signal")
    def emit_signal(request: SignalRequest) -> StatusResponse:
        """Process a discrete signal event."""
        start = time.time()
        try:
            observability.emit_signal(request.signal_type, request.data)
            logger.info(
                "observability_server.emit_signal",
                extra={
                    "operation": "server.emit_signal",
                    "status": "success",
                    "signal_type": request.signal_type,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "observability_server.emit_signal_error",
                extra={
                    "operation": "server.emit_signal",
                    "status": "failure",
                    "signal_type": request.signal_type,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        return StatusResponse(status="ok")

    @app.get("/validate-config")
    def validate_config() -> ConfigValidationResponse:
        """Return loaded domain config validation status."""
        return ConfigValidationResponse(status="ok", domain=obs_config.domain)

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
```

- [ ] **Step 5: Update `observability_layer/main.py`**

Replace the entire file:

```python
"""
observability_layer/main.py

Entry point for the Observability Layer FastAPI service.

Loads config from config/dpg.yaml merged with the domain YAML,
initialises OTel SDK via dpg_telemetry, constructs OtelObservabilityLayer,
creates the FastAPI app, and starts uvicorn on port 8004.

Run:
    python -m main                   (from observability_layer/ directory)
    uvicorn main:app --reload        (dev hot-reload)

Environment:
    CONFIG_FOLDER — optional path to a folder containing observability_layer.yaml.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

_env_local = Path(__file__).parent.parent / ".env.local"
_env_local_warn = _env_local.exists() and not load_dotenv(_env_local)
load_dotenv()

_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dpg_telemetry import init_otel
from otel_observability_layer import OtelObservabilityLayer
from schema.config import ObservabilityConfig
from server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

if _env_local_warn:
    logger.warning(
        "config.env_local_not_loaded",
        extra={
            "operation": "load_dotenv",
            "status": "skipped",
            "error": f"{_env_local} exists but no variables were loaded.",
        },
    )


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        config_dir = Path(config_folder)
        if not config_dir.is_dir():
            raise ValueError(f"CONFIG_FOLDER='{config_folder}' is not a directory.")
        resolved = config_dir / f"{service}.yaml"
        if not resolved.exists():
            raise FileNotFoundError(f"CONFIG_FOLDER='{config_folder}' set but '{resolved}' missing.")
        return resolved
    return Path("config/domain.yaml")


def _build_app():
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config(str(_domain_config_path("observability_layer")))
    config = _deep_merge(dpg_config, domain_config)

    obs_config = ObservabilityConfig.from_config(config)
    init_otel(service_name="observability_layer", config=config)

    observability = OtelObservabilityLayer(config)
    app = create_app(observability, obs_config)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8004)

    logger.info(
        "observability_layer.startup",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
            "domain": obs_config.domain,
        },
    )
    return app, host, port


app, _host, _port = _build_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host=_host, port=_port, log_level="info")
```

- [ ] **Step 6: Run all observability_layer tests**

```bash
cd observability_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected: all tests pass, ≥70% coverage on `src/`.

- [ ] **Step 7: Commit**

```bash
git add observability_layer/src/ observability_layer/main.py observability_layer/tests/
git commit -m "feat(observability): add OtelObservabilityLayer, update server.py and main.py"
```

---

## Task 7: Add `trace_id` to `TurnEvent` and update the Agent Core HTTP client

**Files:**
- Modify: `agent_core/src/models.py`
- Modify: `agent_core/src/http_clients/learning_layer.py`

- [ ] **Step 1: Write the failing test**

Add to `agent_core/tests/test_models.py` (or create if absent):

```python
def test_turn_event_has_trace_id_field():
    from src.models import TurnEvent, TrustCheckResult
    event = TurnEvent(
        session_id="s1",
        turn_id="t1",
        trace_id="abc123def456",
        response_text="hello",
        tool_calls=[],
        trust_input_result=TrustCheckResult(passed=True, action="allow"),
        trust_output_result=TrustCheckResult(passed=True, action="allow"),
        model_used="claude-haiku",
        intent="market_truth",
        input_tokens=10,
        output_tokens=5,
        latency_ms=800,
        timestamp_ms=1700000000000,
    )
    assert event.trace_id == "abc123def456"


def test_turn_event_trace_id_defaults_to_empty_string():
    from src.models import TurnEvent, TrustCheckResult
    event = TurnEvent(
        session_id="s1",
        turn_id="t1",
        response_text="hello",
        tool_calls=[],
        trust_input_result=TrustCheckResult(passed=True, action="allow"),
        trust_output_result=TrustCheckResult(passed=True, action="allow"),
        model_used="claude-haiku",
        intent="market_truth",
        input_tokens=10,
        output_tokens=5,
        latency_ms=800,
        timestamp_ms=1700000000000,
    )
    assert event.trace_id == ""
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd agent_core
uv run pytest tests/ -k "trace_id" -v
```

Expected: `TypeError` — `TurnEvent` does not accept `trace_id`.

- [ ] **Step 3: Add `trace_id` to `TurnEvent` in `agent_core/src/models.py`**

Find the `TurnEvent` dataclass (lines ~195-216) and add `trace_id` after `turn_id`:

```python
@dataclass
class TurnEvent:
    """
    Audit payload emitted to the Observability Layer after every turn.
    Emitted asynchronously — never in the response path.

    NOTE: user_message is intentionally excluded.
    PII is routed only through the Observability Layer's designated audit log path.
    trace_id is included for correlating outcome metrics with distributed traces.
    """

    session_id: str
    turn_id: str
    trace_id: str                    # W3C trace ID — links metrics to the distributed trace
    response_text: str
    tool_calls: list[ToolCall]
    trust_input_result: TrustCheckResult
    trust_output_result: TrustCheckResult
    model_used: str
    intent: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp_ms: int
    trace_id: str = ""               # default empty for backward compat
```

Wait — dataclass fields with defaults must come after fields without defaults. Place `trace_id: str = ""` after all non-default fields. The corrected dataclass:

```python
@dataclass
class TurnEvent:
    """
    Audit payload emitted to the Observability Layer after every turn.
    Emitted asynchronously — never in the response path.

    NOTE: user_message is intentionally excluded.
    PII is routed only through the Observability Layer's audit log path.
    trace_id links outcome metrics to the distributed trace.
    """

    session_id: str
    turn_id: str
    response_text: str
    tool_calls: list[ToolCall]
    trust_input_result: TrustCheckResult
    trust_output_result: TrustCheckResult
    model_used: str
    intent: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp_ms: int
    trace_id: str = ""               # W3C trace ID; empty if span context unavailable
```

- [ ] **Step 4: Update `_serialise_turn_event` in `agent_core/src/http_clients/learning_layer.py`**

In the `_serialise_turn_event` function (lines ~222-232), add `trace_id` to the returned dict:

```python
def _serialise_turn_event(event: Any) -> dict:
    """Convert a TurnEvent dataclass or dict to a flat JSON-serialisable dict."""
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    return {
        "session_id": _get("session_id", ""),
        "turn_id": _get("turn_id", ""),
        "trace_id": _get("trace_id", ""),
        "response_text": _get("response_text", ""),
        "tool_calls": _serialise_tool_calls(_get("tool_calls", [])),
        "trust_input_result": _serialise_trust_result(_get("trust_input_result")),
        "trust_output_result": _serialise_trust_result(_get("trust_output_result")),
        "model_used": _get("model_used", ""),
        "intent": _get("intent", ""),
        "input_tokens": _get("input_tokens", 0),
        "output_tokens": _get("output_tokens", 0),
        "latency_ms": _get("latency_ms", 0),
        "timestamp_ms": _get("timestamp_ms", 0),
    }
```

- [ ] **Step 5: Run the agent_core tests**

```bash
cd agent_core
uv run pytest tests/ -v
```

Expected: all 177 existing tests pass plus the 2 new trace_id tests.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/models.py agent_core/src/http_clients/learning_layer.py agent_core/tests/
git commit -m "feat(agent-core): add trace_id to TurnEvent, pass trace_id in HTTP serialisation"
```

---

## Task 8: Instrument `agent_core` with OTel spans

**Files:**
- Modify: `agent_core/pyproject.toml`
- Modify: `agent_core/src/orchestrator.py`

- [ ] **Step 1: Add `dpg_telemetry` dependency to `agent_core`**

```bash
cd agent_core
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-httpx \
    opentelemetry-instrumentation-fastapi
```

Verify `agent_core/pyproject.toml` now lists these under `dependencies`.

- [ ] **Step 2: Write a failing test for span emission**

Add to `agent_core/tests/test_orchestrator.py`:

```python
def test_process_turn_emits_orchestrator_span(mock_deps):
    """orchestrator.turn span must be created with session_id and turn_id attributes."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # ... set up mock_deps and call process_turn ...
    # assert any span name == "orchestrator.turn"
    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "orchestrator.turn" in span_names
```

Note: adapt `mock_deps` fixture to your existing test setup. The test just verifies the span name.

- [ ] **Step 3: Add `init_otel` call and `orchestrator.turn` span to `agent_core/src/orchestrator.py`**

At the top of `orchestrator.py`, after existing imports, add:

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentation
```

In `AgentCore.__init__` (or wherever the orchestrator is constructed), add after existing setup:

```python
# Initialise OTel — done once at startup via main.py calling init_otel before constructing AgentCore
# Auto-instrument httpx so all outbound HTTP calls propagate W3C traceparent
HTTPXClientInstrumentation().instrument()
```

In the `process_turn` method, wrap the main body with `orchestrator.turn` span. Find where `turn_id` is assigned (likely early in the method) and add:

```python
_tracer = otel_trace.get_tracer(__name__)

with _tracer.start_as_current_span("orchestrator.turn") as _span:
    _span.set_attribute("session_id", turn_input.session_id)
    _span.set_attribute("turn_id", turn_id)
    _span.set_attribute("user_id", getattr(turn_input, "user_id", "") or "")
    _span.set_attribute("dpg.domain", self._config.get("observability", {}).get("domain", "unknown"))

    # Extract trace_id for TurnEvent
    _ctx = otel_trace.get_current_span().get_span_context()
    _trace_id = format(_ctx.trace_id, "032x") if _ctx and _ctx.is_valid else ""

    # ... rest of existing process_turn body ...
    # When constructing TurnEvent, pass trace_id=_trace_id
```

In the `TurnEvent` construction (look for `TurnEvent(` in orchestrator.py), add `trace_id=_trace_id`:

```python
turn_event = TurnEvent(
    session_id=turn_input.session_id,
    turn_id=turn_id,
    trace_id=_trace_id,          # <-- add this
    response_text=response_text,
    # ... rest of fields unchanged ...
)
```

Also add `init_otel` to `agent_core/main.py`. Find the file and add after config loading:

```python
from dpg_telemetry import init_otel
# After config is loaded and before AgentCore is constructed:
init_otel(service_name="agent_core", config=config)
```

- [ ] **Step 4: Run all agent_core tests**

```bash
cd agent_core
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/pyproject.toml agent_core/src/orchestrator.py
git commit -m "feat(agent-core): instrument orchestrator.turn span with OTel, propagate trace_id to TurnEvent"
```

---

## Task 9: Instrument `trust_layer`

**Files:**
- Modify: `trust_layer/pyproject.toml`
- Modify: `trust_layer/src/server.py`
- Modify: `trust_layer/main.py`

- [ ] **Step 1: Add OTel deps**

```bash
cd trust_layer
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-fastapi
```

- [ ] **Step 2: Write a failing test**

Add to `trust_layer/tests/test_server.py`:

```python
def test_check_input_emits_trust_span(client):
    """POST /check/input must produce a trust.input_check span."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    response = client.post("/check/input", json={"message": "hello", "session_id": "s1"})
    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "trust.input_check" in span_names
```

- [ ] **Step 3: Add `init_otel` + span instrumentation to `trust_layer`**

In `trust_layer/main.py`, add after config loading (follow the same pattern as `observability_layer/main.py`):

```python
from dpg_telemetry import init_otel
# After config is loaded:
init_otel(service_name="trust_layer", config=config)
```

In `trust_layer/src/server.py`, add at the top of `create_app`:

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentation

FastAPIInstrumentation().instrument_app(app)
```

In the `/check/input` endpoint handler, wrap the trust check with a span:

```python
@app.post("/check/input")
def check_input(request: InputCheckRequest) -> TrustCheckResponse:
    _tracer = otel_trace.get_tracer(__name__)
    with _tracer.start_as_current_span("trust.input_check") as span:
        span.set_attribute("session_id", request.session_id)
        start = time.time()
        try:
            result = trust.check_input(request.message, request.session_id)
            span.set_attribute("trust.action", result.action)
            logger.info("trust_server.check_input", extra={
                "operation": "server.check_input",
                "status": "success",
                "session_id": request.session_id,
                "action": result.action,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return TrustCheckResponse(passed=result.passed, action=result.action, reason=result.reason)
        except Exception as e:
            span.set_attribute("trust.action", "block")
            logger.error("trust_server.check_input_error", extra={
                "operation": "server.check_input",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return TrustCheckResponse(passed=False, action="block", reason="internal error")
```

Apply the same pattern (`trust.output_check` span) to the `/check/output` endpoint.

- [ ] **Step 4: Run all trust_layer tests**

```bash
cd trust_layer
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add trust_layer/pyproject.toml trust_layer/src/server.py trust_layer/main.py
git commit -m "feat(trust-layer): add OTel span instrumentation (trust.input_check, trust.output_check)"
```

---

## Task 10: Instrument `knowledge_engine`

**Files:**
- Modify: `knowledge_engine/pyproject.toml`
- Modify: `knowledge_engine/src/server.py` (or equivalent FastAPI entry)
- Modify: `knowledge_engine/main.py`

- [ ] **Step 1: Add OTel deps**

```bash
cd knowledge_engine
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-fastapi
```

- [ ] **Step 2: Add `init_otel` to `knowledge_engine/main.py`**

Following the same pattern as trust_layer, add after config loading:

```python
from dpg_telemetry import init_otel
init_otel(service_name="knowledge_engine", config=config)
```

- [ ] **Step 3: Add `ke.prompt_assemble` span in the `/assemble_prompt` handler**

In the FastAPI server file for knowledge_engine, add:

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentation

# In create_app:
FastAPIInstrumentation().instrument_app(app)

# In /assemble_prompt handler:
@app.post("/assemble_prompt")
def assemble_prompt(request: PromptRequest) -> PromptResponse:
    _tracer = otel_trace.get_tracer(__name__)
    with _tracer.start_as_current_span("ke.prompt_assemble") as span:
        span.set_attribute("session_id", request.session_id)
        span.set_attribute("intent", getattr(request, "intent", ""))
        start = time.time()
        try:
            result = engine.assemble_prompt(request)
            logger.info("ke_server.assemble_prompt", extra={
                "operation": "server.assemble_prompt",
                "status": "success",
                "session_id": request.session_id,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            span.record_exception(e)
            logger.error("ke_server.assemble_prompt_error", extra={
                "operation": "server.assemble_prompt",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            raise
```

- [ ] **Step 4: Run all knowledge_engine tests**

```bash
cd knowledge_engine
uv run pytest tests/ -v
```

Expected: all 87 tests pass.

- [ ] **Step 5: Commit**

```bash
git add knowledge_engine/pyproject.toml knowledge_engine/src/ knowledge_engine/main.py
git commit -m "feat(knowledge-engine): add OTel span instrumentation (ke.prompt_assemble)"
```

---

## Task 11: Instrument `memory_layer`

**Files:**
- Modify: `memory_layer/pyproject.toml`
- Modify: `memory_layer/src/server.py`
- Modify: `memory_layer/main.py`

- [ ] **Step 1: Add OTel deps**

```bash
cd memory_layer
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-fastapi
```

- [ ] **Step 2: Add `init_otel` to `memory_layer/main.py`**

```python
from dpg_telemetry import init_otel
init_otel(service_name="memory_layer", config=config)
```

- [ ] **Step 3: Add `memory.read` and `memory.write` spans in `memory_layer/src/server.py`**

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentation

# In create_app:
FastAPIInstrumentation().instrument_app(app)

# In /session/read handler:
@app.post("/session/read")
def session_read(request: SessionReadRequest) -> SessionReadResponse:
    _tracer = otel_trace.get_tracer(__name__)
    with _tracer.start_as_current_span("memory.read") as span:
        span.set_attribute("session_id", request.session_id)
        span.set_attribute("db.system", "redis")
        start = time.time()
        try:
            result = memory.context_bundle(request.session_id)
            logger.info("memory_server.read", extra={
                "operation": "server.session_read",
                "status": "success",
                "session_id": request.session_id,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            span.record_exception(e)
            logger.error("memory_server.read_error", extra={
                "operation": "server.session_read",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            raise

# In /session/write handler — apply memory.write span with db.system="redis" attribute.
```

- [ ] **Step 4: Run all memory_layer tests**

```bash
cd memory_layer
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add memory_layer/pyproject.toml memory_layer/src/server.py memory_layer/main.py
git commit -m "feat(memory-layer): add OTel span instrumentation (memory.read, memory.write)"
```

---

## Task 12: Instrument `action_gateway`

**Files:**
- Modify: `action_gateway/pyproject.toml`
- Modify: `action_gateway/src/mock_server.py`

- [ ] **Step 1: Add OTel deps**

```bash
cd action_gateway
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-fastapi
```

- [ ] **Step 2: Add `init_otel` to `action_gateway/main.py` (or wherever startup occurs)**

```python
from dpg_telemetry import init_otel
init_otel(service_name="action_gateway", config=config)
```

- [ ] **Step 3: Add `action.execute` span in `action_gateway/src/mock_server.py`**

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentation

# In create_app or equivalent:
FastAPIInstrumentation().instrument_app(app)

# In the tool execution endpoint (POST /execute or equivalent):
@app.post("/execute")
def execute_tool(request: ToolExecuteRequest) -> ToolExecuteResponse:
    _tracer = otel_trace.get_tracer(__name__)
    with _tracer.start_as_current_span("action.execute") as span:
        span.set_attribute("dpg.tool_name", request.tool_name)
        start = time.time()
        try:
            result = gateway.execute(request.tool_name, request.params)
            span.set_attribute("dpg.tool_status", "success")
            logger.info("action_server.execute", extra={
                "operation": "server.execute_tool",
                "status": "success",
                "tool_name": request.tool_name,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            span.set_attribute("dpg.tool_status", "failure")
            span.record_exception(e)
            logger.error("action_server.execute_error", extra={
                "operation": "server.execute_tool",
                "status": "failure",
                "tool_name": request.tool_name,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            raise
```

- [ ] **Step 4: Run all action_gateway tests**

```bash
cd action_gateway
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add action_gateway/pyproject.toml action_gateway/src/
git commit -m "feat(action-gateway): add OTel span instrumentation (action.execute)"
```

---

## Task 13: Instrument `reach_layer`

**Files:**
- Modify: `reach_layer/pyproject.toml`
- Modify: `reach_layer/src/web_reach.py` (web channel) and/or `reach_layer/src/base.py`

- [ ] **Step 1: Add OTel deps**

```bash
cd reach_layer
uv add "observability-layer @ file://../observability_layer" \
    opentelemetry-instrumentation-fastapi \
    opentelemetry-instrumentation-httpx
```

- [ ] **Step 2: Add `init_otel` + httpx auto-instrumentation to reach_layer startup**

In `reach_layer/main.py` (or equivalent entry point), add:

```python
from dpg_telemetry import init_otel
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentation

init_otel(service_name="reach_layer", config=config)
HTTPXClientInstrumentation().instrument()
# ^ This auto-injects W3C traceparent on all httpx requests to Agent Core
```

- [ ] **Step 3: Add `reach.inbound` span in `reach_layer/src/web_reach.py`**

In the inbound request handler (the endpoint that receives user messages and calls Agent Core):

```python
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentation

# In create_app:
FastAPIInstrumentation().instrument_app(app)

# In inbound message handler:
@app.post("/message")
async def handle_message(request: MessageRequest) -> MessageResponse:
    _tracer = otel_trace.get_tracer(__name__)
    with _tracer.start_as_current_span("reach.inbound") as span:
        span.set_attribute("session_id", request.session_id)
        span.set_attribute("dpg.channel", "web")
        start = time.time()
        try:
            # httpx auto-instrumentation injects traceparent header into this call:
            result = await _call_agent_core(request)
            logger.info("reach_server.inbound", extra={
                "operation": "server.handle_message",
                "status": "success",
                "session_id": request.session_id,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            span.record_exception(e)
            logger.error("reach_server.inbound_error", extra={
                "operation": "server.handle_message",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            raise
```

- [ ] **Step 4: Run all reach_layer tests**

```bash
cd reach_layer
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/pyproject.toml reach_layer/src/
git commit -m "feat(reach-layer): add OTel span instrumentation (reach.inbound) + httpx trace propagation"
```

---

## Task 14: Add dev-kit YAML configs

**Files:**
- Create: `dev-kit/dpg/observability_layer.yaml`
- Create: `dev-kit/configs/kkb/observability_layer.yaml`

- [ ] **Step 1: Create `dev-kit/dpg/observability_layer.yaml`**

```yaml
# dev-kit/dpg/observability_layer.yaml
# DPG framework defaults for Observability Layer — same for any domain deployment.

server:
  host: "0.0.0.0"
  port: 8004

observability:
  otel:
    collector_endpoint: "http://otelcol:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
  telemetry:
    pii_fields_excluded:
      - "user_message"
  audit:
    pii_fields_excluded:
      - "user_message"
      - "user_id"
    retention_days: 90
  sli:
    turn_latency_p99_ms: 1200
    trust_block_rate_max: 0.05
```

- [ ] **Step 2: Create `dev-kit/configs/kkb/observability_layer.yaml`**

```yaml
# dev-kit/configs/kkb/observability_layer.yaml
# KKB domain observability config — implements ObservabilityConfig schema.
# Defines the full KKB outcome lifecycle, custom metrics, and SLI thresholds.

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
      - name: "placement.applications"
        instrument: counter
        description: "Total job applications submitted via ONEST"
        attributes:
          - "intent"
          - "state"

      - name: "placement.rate"
        instrument: gauge
        description: "Percentage of sessions reaching placed state, computed by OutcomeTracker as placed_sessions / total_sessions"
        unit: "%"

      - name: "drop_off.by_stage"
        instrument: counter
        description: "Sessions that ended at each lifecycle stage"
        attributes:
          - "stage"
          - "intent"

  sli:
    turn_latency_p99_ms: 1200
    trust_block_rate_max: 0.05

  audit:
    retention_days: 90
```

- [ ] **Step 3: Verify schema validation passes against KKB config**

```bash
cd observability_layer
uv run python -c "
import yaml, sys
sys.path.insert(0, 'src')
from schema.config import ObservabilityConfig

with open('../dev-kit/dpg/observability_layer.yaml') as f:
    dpg = yaml.safe_load(f)
with open('../dev-kit/configs/kkb/observability_layer.yaml') as f:
    kkb = yaml.safe_load(f)

# Deep merge
def merge(b, o):
    r = b.copy()
    for k, v in o.items():
        r[k] = merge(r[k], v) if k in r and isinstance(r[k], dict) and isinstance(v, dict) else v
    return r

config = merge(dpg, kkb)
cfg = ObservabilityConfig.from_config(config)
print(f'domain={cfg.domain}, lifecycle states={len(cfg.outcomes.lifecycle)}, metrics={len(cfg.outcomes.metrics)}')
"
```

Expected output: `domain=kkb, lifecycle states=4, metrics=3`

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dpg/observability_layer.yaml dev-kit/configs/kkb/observability_layer.yaml
git commit -m "feat(dev-kit): add observability_layer YAML configs (dpg defaults + KKB domain)"
```

---

## Task 15: Docker Compose — OTel Collector + backends

**Files:**
- Create: `automation/docker/otelcol/otelcol-config.yaml`
- Modify: `automation/docker/docker-compose.dev.yml`

- [ ] **Step 1: Create `automation/docker/otelcol/otelcol-config.yaml`**

```bash
mkdir -p automation/docker/otelcol
```

```yaml
# automation/docker/otelcol/otelcol-config.yaml
# OpenTelemetry Collector pipeline configuration for DPG dev environment.

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024

exporters:
  jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true

  prometheus:
    endpoint: "0.0.0.0:8889"
    namespace: dpg

  loki:
    endpoint: http://loki:3100/loki/api/v1/push

  logging:
    verbosity: normal

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [jaeger, logging]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus, logging]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [loki, logging]
```

- [ ] **Step 2: Add observability services to `automation/docker/docker-compose.dev.yml`**

Add these services after the existing `learning_layer` service block. Also rename `learning_layer` to `observability_layer` in the file:

Replace the `learning_layer` service block:

```yaml
  # ---------------------------------------------------------------------------
  # Observability Layer — OTel-compliant observability (port 8004)
  # ---------------------------------------------------------------------------
  observability_layer:
    image: sanketikahub/dpg-observability-layer:0.1.0
    container_name: observability_layer
    environment:
      - CONFIG_FOLDER=/app/config
    volumes:
      - ../../dev-kit/dpg/observability_layer.yaml:/app/config/dpg.yaml:ro
      - ../../dev-kit/configs/kkb/observability_layer.yaml:/app/config/observability_layer.yaml:ro
    networks:
      - dpg_net
    depends_on:
      otelcol:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 512M
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8004/health', timeout=3)"]
      interval: 10s
      timeout: 8s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

Add the new services:

```yaml
  # ---------------------------------------------------------------------------
  # OTel Collector — receives OTLP, fans out to Jaeger / Prometheus / Loki
  # ---------------------------------------------------------------------------
  otelcol:
    image: otel/opentelemetry-collector-contrib:0.96.0
    container_name: otelcol
    command: ["--config=/etc/otelcol/otelcol-config.yaml"]
    volumes:
      - ./otelcol/otelcol-config.yaml:/etc/otelcol/otelcol-config.yaml:ro
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
      - "8889:8889"   # Prometheus metrics scrape endpoint
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 256M
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:13133/"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    restart: unless-stopped

  # ---------------------------------------------------------------------------
  # Jaeger — distributed trace UI (port 16686)
  # ---------------------------------------------------------------------------
  jaeger:
    image: jaegertracing/all-in-one:1.55
    container_name: jaeger
    ports:
      - "16686:16686"   # Jaeger UI
      - "14250:14250"   # gRPC from Collector
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 256M
    restart: unless-stopped

  # ---------------------------------------------------------------------------
  # Prometheus — metrics store (port 9090)
  # ---------------------------------------------------------------------------
  prometheus:
    image: prom/prometheus:v2.50.1
    container_name: prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 256M
    restart: unless-stopped

  # ---------------------------------------------------------------------------
  # Loki — log aggregation (port 3100)
  # ---------------------------------------------------------------------------
  loki:
    image: grafana/loki:2.9.4
    container_name: loki
    ports:
      - "3100:3100"
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 256M
    restart: unless-stopped

  # ---------------------------------------------------------------------------
  # Grafana — dashboards UI (port 3000)
  # ---------------------------------------------------------------------------
  grafana:
    image: grafana/grafana:10.3.3
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.1'
          memory: 256M
    restart: unless-stopped
```

Also update the `agent_core` `depends_on` block — replace `learning_layer` with `observability_layer`.

- [ ] **Step 3: Create Prometheus scrape config**

```bash
mkdir -p automation/docker/prometheus
```

Create `automation/docker/prometheus/prometheus.yml`:

```yaml
# automation/docker/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: dpg_metrics
    static_configs:
      - targets: ['otelcol:8889']
```

- [ ] **Step 4: Validate compose file parses**

```bash
cd automation/docker
docker compose -f docker-compose.dev.yml config > /dev/null && echo "compose valid"
```

Expected: `compose valid`

- [ ] **Step 5: Commit**

```bash
git add automation/docker/
git commit -m "feat(docker): add OTel Collector, Jaeger, Prometheus, Loki, Grafana services; rename learning_layer → observability_layer"
```

---

## Task 16: Update `ARCHITECTURE.md`

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: Update all occurrences of "Learning Layer" → "Observability Layer"**

```bash
cd /path/to/repo
# Check all occurrences first:
grep -n "Learning Layer\|learning_layer\|learning layer" ARCHITECTURE.md
```

- [ ] **Step 2: Update the Ports table (Section 1)**

Change:
```markdown
| Learning Layer | 8004 |
```
To:
```markdown
| Observability Layer | 8004 |
```

- [ ] **Step 3: Update the Observability Layer block description (Section 3)**

Replace the existing `### Learning Layer 🟡` section with:

```markdown
### Observability Layer 🟡

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

**Current stub:** `OtelObservabilityLayer` with `OutcomeTracker` — functional OTel instrumentation,
no persistent audit DB yet.

**Planned production additions:** Audit log DB (DPDP Act), persistent outcome store, Grafana dashboards.

**Key files:**
- `observability_layer/src/dpg_telemetry/` — shared bootstrap package
- `observability_layer/src/schema/config.py` — `ObservabilityConfig` schema
- `observability_layer/src/outcome_tracker.py` — lifecycle state machine
- `observability_layer/src/otel_observability_layer.py` — `OtelObservabilityLayer`
- `observability_layer/src/server.py` — FastAPI: `/emit/turn`, `/emit/signal`, `/validate-config`, `/health`

**Tests:** ≥70% coverage.
```

- [ ] **Step 4: Update the Runtime Turn Sequence (Section 4)**

Change the last async line:
```
└─ [async] emit TurnEvent → Learning Layer              [all turns including blocked/escalated]
```
To:
```
└─ [async] emit TurnEvent → Observability Layer         [all turns including blocked/escalated; carries trace_id]
```

- [ ] **Step 5: Update Module Interaction Rules table (Section 5)**

Change:
```markdown
| Agent Core | Learning Layer | Emit turn metadata (async, daemon thread) |
```
To:
```markdown
| Agent Core | Observability Layer | Emit turn metadata (async, daemon thread) |
```

- [ ] **Step 6: Update the YAML section → DPG mapping table (Section 6)**

Change:
```markdown
| `evaluation` + `observability` | Learning Layer |
```
To:
```markdown
| `observability` | Observability Layer |
```

- [ ] **Step 7: Update Implementation Status table (Section 8)**

Change:
```markdown
| Learning Layer | 🟡 | Console logging only. |
```
To:
```markdown
| Observability Layer | 🟡 | OTel instrumentation across all blocks. OutcomeTracker with KKB lifecycle config. No persistent audit DB yet. |
```

Also update the feature table entries for `Audit log / eval pipeline`:
```markdown
| Observability Layer (OTel) | 🟡 | All blocks instrumented. Traces → Jaeger, metrics → Prometheus. Audit DB pending. |
```

- [ ] **Step 8: Update Stub Replacement Guide (Section 9)**

Replace the `### Learning Layer` section with:

```markdown
### Observability Layer

1. Implement persistent audit DB writer in `observability_layer/src/audit_store.py` implementing `AuditStoreBase`.
2. Wire into `OtelObservabilityLayer.emit_turn()` — write PII-excluded fields to audit DB asynchronously.
3. Implement Grafana dashboard provisioning in `automation/docker/grafana/provisioning/`.
4. Implement `OutcomeTracker` placement.rate gauge computation (ratio of placed/total sessions).
```

- [ ] **Step 9: Verify no remaining "Learning Layer" references (except historical context)**

```bash
grep -n "Learning Layer\|learning_layer" ARCHITECTURE.md
```

Expected: zero results (or only in comments about migration history).

- [ ] **Step 10: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: update ARCHITECTURE.md — rename Learning Layer to Observability Layer, document OTel design"
```

---

## Final Verification

- [ ] **Run the full test suite across all touched modules**

```bash
for module in observability_layer agent_core trust_layer knowledge_engine; do
    echo "=== $module ==="
    cd $module
    uv run pytest tests/ -v --tb=short 2>&1 | tail -5
    cd ..
done
```

Expected: all pass.

- [ ] **Verify observability_layer coverage meets threshold**

```bash
cd observability_layer
uv run pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=70
```

Expected: coverage ≥70%, no failures.

- [ ] **Validate Docker Compose**

```bash
cd automation/docker
docker compose -f docker-compose.dev.yml config > /dev/null && echo "compose valid"
```

Expected: `compose valid`
