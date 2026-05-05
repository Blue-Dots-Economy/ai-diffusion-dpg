"""DPG framework defaults schema for the Memory Layer block.

Validates the operator-edited ``dev-kit/dpg/memory_layer.yaml``.
"""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.dpg.agent_core import ServerConfig, OtelConfig


class RedisDpg(BaseModel):
    """Connection defaults for the Redis (turn/session) backend."""

    model_config = ConfigDict(extra="forbid")
    host: str = Field(..., min_length=1)
    port: int = Field(default=6379, gt=0, lt=65536)
    db: int = Field(default=0, ge=0, le=15)
    password: Optional[str] = None
    socket_timeout_ms: int = Field(default=2000, gt=0, le=30000)
    socket_connect_timeout_ms: int = Field(default=2000, gt=0, le=30000)


class MemgraphDpg(BaseModel):
    """Connection defaults for the Memgraph (context-graph) backend."""

    model_config = ConfigDict(extra="forbid")
    uri: str = Field(..., pattern=r"^bolt://")
    user: str = Field(..., min_length=1)
    password: Optional[str] = None
    connection_timeout_s: int = Field(default=5, gt=0, le=60)


class ObservabilityDpg(BaseModel):
    """Memory Layer observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class MemoryLayerDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/memory_layer.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    redis: RedisDpg
    memgraph: MemgraphDpg
    observability: ObservabilityDpg
