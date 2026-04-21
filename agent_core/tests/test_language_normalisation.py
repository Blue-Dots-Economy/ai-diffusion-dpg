"""
agent_core/tests/test_language_normalisation.py

Unit tests for LanguageNormaliser (agent_core/src/language_normalisation.py).
All LLM calls are mocked — no real API calls.

Coverage:
- Normal: hindi/hinglish/english/kannada detected and normalised text returned
- Normal: model_override passed from config to llm.call
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

from src.preprocessing.language_normalisation import LanguageNormaliser
from src.models import LLMResponse


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


def make_llm_returning(normalised: str, detected: str) -> MagicMock:
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=json.dumps({
            "detected_language": detected,
            "normalised_text": normalised,
        }),
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
    )
    return llm


@pytest.fixture
def normaliser():
    return LanguageNormaliser()


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


def test_hindi_input_detected_and_returned(normaliser):
    llm = make_llm_returning("kaam chahiye mujhe", "hindi")
    normalised, detected = normaliser.normalise("kaam chahiye mujhe", CONFIG, llm)
    assert detected == "hindi"
    assert normalised == "kaam chahiye mujhe"


def test_hinglish_input_detected(normaliser):
    llm = make_llm_returning("electrician ka kaam chahiye", "hinglish")
    normalised, detected = normaliser.normalise("electrician ka kaam chahiye", CONFIG, llm)
    assert detected == "hinglish"


def test_english_input_detected(normaliser):
    llm = make_llm_returning("I want electrician work", "english")
    _, detected = normaliser.normalise("I want electrician work", CONFIG, llm)
    assert detected == "english"


def test_kannada_input_detected(normaliser):
    llm = make_llm_returning("ನಾನು ವಿದ್ಯುತ್ ತಂತ್ರಜ್ಞ", "kannada")
    _, detected = normaliser.normalise("ನಾನು ವಿದ್ಯುತ್ ತಂತ್ರಜ್ಞ", CONFIG, llm)
    assert detected == "kannada"


def test_normalised_text_from_llm_returned(normaliser):
    llm = make_llm_returning("cleaned and normalised text", "english")
    normalised, _ = normaliser.normalise("original input text", CONFIG, llm)
    assert normalised == "cleaned and normalised text"


def test_model_override_passed_from_config(normaliser):
    llm = make_llm_returning("hello world there", "english")
    normaliser.normalise("hello world there", CONFIG, llm)
    call_kwargs = llm.call.call_args[1]
    assert call_kwargs.get("model_override") == "claude-haiku-4-5-20251001"


def test_json_embedded_in_prose_parsed(normaliser):
    """LLM sometimes wraps JSON in prose — must still parse correctly."""
    prose = 'Here is the result: {"detected_language": "hinglish", "normalised_text": "kaam chahiye mujhe"}'
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=prose, stop_reason="end_turn", model_used="claude-haiku-4-5-20251001"
    )
    normalised, detected = normaliser.normalise("kaam chahiye mujhe", CONFIG, llm)
    assert detected == "hinglish"
    assert normalised == "kaam chahiye mujhe"


def test_missing_preprocessing_config_uses_defaults(normaliser):
    """No preprocessing section → uses supported_languages default, no model_override."""
    llm = make_llm_returning("hello world there", "english")
    normalised, detected = normaliser.normalise("hello world there", CONFIG_NO_PREPROCESSING, llm)
    assert detected == "english"
    assert normalised == "hello world there"
    call_kwargs = llm.call.call_args[1]
    assert call_kwargs.get("model_override") is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_raw_and_empty_language_without_llm_call(normaliser):
    llm = MagicMock()
    normalised, detected = normaliser.normalise("", CONFIG, llm)
    assert normalised == ""
    assert detected == ""
    llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_llm_error_stop_reason_falls_back_to_raw_input(normaliser):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(content=None, stop_reason="error")
    original = "kaam chahiye Hubli mein"
    normalised, detected = normaliser.normalise(original, CONFIG, llm)
    assert normalised == original
    assert detected == ""


def test_llm_exception_falls_back_gracefully(normaliser):
    llm = MagicMock()
    llm.call.side_effect = RuntimeError("network error")
    original = "kaam chahiye Hubli mein"
    normalised, detected = normaliser.normalise(original, CONFIG, llm)
    assert normalised == original
    assert detected == ""


def test_malformed_json_falls_back_to_raw_input(normaliser):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content="not valid json here",
        stop_reason="end_turn",
        model_used="claude-haiku-4-5-20251001",
    )
    original = "kaam chahiye Hubli mein"
    normalised, detected = normaliser.normalise(original, CONFIG, llm)
    assert normalised == original
    assert detected == ""


def test_never_raises_on_unexpected_exception(normaliser):
    """normalise() must never propagate unexpected exceptions to the caller."""
    llm = MagicMock()
    llm.call.side_effect = Exception("totally unexpected")
    result = normaliser.normalise("some input", CONFIG, llm)
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


def test_system_prompt_contains_default_language(normaliser):
    """System prompt sent to LLM must mention the configured default_language."""
    llm = make_llm_returning("hello", "english")
    normaliser.normalise("hello world there", CONFIG_WITH_DEFAULT, llm)
    call_kwargs = llm.call.call_args[1]
    assert "hindi" in call_kwargs["system"]


def test_system_prompt_has_no_hardcoded_domain_content(normaliser):
    """No employment-chatbot or domain-specific example text in the system prompt."""
    llm = make_llm_returning("hello", "english")
    normaliser.normalise("hello world there", CONFIG_WITH_DEFAULT, llm)
    call_kwargs = llm.call.call_args[1]
    assert "employment" not in call_kwargs["system"]
    assert "bijli" not in call_kwargs["system"]


def test_short_input_skips_llm_and_returns_default_language(normaliser):
    """Input with fewer than min_detection_tokens words skips LLM call."""
    llm = MagicMock()
    normalised, detected = normaliser.normalise("ok", CONFIG_WITH_DEFAULT, llm)
    llm.call.assert_not_called()
    assert normalised == "ok"
    assert detected == "hindi"


def test_short_input_default_token_threshold_is_three(normaliser):
    """Exactly 3 words should trigger the LLM; fewer should not."""
    llm = make_llm_returning("hello world foo", "english")
    normaliser.normalise("hello world foo", CONFIG_WITH_DEFAULT, llm)
    llm.call.assert_called_once()  # 3 words → LLM called

    llm2 = MagicMock()
    normaliser.normalise("hello world", CONFIG_WITH_DEFAULT, llm2)
    llm2.call.assert_not_called()  # 2 words → skipped


def test_custom_min_detection_tokens_respected(normaliser):
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
    llm = make_llm_returning("ok", "english")
    normaliser.normalise("ok", cfg, llm)
    llm.call.assert_called_once()  # threshold = 1 → single word triggers LLM




# ---------------------------------------------------------------------------
# GH-151 #3: script-based bypass
# ---------------------------------------------------------------------------


def test_devanagari_input_with_hindi_default_bypasses_llm(normaliser):
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
    llm = MagicMock()
    normalised, detected = normaliser.normalise(
        "मुझे इलेक्ट्रीशियन का काम चाहिए हुबली में", cfg, llm
    )
    assert detected == "hindi"
    assert normalised == "मुझे इलेक्ट्रीशियन का काम चाहिए हुबली में"
    llm.call.assert_not_called()


def test_kannada_input_with_kannada_default_bypasses_llm(normaliser):
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
    llm = MagicMock()
    normalised, detected = normaliser.normalise(
        "ನಾನು ಇಲೆಕ್ಟ್ರೀಷಿಯನ್ ಕೆಲಸ ಮಾಡುತ್ತೇನೆ", cfg, llm
    )
    assert detected == "kannada"
    llm.call.assert_not_called()


def test_hinglish_roman_input_does_not_bypass(normaliser):
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
    llm = make_llm_returning("merko electrician ka kaam chahiye", "hinglish")
    normaliser.normalise("merko electrician ka kaam chahiye", cfg, llm)
    llm.call.assert_called_once()


def test_mixed_script_minority_devanagari_does_not_bypass(normaliser):
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
    llm = make_llm_returning("i want काम urgently please", "hinglish")
    normaliser.normalise("i want काम urgently please", cfg, llm)
    llm.call.assert_called_once()


def test_script_bypass_can_be_disabled_by_config(normaliser):
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
    llm = make_llm_returning("मुझे काम चाहिए हुबली में", "hindi")
    normaliser.normalise("मुझे काम चाहिए हुबली में", cfg, llm)
    llm.call.assert_called_once()


def test_english_default_language_never_bypasses(normaliser):
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
    llm = make_llm_returning("I want an electrician job", "english")
    normaliser.normalise("I want an electrician job", cfg, llm)
    llm.call.assert_called_once()


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
