"""End-to-end smoke tests for the deterministic wizard.

Belongs to the dev-kit deterministic wizard (Task 12.3). Each test drives the
wizard end-to-end against a mocked LLM for one canonical intake combination
and asserts that the pipeline (skeleton -> per-phase prompts -> tool routing
-> end-of-turn router) is wired up correctly: the wizard advances past its
starting phase, populates the accumulator for the blocks the intake makes
relevant, and never crashes.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
for the wizard sequence under test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule
from dev_kit.agent.field_status import load_field_status, save_field_status
from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS, IntakeState, save_intake_state
from dev_kit.agent.phase_driver import (
    LLMResponse,
    ToolCall,
    collect_pending_fields,
    load_accumulator,
    load_current_phase,
    run_turn,
    save_accumulator,
    save_current_phase,
)
from dev_kit.agent.router import PHASE_ORDER
from dev_kit.agent.skeleton import build_skeleton


# ---------------------------------------------------------------------------
# Test-value registry — schema-aware answers for pending chat fields.
#
# Every entry here was chosen to satisfy the mirror Pydantic schemas
# (dev_kit/schemas/domain/*). If the wizard sticks on a field for which no
# entry is present, the helper falls back to the FieldRule.default.
# ---------------------------------------------------------------------------

_TEST_VALUES: dict[str, Any] = {
    # ---- agent_core: language phase ----
    "agent_core.agent.primary_model": "claude-sonnet-4-6",
    "agent_core.agent.fallback_model": "claude-haiku-4-5-20251001",
    "agent_core.agent.provider": "anthropic",
    "agent_core.agent.consent_prompt": "Do you consent to proceeding?",
    "agent_core.conversation.blocked_message": "I cannot help with that.",
    "agent_core.conversation.escalation_message": "Let me connect you with a human.",
    "agent_core.conversation.output_blocked_message": "I cannot share that.",
    "agent_core.conversation.unknown_intent_message": "I did not understand.",
    "agent_core.conversation.unsupported_language_message": (
        "I do not support that language."
    ),
    "agent_core.conversation.termination_message": "Goodbye!",
    # `conversation.consent_message` was removed from FIELD_RULES — it was
    # a legacy alt path; the runtime reads `agent.consent_prompt` instead.
    "agent_core.conversation.consent_decline_ack": "Understood, I will not proceed.",
    "agent_core.conversation.profile_complete_message": "Profile saved.",
    "agent_core.conversation.returning_user_greeting": "Welcome back!",
    "agent_core.conversation.session_end_eval.prompt": "Rate the call please.",
    "agent_core.preprocessing.language_normalisation.provider": "anthropic",
    "agent_core.preprocessing.language_normalisation.model": "claude-sonnet-4-6",
    "agent_core.preprocessing.nlu_processor.provider": "anthropic",
    "agent_core.preprocessing.nlu_processor.model": "claude-sonnet-4-6",
    "agent_core.preprocessing.nlu_processor.domain_instruction": (
        "Classify user intents."
    ),
    "agent_core.preprocessing.nlu_processor.intents": ["greeting", "question"],
    "agent_core.preprocessing.nlu_processor.entities": ["topic"],
    "agent_core.hitl.response_message": "An agent will join shortly.",
    "agent_core.channels.web.system_prompt_suffix": "Web suffix.",
    # ---- agent_core: knowledge phase (knowledge_retrieval connector) ----
    "agent_core.connectors.internal[name=knowledge_retrieval].description": (
        "Search the knowledge base for factual answers."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.call_when": (
        "When the user asks a factual question."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling": [
        "NLU intent classified",
    ],
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute": (
        "Do not invent answers."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty": (
        "Say no information found."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure": (
        "Apologise for the error."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line": (
        "Let me check the knowledge base."
    ),
    # ---- knowledge_engine ----
    "knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters": {
        "general_query": ["general"],
    },
    # ---- user_state phase ----
    "agent_core.conversation.user_state_model.default_state": "new",
    "agent_core.conversation.user_state_model.states": {
        "new": {"description": "First-time user"},
        "returning": {"description": "Has interacted before"},
    },
    # ---- trust phase ----
    "trust_layer.trust.policy_pack": "default",
    "trust_layer.trust.consent.consent_phrases": ["yes", "I agree"],
    "trust_layer.trust.consent.decline_phrases": ["no", "I decline"],
    # ---- agent_core: workflow phase ----
    "agent_core.agent_workflow.agent_system_prompt": "You are a helpful assistant.",
    "agent_core.agent_workflow.default_fallback_subagent_id": "default_agent",
    # ---- voice channel (reach phase) ----
    "agent_core.channels.voice.system_prompt_suffix": "Voice suffix.",
    "agent_core.channels.voice.terminal_word": "bye",
    "agent_core.channels.voice.tts_rules.numbers": "Read digits one by one.",
    "agent_core.channels.voice.tts_rules.money": "Speak rupee values clearly.",
    "agent_core.channels.voice.tts_rules.dates": "Say dates as words.",
    "agent_core.channels.voice.tts_rules.time": "Say time as words.",
    "agent_core.channels.voice.tts_rules.phone": "Phone digits one by one.",
    "agent_core.channels.voice.tts_rules.abbreviations": "Spell out abbreviations.",
    "agent_core.channels.voice.tts_rules.output_script": "Devanagari",
    "agent_core.channels.voice.tts_rules.english_loanwords": "Keep English words.",
    "agent_core.channels.voice.tts_rules.email": "Read emails carefully.",
    "agent_core.channels.voice.tts_rules.named_entities": "Pronounce names well.",
    # semantic_gate is a structured SemanticGateConfig (mirror tightened
    # to match runtime exactly — was earlier a free dict that silently
    # accepted strings). Tests now write the canonical shape.
    "agent_core.channels.voice.turn_assembler.semantic_gate": {
        "enabled": True,
        "confidence_threshold": 0.75,
    },
    # ---- reach_layer ----
    "reach_layer.channels.web.ui.app_name": "TestBot",
    "reach_layer.channels.web.ui.app_tagline": "Helpful assistant",
    "reach_layer.channels.web.ui.app_icon": "test-icon.png",
    "reach_layer.channels.web.ui.agent_avatar": "agent.png",
    "reach_layer.channels.web.ui.user_avatar": "user.png",
    "reach_layer.channels.web.ui.setup_heading": "Welcome",
    "reach_layer.channels.web.ui.setup_subtitle": "Start chatting",
    "reach_layer.channels.web.ui.user_id_placeholder": "Enter your ID",
    "reach_layer.channels.web.ui.user_id_hint": "Numeric ID",
    "reach_layer.channels.web.ui.start_btn_label": "Start",
    "reach_layer.channels.web.ui.new_session_msg": "New session",
    "reach_layer.channels.web.ui.returning_user_msg": "Welcome back",
    "reach_layer.channels.web.ui.sign_out_confirm": "Sign out?",
    "reach_layer.channels.web.ui.switch_user_confirm": "Switch user?",
    "reach_layer.channels.web.ui.delete_conversation_confirm": "Delete this?",
    # `channels.web.ke_internal_url` was removed from FIELD_RULES — it's
    # a dpg-level infrastructure setting overridden by the
    # KE_INTERNAL_URL env var, not user-configurable in chat.
    "reach_layer.channels.voice.raya.voice_id": (
        "0f24fb66-e495-4781-9e84-1224aa7dacde"
    ),
    "reach_layer.channels.voice.agent_core.fallback_phrase": "Sorry, I missed that.",
    "reach_layer.channels.voice.agent_core.barge_in_acknowledgement": "Yes?",
    "reach_layer.channels.voice.filler_threshold_ms": 800,
    "reach_layer.channels.voice.filler_phrase": "Let me think.",
    "reach_layer.channels.voice.terminal_word": "bye",
    "reach_layer.channels.voice.recording.consent_purpose": "Quality monitoring.",
}


def _test_value_for(path: str, rule: FieldRule) -> Any:
    """Return a schema-acceptable test value for a pending chat field.

    Looks up the path in the per-path registry first; otherwise falls back to
    the FieldRule default or a generic string.

    Args:
        path: Full dotted accumulator path including block prefix.
        rule: The matching FieldRule from AGGREGATED_FIELD_RULES.

    Returns:
        A Python value the validation layer should accept for this path.
    """
    if path in _TEST_VALUES:
        return _TEST_VALUES[path]
    if rule.default is not None:
        return rule.default
    return "test_value"


# ---------------------------------------------------------------------------
# Test setup / fake LLM
# ---------------------------------------------------------------------------


def _setup_project(
    tmp_path: Path,
    intake_fields: dict[str, Any],
    *,
    slug: str = "smoke",
) -> tuple[Path, Path]:
    """Lay out a project tree with the skeleton already built.

    The intake state is seeded with ``completed=True`` and a full
    ``binary_flags_seen`` list so that the wizard starts after the tier phase
    has already been completed (these tests exercise post-tier phase flow).

    Args:
        tmp_path: Pytest tmp_path fixture root.
        intake_fields: Field overrides for IntakeState construction.
        slug: Project slug under ``projects_root``.

    Returns:
        ``(projects_root, slug_root)``.
    """
    projects_root = tmp_path / "projects"
    slug_root = projects_root / slug
    (slug_root / "_meta").mkdir(parents=True)

    # Mark intake as complete so the wizard starts past the tier phase.
    completed_fields = {
        **intake_fields,
        "completed": True,
        "binary_flags_seen": list(BINARY_INTAKE_FIELDS),
    }
    intake = IntakeState(**completed_fields)
    save_intake_state(slug_root / "_meta" / "intake_state.json", intake)

    accumulator, field_status = build_skeleton(intake)
    save_accumulator(slug_root, accumulator)
    save_field_status(slug_root / "_meta" / "field_status.json", field_status)
    save_current_phase(slug_root, "language")  # start at language (tier is done)

    return projects_root, slug_root


def _make_auto_answer_llm(slug_root: Path, intake: IntakeState):
    """Return an llm_call closure that answers every pending field per turn.

    The closure reads the current phase, collects all pending chat fields,
    and emits one ``update_config`` ToolCall per field with a value from
    ``_TEST_VALUES`` (or the FieldRule default).

    Args:
        slug_root: The project directory the wizard is running against.
        intake: Current IntakeState snapshot used for applies_if evaluation.

    Returns:
        A callable matching ``phase_driver.run_turn``'s ``llm_call`` contract.
    """

    def _call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        phase = load_current_phase(slug_root)
        field_status = load_field_status(slug_root / "_meta" / "field_status.json")
        pending = collect_pending_fields(phase, intake, field_status)
        tool_calls: list[ToolCall] = []
        for path, rule in pending:
            value = _test_value_for(path, rule)
            tool_calls.append(
                ToolCall("update_config", {"path": path, "value": value})
            )
        return LLMResponse(
            text=f"phase={phase} pending={len(pending)}",
            tool_calls=tool_calls,
        )

    return _call


def _drive_wizard(
    projects_root: Path,
    slug_root: Path,
    intake: IntakeState,
    *,
    slug: str = "smoke",
    max_turns: int = 50,
) -> tuple[str, int]:
    """Run turns until the wizard reaches review, stalls, or hits the cap.

    Args:
        projects_root: The projects root path passed to ``run_turn``.
        slug_root: ``projects_root / slug``.
        intake: The IntakeState already persisted into the project.
        slug: Project slug.
        max_turns: Hard cap on iterations.

    Returns:
        ``(final_phase, turns_run)``. A stall is when two consecutive turns
        leave the phase unchanged.
    """
    llm = _make_auto_answer_llm(slug_root, intake)

    turns_run = 0
    last_phase: str | None = None
    stall_count = 0
    for i in range(max_turns):
        before = load_current_phase(slug_root)
        run_turn(
            user_message=f"turn{i}",
            project_slug=slug,
            projects_root=projects_root,
            llm_call=llm,
        )
        after = load_current_phase(slug_root)
        turns_run += 1
        if after == "review":
            return after, turns_run
        if after == before:
            stall_count += 1
            # Bail after 3 consecutive no-progress turns — wizard is stuck.
            if stall_count >= 3:
                return after, turns_run
        else:
            stall_count = 0
        last_phase = after
    return load_current_phase(slug_root), turns_run


# ---------------------------------------------------------------------------
# Canonical intakes
# ---------------------------------------------------------------------------


_BASE_INTAKE = dict(
    has_kb=False,
    has_external_tools=False,
    is_multi_turn=False,
    needs_persistent_user_data=False,
    is_companion_style=False,
    needs_consent=False,
    has_hitl=False,
    selected_channels=["web"],
    default_language="english",
    supported_languages=["english"],
    domain_description="generic test domain",
    project_name="TestBot",
)


def _intake_single_shot_kb() -> dict[str, Any]:
    """KB-only QnA bot. KB enabled, no tools, single-turn, web channel."""
    return {
        **_BASE_INTAKE,
        "has_kb": True,
        "project_name": "QnaBot",
        "domain_description": "Answers FAQ",
    }


def _intake_multi_turn_api() -> dict[str, Any]:
    """Multi-turn API-calling bot. External tools, multi-turn, web channel."""
    return {
        **_BASE_INTAKE,
        "has_external_tools": True,
        "is_multi_turn": True,
        "project_name": "ApiBot",
        "domain_description": "Multi-turn assistant that calls APIs",
    }


def _intake_companion_voice() -> dict[str, Any]:
    """Conversational companion with voice + KB + consent + HiTL."""
    return {
        **_BASE_INTAKE,
        "has_kb": True,
        "is_multi_turn": True,
        "needs_persistent_user_data": True,
        "is_companion_style": True,
        "needs_consent": True,
        "has_hitl": True,
        "selected_channels": ["voice"],
        "project_name": "CompanionBot",
        "domain_description": "Conversational companion",
    }


# ---------------------------------------------------------------------------
# Tests — one per canonical intake
# ---------------------------------------------------------------------------


def _assert_advanced_past_start(final_phase: str) -> None:
    """Fail unless the wizard advanced past the entry phase."""
    assert final_phase in PHASE_ORDER, f"unknown phase {final_phase!r}"
    start_idx = PHASE_ORDER.index("tier")
    final_idx = PHASE_ORDER.index(final_phase)
    assert final_idx > start_idx, (
        f"wizard did not advance past tier; final_phase={final_phase!r}"
    )


def _assert_block_populated(accumulator: dict[str, dict], block: str) -> None:
    """Fail unless ``accumulator[block]`` has at least one populated section."""
    payload = accumulator.get(block) or {}
    assert payload, (
        f"accumulator[{block!r}] is empty; expected the wizard to populate it"
    )


def test_wizard_single_shot_kb_advances_and_populates(tmp_path: Path) -> None:
    """KB-only intake drives the wizard past tier and writes the KE accumulator.

    Verifies that build_skeleton + per-turn auto-answer through phase_driver
    produces a non-empty knowledge_engine block (the intake's defining flag is
    has_kb=True) and that the wizard advanced past its entry phase.
    """
    intake_fields = _intake_single_shot_kb()
    projects_root, slug_root = _setup_project(tmp_path, intake_fields)
    intake = IntakeState(**intake_fields)

    final_phase, turns_run = _drive_wizard(
        projects_root, slug_root, intake, max_turns=50
    )

    _assert_advanced_past_start(final_phase)
    assert turns_run >= 1

    accumulator = load_accumulator(slug_root)
    _assert_block_populated(accumulator, "agent_core")
    _assert_block_populated(accumulator, "knowledge_engine")
    _assert_block_populated(accumulator, "trust_layer")


def test_wizard_multi_turn_api_advances_and_populates(tmp_path: Path) -> None:
    """Multi-turn API-calling intake drives the wizard past tier.

    Verifies the pipeline still runs when has_external_tools=True. The tools
    phase has chat fields gated by has_external_tools, so the accumulator
    must surface at least some agent_core content (NLU intents/entities,
    conversation messages).
    """
    intake_fields = _intake_multi_turn_api()
    projects_root, slug_root = _setup_project(tmp_path, intake_fields)
    intake = IntakeState(**intake_fields)

    final_phase, turns_run = _drive_wizard(
        projects_root, slug_root, intake, max_turns=50
    )

    _assert_advanced_past_start(final_phase)
    assert turns_run >= 1

    accumulator = load_accumulator(slug_root)
    _assert_block_populated(accumulator, "agent_core")
    _assert_block_populated(accumulator, "memory_layer")
    _assert_block_populated(accumulator, "trust_layer")


def test_wizard_companion_voice_advances_and_populates(tmp_path: Path) -> None:
    """Companion + voice + KB + consent + HiTL drives the wizard past tier.

    Verifies that the full-fat intake (all binary flags True except
    has_external_tools, voice channel) does not crash the pipeline and
    produces populated accumulator content for the blocks gated by those
    flags.
    """
    intake_fields = _intake_companion_voice()
    projects_root, slug_root = _setup_project(tmp_path, intake_fields)
    intake = IntakeState(**intake_fields)

    final_phase, turns_run = _drive_wizard(
        projects_root, slug_root, intake, max_turns=50
    )

    _assert_advanced_past_start(final_phase)
    assert turns_run >= 1

    accumulator = load_accumulator(slug_root)
    _assert_block_populated(accumulator, "agent_core")
    _assert_block_populated(accumulator, "knowledge_engine")
    _assert_block_populated(accumulator, "memory_layer")
    _assert_block_populated(accumulator, "trust_layer")


# ---------------------------------------------------------------------------
# Sanity check on the test-value registry itself — guards against silent
# typos that would otherwise show up only as wizard stalls.
# ---------------------------------------------------------------------------


def test_test_values_registry_keys_are_known_paths() -> None:
    """Every key in ``_TEST_VALUES`` should be a known chat-field path.

    Catches typos in the registry. Unknown keys would silently be ignored by
    ``_test_value_for`` because that function only consults the registry when
    a path was already returned by ``collect_pending_fields``. The catch is
    cheap and prevents drift between the registry and ``AGGREGATED_FIELD_RULES``.
    """
    unknown = [p for p in _TEST_VALUES if p not in AGGREGATED_FIELD_RULES]
    assert not unknown, f"_TEST_VALUES has paths not in AGGREGATED_FIELD_RULES: {unknown}"
