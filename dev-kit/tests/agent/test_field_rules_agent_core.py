"""Tests for agent_core FIELD_RULES content (per catalogue §7.1)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.agent_core import FIELD_RULES


# Catalogue §7.1: the full set of domain-half paths under agent_core.
# This list MUST match the catalogue exactly. When the catalogue changes,
# update this list and the FIELD_RULES dict together.
EXPECTED_PATHS = {
    # Always-asked chat (catalogue §3.1)
    "agent.primary_model",
    "agent.fallback_model",
    "agent.provider",
    "conversation.blocked_message",
    "conversation.escalation_message",
    "conversation.output_blocked_message",
    "conversation.unknown_intent_message",
    "conversation.unsupported_language_message",
    "preprocessing.language_normalisation.enabled",
    "preprocessing.language_normalisation.provider",
    "preprocessing.language_normalisation.model",
    "preprocessing.nlu_processor.provider",
    "preprocessing.nlu_processor.model",
    "agent_workflow.agent_system_prompt",
    "agent_workflow.default_fallback_subagent_id",
    "agent_workflow.subagents",
    "agent_workflow.global_routing",
    "agent_workflow.global_tools",
    "channels.web.system_prompt_suffix",
    "channels.web.turn_assembler.silence_trigger.silence_ms",
    "channels.web.turn_assembler.max_wait_ceiling.max_wait_ms",
    "channels.mcp.system_prompt_suffix",
    "channels.mcp.turn_assembler.silence_trigger.silence_ms",
    "channels.mcp.turn_assembler.max_wait_ceiling.max_wait_ms",
    # Gated chat (catalogue §4)
    "agent.consent_prompt",
    "conversation.termination_message",
    # `conversation.consent_message` removed from FIELD_RULES — legacy
    # alt path; runtime reads agent.consent_prompt directly.
    "conversation.consent_decline_ack",
    "conversation.profile_complete_message",
    "conversation.returning_user_greeting",
    "conversation.user_state_model.default_state",
    "conversation.user_state_model.states",
    "conversation.session_end_eval.prompt",
    "connectors.read",
    "connectors.write",
    "connectors.identity",
    "connectors.internal[name=knowledge_retrieval].description",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.call_when",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line",
    "preprocessing.nlu_processor.signal_intents",
    "preprocessing.nlu_processor.slots",
    "preprocessing.nlu_processor.act_intents",
    "preprocessing.nlu_processor.known_fields",
    "preprocessing.nlu_processor.examples",
    "preprocessing.nlu_processor.termination_gate",
    "preprocessing.nlu_processor.topics",
    "preprocessing.nlu_processor.signals",
    "preprocessing.nlu_processor.off_track",
    "preprocessing.nlu_processor.timeout_ms",
    "preprocessing.nlu_processor.retry_attempts",
    "preprocessing.nlu_processor.history_turns",
    "entity_to_profile_field",
    "hitl.response_message",
    "channels.voice.system_prompt_suffix",
    "channels.voice.tts_rules.numbers",
    "channels.voice.tts_rules.money",
    "channels.voice.tts_rules.dates",
    "channels.voice.tts_rules.time",
    "channels.voice.tts_rules.phone",
    "channels.voice.tts_rules.abbreviations",
    "channels.voice.tts_rules.output_script",
    "channels.voice.tts_rules.english_loanwords",
    "channels.voice.tts_rules.email",
    "channels.voice.tts_rules.named_entities",
    "channels.voice.terminal_word",
    # Predetermined (catalogue §7.1)
    "agent.ask_for_consent",
    "conversation.user_state_model.enabled",
    "conversation.session_end_eval.enabled",
    "preprocessing.language_normalisation.default_language",
    "preprocessing.language_normalisation.supported_languages",
    "connectors.internal[name=knowledge_retrieval]",
    "connectors.internal[name=knowledge_retrieval].name",
    "connectors.internal[name=knowledge_retrieval].route",
    "connectors.internal[name=knowledge_retrieval].input_schema",
    "channels.voice.turn_assembler.silence_trigger.silence_ms",
    "channels.voice.turn_assembler.max_wait_ceiling.max_wait_ms",
    "agent_workflow.version",   # default '1.0.0', auto_answer
    # Derived
    "agent_workflow.workflow_id",
    "observability.domain",
}


def test_all_expected_paths_present():
    actual = set(FIELD_RULES.keys())
    missing = EXPECTED_PATHS - actual
    extra = actual - EXPECTED_PATHS
    assert missing == set(), f"missing rules: {sorted(missing)}"
    # `extra` is allowed only if catalogue was updated and this test is stale —
    # but flag it as a warning so the catalogue stays in sync.
    if extra:
        pytest.fail(f"unexpected rules not in catalogue: {sorted(extra)}")


def test_deploy_overridable_fields_are_chat():
    for path in ("agent.primary_model", "agent.fallback_model", "agent.provider"):
        rule = FIELD_RULES[path]
        assert rule.category == "chat", f"{path} must be chat"
        assert rule.deploy_overridable is True, f"{path} must be deploy_overridable"


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


# NLU single-mode (spec §16): the wizard never asks for intents or entities.
_REMOVED_LEAVES = {
    "intents", "entities", "domain_instruction", "global_intents", "valid_intents",
    "sentiment_classes",
}
_REMOVED_NLU_PATHS = {
    "agent_core.preprocessing.nlu_processor.mode",
    "agent_core.preprocessing.nlu_processor.confidence_threshold",
}


def _removed(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in _REMOVED_LEAVES or path in _REMOVED_NLU_PATHS


def test_registry_has_no_intent_mode_rules():
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
    bad = sorted(p for p in AGGREGATED_FIELD_RULES if _removed(p))
    assert bad == []


def test_no_rule_is_invalidated_by_a_removed_path():
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
    bad = {
        path: [dep for dep in (rule.invalidated_by or []) if _removed(dep) or _removed(f"agent_core.{dep}")]
        for path, rule in AGGREGATED_FIELD_RULES.items()
    }
    assert {p: d for p, d in bad.items() if d} == {}
