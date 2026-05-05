"""DPG framework defaults schema for the Trust Layer block.

Validates the operator-edited ``dev-kit/dpg/trust_layer.yaml``.
"""
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.enums import DignityFailAction
from dev_kit.schemas.dpg.agent_core import ServerConfig, OtelConfig


class DignityCheckDpg(BaseModel):
    """Framework defaults for the optional dignity-check pass."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    questions: list[str] = Field(default_factory=list)
    fail_action: DignityFailAction = DignityFailAction.rewrite


class ObservabilityDpg(BaseModel):
    """Trust Layer observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class TrustLayerDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/trust_layer.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    observability: ObservabilityDpg
    dignity_check: DignityCheckDpg = Field(default_factory=DignityCheckDpg)
