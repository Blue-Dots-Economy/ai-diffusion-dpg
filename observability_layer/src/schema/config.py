"""
ObservabilityConfig — the domain config schema for the Observability Layer.

Domain implementors (e.g. KKB) fill in this schema via YAML. The framework
validates it at startup via Pydantic v2. Invalid config raises at startup,
never at runtime.

Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class InstrumentType(str, Enum):
    """OTel metric instrument type."""

    counter = "counter"
    gauge = "gauge"
    histogram = "histogram"


class LifecycleState(BaseModel):
    model_config = ConfigDict(frozen=True)

    """One state in the domain outcome lifecycle state machine.

    Args:
        state: State name (e.g. "applied", "placed").
        trigger_tool: Tool name whose execution transitions into this state.
            None for the initial/entry state.
        trigger_condition: Optional condition expression. Reserved for future
            use — currently ignored. Any invocation of ``trigger_tool`` triggers
            the state transition regardless of result.
    """

    state: str
    trigger_tool: Optional[str] = None
    trigger_condition: Optional[str] = None


class MetricDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    """Domain-specific outcome lifecycle and metrics configuration.

    Attributes:
        lifecycle: Ordered list of lifecycle states. Entry state has trigger_tool=None.
        metrics: OTel metric instruments to create and track.
    """

    lifecycle: list[LifecycleState] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)


class SLIConfig(BaseModel):
    """Service Level Indicator thresholds used for alerting and dashboards.

    Attributes:
        turn_latency_p99_ms: P99 turn latency threshold in milliseconds.
            Turns exceeding this value are flagged in the dashboard.
        trust_block_rate_max: Maximum acceptable fraction of turns blocked
            by the Trust Layer (0.0–1.0). Exceeding this triggers an alert.
    """

    model_config = ConfigDict(frozen=True)

    turn_latency_p99_ms: int = Field(default=1200, gt=0)
    trust_block_rate_max: float = Field(default=0.05, ge=0.0, le=1.0)


class AuditConfig(BaseModel):
    """Audit log configuration.

    Fields listed in pii_fields_excluded are never written to the audit log
    (DPDP Act compliance). user_id is excluded from audit but allowed in
    telemetry for dashboarding.
    """

    model_config = ConfigDict(frozen=True)

    retention_days: int = Field(default=90, gt=0)
    pii_fields_excluded: list[str] = Field(
        default_factory=lambda: ["user_message", "user_id"]
    )


class TelemetryConfig(BaseModel):
    """OTel telemetry PII configuration.

    user_id is allowed in traces for dashboarding but excluded from audit log.
    """

    model_config = ConfigDict(frozen=True)

    pii_fields_excluded: list[str] = Field(
        default_factory=lambda: ["user_message"]
    )


class OtelConfig(BaseModel):
    """OTel SDK exporter and sampling configuration.

    Attributes:
        collector_endpoint: gRPC endpoint for the OTel Collector
            (e.g. "http://otelcol:4317").
        sample_rate: Fraction of traces to sample (0.0–1.0). 1.0 means
            all traces are recorded.
        export_interval_ms: Metrics export interval in milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ObservabilityConfig(BaseModel):
    """Full observability configuration validated at service startup.

    Any domain implementing this schema must provide an ``observability``
    section in their domain YAML. Validated by Pydantic v2 at startup —
    invalid config raises, never at request time.

    Args:
        domain: Domain identifier (e.g. "kkb").
        otel: OTel exporter and sampling settings.
        outcomes: Domain outcome lifecycle and metric definitions.
        sli: SLI thresholds.
        audit: Audit log PII exclusions and retention.
        telemetry: Telemetry PII exclusions (less strict than audit).
    """

    model_config = ConfigDict(frozen=True)

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
            config: Full merged config dict (dpg + domain YAMLs deep-merged).

        Returns:
            Validated ObservabilityConfig instance.

        Raises:
            pydantic.ValidationError: If the observability section is malformed.
            TypeError: If config is None.
        """
        if config is None:
            raise TypeError("config must be a dict, got None")
        obs = config.get("observability", {})
        return cls.model_validate(obs)
