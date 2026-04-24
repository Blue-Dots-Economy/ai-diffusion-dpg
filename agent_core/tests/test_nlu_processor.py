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
                "greeting_intent", "evaluate_option",
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
    llm = make_llm_returning(intent="evaluate_option")
    processor.process("welder", "Aap kaun sa kaam karte hain?", "profile_collection", llm)
    call_kwargs = llm.call.call_args[1]
    messages_sent = call_kwargs.get("messages", [])
    assert len(messages_sent) == 1
    content = messages_sent[0]["content"]
    assert "Aap kaun sa kaam karte hain?" in content
    assert "welder" in content


def test_workflow_step_injected_into_llm_message(processor):
    """NLU injects workflow_step for context-aware classification."""
    llm = make_llm_returning(intent="evaluate_option")
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


def _system_text(call_kwargs) -> str:
    """Extract the system prompt text whether it's a string or a list of blocks."""
    sys_payload = call_kwargs.get("system", "")
    if isinstance(sys_payload, list):
        return "\n".join(b.get("text", "") for b in sys_payload)
    return sys_payload


def _user_message_text(call_kwargs) -> str:
    """Extract the first user message content (GH-195 — dynamic context lives here)."""
    msgs = call_kwargs.get("messages", [])
    if not msgs:
        return ""
    return msgs[0].get("content", "")


def test_existing_profile_keys_injected_into_user_message(processor):
    """GH-195 — existing_profile_keys is per-turn dynamic; it must live in the
    USER message, not in the (cached) system prompt."""
    llm = make_llm_returning(intent="evaluate_option", entities={"location": "Mumbai"})
    processor.process(
        "Mumbai mein kaam chahiye", "", "", llm,
        existing_profile_keys=["name", "location", "trade_or_stream"],
    )
    call_kwargs = llm.call.call_args[1]
    user_msg = _user_message_text(call_kwargs)
    system_text = _system_text(call_kwargs)
    assert "name, location, trade_or_stream" in user_msg
    # Static dedup rule lives in the cached system prompt.
    assert "reuse that exact field name" in system_text
    # The actual dynamic list must NOT appear in the cached portion.
    assert "name, location, trade_or_stream" not in system_text


def test_no_profile_keys_omits_user_line(processor):
    """When no profile keys are available, no dedicated line is added to the user message."""
    llm = make_llm_returning(intent="evaluate_option")
    processor.process("kaam chahiye", "", "", llm, existing_profile_keys=None)
    call_kwargs = llm.call.call_args[1]
    user_msg = _user_message_text(call_kwargs)
    system_text = _system_text(call_kwargs)
    assert "Existing profile fields" not in user_msg
    # The static rule still describes what to do when no keys are present.
    assert "no existing fields are listed" in system_text.lower()


def test_empty_profile_keys_list_omits_user_line(processor):
    """An empty list is treated the same as None — no Existing profile line."""
    llm = make_llm_returning(intent="evaluate_option")
    processor.process("kaam chahiye", "", "", llm, existing_profile_keys=[])
    call_kwargs = llm.call.call_args[1]
    assert "Existing profile fields" not in _user_message_text(call_kwargs)


def test_adhoc_keys_included_in_user_message(processor):
    """Ad-hoc attribute keys from previous sessions appear in the user message."""
    llm = make_llm_returning(intent="evaluate_option", entities={"employer_name": "Reliance"})
    processor.process(
        "I work at Reliance", "", "", llm,
        existing_profile_keys=["name", "location", "employer_name"],
    )
    call_kwargs = llm.call.call_args[1]
    assert "employer_name" in _user_message_text(call_kwargs)


def test_process_without_profile_keys_backward_compatible(processor):
    """Calling process() without existing_profile_keys still works (backward compat)."""
    llm = make_llm_returning(intent="market_truth_query", entities={"location": "Hubli"})
    result = processor.process("kaam chahiye Hubli mein", "", "", llm)
    assert result.intent == "market_truth_query"
    assert result.entities.get("location") == "Hubli"


# ---------------------------------------------------------------------------
# User-state model — init / config validation (GH-139 Task 3)
# ---------------------------------------------------------------------------

import pytest
from src.exceptions import ConfigurationError


