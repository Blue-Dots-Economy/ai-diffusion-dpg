"""
MergedConfig — strict schema for the Reach Layer merged runtime config.

Merged config = dev-kit/dpg/reach_layer.yaml (framework defaults, infra
                ports / endpoints / adapter keys) deep-merged with a
                domain YAML (e.g. dev-kit/configs/kkb/reach_layer.yaml,
                which carries UI strings, voice language, SSO policy).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Validation is called from ``load_reach_config`` **before** the legacy
top-level aliases (``agent_core_client``, ``ui``, ``server``, ``auth``,
``sessions``) are injected — the aliases are
duplication for in-service code, not a second schema surface.

Belongs to the Reach Layer DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AssemblyMode(str, Enum):
    """How a channel hands inbound segments to Agent Core.

    - ``session``: POST /sessions/{id}/input + SSE events via TurnAssembler
    - ``direct``: POST /process_turn synchronous
    """

    session = "session"
    direct = "direct"


class CookieSameSite(str, Enum):
    """SameSite policy for the session cookie."""

    strict = "strict"
    lax = "lax"
    none = "none"


# ---------------------------------------------------------------------------
# Framework / observability
# ---------------------------------------------------------------------------


class OtelConfig(BaseModel):
    """OTel SDK exporter and sampling configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ObservabilityConfig(BaseModel):
    """Observability settings — OTel plus domain identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = "unknown"
    otel: OtelConfig = Field(default_factory=OtelConfig)


class HttpClientConfig(BaseModel):
    """Generic HTTP client config used by reach layer for inter-service calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    timeout_s: float = Field(default=10.0, gt=0)


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------


class CommonConfig(BaseModel):
    """Settings shared by every channel service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_core_client: HttpClientConfig = Field(
        default_factory=lambda: HttpClientConfig(
            endpoint="http://agent_core:8000/process_turn", timeout_s=30.0
        )
    )
    memory_layer_client: HttpClientConfig = Field(
        default_factory=lambda: HttpClientConfig(
            endpoint="http://memory_layer:8002", timeout_s=10.0
        )
    )
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


# ---------------------------------------------------------------------------
# CLI channel
# ---------------------------------------------------------------------------


class CliChannelConfig(BaseModel):
    """CLI channel — simple terminal REPL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    assembly_mode: AssemblyMode = AssemblyMode.session
    prompt: str = ""
    agent_prefix: str = ""


# ---------------------------------------------------------------------------
# Web channel
# ---------------------------------------------------------------------------


class WebServerConfig(BaseModel):
    """Uvicorn bind for the web channel service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8005, gt=0, lt=65536)


class WebSessionsConfig(BaseModel):
    """Per-user session cap for the web channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=25, gt=0)


class WebAuthConfig(BaseModel):
    """Google SSO + session cookie settings for the web channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    google_client_id: str = ""
    cookie_secure: bool = True
    session_cookie_name: str = "reach_session"
    session_ttl_s: int = Field(default=86400, gt=0)
    cookie_samesite: CookieSameSite = CookieSameSite.lax


class WebUiConfig(BaseModel):
    """Branding and UI copy for the web channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_name: str = ""
    app_tagline: str = ""
    app_icon: str = ""
    agent_avatar: str = ""
    user_avatar: str = ""
    setup_heading: str = ""
    setup_subtitle: str = ""
    user_id_placeholder: str = ""
    user_id_hint: str = ""
    start_btn_label: str = ""
    new_session_msg: str = ""
    returning_user_msg: str = ""
    storage_key: str = ""
    theme_storage_key: str = ""
    sign_out_confirm: str = ""
    switch_user_confirm: str = ""
    delete_conversation_confirm: str = ""


class WebChannelConfig(BaseModel):
    """Web channel service config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    assembly_mode: AssemblyMode = AssemblyMode.session
    server: WebServerConfig = Field(default_factory=WebServerConfig)
    sessions: WebSessionsConfig = Field(default_factory=WebSessionsConfig)
    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: WebUiConfig = Field(default_factory=WebUiConfig)
    # Optional direct-to-KE URL used by the web channel's upload proxy;
    # overrides the default agent_core_client path for large file uploads.
    ke_internal_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Voice channel
# ---------------------------------------------------------------------------


class VobizConfig(BaseModel):
    """Vobiz telephony adapter credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_id: str = ""
    auth_token: str = ""
    api_base: str = "https://api.vobiz.ai/api/v1"
    from_number: str = ""


