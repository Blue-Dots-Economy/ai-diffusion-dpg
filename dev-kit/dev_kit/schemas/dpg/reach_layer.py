"""DPG framework defaults schema for the Reach Layer block.

Validates the operator-edited ``dev-kit/dpg/reach_layer.yaml``. The YAML wraps
all keys under a top-level ``reach_layer`` section, so the root config matches
that shape via :class:`ReachLayerDpgConfig`.
"""
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field

AssemblyMode = Literal["session", "direct"]
CookieSameSite = Literal["strict", "lax", "none"]


class HttpClientConfig(BaseModel):
    """Generic HTTP client config used by Reach Layer to call sibling blocks."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(..., pattern=r"^https?://")
    timeout_s: float = Field(..., gt=0, le=120)


class LearningClientDpg(BaseModel):
    """Observability Layer learning-signal client — uses ms timeouts."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(..., pattern=r"^https?://")
    timeout_ms: int = Field(..., gt=0, le=60000)


class OtelConfig(BaseModel):
    """OpenTelemetry exporter configuration for Reach Layer.

    Reach Layer keeps a permissive ``collector_endpoint`` field (no URL pattern)
    because some channel adapters point to in-cluster collectors via short names.
    """

    model_config = ConfigDict(extra="forbid")
    collector_endpoint: str
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ReachObservability(BaseModel):
    """Reach Layer observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class CommonDpg(BaseModel):
    """Defaults shared by every Reach Layer channel adapter."""

    model_config = ConfigDict(extra="forbid")
    agent_core_client: HttpClientConfig
    memory_layer_client: HttpClientConfig
    learning_client: LearningClientDpg
    observability: ReachObservability


class CliDpg(BaseModel):
    """Defaults for the CLI channel adapter."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    assembly_mode: AssemblyMode = "session"


class WebServerDpg(BaseModel):
    """HTTP server bind configuration for the Web channel adapter."""

    model_config = ConfigDict(extra="forbid")
    host: str = "0.0.0.0"
    port: int = Field(default=8005, gt=0, lt=65536)


class WebSessionsDpg(BaseModel):
    """Web channel session-store limits."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=25, gt=0, le=10000)


class WebAuthDpg(BaseModel):
    """Web channel authentication and cookie defaults."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    google_client_id: str = ""
    cookie_secure: bool = False
    session_cookie_name: str = "reach_session"
    session_ttl_s: int = Field(default=86400, gt=0, le=604800)
    cookie_samesite: CookieSameSite = "lax"


class WebDpg(BaseModel):
    """Defaults for the Web channel adapter."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    assembly_mode: AssemblyMode = "direct"
    server: WebServerDpg = Field(default_factory=WebServerDpg)
    sessions: WebSessionsDpg = Field(default_factory=WebSessionsDpg)
    auth: WebAuthDpg = Field(default_factory=WebAuthDpg)


class VobizDpg(BaseModel):
    """Vobiz telephony provider connection defaults."""

    model_config = ConfigDict(extra="forbid")
    auth_id: str
    auth_token: str
    api_base: str = "https://api.vobiz.ai/api/v1"
    from_number: str = ""


class VadDpg(BaseModel):
    """Voice-activity-detection tuning defaults."""

    model_config = ConfigDict(extra="forbid")
    stop_secs: float = Field(default=0.4, ge=0.0, le=5.0)
    min_volume: float = Field(default=0.4, ge=0.0, le=1.0)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    start_secs: float = Field(default=0.2, ge=0.0, le=5.0)
    smoothing_factor: float = Field(default=0.1, ge=0.0, le=1.0)


class RayaDpg(BaseModel):
    """Raya STT/TTS provider connection defaults."""

    model_config = ConfigDict(extra="forbid")
    api_key: str
    stt_wss_url: str
    tts_base_url: str
    tts_model: str = "standard"
    tts_speed: float = Field(default=1.0, gt=0, le=3.0)
    stt_ttfs_p99_latency: float = Field(default=1.0, gt=0)


class VoiceAgentCoreDpg(BaseModel):
    """Agent Core client config used by the Voice channel adapter."""

    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(..., pattern=r"^https?://")
    timeout_ms: int = Field(default=5000, gt=0, le=60000)
    barge_in_acknowledgement: str = ""


class VoiceObservabilityDpg(BaseModel):
    """Voice-channel-specific observability tuning (heartbeat cadence, etc.)."""

    model_config = ConfigDict(extra="forbid")
    heartbeat_interval_s: float = Field(default=10.0, ge=0.0)


class RecordingLocalDpg(BaseModel):
    """Local-disk storage settings for voice recording artifacts."""

    model_config = ConfigDict(extra="forbid")
    base_path: str = "/var/recordings"


class RecordingS3Dpg(BaseModel):
    """S3-compatible storage settings for voice recording artifacts."""

    model_config = ConfigDict(extra="forbid")
    bucket: str = ""
    prefix: str = "recordings/"
    region: str = "ap-south-1"
    kms_key_id: str = ""


class RecordingStoreDpg(BaseModel):
    """Pluggable storage backend selection for voice recording artifacts."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["local", "s3"] = "local"
    local: RecordingLocalDpg = Field(default_factory=RecordingLocalDpg)
    s3: RecordingS3Dpg = Field(default_factory=RecordingS3Dpg)


class RecordingDpg(BaseModel):
    """Voice channel recording defaults. ``source=disabled`` is a no-op."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["disabled", "vobiz", "pipeline"] = "disabled"
    consent_purpose: str = "recording"
    # See reach_layer_base.schema.config.RecordingConfig — Vobiz's recording
    # callback can take a few minutes after stop. Default raised to 5 min.
    webhook_timeout_s: float = 300.0
    fetch_timeout_s: float = 60.0
    min_duration_ms: int = 500
    caller_id_hash_salt: str = ""
    # Testing/disclosure escape hatch (#332): start recording on websocket
    # connect, bypassing the Trust Layer consent gate.
    start_on_connect: bool = False
    store: RecordingStoreDpg = Field(default_factory=RecordingStoreDpg)


class VoiceDpg(BaseModel):
    """Defaults for the Voice channel adapter (telephony + STT/TTS)."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    assembly_mode: AssemblyMode = "session"
    port: int = Field(default=8006, gt=0, lt=65536)
    public_url: str = ""
    vobiz: VobizDpg
    vad: VadDpg = Field(default_factory=VadDpg)
    raya: RayaDpg
    agent_core: VoiceAgentCoreDpg
    observability: VoiceObservabilityDpg = Field(default_factory=VoiceObservabilityDpg)
    filler_threshold_ms: Optional[int] = None
    filler_phrase: str = ""
    terminal_word: Optional[str] = None
    recording: RecordingDpg = Field(default_factory=RecordingDpg)


class ChannelsDpg(BaseModel):
    """Container for all Reach Layer channel adapter defaults."""

    model_config = ConfigDict(extra="forbid")
    cli: CliDpg
    web: WebDpg
    voice: VoiceDpg


class ReachLayerInner(BaseModel):
    """Inner content of the wrapped ``reach_layer`` section."""

    model_config = ConfigDict(extra="forbid")
    common: CommonDpg
    channels: ChannelsDpg


class ReachLayerDpgConfig(BaseModel):
    """The dpg/reach_layer.yaml has a top-level reach_layer key wrapping everything."""

    model_config = ConfigDict(extra="forbid")
    reach_layer: ReachLayerInner
