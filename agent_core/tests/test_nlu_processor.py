"""
agent_core/tests/test_nlu_processor.py

Unit tests for NLUProcessor (agent_core/src/nlu_processor.py).
All LLM calls are mocked — no real API calls.

Coverage:
- Normal: various intents classified correctly
- Normal: entities extracted from message
- Normal: sentiment detected
- Normal: confidence value returned
- Normal: chat_provider.call() invoked (model owned by provider, not per-call override)
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

from src.chat_provider.base import Capabilities, ChatProviderBase
from src.chat_provider.types import (
    ChatRequest, ChatResponse, Message, SystemPrompt, TextBlock, TokenUsage
)


# Default capabilities for mocked providers — match Anthropic's intrinsic flags
# so tests exercise the cache-hint code path. Override per-test for OpenAI-shaped
# (supports_prompt_cache=False) coverage.
_DEFAULT_TEST_CAPS = Capabilities(
    supports_tools=True,
    supports_streaming=True,
    supports_prompt_cache=True,
    supports_image_input=True,
    supports_audio_input=False,
    supports_structured_output=True,
    supports_force_tool_choice=True,
)
from src.preprocessing.nlu_processor import NLUProcessor
from src.models import NLUResult


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


def _make_chat_response(intent="unknown", entities=None, sentiment="neutral",
                        confidence=0.9, stop_reason="end_turn") -> ChatResponse:
    text = json.dumps({
        "intent": intent,
        "entities": entities or {},
        "sentiment": sentiment,
        "confidence": confidence,
    })
    return ChatResponse(
        content=[TextBlock(text=text)],
        stop_reason=stop_reason,
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def make_provider_returning(intent="unknown", entities=None, sentiment="neutral",
                            confidence=0.9, capabilities: Capabilities | None = None) -> MagicMock:
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = capabilities or _DEFAULT_TEST_CAPS
    provider.call.return_value = _make_chat_response(
        intent=intent, entities=entities, sentiment=sentiment, confidence=confidence
    )
    return provider


@pytest.fixture
def mock_provider():
    return make_provider_returning()


@pytest.fixture
def processor(mock_provider):
    return NLUProcessor(CONFIG, chat_provider=mock_provider)


# ---------------------------------------------------------------------------
# Normal execution — intent classification
# ---------------------------------------------------------------------------


def test_market_truth_query_classified():
    provider = make_provider_returning(intent="market_truth_query", entities={"location": "Hubli"})
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye Hubli mein", "", "")
    assert result.intent == "market_truth_query"
    assert result.entities.get("location") == "Hubli"
    assert result.confidence == 0.9


def test_scheme_query_classified():
    provider = make_provider_returning(intent="scheme_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("PMKVY ke baare mein batao", "", "")
    assert result.intent == "scheme_query"


def test_training_query_classified():
    provider = make_provider_returning(intent="training_query", entities={"trade": "electrician"})
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("electrician course kahan hai", "", "")
    assert result.intent == "training_query"
    assert result.entities.get("trade") == "electrician"


def test_apply_now_classified():
    provider = make_provider_returning(intent="apply_now")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("apply kar do", "", "")
    assert result.intent == "apply_now"


def test_counsellor_request_classified():
    provider = make_provider_returning(intent="counsellor_request")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("counsellor chahiye", "", "")
    assert result.intent == "counsellor_request"


def test_pay_range_query_classified():
    provider = make_provider_returning(intent="pay_range_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kitna milega", "", "")
    assert result.intent == "pay_range_query"


def test_distress_sentiment_detected():
    provider = make_provider_returning(intent="unknown", sentiment="distressed")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("bahut mushkil hai", "", "")
    assert result.sentiment == "distressed"


def test_multiple_entities_extracted():
    provider = make_provider_returning(
        intent="market_truth_query",
        entities={"trade": "welder", "location": "Dharwad"},
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("welder Dharwad mein kaam chahiye", "", "")
    assert result.entities.get("trade") == "welder"
    assert result.entities.get("location") == "Dharwad"


def test_provider_call_invoked(processor, mock_provider):
    """chat_provider.call() must be invoked with a ChatRequest (no model_override kwarg)."""
    processor.process("kaam chahiye", "", "")
    mock_provider.call.assert_called_once()
    request = mock_provider.call.call_args.args[0]
    assert isinstance(request, ChatRequest)


def test_cache_hint_set_when_provider_supports_caching():
    """Caching-capable provider → NLU emits cache_hint='session' on the system block."""
    provider = make_provider_returning(intent="market_truth_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process("kaam chahiye", "", "")
    request = provider.call.call_args.args[0]
    assert request.system.blocks[0].cache_hint == "session"


def test_cache_hint_omitted_when_provider_does_not_support_caching():
    """Regression for the OpenAI deployment bug: when supports_prompt_cache=False
    NLU must NOT set cache_hint, otherwise _validate_request raises and every NLU
    turn returns the fallback NLUResult.
    """
    no_cache_caps = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )
    provider = make_provider_returning(intent="market_truth_query", capabilities=no_cache_caps)
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    # No NLU error — request shape is provider-compatible.
    assert result.intent == "market_truth_query"
    request = provider.call.call_args.args[0]
    assert request.system.blocks[0].cache_hint is None


# ---------------------------------------------------------------------------
# Context injection — current_question and workflow_step grounding
# ---------------------------------------------------------------------------


def test_current_question_injected_into_llm_message():
    """NLU injects current_question so follow-up answers are resolved correctly."""
    provider = make_provider_returning(intent="evaluate_option")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process("welder", "Aap kaun sa kaam karte hain?", "profile_collection")
    request: ChatRequest = provider.call.call_args.args[0]
    user_content = request.messages[0].content[0].text
    assert "Aap kaun sa kaam karte hain?" in user_content
    assert "welder" in user_content


def test_workflow_step_injected_into_llm_message():
    """NLU injects workflow_step for context-aware classification."""
    provider = make_provider_returning(intent="evaluate_option")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process("electrician", "", "profile_collection")
    request: ChatRequest = provider.call.call_args.args[0]
    user_content = request.messages[0].content[0].text
    assert "profile_collection" in user_content


def test_empty_context_fields_do_not_crash():
    provider = make_provider_returning(intent="market_truth_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert result.intent == "market_truth_query"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_fallback_without_llm_call():
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("", "", "")
    assert result.intent == "unknown"
    assert result.confidence == 0.0
    provider.call.assert_not_called()


def test_invalid_intent_from_llm_falls_back_to_unknown():
    provider = make_provider_returning(intent="completely_invalid_intent", confidence=0.95)
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("some message", "", "")
    assert result.intent == "unknown"


def test_non_dict_entities_treated_as_empty():
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text=json.dumps({
            "intent": "market_truth_query",
            "entities": "not a dict",
            "sentiment": "neutral",
            "confidence": 0.9,
        }))],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert result.entities == {}


def test_returns_nlu_result_type():
    provider = make_provider_returning(intent="market_truth_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert isinstance(result, NLUResult)


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_llm_error_stop_reason_returns_fallback():
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(),
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_llm_exception_returns_fallback_gracefully():
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.side_effect = RuntimeError("network error")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_malformed_json_returns_fallback():
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text="This is not JSON at all")],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye", "", "")
    assert result.intent == "unknown"
    assert result.confidence == 0.0


def test_json_in_prose_extracted_and_parsed():
    prose = 'Sure! Here is the result: {"intent": "scheme_query", "entities": {}, "sentiment": "neutral", "confidence": 0.85}'
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text=prose)],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("PMKVY batao", "", "")
    assert result.intent == "scheme_query"
    assert result.confidence == 0.85


def test_never_raises_on_unexpected_exception():
    """process() must never propagate unexpected exceptions to the caller."""
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.side_effect = Exception("totally unexpected")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("some input", "", "")
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


def _system_text(request: ChatRequest) -> str:
    """Extract the system prompt text from a ChatRequest."""
    if request.system is None:
        return ""
    return "\n".join(b.text for b in request.system.blocks)


def _user_message_text(request: ChatRequest) -> str:
    """Extract the first user message content from a ChatRequest."""
    if not request.messages:
        return ""
    content = request.messages[0].content
    if not content:
        return ""
    block = content[0]
    return block.text if hasattr(block, "text") else ""


def test_existing_profile_keys_injected_into_user_message():
    """GH-195 — existing_profile_keys is per-turn dynamic; it must live in the
    USER message, not in the (cached) system prompt."""
    provider = make_provider_returning(intent="evaluate_option", entities={"location": "Mumbai"})
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process(
        "Mumbai mein kaam chahiye", "", "",
        existing_profile_keys=["name", "location", "trade_or_stream"],
    )
    request: ChatRequest = provider.call.call_args.args[0]
    user_msg = _user_message_text(request)
    system_text = _system_text(request)
    assert "name, location, trade_or_stream" in user_msg
    # Static dedup rule lives in the cached system prompt.
    assert "reuse that exact field name" in system_text
    # The actual dynamic list must NOT appear in the cached portion.
    assert "name, location, trade_or_stream" not in system_text


def test_no_profile_keys_omits_user_line():
    """When no profile keys are available, no dedicated line is added to the user message."""
    provider = make_provider_returning(intent="evaluate_option")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process("kaam chahiye", "", "", existing_profile_keys=None)
    request: ChatRequest = provider.call.call_args.args[0]
    user_msg = _user_message_text(request)
    system_text = _system_text(request)
    assert "Existing profile fields" not in user_msg
    # The static rule still describes what to do when no keys are present.
    assert "no existing fields are listed" in system_text.lower()


def test_empty_profile_keys_list_omits_user_line():
    """An empty list is treated the same as None — no Existing profile line."""
    provider = make_provider_returning(intent="evaluate_option")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process("kaam chahiye", "", "", existing_profile_keys=[])
    request: ChatRequest = provider.call.call_args.args[0]
    assert "Existing profile fields" not in _user_message_text(request)


def test_adhoc_keys_included_in_user_message():
    """Ad-hoc attribute keys from previous sessions appear in the user message."""
    provider = make_provider_returning(intent="evaluate_option", entities={"employer_name": "Reliance"})
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    proc.process(
        "I work at Reliance", "", "",
        existing_profile_keys=["name", "location", "employer_name"],
    )
    request: ChatRequest = provider.call.call_args.args[0]
    assert "employer_name" in _user_message_text(request)


def test_process_without_profile_keys_backward_compatible():
    """Calling process() without existing_profile_keys still works (backward compat)."""
    provider = make_provider_returning(intent="market_truth_query", entities={"location": "Hubli"})
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    result = proc.process("kaam chahiye Hubli mein", "", "")
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


def _make_provider():
    return MagicMock(spec=ChatProviderBase)


def test_nlu_user_state_disabled_by_default():
    p = NLUProcessor(_base_config(), chat_provider=_make_provider())
    assert p._user_state_enabled is False
    assert p._user_states == []
    assert p._user_state_threshold == 0.4


def test_nlu_user_state_threshold_read_from_config():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 0.3
    p = NLUProcessor(cfg, chat_provider=_make_provider())
    assert p._user_state_threshold == 0.3


def test_nlu_user_state_enabled_reads_states():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently."},
            {"id": "orientation", "signals": [], "guidance": "Show the map."},
        ],
    }), chat_provider=_make_provider())
    assert p._user_state_enabled is True
    assert {s["id"] for s in p._user_states} == {"fog", "orientation"}
    assert p._user_state_default == "fog"


def test_nlu_user_state_enabled_without_default_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }), chat_provider=_make_provider())


def test_nlu_user_state_enabled_without_states_raises():
    with pytest.raises(ConfigurationError, match="states"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [],
        }), chat_provider=_make_provider())


def test_nlu_user_state_default_not_in_states_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "nonexistent",
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }), chat_provider=_make_provider())


def test_nlu_user_state_duplicate_ids_raise():
    with pytest.raises(ConfigurationError, match="unique"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "signals": [], "guidance": "g1"},
                {"id": "fog", "signals": [], "guidance": "g2"},
            ],
        }), chat_provider=_make_provider())


def test_nlu_user_state_empty_guidance_raises():
    with pytest.raises(ConfigurationError, match="guidance"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [{"id": "fog", "signals": [], "guidance": ""}],
        }), chat_provider=_make_provider())


def test_nlu_user_state_threshold_out_of_range_raises():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 1.5
    with pytest.raises(ConfigurationError, match="user_state_confidence_threshold"):
        NLUProcessor(cfg, chat_provider=_make_provider())


# ---------------------------------------------------------------------------
# User-state model — process() integration (GH-139 Task 4)
# ---------------------------------------------------------------------------

from src.models import UserStateClassification


def _enabled_processor():
    return NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=_make_provider())


def _disabled_processor():
    return NLUProcessor(_base_config(), chat_provider=_make_provider())


def _mock_provider_with_payload(payload_json: str) -> MagicMock:
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text=payload_json)],
        stop_reason="end_turn",
        model_used="haiku",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    return provider


def test_process_returns_user_state_when_enabled_and_valid():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=_mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.82}}'
    ))
    result = p.process(
        normalised_input="kitna pay hai",
        current_question="",
        current_subagent_id="main",
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"
    assert abs(result.user_state.confidence - 0.82) < 1e-6


def test_process_sticky_when_below_threshold():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=_mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.2}}'
    ))
    result = p.process(
        normalised_input="hmm",
        current_question="",
        current_subagent_id="main",
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"
    assert result.user_state.confidence == 0.2


def test_process_sticky_when_id_unknown():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=_mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"gibberish","confidence":0.95}}'
    ))
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"


def test_process_sticky_when_key_missing():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=_mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    ))
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state="orientation",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"


def test_process_returns_none_when_disabled():
    p = NLUProcessor(_base_config(), chat_provider=_mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    ))
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state=None,
    )
    assert result.user_state is None


def test_process_prompt_includes_state_section_when_enabled():
    provider = _mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"fog","confidence":0.9}}'
    )
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }), chat_provider=provider)
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state="fog",
    )
    request: ChatRequest = provider.call.call_args.args[0]
    system_prompt = _system_text(request)
    user_msg = _user_message_text(request)
    assert "User mental state classification" in system_prompt
    # State IDs are part of the static prompt (cacheable)...
    assert "fog" in system_prompt
    assert "orientation" in system_prompt
    # ...but the per-turn previous state is dynamic and lives in the user
    # message, not in the cached system prefix (GH-195).
    assert "Previous mental state: fog" in user_msg
    assert "Previous state: fog" not in system_prompt


def test_process_prompt_excludes_state_section_when_disabled():
    provider = _mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    p = NLUProcessor(_base_config(), chat_provider=provider)
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state=None,
    )
    request: ChatRequest = provider.call.call_args.args[0]
    system_prompt = _system_text(request)
    assert "User mental state classification" not in system_prompt


# ---------------------------------------------------------------------------
# GH-195 — prompt-cache fix
# ---------------------------------------------------------------------------


def test_system_prompt_sent_as_system_prompt_with_cache_hint(processor, mock_provider):
    """The NLU system prompt must be a SystemPrompt with a cache_hint so
    Anthropic's prompt cache activates from turn 2."""
    processor.process("some input", "", "")
    request: ChatRequest = mock_provider.call.call_args.args[0]
    assert request.system is not None
    assert isinstance(request.system, SystemPrompt)
    assert len(request.system.blocks) >= 1
    first = request.system.blocks[0]
    assert first.type == "text"
    assert first.cache_hint == "session"
    assert first.text  # non-empty


