"""DPG framework defaults schema for the Agent Core block.

Validates the operator-edited ``dev-kit/dpg/agent_core.yaml``. This module also
defines the shared ``ServerConfig`` and ``OtelConfig`` types reused by the other
DPG schemas in this package.
"""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.enums import ProviderField


class ServerConfig(BaseModel):
    """HTTP server bind configuration shared across DPG block schemas."""

    model_config = ConfigDict(extra="forbid")
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)


class ClientConfig(BaseModel):
    """Generic HTTP client config used by Agent Core to call sibling blocks."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(..., pattern=r"^https?://")
    timeout_ms: int = Field(..., gt=0, le=60000)


class CheckOutputBatch(BaseModel):
    """Trust Layer output-check batching defaults for streamed responses."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_sentences: int = Field(default=3, ge=1, le=20)
    max_interval_ms: int = Field(default=500, ge=1, le=5000)


class TrustClientConfig(ClientConfig):
    """Trust Layer client config with extra batching controls for output checks."""

    check_output_batch: CheckOutputBatch = Field(default_factory=CheckOutputBatch)


class TerminationShortCircuit(BaseModel):
    """Controls early-exit for low-confidence NLU classifications."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class RecentToolExchanges(BaseModel):
    """Bounds on the recent tool-call window included in subsequent prompts."""

    model_config = ConfigDict(extra="forbid")
    max_items: int = Field(default=3, ge=0, le=20)
    max_chars: int = Field(default=4000, ge=0, le=50000)


class FeaturesDpg(BaseModel):
    """Per-deployment chat-provider feature toggles (DPG defaults)."""

    model_config = ConfigDict(extra="forbid")
    prompt_cache: Optional[bool] = None
    streaming: Optional[bool] = None
    image_input: Optional[bool] = None


class AgentDpgDefaults(BaseModel):
    """Framework defaults for agent-level behaviour (provider, retries, loop limits)."""

    model_config = ConfigDict(extra="forbid")
    provider: ProviderField = "anthropic"
    features: FeaturesDpg = Field(default_factory=FeaturesDpg)
    ask_for_consent: bool = False
    consent_prompt: str = ""
    timeout_ms: int = Field(default=10000, gt=0, le=60000)
    retry_attempts: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=3, ge=1, le=20)
    termination_short_circuit: TerminationShortCircuit = Field(default_factory=TerminationShortCircuit)
    recent_tool_exchanges: RecentToolExchanges = Field(default_factory=RecentToolExchanges)


class OtelConfig(BaseModel):
    """OpenTelemetry exporter configuration shared by all DPG block schemas."""

    model_config = ConfigDict(extra="forbid")
    collector_endpoint: str = Field(..., pattern=r"^https?://")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0, le=300000)


class ObservabilityDpg(BaseModel):
    """Agent Core observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class TurnAssemblerDpg(BaseModel):
    """Defaults for the streaming TurnAssembler used by session-mode channels."""

    model_config = ConfigDict(extra="forbid")
    semantic_gate: dict = Field(default_factory=lambda: {"enabled": False, "confidence_threshold": 0.75})
    silence_trigger: dict = Field(default_factory=lambda: {"silence_ms": 400})
    max_wait_ceiling: dict = Field(default_factory=lambda: {"max_wait_ms": 8000})


class ReachLayerDefaults(BaseModel):
    """Reach Layer–facing defaults that Agent Core publishes (e.g. TurnAssembler)."""

    model_config = ConfigDict(extra="forbid")
    turn_assembler: TurnAssemblerDpg = Field(default_factory=TurnAssemblerDpg)


class AgentCoreDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/agent_core.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    agent: AgentDpgDefaults
    ke_client: ClientConfig
    memory_client: ClientConfig
    trust_client: TrustClientConfig
    learning_client: ClientConfig
    action_gateway_client: ClientConfig
    reach_layer: ReachLayerDefaults
    observability: ObservabilityDpg