def _base_config(user_state_model=None):
    cfg = {
        "preprocessing": {
            "nlu_processor": {
                "model": "claude-haiku-4-5-20251001",
                "confidence_threshold": 0.5,
                "domain_instruction": "d",
                "intents": ["unknown"],
                "entities": [],
                "sentiment_classes": ["neutral"],
            },
        },
    }
    if user_state_model is not None:
        cfg["conversation"] = {"user_state_model": user_state_model}
    return cfg


def test_nlu_user_state_disabled_by_default():
    p = NLUProcessor(_base_config())
    assert p._user_state_enabled is False
    assert p._user_states == []
    assert p._user_state_threshold == 0.4


def test_nlu_user_state_threshold_read_from_config():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 0.3
    p = NLUProcessor(cfg)
    assert p._user_state_threshold == 0.3


def test_nlu_user_state_enabled_reads_states():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently."},
            {"id": "orientation", "signals": [], "guidance": "Show the map."},
        ],
    }))
    assert p._user_state_enabled is True
    assert {s["id"] for s in p._user_states} == {"fog", "orientation"}
    assert p._user_state_default == "fog"


def test_nlu_user_state_enabled_without_default_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }))


def test_nlu_user_state_enabled_without_states_raises():
    with pytest.raises(ConfigurationError, match="states"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [],
        }))


def test_nlu_user_state_default_not_in_states_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "nonexistent",
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }))


def test_nlu_user_state_duplicate_ids_raise():
    with pytest.raises(ConfigurationError, match="unique"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "signals": [], "guidance": "g1"},
                {"id": "fog", "signals": [], "guidance": "g2"},
            ],
        }))


def test_nlu_user_state_empty_guidance_raises():
    with pytest.raises(ConfigurationError, match="guidance"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [{"id": "fog", "signals": [], "guidance": ""}],
        }))


def test_nlu_user_state_threshold_out_of_range_raises():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 1.5
    with pytest.raises(ConfigurationError, match="user_state_confidence_threshold"):
        NLUProcessor(cfg)


# ---------------------------------------------------------------------------
# User-state model — process() integration (GH-139 Task 4)
# ---------------------------------------------------------------------------

from src.models import LLMResponse, UserStateClassification


def _enabled_processor():
    return NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }))


def _disabled_processor():
    return NLUProcessor(_base_config())


def _mock_llm(payload_json: str):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=payload_json, stop_reason="end_turn", model_used="haiku",
    )
    return llm


def test_process_returns_user_state_when_enabled_and_valid():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.82}}'
    )
    result = p.process(
        normalised_input="kitna pay hai",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"
    assert abs(result.user_state.confidence - 0.82) < 1e-6


def test_process_sticky_when_below_threshold():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.2}}'
    )
    result = p.process(
        normalised_input="hmm",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"
    assert result.user_state.confidence == 0.2


def test_process_sticky_when_id_unknown():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"gibberish","confidence":0.95}}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"


def test_process_sticky_when_key_missing():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="orientation",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"


def test_process_returns_none_when_disabled():
    p = _disabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state=None,
    )
    assert result.user_state is None


def test_process_prompt_includes_state_section_when_enabled():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"fog","confidence":0.9}}'
    )
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    call_kwargs = llm.call.call_args.kwargs
    system_prompt = _system_text(call_kwargs)
    user_msg = _user_message_text(call_kwargs)
    assert "User mental state classification" in system_prompt
    # State IDs are part of the static prompt (cacheable)...
    assert "fog" in system_prompt
    assert "orientation" in system_prompt
    # ...but the per-turn previous state is dynamic and lives in the user
    # message, not in the cached system prefix (GH-195).
    assert "Previous mental state: fog" in user_msg
    assert "Previous state: fog" not in system_prompt


def test_process_prompt_excludes_state_section_when_disabled():
    p = _disabled_processor()
    llm = _mock_llm('{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}')
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state=None,
    )
    call_kwargs = llm.call.call_args.kwargs
    system_prompt = _system_text(call_kwargs)
    assert "User mental state classification" not in system_prompt


# ---------------------------------------------------------------------------
# GH-195 — prompt-cache fix
# ---------------------------------------------------------------------------


