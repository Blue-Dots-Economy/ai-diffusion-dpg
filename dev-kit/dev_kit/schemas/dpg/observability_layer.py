"""DPG framework defaults schema for the Observability Layer block.

Validates the operator-edited ``dev-kit/dpg/observability_layer.yaml``.
"""
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.dpg.agent_core import ServerConfig, OtelConfig


class TelemetryDpg(BaseModel):
    """Defaults for the live telemetry export path (PII exclusion)."""

    model_config = ConfigDict(extra="forbid")
    pii_fields_excluded: list[str] = Field(default_factory=lambda: ["user_message"])


class AuditDpg(BaseModel):
    """Defaults for the audit log path (PII exclusion + retention policy)."""

    model_config = ConfigDict(extra="forbid")
    pii_fields_excluded: list[str] = Field(
        default_factory=lambda: ["user_message", "user_id"]
    )
    retention_days: int = Field(default=90, gt=0, le=3650)


class SliDpg(BaseModel):
    """Service-level-indicator targets enforced by the Observability Layer."""

    model_config = ConfigDict(extra="forbid")
    turn_latency_p99_ms: int = Field(default=1200, gt=0, le=10000)
    trust_block_rate_max: float = Field(default=0.05, ge=0.0, le=1.0)


class ObservabilityDpg(BaseModel):
    """Top-level ``observability`` section in the framework defaults YAML."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig
    telemetry: TelemetryDpg = Field(default_factory=TelemetryDpg)
    audit: AuditDpg = Field(default_factory=AuditDpg)
    sli: SliDpg = Field(default_factory=SliDpg)


class ObservabilityLayerDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/observability_layer.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    observability: ObservabilityDpg