def test_cached_system_prompt_has_no_per_turn_dynamic_values(processor, mock_provider):
    """Guardrail: values that change per turn must not leak into the cached
    system prefix, or the cache key changes every turn (GH-195 root cause)."""
    processor.process(
        "some input",
        current_question="",
        current_subagent_id="",
        existing_profile_keys=["name", "trade_or_stream", "dynamic_hobby_xyz"],
    )
    request: ChatRequest = mock_provider.call.call_args.args[0]
    system_text = _system_text(request)
    # Dynamic profile-key list must NOT appear in the cached block.
    assert "dynamic_hobby_xyz" not in system_text
    assert "trade_or_stream" not in system_text
    # ...it must appear in the user message.
    assert "dynamic_hobby_xyz" in _user_message_text(request)


def test_user_state_previous_state_not_in_cached_prompt():
    """`previous_user_state` changes turn-to-turn and must live in the user message."""
    provider = _mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.9}}'
    )
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": [], "guidance": "g1"},
            {"id": "orientation", "signals": [], "guidance": "g2"},
        ],
    }), chat_provider=provider)
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        previous_user_state="orientation",
    )
    request: ChatRequest = provider.call.call_args.args[0]
    system_text = _system_text(request)
    user_msg = _user_message_text(request)
    assert "Previous mental state: orientation" in user_msg
    assert "Previous state: orientation" not in system_text
    assert "Previous mental state: orientation" not in system_text