def test_system_prompt_sent_as_cache_controlled_list(processor):
    """The NLU system prompt must be a list with a cache_control marker so
    Anthropic's prompt cache activates from turn 2."""
    llm = make_llm_returning(intent="evaluate_option")
    processor.process("some input", "", "", llm)
    call_kwargs = llm.call.call_args.kwargs
    sys_payload = call_kwargs["system"]
    assert isinstance(sys_payload, list), (
        "system must be a list of content blocks for prompt caching"
    )
    assert len(sys_payload) >= 1
    first = sys_payload[0]
    assert first.get("type") == "text"
    assert first.get("cache_control") == {"type": "ephemeral"}
    assert first.get("text")  # non-empty


def test_cached_system_prompt_has_no_per_turn_dynamic_values(processor):
    """Guardrail: values that change per turn must not leak into the cached
    system prefix, or the cache key changes every turn (GH-195 root cause)."""
    llm = make_llm_returning(intent="evaluate_option")
    processor.process(
        "some input",
        current_question="",
        current_subagent_id="",
        llm=llm,
        existing_profile_keys=["name", "trade_or_stream", "dynamic_hobby_xyz"],
    )
    call_kwargs = llm.call.call_args.kwargs
    system_text = _system_text(call_kwargs)
    # Dynamic profile-key list must NOT appear in the cached block.
    assert "dynamic_hobby_xyz" not in system_text
    assert "trade_or_stream" not in system_text
    # ...it must appear in the user message.
    assert "dynamic_hobby_xyz" in _user_message_text(call_kwargs)


def test_user_state_previous_state_not_in_cached_prompt():
    """`previous_user_state` changes turn-to-turn and must live in the user message."""
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": [], "guidance": "g1"},
            {"id": "orientation", "signals": [], "guidance": "g2"},
        ],
    }))
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.9}}'
    )
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="orientation",
    )
    call_kwargs = llm.call.call_args.kwargs
    system_text = _system_text(call_kwargs)
    user_msg = _user_message_text(call_kwargs)
    assert "Previous mental state: orientation" in user_msg
    assert "Previous state: orientation" not in system_text
    assert "Previous mental state: orientation" not in system_text


def test_prompt_cache_disabled_sends_plain_string():
    """When prompt_cache_enabled=false, the system prompt is a plain string —
    allows debugging / opt-out without editing code."""
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["prompt_cache_enabled"] = False
    p = NLUProcessor(cfg)
    llm = _mock_llm('{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}')
    p.process("x", "", "", llm)
    sys_payload = llm.call.call_args.kwargs["system"]
    assert isinstance(sys_payload, str)


def test_cache_usage_tokens_logged_on_success(processor, caplog):
    """GH-195 — cache_read_input_tokens and cache_creation_input_tokens must
    appear as structured log fields so ops can verify the cache is hitting."""
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=json.dumps({
            "intent": "market_truth_query",
            "entities": {},
            "sentiment": "neutral",
            "confidence": 0.9,
        }),
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=1500,
        cache_creation_input_tokens=0,
    )
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="src.preprocessing.nlu_processor"):
        processor.process("kaam chahiye", "", "", llm)
    matches = [r for r in caplog.records if r.message == "nlu_processor.process"]
    assert matches, "no nlu_processor.process log record found"
    rec = matches[-1]
    assert getattr(rec, "cache_read_input_tokens", None) == 1500
    assert getattr(rec, "cache_creation_input_tokens", None) == 0
    assert getattr(rec, "prompt_cache_enabled", None) is True


def test_cache_marker_is_stable_across_turns(processor):
    """Cache key = static text before the cache_control marker. Two calls with
    different per-turn inputs must produce the EXACT same cached block text."""
    llm1 = make_llm_returning(intent="evaluate_option")
    processor.process(
        "turn 1 input", current_question="q1", current_subagent_id="sub_a",
        llm=llm1, existing_profile_keys=["a"], previous_user_state="fog",
    )
    llm2 = make_llm_returning(intent="evaluate_option")
    processor.process(
        "turn 2 input", current_question="q2", current_subagent_id="sub_a",
        llm=llm2, existing_profile_keys=["a", "b", "c"], previous_user_state="orientation",
    )
    sys1 = llm1.call.call_args.kwargs["system"]
    sys2 = llm2.call.call_args.kwargs["system"]
    # Same subagent → same allowed_intents → cached block must be byte-identical.
    assert sys1 == sys2
