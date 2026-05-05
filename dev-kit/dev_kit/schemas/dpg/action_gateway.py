"""DPG framework defaults schema for the Action Gateway block.

Validates the operator-edited ``dev-kit/dpg/action_gateway.yaml``. Tool catalogs
live in domain configuration; this DPG layer keeps only the shape/observability.
"""
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.dpg.agent_core import OtelConfig


class ServerConfig(BaseModel):
    """HTTP server bind configuration. Action Gateway defaults to port 9999."""

    model_config = ConfigDict(extra="forbid")
    host: str = "0.0.0.0"
    port: int = Field(default=9999, gt=0, lt=65536)


class ObservabilityDpg(BaseModel):
    """Action Gateway observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class ActionGatewayDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/action_gateway.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    tools: list = Field(default_factory=list)   # tools live in domain config; DPG keeps shape
    observability: ObservabilityDpg