def test_prompt_cache_disabled_sends_system_prompt_without_hint():
    """When prompt_cache_enabled=false, the system prompt has no cache_hint."""
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["prompt_cache_enabled"] = False
    provider = _mock_provider_with_payload(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    p = NLUProcessor(cfg, chat_provider=provider)
    p.process("x", "", "")
    request: ChatRequest = provider.call.call_args.args[0]
    assert request.system is not None
    assert all(b.cache_hint is None for b in request.system.blocks)


def test_cache_usage_tokens_logged_on_success(caplog):
    """GH-195 — cache_read_input_tokens and cache_creation_input_tokens must
    appear as structured log fields so ops can verify the cache is hitting."""
    provider = MagicMock(spec=ChatProviderBase)
    provider.capabilities = _DEFAULT_TEST_CAPS
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text=json.dumps({
            "intent": "market_truth_query",
            "entities": {},
            "sentiment": "neutral",
            "confidence": 0.9,
        }))],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=1500,
            cache_creation_tokens=0,
        ),
    )
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="src.preprocessing.nlu_processor"):
        proc.process("kaam chahiye", "", "")
    matches = [r for r in caplog.records if r.message == "nlu_processor.process"]
    assert matches, "no nlu_processor.process log record found"
    rec = matches[-1]
    assert getattr(rec, "cache_read_input_tokens", None) == 1500
    assert getattr(rec, "cache_creation_input_tokens", None) == 0
    assert getattr(rec, "prompt_cache_enabled", None) is True


