"""
agent_core/tests/test_workflow_gate.py

Unit tests for the AgentCore workflow gate:
  consent gate, profile building rounds, conditional round skip,
  grace turn, returning user resume, hard_min check.

All Memory Layer writes are mocked. No real Redis or Neo4j calls.

Coverage:
- Consent gate: first visit → shows consent message
- Consent gate: YES intent → advances to profile_building, asks round 1
- Consent gate: NO intent → session-only mode, shows decline ack + round 1
- Consent gate: ambiguous intent → re-asks consent
- Profile building: entities written with correct scope (consent True → persistent)
- Profile building: entities written with correct scope (consent False → session)
- Profile building: advances to next round unconditionally
- Profile building: skips conditional round when condition not met
- Profile building: includes conditional round when condition met
- Profile building: all rounds done + hard_min met → market_truth
- Profile building: all rounds done + hard_min not met → grace_turn
- Grace turn: extracts entities, unconditionally advances to market_truth
- Grace turn: no entities extracted → still advances to market_truth
- Returning user: has consent_flag + hard_min fields → resumes at market_truth
- Returning user: has consent_flag, missing hard_min → resumes at profile_building
- Returning user: no consent_flag → stays at awaiting_consent
- Step 5b bypass: profile nodes skip unknown-intent early exit
- _check_hard_min_met: field in profile → True
- _check_hard_min_met: field in just_extracted → True
- _check_hard_min_met: field missing both → False
- _get_next_round: returns first unskipped round
- _get_next_round: skips completed rounds
- _get_next_round: returns None when all done
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.models import (
    ContextBundle,
    LLMResponse,
    NLUResult,
    TrustCheckResult,
    TurnInput,
    TurnResult,
)
from src.orchestrator import AgentCore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = "sess_gate_001"
USER_ID    = "user_gate_001"
TIMESTAMP  = int(time.time() * 1000)

ROUNDS_CFG = [
    {
        "round": 1,
        "layer": "identity",
        "entities": ["name", "age_bracket", "location"],
        "condition": None,
        "question": "Please ask the user to provide ALL of the following details in a SINGLE conversational question: name, age_bracket, location.",
    },
    {
        "round": 2,
        "layer": "capability_basic",
        "entities": ["education_level"],
        "condition": None,
        "question": "Please ask the user to provide ALL of the following details in a SINGLE conversational question: education_level.",
    },
    {
        "round": 3,
        "layer": "capability_trade",
        "entities": ["trade_or_stream", "years_experience"],
        "condition": {"field": "education_level", "in": ["iti", "diploma", "10th", "12th"]},
        "question": "Please ask the user to provide ALL of the following details in a SINGLE conversational question: trade_or_stream, years_experience.",
    },
    {
        "round": 4,
        "layer": "constraint",
        "entities": ["income_urgency", "max_commute_km"],
        "condition": None,
        "question": "Please ask the user to provide ALL of the following details in a SINGLE conversational question: income_urgency, max_commute_km.",
    },
    {
        "round": 5,
        "layer": "aspiration",
        "entities": ["sector_preference", "open_to_training"],
        "condition": None,
        "question": "Please ask the user to provide ALL of the following details in a SINGLE conversational question: sector_preference, open_to_training.",
    },
]

BASE_CONFIG = {
    "conversation": {
        "blocked_message":         "Blocked.",
        "escalation_message":      "Escalating.",
        "output_blocked_message":  "Output blocked.",
        "unknown_intent_message":  "I didn't understand that.",
        "termination_message":     "Goodbye.",
        "consent_message":         "Consent question?",
        "consent_decline_ack":     "Decline ack.",
        "profile_complete_message":"Profile done!",
    },
    "preprocessing": {
        "nlu_processor": {"confidence_threshold": 0.5},
    },
    "profile_collection": {
        "rounds": ROUNDS_CFG,
        "hard_min_fields": ["trade_or_stream", "location"],
        "field_labels": {},
    },
    "entity_to_profile_field": {
        "trade_or_stream":  "trade_or_stream",
        "location":         "location",
        "education_level":  "education_level",
        "name":             "name",
        "income_urgency":   "income_urgency",
    },
    "hitl": {"loop_count_threshold": 3},
    "workflow": {"termination_intents": ["termination_intent"], "steps": []},
    "agent": {"primary_model": "test-model", "fallback_model": "test-model"},
}


def _make_turn_input(msg: str = "hello") -> TurnInput:
    return TurnInput(
        session_id=SESSION_ID,
        user_id=USER_ID,
        user_message=msg,
        channel="cli",
        timestamp_ms=TIMESTAMP,
    )


def _make_bundle(
    current_node: str = "awaiting_consent",
    current_question: str = "",
    collection_round: int = 0,
    consent: bool | None = None,
    profile: dict | None = None,
    is_returning: bool = False,
) -> ContextBundle:
    session: dict = {
        "current_node":     current_node,
        "current_question": current_question,
        "collection_round": collection_round,
        "loop_count":       0,
        "is_returning":     is_returning,
    }
    if consent is not None:
        session["consent"] = consent
    return ContextBundle(
        session=session,
        profile=profile or {},
        journey=None,
    )


def _make_nlu(intent: str = "profile_answer", entities: dict | None = None,
              confidence: float = 0.9) -> NLUResult:
    return NLUResult(
        intent=intent,
        entities=entities or {},
        sentiment="neutral",
        confidence=confidence,
    )


def _make_orchestrator() -> tuple[AgentCore, MagicMock]:
    """Return (orchestrator, mock_memory)."""
    memory      = MagicMock()
    trust       = MagicMock()
    ke          = MagicMock()
    registry    = MagicMock()
    manager     = MagicMock()
    learning    = MagicMock()
    llm         = MagicMock()

    trust.check_input.return_value  = TrustCheckResult(passed=True, action="allow")
    trust.check_output.return_value = TrustCheckResult(passed=True, action="allow")
    llm.call.return_value = LLMResponse(content="LLM response", stop_reason="end_turn", model_used="test")
    manager.build_system_prompt.return_value = "system"
    manager.build_messages.return_value      = [{"role": "user", "content": "hello"}]
    manager.run_turn.return_value            = ("LLM response", [])
    ke.retrieve.return_value                 = []

    orch = AgentCore(
        config=BASE_CONFIG,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=ke,
        tool_registry=registry,
        manager_agent=manager,
        learning=learning,
    )
    return orch, memory


# ---------------------------------------------------------------------------
# _get_next_round
# ---------------------------------------------------------------------------

class TestGetNextRound:
    def setup_method(self):
        self.orch, _ = _make_orchestrator()

    def test_returns_first_round_when_none_done(self):
        result = self.orch._get_next_round(0, {}, ROUNDS_CFG)
        assert result is not None
        assert result["round"] == 1

    def test_skips_completed_rounds(self):
        result = self.orch._get_next_round(2, {}, ROUNDS_CFG)
        # Round 3 has condition; profile is empty so it should be skipped.
        assert result is not None
        assert result["round"] == 4

    def test_includes_conditional_round_when_condition_met(self):
        profile = {"education_level": "iti"}
        result = self.orch._get_next_round(2, profile, ROUNDS_CFG)
        assert result is not None
        assert result["round"] == 3

    def test_skips_conditional_round_when_condition_not_met(self):
        profile = {"education_level": "graduate"}
        result = self.orch._get_next_round(2, profile, ROUNDS_CFG)
        assert result is not None
        assert result["round"] == 4

    def test_returns_none_when_all_rounds_done(self):
        result = self.orch._get_next_round(5, {}, ROUNDS_CFG)
        assert result is None


# ---------------------------------------------------------------------------
# _check_hard_min_met
# ---------------------------------------------------------------------------

class TestCheckHardMinMet:
    def setup_method(self):
        self.orch, _ = _make_orchestrator()

    def test_met_when_fields_in_profile(self):
        profile = {"trade_or_stream": "electrician", "location": "Hubli"}
        assert self.orch._check_hard_min_met(profile, {}) is True

    def test_met_when_fields_in_just_extracted(self):
        assert self.orch._check_hard_min_met({}, {"trade_or_stream": "plumber", "location": "Mysore"}) is True

    def test_met_when_split_across_profile_and_extracted(self):
        assert self.orch._check_hard_min_met(
            {"location": "Hubli"},
            {"trade_or_stream": "electrician"},
        ) is True

    def test_not_met_when_trade_missing(self):
        assert self.orch._check_hard_min_met({"location": "Hubli"}, {}) is False

    def test_not_met_when_both_missing(self):
        assert self.orch._check_hard_min_met({}, {}) is False


# ---------------------------------------------------------------------------
# _infer_resume_node
# ---------------------------------------------------------------------------

class TestInferResumeNode:
    def setup_method(self):
        self.orch, _ = _make_orchestrator()

    def test_no_consent_flag_stays_awaiting_consent(self):
        bundle = _make_bundle(profile={})
        assert self.orch._infer_resume_node(bundle) == "awaiting_consent"

    def test_consent_flag_with_hard_min_resumes_market_truth(self):
        bundle = _make_bundle(profile={
            "consent_flag": True, "trade_or_stream": "electrician", "location": "Hubli",
        })
        assert self.orch._infer_resume_node(bundle) == "market_truth"

    def test_consent_flag_missing_hard_min_resumes_profile_building(self):
        bundle = _make_bundle(profile={"consent_flag": True})
        assert self.orch._infer_resume_node(bundle) == "profile_building"


# ---------------------------------------------------------------------------
# Consent gate via _workflow_gate
# ---------------------------------------------------------------------------

class TestConsentGate:
    def setup_method(self):
        self.orch, self.mem = _make_orchestrator()

    def _call_gate(self, bundle, nlu):
        return self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )

    def test_first_visit_shows_consent_message(self):
        # Gate always returns None — falls through to LLM for language-aware delivery.
        bundle = _make_bundle(current_node="awaiting_consent", current_question="")
        nlu    = _make_nlu("greeting_intent")
        result = self._call_gate(bundle, nlu)
        assert result is None
        # current_question written sync AND set in-place on bundle.session
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_question",
                                       BASE_CONFIG["conversation"]["consent_message"])
        assert bundle.session["current_question"] == BASE_CONFIG["conversation"]["consent_message"]

    def test_consent_yes_advances_to_profile_building_round1(self):
        bundle = _make_bundle(
            current_node="awaiting_consent",
            current_question="Consent question?",
        )
        nlu = _make_nlu("consent_granted")
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will ask round 1 in user's language.
        assert result is None
        # Verify consent=True and current_node=profile_building written
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "consent", True)
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "profile_building")
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "persistent", "consent_flag", True)
        # bundle.session updated in-place so LLM sees round 1 question
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: name, age_bracket, location."
        assert bundle.session["current_node"] == "profile_building"

    def test_consent_yes_alias_works(self):
        bundle = _make_bundle(
            current_node="awaiting_consent", current_question="Consent question?",
        )
        result = self._call_gate(bundle, _make_nlu("consent_yes"))
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "consent", True)

    def test_consent_no_sets_session_scope_and_shows_decline_ack(self):
        bundle = _make_bundle(
            current_node="awaiting_consent", current_question="Consent question?",
        )
        nlu = _make_nlu("consent_declined")
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will acknowledge decline + ask round 1 in user's language.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "consent", False)
        # bundle.session updated in-place with round 1 question template
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: name, age_bracket, location."
        assert bundle.session["current_node"] == "profile_building"

    def test_consent_no_alias_works(self):
        bundle = _make_bundle(
            current_node="awaiting_consent", current_question="Consent question?",
        )
        result = self._call_gate(bundle, _make_nlu("consent_no"))
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "consent", False)

    def test_ambiguous_answer_re_asks_consent(self):
        bundle = _make_bundle(
            current_node="awaiting_consent", current_question="Consent question?",
        )
        nlu = _make_nlu("unknown", confidence=0.3)
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will re-ask consent using current_question from session.
        assert result is None
        # current_question unchanged (still has the consent question template)
        assert bundle.session["current_question"] == "Consent question?"


# ---------------------------------------------------------------------------
# Profile building via _workflow_gate
# ---------------------------------------------------------------------------

class TestProfileBuilding:
    def setup_method(self):
        self.orch, self.mem = _make_orchestrator()

    def _call_gate(self, bundle, nlu):
        return self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )

    def test_entities_written_persistent_when_consent_true(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=1,
            consent=True,
        )
        nlu = _make_nlu("profile_answer", {"location": "Hubli"})
        self._call_gate(bundle, nlu)
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "persistent", "location", "Hubli")

    def test_entities_written_session_when_consent_false(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=1,
            consent=False,
        )
        nlu = _make_nlu("profile_answer", {"location": "Hubli"})
        self._call_gate(bundle, nlu)
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "location", "Hubli")

    def test_advances_to_next_round_unconditionally(self):
        bundle = _make_bundle(current_node="profile_building", collection_round=1, consent=True)
        nlu = _make_nlu("profile_answer", {})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will ask round 2 in user's language.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "collection_round", 2)
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: education_level."

    def test_skips_round3_when_condition_not_met(self):
        # After round 2, education_level=graduate → round 3 skipped → round 4
        bundle = _make_bundle(
            current_node="profile_building", collection_round=2, consent=True,
            profile={"location": "Hubli", "trade_or_stream": "electrician"},
        )
        nlu = _make_nlu("profile_answer", {"education_level": "graduate"})
        result = self._call_gate(bundle, nlu)
        assert result is None
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: income_urgency, max_commute_km."

    def test_includes_round3_when_condition_met(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=2, consent=True,
            profile={"location": "Hubli"},
        )
        nlu = _make_nlu("profile_answer", {"education_level": "iti"})
        result = self._call_gate(bundle, nlu)
        assert result is None
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: trade_or_stream, years_experience."

    def test_hard_min_met_after_all_rounds_goes_to_market_truth(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=5, consent=True,
            profile={"trade_or_stream": "electrician", "location": "Hubli"},
        )
        nlu = _make_nlu("profile_answer", {})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — falls through to LLM so ONEST lookup happens this turn.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "market_truth")
        assert bundle.session["current_node"] == "market_truth"

    def test_hard_min_not_met_goes_to_grace_turn(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=5, consent=True,
            profile={"location": "Hubli"},  # trade_or_stream missing
        )
        nlu = _make_nlu("profile_answer", {})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will ask grace question in user's language.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "grace_turn")
        assert bundle.session["current_node"] == "grace_turn"
        assert bundle.session["current_question"] == "We must collect the following final details before proceeding: trade_or_stream."

    def test_hard_min_met_via_just_extracted_goes_to_market_truth(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=5, consent=True,
            profile={"location": "Hubli"},
        )
        nlu = _make_nlu("profile_answer", {"trade_or_stream": "plumber"})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — falls through to LLM for ONEST auto-trigger.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "market_truth")
        assert bundle.session["current_node"] == "market_truth"


# ---------------------------------------------------------------------------
# Grace turn
# ---------------------------------------------------------------------------

class TestGraceTurn:
    def setup_method(self):
        self.orch, self.mem = _make_orchestrator()

    def _call_gate(self, bundle, nlu):
        return self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )

    def test_extracts_entities_and_advances_to_market_truth(self):
        bundle = _make_bundle(current_node="grace_turn", consent=True)
        nlu = _make_nlu("profile_answer", {"trade_or_stream": "mason", "location": "Dharwad"})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — falls through to LLM for ONEST auto-trigger.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "persistent", "trade_or_stream", "mason")
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "persistent", "location",        "Dharwad")
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session",    "current_node",    "market_truth")

    def test_no_entities_still_advances_to_market_truth(self):
        bundle = _make_bundle(current_node="grace_turn", consent=True)
        nlu = _make_nlu("unknown", {})
        result = self._call_gate(bundle, nlu)
        # Gate returns None — falls through to LLM for ONEST auto-trigger.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "market_truth")

    def test_session_scope_when_consent_false(self):
        bundle = _make_bundle(current_node="grace_turn", consent=False)
        nlu = _make_nlu("profile_answer", {"trade_or_stream": "welder"})
        self._call_gate(bundle, nlu)
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "trade_or_stream", "welder")


# ---------------------------------------------------------------------------
# Returning user resume
# ---------------------------------------------------------------------------

class TestReturningUserResume:
    def setup_method(self):
        self.orch, self.mem = _make_orchestrator()

    def _call_gate(self, bundle, nlu):
        return self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )

    def test_returning_with_full_profile_resumes_market_truth_passes_through(self):
        bundle = _make_bundle(
            current_node="awaiting_consent",
            is_returning=True,
            profile={"consent_flag": True, "trade_or_stream": "electrician", "location": "Hubli"},
        )
        nlu = _make_nlu("market_truth_query")
        result = self._call_gate(bundle, nlu)
        # Gate returns None (falls through to LLM) because node=market_truth
        assert result is None

    def test_returning_missing_hard_min_resumes_profile_building(self):
        bundle = _make_bundle(
            current_node="awaiting_consent",
            is_returning=True,
            profile={"consent_flag": True},  # no trade_or_stream, no location
        )
        nlu = _make_nlu("greeting_intent")
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will ask round 1 question in user's language.
        assert result is None
        self.mem.write.assert_any_call(SESSION_ID, USER_ID, "session", "current_node", "profile_building")
        assert bundle.session["current_question"] == "Please ask the user to provide ALL of the following details in a SINGLE conversational question: name, age_bracket, location."

    def test_returning_no_consent_flag_asks_consent(self):
        bundle = _make_bundle(
            current_node="awaiting_consent",
            is_returning=True,
            profile={},  # no consent_flag
        )
        nlu = _make_nlu("greeting_intent")
        result = self._call_gate(bundle, nlu)
        # Gate returns None — LLM will ask consent question in user's language.
        assert result is None
        assert bundle.session["current_question"] == BASE_CONFIG["conversation"]["consent_message"]


# ---------------------------------------------------------------------------
# Step 5b bypass: profile nodes skip unknown-intent early exit
# ---------------------------------------------------------------------------

class TestStep5bBypass:
    """
    Verify that process_turn does NOT early-exit on unknown/low-confidence intent
    when current_node is a profile collection node.
    The gate result should be returned instead.
    """

    def setup_method(self):
        self.orch, self.mem = _make_orchestrator()

    def _run_turn(self, bundle: ContextBundle, nlu_intent: str, confidence: float = 0.1):
        turn_input = _make_turn_input("mumble")
        # Patch memory.context_bundle to return our bundle
        self.mem.context_bundle.return_value = bundle
        # Patch language normaliser and NLU processor
        with (
            patch.object(self.orch._language_normaliser, "normalise",
                         return_value=("mumble", "hindi")),
            patch.object(self.orch._nlu_processor, "process",
                         return_value=_make_nlu(nlu_intent, confidence=confidence)),
        ):
            return self.orch.process_turn(turn_input)

    def test_awaiting_consent_bypasses_unknown_exit(self):
        bundle = _make_bundle(current_node="awaiting_consent", current_question="")
        result = self._run_turn(bundle, "unknown", confidence=0.1)
        # Gate falls through to LLM (not unknown_intent early exit) — LLM mock returns "LLM response".
        assert result.response_text != BASE_CONFIG["conversation"]["unknown_intent_message"]
        assert result.response_text == "LLM response"

    def test_profile_building_bypasses_unknown_exit(self):
        bundle = _make_bundle(
            current_node="profile_building", collection_round=1, consent=True,
            current_question="Please ask the user to provide ALL of the following details in a SINGLE conversational question: name, age_bracket, location.",
        )
        result = self._run_turn(bundle, "unknown", confidence=0.1)
        # Gate falls through to LLM — not unknown_intent_message
        assert result.response_text != BASE_CONFIG["conversation"]["unknown_intent_message"]

    def test_grace_turn_bypasses_unknown_exit(self):
        bundle = _make_bundle(
            current_node="grace_turn", consent=True,
            current_question="Trade and location?",
        )
        result = self._run_turn(bundle, "unknown", confidence=0.1)
        # Grace turn transitions to market_truth and falls through to LLM (for ONEST auto-trigger).
        # Unknown-intent early exit is bypassed (gate node). LLM mock returns "LLM response".
        assert result.response_text != BASE_CONFIG["conversation"]["unknown_intent_message"]
        assert result.response_text == "LLM response"

    def test_market_truth_does_not_bypass_unknown_exit(self):
        bundle = _make_bundle(current_node="market_truth")
        result = self._run_turn(bundle, "unknown", confidence=0.1)
        # Not a profile node — unknown intent early exit applies
        assert result.response_text == BASE_CONFIG["conversation"]["unknown_intent_message"]


# ---------------------------------------------------------------------------
# Workflow gate returns None for non-profile nodes (pass-through to LLM)
# ---------------------------------------------------------------------------

class TestWorkflowGatePassThrough:
    def setup_method(self):
        self.orch, _ = _make_orchestrator()

    def _call_gate(self, node: str):
        bundle = _make_bundle(current_node=node)
        nlu    = _make_nlu("market_truth_query")
        return self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )

    def test_market_truth_passes_through(self):
        assert self._call_gate("market_truth") is None

    def test_evaluation_passes_through(self):
        assert self._call_gate("evaluation") is None

    def test_commitment_passes_through(self):
        assert self._call_gate("commitment") is None

    def test_empty_node_passes_through(self):
        # awaiting_consent, first visit: gate sets current_question in-place and returns None
        # (falls through to LLM for language-aware consent question delivery).
        bundle = _make_bundle(current_node="awaiting_consent", current_question="")
        nlu = _make_nlu("profile_answer")
        result = self.orch._workflow_gate(
            session_id=SESSION_ID, user_id=USER_ID,
            bundle=bundle, nlu_result=nlu,
            turn_input=_make_turn_input(), start=time.time(),
            trust_input=TrustCheckResult(passed=True, action="allow"),
            detected_language="hindi",
        )
        # Gate returns None — consent question set in bundle.session for LLM to use.
        assert result is None
        assert bundle.session["current_question"] == BASE_CONFIG["conversation"]["consent_message"]
