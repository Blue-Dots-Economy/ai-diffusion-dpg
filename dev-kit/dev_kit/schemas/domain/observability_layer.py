"""Domain schemas for observability_layer block.

Sections written by the LLM during the observability phase. The SLI / audit
/ telemetry overrides allow domains to tighten thresholds beyond DPG defaults
(e.g., employ-voice-bot bumps turn_latency_p99_ms from 1200 to 1500).
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.enums import InstrumentType


class LifecycleState(BaseModel):
    """One outcome lifecycle state. trigger_tool=None means entry/initial state."""
    model_config = ConfigDict(extra="forbid")
    state: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    trigger_tool: Optional[str] = Field(default=None, min_length=1)
    trigger_condition: Optional[str] = Field(default=None, min_length=1)


class MetricDefinition(BaseModel):
    """One OTel metric instrument definition."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_.]*$")
    instrument: InstrumentType
    description: str = Field(..., min_length=1)
    unit: str = ""
    attributes: list[str] = Field(default_factory=list)


class OutcomesConfig(BaseModel):
    """Domain outcomes — the journey states + metrics worth tracking."""
    model_config = ConfigDict(extra="forbid")
    lifecycle: list[LifecycleState] = Field(..., min_length=1)
    metrics: list[MetricDefinition] = Field(default_factory=list)


# Domain-overridable SLI / audit / telemetry sections. The DPG schema (7.7)
# defines framework defaults for these; domains can override individual fields
# (e.g., employ-voice-bot bumps turn_latency_p99_ms to 1500). All optional —
# if absent, DPG defaults stand.

class SliOverride(BaseModel):
    """Service-level indicator threshold overrides.

    Domains may override DPG defaults to reflect realistic per-domain SLOs.
    For example, employ-voice-bot bumps `turn_latency_p99_ms` from the DPG
    default (1200) to 1500 because voice deployments tolerate higher latency.
    All fields are Optional — only set the ones a domain wants to override.

    Caps:
      - turn_latency_p99_ms: 1–10000 ms (10s ceiling — anything higher is
        symptomatic, not a healthy threshold).
      - trust_block_rate_max: 0.0–1.0 (fraction of turns blocked).
    """
    model_config = ConfigDict(extra="forbid")
    turn_latency_p99_ms: Optional[int] = Field(default=None, gt=0, le=10000)
    trust_block_rate_max: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AuditOverride(BaseModel):
    """Audit log overrides — retention period and PII exclusion list.

    The 3650-day (10-year) cap on retention_days reflects practical legal
    retention horizons (e.g., DPDP Act). Increase pii_fields_excluded for
    domains that emit additional sensitive fields beyond the framework defaults.
    All fields Optional — DPG defaults stand if absent.
    """
    model_config = ConfigDict(extra="forbid")
    pii_fields_excluded: Optional[list[str]] = None
    retention_days: Optional[int] = Field(default=None, gt=0, le=3650)


class TelemetryOverride(BaseModel):
    """OTel telemetry overrides — PII exclusion list.

    Domains may add fields to the framework default exclusion set so the
    OTel exporter never emits them. All fields Optional — DPG defaults stand
    if absent.
    """
    model_config = ConfigDict(extra="forbid")
    pii_fields_excluded: Optional[list[str]] = None


class ObservabilitySection(BaseModel):
    """Top-level observability_layer.observability section."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
    outcomes: Optional[OutcomesConfig] = None
    sli: Optional[SliOverride] = None
    audit: Optional[AuditOverride] = None
    telemetry: Optional[TelemetryOverride] = None
