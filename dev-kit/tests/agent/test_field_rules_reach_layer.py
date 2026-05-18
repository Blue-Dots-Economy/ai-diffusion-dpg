"""Tests for reach_layer FIELD_RULES content (per catalogue §7.3)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.reach_layer import FIELD_RULES


# Catalogue §7.3: the full set of domain-half paths under reach_layer.
# REACH_LAYER_WEB_MODE is a compose-level env var (not a YAML field) —
# it is NOT included here; see comment in reach_layer.py.
EXPECTED_PATHS = {
    # Derived (common)
    "common.observability.domain",
    # Web UI chat (gated by "web" in selected_channels)
    "channels.web.ui.app_name",
    "channels.web.ui.app_tagline",
    "channels.web.ui.app_icon",
    "channels.web.ui.agent_avatar",
    "channels.web.ui.user_avatar",
    "channels.web.ui.setup_heading",
    "channels.web.ui.setup_subtitle",
    "channels.web.ui.user_id_placeholder",
    "channels.web.ui.user_id_hint",
    "channels.web.ui.start_btn_label",
    "channels.web.ui.new_session_msg",
    "channels.web.ui.returning_user_msg",
    "channels.web.ui.sign_out_confirm",
    "channels.web.ui.switch_user_confirm",
    "channels.web.ui.delete_conversation_confirm",
    # Web derived
    "channels.web.ui.storage_key",
    "channels.web.ui.theme_storage_key",
    # `channels.web.ke_internal_url` was removed from FIELD_RULES — it's a
    # dpg-level infrastructure setting overridden by KE_INTERNAL_URL env
    # var at deploy time, not user-configurable in chat.
    # Web deploy
    "channels.web.auth.enabled",
    # Voice predetermined
    "channels.voice.raya.stt_language",
    "channels.voice.raya.tts_language",
    # Voice chat
    "channels.voice.raya.voice_id",
    "channels.voice.agent_core.fallback_phrase",
    "channels.voice.agent_core.barge_in_acknowledgement",
    "channels.voice.agent_core.timeout_ms",
    "channels.voice.filler_threshold_ms",
    "channels.voice.filler_phrase",
    "channels.voice.terminal_word",
    "channels.voice.recording.consent_purpose",
    # Voice deploy
    "channels.voice.raya.api_key",
    "channels.voice.public_url",
    "channels.voice.vobiz",
    "channels.voice.vad",
    "channels.voice.recording",
}


def test_all_expected_paths_present():
    actual = set(FIELD_RULES.keys())
    missing = EXPECTED_PATHS - actual
    extra = actual - EXPECTED_PATHS
    assert missing == set(), f"missing rules: {sorted(missing)}"
    if extra:
        pytest.fail(f"unexpected rules not in catalogue: {sorted(extra)}")


def test_predetermined_have_rule_expressions():
    for path, rule in FIELD_RULES.items():
        if rule.category == "predetermined":
            assert rule.rule, f"{path}: predetermined rule must define `rule`"


def test_chat_fields_have_phase():
    for path, rule in FIELD_RULES.items():
        if rule.category == "chat":
            assert rule.phase, f"{path}: chat rule must define `phase`"
            assert rule.phase in FIELD_RULES_PHASES_VALID, (
                f"{path}: phase {rule.phase!r} not in FIELD_RULES_PHASES_VALID"
            )