def test_cache_marker_is_stable_across_turns():
    """Cache key = static text before the cache_hint marker. Two calls with
    different per-turn inputs must produce the EXACT same cached block text."""
    provider1 = make_provider_returning(intent="evaluate_option")
    proc = NLUProcessor(CONFIG, chat_provider=provider1)
    proc.process(
        "turn 1 input", current_question="q1", current_subagent_id="sub_a",
        existing_profile_keys=["a"], previous_user_state="fog",
    )

    provider2 = make_provider_returning(intent="evaluate_option")
    proc2 = NLUProcessor(CONFIG, chat_provider=provider2)
    proc2.process(
        "turn 2 input", current_question="q2", current_subagent_id="sub_a",
        existing_profile_keys=["a", "b", "c"], previous_user_state="orientation",
    )
    req1: ChatRequest = provider1.call.call_args.args[0]
    req2: ChatRequest = provider2.call.call_args.args[0]
    # Same subagent → same allowed_intents → cached block text must be byte-identical.
    assert _system_text(req1) == _system_text(req2)


# ---------------------------------------------------------------------------
# GH-218: triage log
# ---------------------------------------------------------------------------


def test_triage_log_emits_safe_summary_by_default(processor, mock_provider, caplog):
    """Default (log_raw_response=False) → triage log present, no raw fields."""
    mock_provider.call.return_value = _make_chat_response(
        intent="market_truth_query",
        entities={"location": "Hubli", "trade": "electrician"},
    )
    with caplog.at_level("INFO"):
        processor.process("kaam chahiye Hubli", "what trade?", "enquiry")
    triage = [r for r in caplog.records if r.message == "nlu_processor.triage"]
    assert len(triage) == 1, "expected exactly one triage log line"
    extra = triage[0].__dict__
    # Safe fields are always present.
    assert extra["intent"] == "market_truth_query"
    assert extra["entity_keys"] == ["location", "trade"]
    assert extra["user_message_chars"] > 0
    assert len(extra["user_message_sha256_prefix"]) == 12
    assert extra["raw_response_chars"] > 0
    # Raw fields opt-in only.
    assert "parsed_response" not in extra
    assert "user_message" not in extra


