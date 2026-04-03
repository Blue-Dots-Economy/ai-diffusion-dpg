"""
tests/test_glossary.py

Unit tests for GlossaryBlock (Block 3).
No LLM or DB calls involved — pure string matching.
"""

import pytest
from unittest.mock import MagicMock

from src.blocks.glossary import GlossaryBlock
from src.base import KEContext
from src.models import SessionState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_MAPPINGS = [
    {"colloquial": ["kaam chahiye", "naukri chahiye", "job chahiye"], "canonical": "market_truth_query"},
    {"colloquial": ["ITI", "tradesman", "technician"], "canonical": "iti_graduate"},
    {"colloquial": ["course", "training", "sikhai"], "canonical": "training_query"},
    {"colloquial": ["apply kar do", "form bharo"], "canonical": "apply_now"},
    {"colloquial": ["counsellor chahiye", "kisi se baat karni hai"], "canonical": "counsellor_request"},
    {"colloquial": ["kitna milega", "salary kya hai", "pay kya hai"], "canonical": "pay_range_query"},
    {"colloquial": ["electrician", "bijli wala"], "canonical": "trade:electrician"},
    {"colloquial": ["fitter"], "canonical": "trade:fitter"},
    {"colloquial": ["welder"], "canonical": "trade:welder"},
    {"colloquial": ["PMKVY", "Pradhan Mantri Kaushal"], "canonical": "scheme:pmkvy"},
    {"colloquial": ["Hubli", "Dharwad", "Belgaum"], "canonical": "location:karnataka_north"},
]

CONFIG = {
    "knowledge": {
        "blocks": {
            "glossary": {
                "enabled": True,
                "mappings": SAMPLE_MAPPINGS,
                "apply_to": ["normalised_input", "entities"],
            }
        }
    }
}


def make_context(normalised_input: str, entities: dict = None) -> KEContext:
    return KEContext(
        session_id="test-session",
        raw_input=normalised_input,
        normalised_input=normalised_input,
        detected_language="hinglish",
        intent="unknown",
        entities=entities or {},
        sentiment="neutral",
        confidence=0.0,
        retrieval_chunks=[],
        always_include_chunks=[],
        session_state=SessionState.empty("test-session"),
    )


@pytest.fixture
def block():
    return GlossaryBlock()


@pytest.fixture
def mock_llm():
    return MagicMock()


# ---------------------------------------------------------------------------
# Normal execution — all 11 sample mappings
# ---------------------------------------------------------------------------


def test_kaam_chahiye_mapped(block, mock_llm):
    ctx = make_context("kaam chahiye Hubli mein")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "market_truth_query" in result.normalised_input


def test_naukri_chahiye_mapped(block, mock_llm):
    ctx = make_context("naukri chahiye mujhe")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "market_truth_query" in result.normalised_input


def test_job_chahiye_mapped(block, mock_llm):
    ctx = make_context("job chahiye abhi")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "market_truth_query" in result.normalised_input


def test_iti_mapped(block, mock_llm):
    ctx = make_context("ITI pass kiya hai")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "iti_graduate" in result.normalised_input


def test_training_mapped(block, mock_llm):
    ctx = make_context("training kahan milegi")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "training_query" in result.normalised_input


def test_apply_kar_do_mapped(block, mock_llm):
    ctx = make_context("apply kar do mere liye")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "apply_now" in result.normalised_input


def test_counsellor_chahiye_mapped(block, mock_llm):
    ctx = make_context("counsellor chahiye mujhe")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "counsellor_request" in result.normalised_input


def test_kitna_milega_mapped(block, mock_llm):
    ctx = make_context("kitna milega electrician ko")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "pay_range_query" in result.normalised_input


def test_electrician_mapped(block, mock_llm):
    ctx = make_context("electrician ka kaam chahiye")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "trade:electrician" in result.normalised_input


