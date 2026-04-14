"""
agent_core/tests/test_nlu_processor.py

Unit tests for NLUProcessor (agent_core/src/nlu_processor.py).
All LLM calls are mocked — no real API calls.

Coverage:
- Normal: various intents classified correctly
- Normal: entities extracted from message
- Normal: sentiment detected
- Normal: confidence value returned
- Normal: model_override passed from config to llm.call
- Normal: current_question injected into LLM message for context resolution
- Normal: workflow_step injected into LLM message for context resolution
- Edge: empty current_question and workflow_step do not crash
- Edge: empty input returns NLUResult(intent="unknown", confidence=0.0)
- Edge: invalid intent from LLM falls back to "unknown"
- Edge: non-dict entities from LLM treated as empty dict
- Failure: LLM returns error stop_reason → fallback NLUResult
- Failure: LLM raises exception → fallback NLUResult
- Failure: malformed JSON → fallback NLUResult
- Failure: JSON in prose still parsed correctly
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from src.preprocessing.nlu_processor import NLUProcessor
from src.models import NLUResult, LLMResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "preprocessing": {
        "nlu_processor": {
            "model": "claude-haiku-4-5-20251001",
            "confidence_threshold": 0.5,
            "intents": [
                "greeting_intent", "profile_answer",
                "market_truth_query", "scheme_query", "training_query",
                "apply_now", "counsellor_request", "pay_range_query",
                "termination_intent", "unknown",
            ],
            "entities": ["trade", "location", "distance_km", "income_urgency"],
            "sentiment_classes": ["neutral", "positive", "distressed", "frustrated"],
        }
    }
}


def make_llm_returning(intent="unknown", entities=None, sentiment="neutral", confidence=0.9) -> MagicMock:
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=json.dumps({
            "intent": intent,
            "entities": entities or {},
            "sentiment": sentiment,
            "confidence": confidence,
        }),
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
    )
    return llm


@pytest.fixture
def processor():
    return NLUProcessor(CONFIG)


# ---------------------------------------------------------------------------
# Normal execution — intent classification
# ---------------------------------------------------------------------------


def test_market_truth_query_classified(processor):
    llm = make_llm_returning(intent="market_truth_query", entities={"location": "Hubli"})
    result = processor.process("kaam chahiye Hubli mein", "", "", llm)
    assert result.intent == "market_truth_query"
    assert result.entities.get("location") == "Hubli"
    assert result.confidence == 0.9


def test_scheme_query_classified(processor):
    llm = make_llm_returning(intent="scheme_query")
    result = processor.process("PMKVY ke baare mein batao", "", "", llm)
    assert result.intent == "scheme_query"


def test_training_query_classified(processor):
    llm = make_llm_returning(intent="training_query", entities={"trade": "electrician"})
    result = processor.process("electrician course kahan hai", "", "", llm)
    assert result.intent == "training_query"
    assert result.entities.get("trade") == "electrician"


def test_apply_now_classified(processor):
    llm = make_llm_returning(intent="apply_now")
    result = processor.process("apply kar do", "", "", llm)
    assert result.intent == "apply_now"


def test_counsellor_request_classified(processor):
    llm = make_llm_returning(intent="counsellor_request")
    result = processor.process("counsellor chahiye", "", "", llm)
    assert result.intent == "counsellor_request"


def test_pay_range_query_classified(processor):
    llm = make_llm_returning(intent="pay_range_query")
    result = processor.process("kitna milega", "", "", llm)
    assert result.intent == "pay_range_query"


def test_distress_sentiment_detected(processor):
    llm = make_llm_returning(intent="unknown", sentiment="distressed")
    result = processor.process("bahut mushkil hai", "", "", llm)
    assert result.sentiment == "distressed"


def test_multiple_entities_extracted(processor):
    llm = make_llm_returning(
        intent="market_truth_query",
        entities={"trade": "welder", "location": "Dharwad"},
    )
    result = processor.process("welder Dharwad mein kaam chahiye", "", "", llm)
    assert result.entities.get("trade") == "welder"
    assert result.entities.get("location") == "Dharwad"


def test_model_override_passed_from_config(processor):
    llm = make_llm_returning(intent="market_truth_query")
    processor.process("kaam chahiye", "", "", llm)
    call_kwargs = llm.call.call_args[1]
    assert call_kwargs.get("model_override") == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Context injection — current_question and workflow_step grounding
# ---------------------------------------------------------------------------


def test_current_question_injected_into_llm_message(processor):
    """NLU injects current_question so follow-up answers are resolved correctly."""
    llm = make_llm_returning(intent="profile_answer")
    processor.process("welder", "Aap kaun sa kaam karte hain?", "profile_collection", llm)
    call_kwargs = llm.call.call_args[1]
    messages_sent = call_kwargs.get("messages", [])
    assert len(messages_sent) == 1
    content = messages_sent[0]["content"]
    assert "Aap kaun sa kaam karte hain?" in content
    assert "welder" in content


def test_workflow_step_injected_into_llm_message(processor):
    """NLU injects workflow_step for context-aware classification."""
    llm = make_llm_returning(intent="profile_answer")
    processor.process("electrician", "", "profile_collection", llm)
    call_kwargs = llm.call.call_args[1]
    messages_sent = call_kwargs.get("messages", [])
    content = messages_sent[0]["content"]
    assert "profile_collection" in content


def test_empty_context_fields_do_not_crash(processor):
    llm = make_llm_returning(intent="market_truth_query")
    result = processor.process("kaam chahiye", "", "", llm)
    assert result.intent == "market_truth_query"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_fallback_without_llm_call(processor):
    llm = MagicMock()
    result = processor.process("", "", "", llm)
    assert result.intent == "unknown"
    assert result.confidence == 0.0
    llm.call.assert_not_called()


def test_invalid_intent_from_llm_falls_back_to_unknown(processor):
    llm = make_llm_returning(intent="completely_invalid_intent", confidence=0.95)
    result = processor.process("some message", "", "", llm)
    assert result.intent == "unknown"


def test_non_dict_entities_treated_as_empty(processor):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=json.dumps({
            "intent": "market_truth_query",
            "entities": "not a dict",
            "sentiment": "neutral",
            "confidence": 0.9,
        }),
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
    )
    result = processor.process("kaam chahiye", "", "", llm)
    assert result.entities == {}


def test_returns_nlu_result_type(processor):
    llm = make_llm_returning(intent="market_truth_query")
    result = processor.process("kaam chahiye", "", "", llm)
    assert isinstance(result, NLUResult)


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_llm_error_stop_reason_returns_fallback(processor):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(content=None, stop_reason="error")
    result = processor.process("kaam chahiye", "", "", llm)
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_llm_exception_returns_fallback_gracefully(processor):
    llm = MagicMock()
    llm.call.side_effect = RuntimeError("network error")
    result = processor.process("kaam chahiye", "", "", llm)
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_malformed_json_returns_fallback(processor):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content="This is not JSON at all",
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
    )
    result = processor.process("kaam chahiye", "", "", llm)
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_json_in_prose_extracted_and_parsed(processor):
    prose = 'Sure! Here is the result: {"intent": "scheme_query", "entities": {}, "sentiment": "neutral", "confidence": 0.85}'
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=prose, stop_reason="end_turn", model_used="claude-haiku-4-5-20251001"
    )
    result = processor.process("PMKVY batao", "", "", llm)
    assert result.intent == "scheme_query"
    assert result.confidence == 0.85


def test_never_raises_on_unexpected_exception(processor):
    """process() must never propagate unexpected exceptions to the caller."""
    llm = MagicMock()
    llm.call.side_effect = Exception("totally unexpected")
    result = processor.process("some input", "", "", llm)
    assert isinstance(result, NLUResult)


# ---------------------------------------------------------------------------
# NLUResult.active_risks field
# ---------------------------------------------------------------------------


def test_nlu_result_active_risks_default_none():
    result = NLUResult(intent="greeting", entities={}, sentiment="neutral", confidence=0.9)
    assert result.active_risks is None


def test_nlu_result_active_risks_set():
    result = NLUResult(
        intent="greeting", entities={}, sentiment="neutral",
        confidence=0.9, active_risks=["false_certainty"]
    )
    assert result.active_risks == ["false_certainty"]


# ---------------------------------------------------------------------------
# Profile-key-aware entity dedup
# ---------------------------------------------------------------------------


def test_existing_profile_keys_injected_into_system_prompt(processor):
    """When existing_profile_keys is provided, the NLU system prompt includes them."""
    llm = make_llm_returning(intent="profile_answer", entities={"location": "Mumbai"})
    processor.process(
        "Mumbai mein kaam chahiye", "", "", llm,
        existing_profile_keys=["name", "location", "trade_or_stream"],
    )
    call_kwargs = llm.call.call_args[1]
    system_prompt = call_kwargs.get("system", "")
    assert "name, location, trade_or_stream" in system_prompt
    assert "reuse that exact field name" in system_prompt


def test_no_profile_keys_uses_fallback_rule(processor):
    """When existing_profile_keys is None, the prompt uses the fallback rule."""
    llm = make_llm_returning(intent="profile_answer")
    processor.process("kaam chahiye", "", "", llm, existing_profile_keys=None)
    call_kwargs = llm.call.call_args[1]
    system_prompt = call_kwargs.get("system", "")
    assert "No existing profile fields" in system_prompt


def test_empty_profile_keys_list_uses_fallback_rule(processor):
    """An empty list is treated the same as None — fallback rule."""
    llm = make_llm_returning(intent="profile_answer")
    processor.process("kaam chahiye", "", "", llm, existing_profile_keys=[])
    call_kwargs = llm.call.call_args[1]
    system_prompt = call_kwargs.get("system", "")
    assert "No existing profile fields" in system_prompt


def test_adhoc_keys_included_in_profile_keys_prompt(processor):
    """Ad-hoc attribute keys from previous sessions appear in the prompt."""
    llm = make_llm_returning(intent="profile_answer", entities={"employer_name": "Reliance"})
    processor.process(
        "I work at Reliance", "", "", llm,
        existing_profile_keys=["name", "location", "employer_name"],
    )
    call_kwargs = llm.call.call_args[1]
    system_prompt = call_kwargs.get("system", "")
    assert "employer_name" in system_prompt


def test_process_without_profile_keys_backward_compatible(processor):
    """Calling process() without existing_profile_keys still works (backward compat)."""
    llm = make_llm_returning(intent="market_truth_query", entities={"location": "Hubli"})
    result = processor.process("kaam chahiye Hubli mein", "", "", llm)
    assert result.intent == "market_truth_query"
    assert result.entities.get("location") == "Hubli"
