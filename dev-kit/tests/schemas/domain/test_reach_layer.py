"""Tests for reach_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.reach_layer import (
    ChannelsSection,
    CommonObservabilityConfig,
    CommonSection,
    RayaVoiceConfig,
    ReachLayerSection,
    VoiceAgentCoreClient,
    VoiceChannelSection,
    WebChannelSection,
    WebUiConfig,
)
from dev_kit.schemas.enums import RAYA_VOICE_LANGUAGE


# Helpers — pick a known voice/language pair from enums config.
_FIRST_VOICE_ID = next(iter(RAYA_VOICE_LANGUAGE.keys()))
_FIRST_LANGUAGE = RAYA_VOICE_LANGUAGE[_FIRST_VOICE_ID]


def _voice_pair():
    """Pick the first registered voice + its language."""
    return _FIRST_VOICE_ID, _FIRST_LANGUAGE


# -- WebUiConfig -------------------------------------------------------------

def test_web_ui_app_name_required():
    with pytest.raises(ValidationError):
        WebUiConfig(app_name="")


def test_web_ui_minimal():
    ui = WebUiConfig(app_name="KKB")
    assert ui.app_tagline == ""
    assert ui.start_btn_label == ""


def test_web_ui_full():
    ui = WebUiConfig(
        app_name="KKB",
        app_tagline="Tagline",
        app_icon="🌾",
        new_session_msg="Welcome back",
    )
    assert ui.app_icon == "🌾"


def test_web_ui_extra_forbidden():
    with pytest.raises(ValidationError):
        WebUiConfig(app_name="x", unknown="y")


# -- WebChannelSection -------------------------------------------------------

def test_web_channel_requires_ui():
    with pytest.raises(ValidationError):
        WebChannelSection()


def test_web_channel_full():
    s = WebChannelSection(ui=WebUiConfig(app_name="KKB"))
    assert s.ui.app_name == "KKB"


# -- RayaVoiceConfig (with cross-field validator) ----------------------------

def test_raya_voice_minimal_valid():
    voice_id, lang = _voice_pair()
    r = RayaVoiceConfig(stt_language=lang, tts_language=lang, voice_id=voice_id)
    assert r.voice_id == voice_id


def test_raya_voice_id_must_match_stt_language():
    voice_id, voice_lang = _voice_pair()
    other_lang = next(l for l in RAYA_VOICE_LANGUAGE.values() if l != voice_lang)
    with pytest.raises(ValidationError, match="stt_language"):
        RayaVoiceConfig(stt_language=other_lang, tts_language=voice_lang, voice_id=voice_id)


def test_raya_voice_id_must_match_tts_language():
    voice_id, voice_lang = _voice_pair()
    other_lang = next(l for l in RAYA_VOICE_LANGUAGE.values() if l != voice_lang)
    with pytest.raises(ValidationError, match="tts_language"):
        RayaVoiceConfig(stt_language=voice_lang, tts_language=other_lang, voice_id=voice_id)


def test_raya_voice_id_unknown_rejected():
    """RayaVoiceIdField rejects unknown UUIDs."""
    with pytest.raises(ValidationError):
        RayaVoiceConfig(
            stt_language="en-in", tts_language="en-in",
            voice_id="00000000-not-a-real-voice-id",
        )


def test_raya_language_unknown_rejected():
    """RayaLanguageField rejects languages not in the voice table."""
    voice_id, _ = _voice_pair()
    with pytest.raises(ValidationError):
        RayaVoiceConfig(
            stt_language="klingon", tts_language="klingon", voice_id=voice_id,
        )


# -- VoiceAgentCoreClient ----------------------------------------------------

def test_voice_agent_core_client_minimal():
    c = VoiceAgentCoreClient(fallback_phrase="Sorry, please retry.")
    assert c.timeout_ms == 15000
    assert c.barge_in_acknowledgement == ""


def test_voice_agent_core_client_fallback_phrase_required():
    with pytest.raises(ValidationError):
        VoiceAgentCoreClient(fallback_phrase="")


def test_voice_agent_core_client_timeout_bounds():
    VoiceAgentCoreClient(fallback_phrase="x", timeout_ms=1)
    VoiceAgentCoreClient(fallback_phrase="x", timeout_ms=60000)
    with pytest.raises(ValidationError):
        VoiceAgentCoreClient(fallback_phrase="x", timeout_ms=0)
    with pytest.raises(ValidationError):
        VoiceAgentCoreClient(fallback_phrase="x", timeout_ms=60001)


# -- VoiceChannelSection -----------------------------------------------------

def _voice_channel(**overrides):
    voice_id, lang = _voice_pair()
    base = dict(
        raya=RayaVoiceConfig(stt_language=lang, tts_language=lang, voice_id=voice_id),
        agent_core=VoiceAgentCoreClient(fallback_phrase="Sorry."),
    )
    base.update(overrides)
    return VoiceChannelSection(**base)


def test_voice_channel_minimal():
    s = _voice_channel()
    assert s.terminal_word is None
    assert s.filler_phrase is None
    assert s.filler_threshold_ms is None
    assert s.barge_in_recency_ms is None


def test_voice_channel_filler_threshold_bounds():
    _voice_channel(filler_threshold_ms=1)
    _voice_channel(filler_threshold_ms=10000)
    with pytest.raises(ValidationError):
        _voice_channel(filler_threshold_ms=0)
    with pytest.raises(ValidationError):
        _voice_channel(filler_threshold_ms=10001)


def test_voice_channel_barge_in_recency_bounds():
    _voice_channel(barge_in_recency_ms=1500)
    _voice_channel(barge_in_recency_ms=1)
    _voice_channel(barge_in_recency_ms=10000)
    with pytest.raises(ValidationError):
        _voice_channel(barge_in_recency_ms=0)
    with pytest.raises(ValidationError):
        _voice_channel(barge_in_recency_ms=10001)


def test_voice_channel_terminal_word_rejects_empty():
    """Optional[str] empty rejection pattern."""
    with pytest.raises(ValidationError):
        _voice_channel(terminal_word="")


def test_voice_channel_filler_phrase_rejects_empty():
    with pytest.raises(ValidationError):
        _voice_channel(filler_phrase="")


# -- ChannelsSection ---------------------------------------------------------

def test_channels_section_all_optional():
    c = ChannelsSection()
    assert c.web is None
    assert c.voice is None


def test_channels_section_web_only():
    c = ChannelsSection(web=WebChannelSection(ui=WebUiConfig(app_name="KKB")))
    assert c.web is not None and c.voice is None


# -- CommonSection -----------------------------------------------------------

def test_common_observability_domain_required():
    with pytest.raises(ValidationError):
        CommonObservabilityConfig()
    with pytest.raises(ValidationError):
        CommonObservabilityConfig(domain="")


def test_common_section_full():
    c = CommonSection(observability=CommonObservabilityConfig(domain="kkb"))
    assert c.observability.domain == "kkb"


# -- ReachLayerSection -------------------------------------------------------

def test_reach_layer_section_all_optional():
    r = ReachLayerSection()
    assert r.channels is None
    assert r.common is None


def test_reach_layer_section_full():
    voice_id, lang = _voice_pair()
    r = ReachLayerSection(
        channels=ChannelsSection(
            web=WebChannelSection(ui=WebUiConfig(app_name="KKB")),
            voice=_voice_channel(),
        ),
        common=CommonSection(observability=CommonObservabilityConfig(domain="kkb")),
    )
    assert r.channels.web is not None
    assert r.channels.voice is not None


def test_reach_layer_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ReachLayerSection(unknown="x")
