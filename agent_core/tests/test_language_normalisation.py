"""
agent_core/tests/test_language_normalisation.py

Unit tests for LanguageNormaliser (agent_core/src/language_normalisation.py).
All LLM calls are mocked — no real API calls.

Coverage:
- Normal: hindi/hinglish/english/kannada detected and normalised text returned
- Normal: chat_provider.call() invoked (model owned by provider, not per-call override)
- Normal: JSON embedded in prose is still parsed correctly
- Edge: empty input returns (raw_input, "") without calling LLM
- Edge: missing config section uses defaults
- Failure: LLM returns error stop_reason → falls back to (raw_input, "")
- Failure: LLM raises exception → falls back to (raw_input, "")
- Failure: malformed JSON → falls back to (raw_input, "")
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import (
    ChatRequest, ChatResponse, Message, SystemPrompt, TextBlock, TokenUsage
)
from src.preprocessing.language_normalisation import LanguageNormaliser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "preprocessing": {
        "language_normalisation": {
            "model": "claude-haiku-4-5-20251001",
            "provider": "llm_native",
            "supported_languages": ["hindi", "kannada", "english", "hinglish"],
            "transliteration": True,
            "code_switching": True,
        }
    }
}

CONFIG_NO_PREPROCESSING = {}


def _make_chat_response(normalised: str, detected: str) -> ChatResponse:
    return ChatResponse(
        content=[TextBlock(text=json.dumps({
            "detected_language": detected,
            "normalised_text": normalised,
        }))],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def make_provider_returning(normalised: str, detected: str) -> MagicMock:
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.return_value = _make_chat_response(normalised, detected)
    return provider


@pytest.fixture
def normaliser():
    provider = MagicMock(spec=ChatProviderBase)
    return LanguageNormaliser(chat_provider=provider)


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


def test_hindi_input_detected_and_returned():
    provider = make_provider_returning("kaam chahiye mujhe", "hindi")
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("kaam chahiye mujhe", CONFIG)
    assert detected == "hindi"
    assert normalised == "kaam chahiye mujhe"


def test_hinglish_input_detected():
    provider = make_provider_returning("electrician ka kaam chahiye", "hinglish")
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("electrician ka kaam chahiye", CONFIG)
    assert detected == "hinglish"


def test_english_input_detected():
    provider = make_provider_returning("I want electrician work", "english")
    n = LanguageNormaliser(chat_provider=provider)
    _, detected = n.normalise("I want electrician work", CONFIG)
    assert detected == "english"


def test_kannada_input_detected():
    provider = make_provider_returning("ನಾನು ವಿದ್ಯುತ್ ತಂತ್ರಜ್ಞ", "kannada")
    n = LanguageNormaliser(chat_provider=provider)
    _, detected = n.normalise("ನಾನು ವಿದ್ಯುತ್ ತಂತ್ರಜ್ಞ", CONFIG)
    assert detected == "kannada"


def test_normalised_text_from_llm_returned():
    provider = make_provider_returning("cleaned and normalised text", "english")
    n = LanguageNormaliser(chat_provider=provider)
    normalised, _ = n.normalise("original input text", CONFIG)
    assert normalised == "cleaned and normalised text"


def test_provider_call_invoked():
    """chat_provider.call() must be invoked with a ChatRequest (no model_override kwarg)."""
    provider = make_provider_returning("hello world there", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("hello world there", CONFIG)
    provider.call.assert_called_once()
    request = provider.call.call_args.args[0]
    assert isinstance(request, ChatRequest)


def test_json_embedded_in_prose_parsed():
    """LLM sometimes wraps JSON in prose — must still parse correctly."""
    prose = 'Here is the result: {"detected_language": "hinglish", "normalised_text": "kaam chahiye mujhe"}'
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text=prose)],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("kaam chahiye mujhe", CONFIG)
    assert detected == "hinglish"
    assert normalised == "kaam chahiye mujhe"


def test_missing_preprocessing_config_uses_defaults():
    """No preprocessing section → uses supported_languages default, model owned by provider."""
    provider = make_provider_returning("hello world there", "english")
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("hello world there", CONFIG_NO_PREPROCESSING)
    assert detected == "english"
    assert normalised == "hello world there"
    # No model_override kwarg expected — provider owns the model
    provider.call.assert_called_once()
    request = provider.call.call_args.args[0]
    assert isinstance(request, ChatRequest)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_raw_and_empty_language_without_llm_call():
    provider = MagicMock(spec=ChatProviderBase)
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("", CONFIG)
    assert normalised == ""
    assert detected == ""
    provider.call.assert_not_called()


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_llm_error_stop_reason_falls_back_to_raw_input():
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.return_value = ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(),
    )
    n = LanguageNormaliser(chat_provider=provider)
    original = "kaam chahiye Hubli mein"
    normalised, detected = n.normalise(original, CONFIG)
    assert normalised == original
    assert detected == ""


def test_llm_exception_falls_back_gracefully():
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.side_effect = RuntimeError("network error")
    n = LanguageNormaliser(chat_provider=provider)
    original = "kaam chahiye Hubli mein"
    normalised, detected = n.normalise(original, CONFIG)
    assert normalised == original
    assert detected == ""


def test_malformed_json_falls_back_to_raw_input():
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.return_value = ChatResponse(
        content=[TextBlock(text="not valid json here")],
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    n = LanguageNormaliser(chat_provider=provider)
    original = "kaam chahiye Hubli mein"
    normalised, detected = n.normalise(original, CONFIG)
    assert normalised == original
    assert detected == ""


def test_never_raises_on_unexpected_exception():
    """normalise() must never propagate unexpected exceptions to the caller."""
    provider = MagicMock(spec=ChatProviderBase)
    provider.call.side_effect = Exception("totally unexpected")
    n = LanguageNormaliser(chat_provider=provider)
    result = n.normalise("some input", CONFIG)
    assert isinstance(result, tuple)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# #124 — config-driven prompt, default_language weighting, short-input bypass
# ---------------------------------------------------------------------------

CONFIG_WITH_DEFAULT = {
    "preprocessing": {
        "language_normalisation": {
            "model": "claude-haiku-4-5-20251001",
            "provider": "llm_native",
            "default_language": "hindi",
            "supported_languages": ["hindi", "kannada", "english", "hinglish"],
        }
    }
}


def test_system_prompt_contains_default_language():
    """System prompt sent to LLM must mention the configured default_language."""
    provider = make_provider_returning("hello", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("hello world there", CONFIG_WITH_DEFAULT)
    request: ChatRequest = provider.call.call_args.args[0]
    system_text = "\n".join(b.text for b in request.system.blocks)
    assert "hindi" in system_text


def test_system_prompt_has_no_hardcoded_domain_content():
    """No employment-chatbot or domain-specific example text in the system prompt."""
    provider = make_provider_returning("hello", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("hello world there", CONFIG_WITH_DEFAULT)
    request: ChatRequest = provider.call.call_args.args[0]
    system_text = "\n".join(b.text for b in request.system.blocks)
    assert "employment" not in system_text
    assert "bijli" not in system_text


def test_short_input_skips_llm_and_returns_default_language():
    """Input with fewer than min_detection_tokens words skips LLM call."""
    provider = MagicMock(spec=ChatProviderBase)
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise("ok", CONFIG_WITH_DEFAULT)
    provider.call.assert_not_called()
    assert normalised == "ok"
    assert detected == "hindi"


def test_short_input_default_token_threshold_is_three():
    """Exactly 3 words should trigger the LLM; fewer should not."""
    provider = make_provider_returning("hello world foo", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("hello world foo", CONFIG_WITH_DEFAULT)
    provider.call.assert_called_once()  # 3 words → LLM called

    provider2 = MagicMock(spec=ChatProviderBase)
    n2 = LanguageNormaliser(chat_provider=provider2)
    n2.normalise("hello world", CONFIG_WITH_DEFAULT)
    provider2.call.assert_not_called()  # 2 words → skipped


def test_custom_min_detection_tokens_respected():
    """min_detection_tokens from config overrides the default of 3."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "english",
                "supported_languages": ["english"],
                "min_detection_tokens": 1,
            }
        }
    }
    provider = make_provider_returning("ok", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("ok", cfg)
    provider.call.assert_called_once()  # threshold = 1 → single word triggers LLM


# ---------------------------------------------------------------------------
# GH-151 #3: script-based bypass
# ---------------------------------------------------------------------------


def test_devanagari_input_with_hindi_default_bypasses_llm():
    """Pure Devanagari input + default_language=hindi → LLM call skipped."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "hindi",
                "supported_languages": ["hindi", "hinglish", "english"],
                "min_detection_tokens": 2,
            }
        }
    }
    provider = MagicMock(spec=ChatProviderBase)
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise(
        "मुझे इलेक्ट्रीशियन का काम चाहिए हुबली में", cfg
    )
    assert detected == "hindi"
    assert normalised == "मुझे इलेक्ट्रीशियन का काम चाहिए हुबली में"
    provider.call.assert_not_called()


def test_kannada_input_with_kannada_default_bypasses_llm():
    """Pure Kannada script + default_language=kannada → LLM call skipped."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "kannada",
                "supported_languages": ["kannada", "english"],
                "min_detection_tokens": 2,
            }
        }
    }
    provider = MagicMock(spec=ChatProviderBase)
    n = LanguageNormaliser(chat_provider=provider)
    normalised, detected = n.normalise(
        "ನಾನು ಇಲೆಕ್ಟ್ರೀಷಿಯನ್ ಕೆಲಸ ಮಾಡುತ್ತೇನೆ", cfg
    )
    assert detected == "kannada"
    provider.call.assert_not_called()