def test_triage_log_emits_raw_when_enabled(caplog):
    """log_raw_response=True → parsed_response and user_message appear in the log."""
    cfg = {
        "preprocessing": {
            "nlu_processor": {
                **CONFIG["preprocessing"]["nlu_processor"],
                "log_raw_response": True,
            }
        }
    }
    provider = make_provider_returning(
        intent="market_truth_query", entities={"location": "Hubli"}
    )
    proc = NLUProcessor(cfg, chat_provider=provider)
    with caplog.at_level("INFO"):
        proc.process("kaam chahiye Hubli", "what trade?", "enquiry")
    triage = [r for r in caplog.records if r.message == "nlu_processor.triage"]
    assert len(triage) == 1
    extra = triage[0].__dict__
    assert "parsed_response" in extra
    assert "market_truth_query" in extra["parsed_response"]
    assert "user_message" in extra
    assert "kaam chahiye Hubli" in extra["user_message"]


def test_triage_log_user_message_hash_is_stable(caplog):
    """Same user message text → same sha256 prefix across runs."""
    provider = make_provider_returning(intent="market_truth_query")
    proc = NLUProcessor(CONFIG, chat_provider=provider)
    with caplog.at_level("INFO"):
        proc.process(
            "इलेक्ट्रिशियन का काम है",
            current_question="trade?",
            current_subagent_id="enquiry",
        )
        first = next(r.__dict__["user_message_sha256_prefix"]
                     for r in caplog.records if r.message == "nlu_processor.triage")
    caplog.clear()
    with caplog.at_level("INFO"):
        proc.process(
            "इलेक्ट्रिशियन का काम है",
            current_question="trade?",
            current_subagent_id="enquiry",
        )
        second = next(r.__dict__["user_message_sha256_prefix"]
                      for r in caplog.records if r.message == "nlu_processor.triage")
    assert first == second, "hash should be stable for identical user_message_text"


def test_triage_log_truncates_to_max_chars(caplog):
    """log_raw_response_max_chars caps the parsed_response and user_message fields."""
    cfg = {
        "preprocessing": {
            "nlu_processor": {
                **CONFIG["preprocessing"]["nlu_processor"],
                "log_raw_response": True,
                "log_raw_response_max_chars": 50,
            }
        }
    }
    provider = make_provider_returning(
        intent="market_truth_query",
        entities={"location": "x" * 500},  # forces a long parsed payload
    )
    proc = NLUProcessor(cfg, chat_provider=provider)
    with caplog.at_level("INFO"):
        proc.process("a very long user message " * 10, "q", "enquiry")
    triage = next(r.__dict__ for r in caplog.records if r.message == "nlu_processor.triage")
    assert len(triage["parsed_response"]) <= 50
    assert len(triage["user_message"]) <= 50