def test_bijli_wala_mapped(block, mock_llm):
    ctx = make_context("bijli wala hun main")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "trade:electrician" in result.normalised_input


def test_pmkvy_mapped(block, mock_llm):
    ctx = make_context("PMKVY ke baare mein batao")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "scheme:pmkvy" in result.normalised_input


def test_hubli_mapped(block, mock_llm):
    ctx = make_context("Hubli mein kaam chahiye")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "location:karnataka_north" in result.normalised_input


def test_fitter_mapped(block, mock_llm):
    ctx = make_context("fitter ka kaam chahiye")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "trade:fitter" in result.normalised_input


def test_welder_mapped(block, mock_llm):
    ctx = make_context("welder hun main")
    result = block.process(ctx, mock_llm, CONFIG)
    assert "trade:welder" in result.normalised_input


# ---------------------------------------------------------------------------
# Entity normalisation
# ---------------------------------------------------------------------------


def test_entity_value_bijli_wala_normalised(block, mock_llm):
    ctx = make_context("kaam chahiye", entities={"trade": "bijli wala"})
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.entities.get("trade") == "trade:electrician"


def test_entity_value_electrician_normalised(block, mock_llm):
    ctx = make_context("kaam chahiye", entities={"trade": "electrician"})
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.entities.get("trade") == "trade:electrician"


def test_entity_value_unmatched_preserved(block, mock_llm):
    ctx = make_context("kaam chahiye", entities={"trade": "plumber"})
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.entities.get("trade") == "plumber"


def test_entity_non_string_value_preserved(block, mock_llm):
    ctx = make_context("kaam chahiye", entities={"distance_km": 10})
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.entities.get("distance_km") == 10


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_match_input_unchanged(block, mock_llm):
    original = "main bahut acha hun"
    ctx = make_context(original)
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.normalised_input == original


def test_empty_input_returned_unchanged(block, mock_llm):
    ctx = make_context("")
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.normalised_input == ""


def test_raw_input_never_modified(block, mock_llm):
    original = "kaam chahiye Hubli mein"
    ctx = make_context(original)
    block.process(ctx, mock_llm, CONFIG)
    assert ctx.raw_input == original


def test_empty_entities_returns_empty_entities(block, mock_llm):
    ctx = make_context("some text", entities={})
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.entities == {}


# ---------------------------------------------------------------------------
# Config-driven behaviour
# ---------------------------------------------------------------------------


def test_disabled_block_passes_context_unchanged(block, mock_llm):
    disabled_config = {
        "knowledge": {
            "blocks": {
                "glossary": {
                    "enabled": False,
                    "mappings": SAMPLE_MAPPINGS,
                    "apply_to": ["normalised_input", "entities"],
                }
            }
        }
    }
    original = "kaam chahiye"
    ctx = make_context(original)
    result = block.process(ctx, mock_llm, disabled_config)
    assert result.normalised_input == original


def test_empty_mappings_no_error(block, mock_llm):
    empty_config = {
        "knowledge": {
            "blocks": {
                "glossary": {
                    "enabled": True,
                    "mappings": [],
                    "apply_to": ["normalised_input", "entities"],
                }
            }
        }
    }
    original = "kaam chahiye"
    ctx = make_context(original)
    result = block.process(ctx, mock_llm, empty_config)
    assert result.normalised_input == original


def test_apply_to_normalised_input_only(block, mock_llm):
    """When apply_to only has normalised_input, entities should not be changed."""
    config = {
        "knowledge": {
            "blocks": {
                "glossary": {
                    "enabled": True,
                    "mappings": SAMPLE_MAPPINGS,
                    "apply_to": ["normalised_input"],
                }
            }
        }
    }
    ctx = make_context("electrician ka kaam", entities={"trade": "bijli wala"})
    result = block.process(ctx, mock_llm, config)
    assert "trade:electrician" in result.normalised_input
    assert result.entities.get("trade") == "bijli wala"  # unchanged