def test_hinglish_roman_input_does_not_bypass():
    """Majority-Latin Hinglish input must still go through the LLM — Roman
    script could be English, transliterated Hindi, or a mix."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "hindi",
                "supported_languages": ["hindi", "hinglish", "english"],
                "min_detection_tokens": 2,
            }
        }
    }
    provider = make_provider_returning("merko electrician ka kaam chahiye", "hinglish")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("merko electrician ka kaam chahiye", cfg)
    provider.call.assert_called_once()


def test_mixed_script_minority_devanagari_does_not_bypass():
    """Devanagari fragment in a mostly-Latin sentence must not trigger bypass."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "hindi",
                "supported_languages": ["hindi", "hinglish"],
                "min_detection_tokens": 2,
            }
        }
    }
    provider = make_provider_returning("i want काम urgently please", "hinglish")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("i want काम urgently please", cfg)
    provider.call.assert_called_once()


def test_script_bypass_can_be_disabled_by_config():
    """script_bypass=false forces the LLM path even for default-script input."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "hindi",
                "supported_languages": ["hindi", "hinglish"],
                "min_detection_tokens": 2,
                "script_bypass": False,
            }
        }
    }
    provider = make_provider_returning("मुझे काम चाहिए हुबली में", "hindi")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("मुझे काम चाहिए हुबली में", cfg)
    provider.call.assert_called_once()


def test_english_default_language_never_bypasses():
    """Latin script is ambiguous across English/Hinglish/mis-transcribed — the
    bypass map intentionally excludes ``english`` so the LLM always runs."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "english",
                "supported_languages": ["english", "hinglish"],
                "min_detection_tokens": 2,
            }
        }
    }
    provider = make_provider_returning("I want an electrician job", "english")
    n = LanguageNormaliser(chat_provider=provider)
    n.normalise("I want an electrician job", cfg)
    provider.call.assert_called_once()


def test_is_input_in_default_script_helper():
    from src.preprocessing.language_normalisation import _is_input_in_default_script

    assert _is_input_in_default_script("मुझे काम चाहिए", "hindi") is True
    assert _is_input_in_default_script("merko kaam chahiye", "hindi") is False
    assert _is_input_in_default_script("ನಾನು ಕೆಲಸ ಮಾಡುತ್ತೇನೆ", "kannada") is True
    assert _is_input_in_default_script("i want work", "kannada") is False
    # Unsupported default language — always False
    assert _is_input_in_default_script("whatever", "klingon") is False
    # Empty and whitespace-only inputs
    assert _is_input_in_default_script("", "hindi") is False
    assert _is_input_in_default_script("   ", "hindi") is False
