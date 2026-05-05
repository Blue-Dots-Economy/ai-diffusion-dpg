"""Domain schemas for reach_layer block.

Sections written by the LLM during the reach phase. The Raya voice config
includes a cross-field validator: the chosen voice_id's language must match
both stt_language and tts_language (otherwise STT/TTS hits the wrong language).
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import (
    RayaVoiceIdField, RayaLanguageField, RAYA_VOICE_LANGUAGE,
)


class WebUiConfig(BaseModel):
    """Web channel UI strings — branding + chat-screen copy."""
    model_config = ConfigDict(extra="forbid")
    app_name: str = Field(..., min_length=1)
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


class WebAuthConfig(BaseModel):
    """Web channel auth toggle (Google SSO etc.). Mirrors runtime WebAuthConfig.

    Domains may override individual fields (kkb sets cookie_secure=False for
    local dev); session cookie name + TTL stay framework defaults.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    google_client_id: str = ""
    cookie_secure: bool = True
    session_cookie_name: str = "reach_session"
    session_ttl_s: int = Field(default=86400, gt=0)
    cookie_samesite: str = "lax"


class WebChannelSection(BaseModel):
    """reach_layer.channels.web — web-channel domain config."""
    model_config = ConfigDict(extra="forbid")
    ui: WebUiConfig
    auth: Optional[WebAuthConfig] = None


class RayaVoiceConfig(BaseModel):
    """Raya STT/TTS settings — language + voice_id (must be language-consistent)."""
    model_config = ConfigDict(extra="forbid")
    stt_language: RayaLanguageField
    tts_language: RayaLanguageField
    voice_id: RayaVoiceIdField

    @model_validator(mode="after")
    def voice_id_matches_language(self) -> "RayaVoiceConfig":
        """The chosen voice_id must be for a language that matches stt/tts_language.

        If stt_language=en-in but voice_id is for hi (Hindi), the TTS speaks Hindi
        while the STT expects English — guaranteed dialogue failure.
        """
        voice_lang = RAYA_VOICE_LANGUAGE[self.voice_id]
        if voice_lang != self.stt_language:
            raise ValueError(
                f"voice_id is for language {voice_lang!r}, but stt_language is {self.stt_language!r}"
            )
        if voice_lang != self.tts_language:
            raise ValueError(
                f"voice_id is for language {voice_lang!r}, but tts_language is {self.tts_language!r}"
            )
        return self


class VoiceAgentCoreClient(BaseModel):
    """Voice channel's HTTP client to Agent Core — timeout + fallback phrase."""
    model_config = ConfigDict(extra="forbid")
    timeout_ms: int = Field(default=15000, gt=0, le=60000)
    fallback_phrase: str = Field(..., min_length=1)
    barge_in_acknowledgement: str = ""


class VadConfig(BaseModel):
    """Silero VAD tuning for telephony audio (8 kHz). Mirrors runtime VadConfig.

    KKB tightens stop_secs to 1.0 for Hindi cadence with rural callers.
    """
    model_config = ConfigDict(extra="forbid")
    stop_secs: float = Field(default=0.4, ge=0.0, le=10.0)


class VoiceChannelSection(BaseModel):
    """reach_layer.channels.voice — voice-channel domain config.

    barge_in_recency_ms is domain-tunable for languages with slower TTS / longer
    drain time. None → use runtime default (1500ms).
    """
    model_config = ConfigDict(extra="forbid")
    raya: RayaVoiceConfig
    agent_core: VoiceAgentCoreClient
    terminal_word: Optional[str] = Field(default=None, min_length=1)
    filler_phrase: Optional[str] = Field(default=None, min_length=1)
    filler_threshold_ms: Optional[int] = Field(default=None, gt=0, le=10000)
    barge_in_recency_ms: Optional[int] = Field(default=None, gt=0, le=10000)
    vad: Optional[VadConfig] = None


class CliChannelSection(BaseModel):
    """reach_layer.channels.cli — terminal REPL prompt + agent label."""
    model_config = ConfigDict(extra="forbid")
    prompt: str = ""
    agent_prefix: str = ""


class ChannelsSection(BaseModel):
    """reach_layer.channels — at most one entry per channel type."""
    model_config = ConfigDict(extra="forbid")
    web: Optional[WebChannelSection] = None
    voice: Optional[VoiceChannelSection] = None
    cli: Optional[CliChannelSection] = None


class CommonObservabilityConfig(BaseModel):
    """reach_layer.common.observability — domain identifier."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)


class CommonSection(BaseModel):
    """reach_layer.common — shared config across channels."""
    model_config = ConfigDict(extra="forbid")
    observability: CommonObservabilityConfig


class ReachLayerSection(BaseModel):
    """Top-level reach_layer wrapper — matches the YAML's reach_layer: {} root key.

    Both children optional so the section can be added incrementally during the
    reach phase (web first, then voice).
    """
    model_config = ConfigDict(extra="forbid")
    channels: Optional[ChannelsSection] = None
    common: Optional[CommonSection] = None