class VadConfig(BaseModel):
    """Silero VAD tuning for 8 kHz telephony audio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_secs: float = Field(default=0.4, ge=0.0)
    min_volume: float = Field(default=0.7, ge=0.0, le=1.0)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    start_secs: float = Field(default=0.25, ge=0.0)
    smoothing_factor: float = Field(default=0.1, ge=0.0, le=1.0)


class RayaConfig(BaseModel):
    """Raya STT/TTS adapter settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str = ""
    stt_wss_url: str = "https://hub.getraya.app/transcribe"
    tts_base_url: str = "https://hub.getraya.app/v1"
    tts_model: str = "standard"
    tts_speed: float = Field(default=1.0, gt=0)
    stt_ttfs_p99_latency: float = Field(default=1.0, gt=0)
    # Domain-provided language / voice choices:
    stt_language: str = ""
    tts_language: str = ""
    voice_id: str = ""


class VoiceAgentCoreConfig(BaseModel):
    """Voice channel → Agent Core connection and spoken-fallback config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "http://agent_core:8000"
    timeout_ms: int = Field(default=5000, gt=0)
    fallback_phrase: str = ""
    barge_in_acknowledgement: str = ""


class VoiceObservabilityConfig(BaseModel):
    """Voice channel observability knobs (GH-238)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Seconds between `voice.heartbeat` log entries during a call. Set to 0
    # (or negative) to disable.
    heartbeat_interval_s: float = Field(default=10.0)


class VoiceChannelConfig(BaseModel):
    """Voice channel service config (pipecat + Raya + Vobiz)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    assembly_mode: AssemblyMode = AssemblyMode.session
    port: int = Field(default=8006, gt=0, lt=65536)
    public_url: str = ""
    vobiz: VobizConfig = Field(default_factory=VobizConfig)
    vad: VadConfig = Field(default_factory=VadConfig)
    raya: RayaConfig = Field(default_factory=RayaConfig)
    agent_core: VoiceAgentCoreConfig = Field(default_factory=VoiceAgentCoreConfig)
    observability: VoiceObservabilityConfig = Field(
        default_factory=VoiceObservabilityConfig
    )
    # GH-242: filler utterance and end-of-call terminal word are read by the
    # voice processor (reach_layer/voice/.../agent_core_llm.py). They were
    # previously declared only in agent_core.yaml so the voice service never
    # received them at runtime — the filler timer never fired.
    filler_threshold_ms: Optional[int] = Field(default=None, gt=0)
    filler_phrase: Optional[str] = None
    terminal_word: Optional[str] = None
    # GH-203: window after the last TTSSpeakFrame during which a barge-in still
    # counts as "interrupting the bot". Read by the compound barge-in gate.
    barge_in_recency_ms: Optional[int] = Field(default=None, gt=0)
    # GH-202: max seconds to wait for the opening-phrase task to unwind.
    opening_phrase_join_timeout_ms: Optional[int] = Field(default=None, gt=0)


# ---------------------------------------------------------------------------
# Channels container + top-level
# ---------------------------------------------------------------------------


class ChannelsConfig(BaseModel):
    """All channel service configs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cli: CliChannelConfig = Field(default_factory=CliChannelConfig)
    web: WebChannelConfig = Field(default_factory=WebChannelConfig)
    voice: VoiceChannelConfig = Field(default_factory=VoiceChannelConfig)


class ReachLayerConfig(BaseModel):
    """Top-level ``reach_layer`` section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    common: CommonConfig = Field(default_factory=CommonConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged reach_layer config.

    Validated **before** legacy top-level aliases are injected by
    ``load_reach_config``. The alias top-level keys
    (``agent_core_client``, ``ui``, ``server``, ``auth``, ``sessions``,
    ``memory_layer_client``, ``observability``) are not part of this
    schema. The voice ``telephony_adapter`` alias was removed in GH-248.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reach_layer: ReachLayerConfig = Field(default_factory=ReachLayerConfig)

    @classmethod
    def validate_full(cls, config: dict) -> "MergedConfig":
        """Validate the full merged config dict against the strict schema.

        Args:
            config: Merged dict (dpg defaults + domain overrides).

        Returns:
            Validated MergedConfig instance.

        Raises:
            pydantic.ValidationError: If the config contains unknown keys,
                wrong value types, or values outside the allowed ranges at
                any nesting level.
            TypeError: If config is None.
        """
        if config is None:
            raise TypeError("config must be a dict, got None")
        return cls.model_validate(config)
