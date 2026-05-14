"""Tests for reach_layer FIELD_RULES content (per catalogue §7.3)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.reach_layer import FIELD_RULES


# Catalogue §7.3: the full set of domain-half paths under reach_layer.
# REACH_LAYER_WEB_MODE is a compose-level env var (not a YAML field) —
# it is NOT included here; see comment in reach_layer.py.
EXPECTED_PATHS = {
    # Derived (common)
    "reach_layer.common.observability.domain",
    # Web UI chat (gated by "web" in selected_channels)
    "reach_layer.channels.web.ui.app_name",
    "reach_layer.channels.web.ui.app_tagline",
    "reach_layer.channels.web.ui.app_icon",
    "reach_layer.channels.web.ui.agent_avatar",
    "reach_layer.channels.web.ui.user_avatar",
    "reach_layer.channels.web.ui.setup_heading",
    "reach_layer.channels.web.ui.setup_subtitle",
    "reach_layer.channels.web.ui.user_id_placeholder",
    "reach_layer.channels.web.ui.user_id_hint",
    "reach_layer.channels.web.ui.start_btn_label",
    "reach_layer.channels.web.ui.new_session_msg",
    "reach_layer.channels.web.ui.returning_user_msg",
    "reach_layer.channels.web.ui.sign_out_confirm",
    "reach_layer.channels.web.ui.switch_user_confirm",
    "reach_layer.channels.web.ui.delete_conversation_confirm",
    # Web derived
    "reach_layer.channels.web.ui.storage_key",
    "reach_layer.channels.web.ui.theme_storage_key",
    # Web gated chat
    "reach_layer.channels.web.ke_internal_url",
    # Web deploy
    "reach_layer.channels.web.auth.enabled",
    # Voice predetermined
    "reach_layer.channels.voice.raya.stt_language",
    "reach_layer.channels.voice.raya.tts_language",
    # Voice chat
    "reach_layer.channels.voice.raya.voice_id",
    "reach_layer.channels.voice.agent_core.fallback_phrase",
    "reach_layer.channels.voice.agent_core.barge_in_acknowledgement",
    "reach_layer.channels.voice.agent_core.timeout_ms",
    "reach_layer.channels.voice.filler_threshold_ms",
    "reach_layer.channels.voice.filler_phrase",
    "reach_layer.channels.voice.terminal_word",
    "reach_layer.channels.voice.recording.consent_purpose",
    # Voice deploy
    "reach_layer.channels.voice.raya.api_key",
    "reach_layer.channels.voice.public_url",
    "reach_layer.channels.voice.vobiz",
    "reach_layer.channels.voice.vad",
    "reach_layer.channels.voice.recording",
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
