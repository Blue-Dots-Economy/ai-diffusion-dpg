"""FIELD_RULES for reach_layer. See catalogue §7.3 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the reach_layer runtime block.

NOTE: ``REACH_LAYER_WEB_MODE`` is a compose-level environment variable (NOT a
YAML field). It is set to ``full`` when ``"web" in selected_channels``, else
``routing_only``. This is handled by the compose generator
(``automation/docker/docker-compose.yml``) based on intake state, not by
FIELD_RULES. See catalogue §7.3 "Compose-level env var" note and design §8.

Locked decision #6: ``voice.recording.consent_purpose`` is a standalone chat
field (applies_if includes voice.recording.source != "disabled").
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ── Derived: reach_layer.common.observability.domain ─────────────────────
    # Path is reach_layer.common.observability.domain (NOT reach_layer.observability.domain).

    "common.observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="CommonObservabilityConfig",
    ),

    # ── Gated chat: web UI strings (catalogue §3.3 + §7.3) ───────────────────

    "channels.web.ui.app_name": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["project_name", "domain_description", "default_language", "supported_languages"],
        description="Application name displayed in the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.app_tagline": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["domain_description", "default_language", "supported_languages"],
        description="Short tagline displayed below the app name in the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.app_icon": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["domain_description"],
        description="App icon path or emoji for the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.agent_avatar": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["domain_description"],
        description="Agent avatar image path or URL for the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.user_avatar": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=[],
        description="User avatar image path or URL for the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.setup_heading": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["project_name", "default_language", "supported_languages"],
        description="Heading shown on the web UI setup/login screen.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.setup_subtitle": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Subtitle shown on the web UI setup/login screen.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.user_id_placeholder": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["domain_description", "default_language", "supported_languages"],
        description="Placeholder text for the user ID input field.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.user_id_hint": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["domain_description", "default_language", "supported_languages"],
        description="Hint text below the user ID input field.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.start_btn_label": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Label for the start/launch button in the web UI.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.new_session_msg": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["project_name", "domain_description", "default_language", "supported_languages"],
        description='Opening message for new sessions. Often "Hello! I\'m <project_name>..."',
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.returning_user_msg": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Welcome-back message for returning users.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.sign_out_confirm": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Confirmation prompt for sign-out action.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.switch_user_confirm": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Confirmation prompt for switch-user action.",
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.delete_conversation_confirm": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"web" in selected_channels',
        invalidated_by=["default_language", "supported_languages"],
        description="Confirmation prompt for deleting conversation history.",
        pydantic_class="WebUiConfig",
    ),

    # ── Derived: web UI keys ──────────────────────────────────────────────────

    "channels.web.ui.storage_key": FieldRule(
        category="derived",
        compute='f"{project_slug}_user_id"',
        pydantic_class="WebUiConfig",
    ),
    "channels.web.ui.theme_storage_key": FieldRule(
        category="derived",
        compute='f"{project_slug}_theme"',
        pydantic_class="WebUiConfig",
    ),

    # NOTE: `channels.web.ke_internal_url` used to be a chat field here but
    # has been removed. It is an infrastructure/DNS setting with a sensible
    # default in `reach_layer/config/dpg.yaml` (in-cluster service name) and
    # is overridden by the `KE_INTERNAL_URL` env var at deploy time. The
    # mirror `WebChannelSection` doesn't expose it either, so chat-time
    # writes used to fail validation with "extra_forbidden" and stall the
    # reach phase indefinitely. End users have no way to know the cluster
    # DNS name; this belongs at deploy time, not in the wizard.

    # ── Deploy: web.auth.enabled ──────────────────────────────────────────────

    "channels.web.auth.enabled": FieldRule(
        category="deploy",
        description="Toggle Google SSO authentication for the web channel. Default: true (dpg).",
        pydantic_class="WebAuthConfig",
    ),

    # ── Gated chat: voice.raya.stt_language / tts_language ───────────────────
    #
    # The voice channel speaks a SINGLE language at a time, which may
    # differ from the project's default_language (a multi-language web
    # project can run a Hindi-only voice line, etc.). Earlier these
    # were `predetermined` and computed via `lang_code(default_language)`
    # — that locked them to the default and the LLM had no way to
    # override when the user picked a different voice language. The
    # mirror's `voice_id_matches_language` validator then rejected the
    # voice_id update, and the wizard stalled.
    #
    # Now they're chat fields: the LLM asks "which language?", picks the
    # matching Raya voice_id from the prompt's injected allowlist, and
    # writes all three in the same turn. No default — the LLM MUST
    # write them when voice is selected (the prompt enforces this).
    "channels.voice.raya.stt_language": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels"],
        description="STT language tag for Raya (e.g. 'en-in', 'hi'). Must match voice_id's language.",
        pydantic_class="RayaVoiceConfig",
    ),
    "channels.voice.raya.tts_language": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels"],
        description="TTS language tag for Raya (e.g. 'en-in', 'hi'). Must match voice_id's language.",
        pydantic_class="RayaVoiceConfig",
    ),

    # ── Gated chat: voice.raya.voice_id ──────────────────────────────────────

    "channels.voice.raya.voice_id": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Raya TTS voice ID. Deploy form pre-fills; operator can swap per-deploy.",
        deploy_overridable=True,
        pydantic_class="RayaVoiceConfig",
    ),

    # ── Gated chat: voice.agent_core.* ───────────────────────────────────────

    "channels.voice.agent_core.fallback_phrase": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Phrase the voice channel speaks when Agent Core is unreachable.",
        pydantic_class="VoiceAgentCoreClient",
    ),
    "channels.voice.agent_core.barge_in_acknowledgement": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Short acknowledgement phrase when the user barges in.",
        pydantic_class="VoiceAgentCoreClient",
    ),
    "channels.voice.agent_core.timeout_ms": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels"],
        default=15000,
        description="Timeout in ms for Agent Core HTTP calls from voice channel.",
        pydantic_class="VoiceAgentCoreClient",
    ),

    # ── Gated chat: voice filler + terminal ──────────────────────────────────

    "channels.voice.filler_threshold_ms": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels"],
        description="Threshold in ms before a filler phrase is played while waiting.",
        pydantic_class="VoiceChannelSection",
    ),
    "channels.voice.filler_phrase": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Filler phrase spoken while the agent is thinking.",
        pydantic_class="VoiceChannelSection",
    ),
    "channels.voice.terminal_word": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Terminal word signalling end of agent TTS output.",
        pydantic_class="VoiceChannelSection",
    ),

    # ── Gated chat: voice.recording.consent_purpose (locked decision #6) ─────

    "channels.voice.recording.consent_purpose": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["needs_consent"],
        description="Consent purpose string for voice recording. Ties to Trust Layer consent grants.",
        pydantic_class="VoiceChannelSection",
    ),

    # ── Deploy: voice ─────────────────────────────────────────────────────────

    "channels.voice.raya.api_key": FieldRule(
        category="deploy",
        applies_if='"voice" in selected_channels',
        description="Raya API key (deploy secret).",
        pydantic_class="RayaVoiceConfig",
    ),
    "channels.voice.public_url": FieldRule(
        category="deploy",
        applies_if='"voice" in selected_channels',
        description="Public URL for voice channel (e.g. ngrok).",
    ),
    "channels.voice.vobiz": FieldRule(
        category="deploy",
        applies_if='"voice" in selected_channels',
        description="Telephony adapter configuration (deploy form).",
    ),
    "channels.voice.vad": FieldRule(
        category="deploy",
        advanced=True,
        applies_if='"voice" in selected_channels',
        invalidated_by=["default_language"],
        description="Silero VAD tuning (advanced deploy form). Hindi/voice cadence overrides.",
        pydantic_class="VoiceChannelSection",
    ),
    "channels.voice.recording": FieldRule(
        category="deploy",
        advanced=True,
        applies_if='"voice" in selected_channels',
        description="Voice recording configuration (advanced deploy form).",
    ),
}

register_block_rules("reach_layer", FIELD_RULES)
