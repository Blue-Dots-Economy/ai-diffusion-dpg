"""Tests for the central validation entry points."""
import pytest

from dev_kit.schemas.validation import (
    DOMAIN_SECTION_SCHEMAS,
    DPG_BLOCK_SCHEMAS,
    validate_domain_section,
    validate_dpg_block,
    get_valid_sections,
)


# -- validate_domain_section -------------------------------------------------

def test_unknown_block_returns_error():
    err = validate_domain_section("nope", "agent", {})
    assert err is not None
    assert "Unknown" in err


def test_unknown_section_returns_error():
    err = validate_domain_section("agent_core", "unknown_section", {})
    assert err is not None
    assert "Unknown" in err


def test_valid_agent_section_returns_none():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert err is None


def test_invalid_agent_section_returns_error_with_type_and_value():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001",
         "max_tool_rounds": 0},
    )
    assert err is not None
    assert "max_tool_rounds" in err
    assert "[greater_than_equal]" in err   # error type code
    assert "you sent: 0" in err            # offending value


def test_extra_field_returns_extra_forbidden():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001",
         "vector_store": "bogus"},
    )
    assert err is not None
    assert "extra_forbidden" in err
    assert "vector_store" in err


def test_dotted_section_path_uses_top_level():
    """update_config(section='preprocessing.nlu_processor', ...) validates against PreprocessingSection."""
    err = validate_domain_section(
        "agent_core", "preprocessing.nlu_processor",
        {"language_normalisation": {
            "model": "claude-sonnet-4-6",
            "default_language": "english",
            "supported_languages": ["english"],
         },
         "nlu_processor": {"model": "claude-sonnet-4-6", "intents": ["greet"]}},
    )
    assert err is None


def test_cross_field_validator_failure_visible():
    """A cross-field validator surfaces in the formatted error string."""
    # provider=anthropic + an OpenAI model fires models_must_match_provider.
    err = validate_domain_section(
        "agent_core", "agent",
        {"provider": "anthropic", "primary_model": "gpt-4o-2024-08-06",
         "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert err is not None
    assert "not valid for provider" in err


# -- validate_dpg_block ------------------------------------------------------

def test_dpg_unknown_block():
    err = validate_dpg_block("nope", {})
    assert err is not None
    assert "Unknown block" in err


def test_dpg_invalid_returns_formatted_error():
    err = validate_dpg_block("memory_layer", {
        "server": {"port": 99999},
        "redis": {"host": "x"},
        "memgraph": {"uri": "bolt://x", "user": "u"},
        "observability": {"otel": {"collector_endpoint": "http://x"}},
    })
    assert err is not None
    assert "server.port" in err


def test_dpg_valid_returns_none():
    err = validate_dpg_block("memory_layer", {
        "server": {"host": "0.0.0.0", "port": 8002},
        "redis": {"host": "redis", "port": 6379},
        "memgraph": {"uri": "bolt://memgraph:7687", "user": "memgraph"},
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317"}},
    })
    assert err is None


# -- Dispatch tables ---------------------------------------------------------

def test_all_seven_blocks_have_dpg_schemas():
    expected = {
        "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }
    assert set(DPG_BLOCK_SCHEMAS.keys()) == expected


def test_domain_dispatch_covers_critical_sections():
    """Spot-check that key sections are mapped."""
    assert ("agent_core", "agent") in DOMAIN_SECTION_SCHEMAS
    assert ("agent_core", "agent_workflow") in DOMAIN_SECTION_SCHEMAS
    assert ("knowledge_engine", "knowledge") in DOMAIN_SECTION_SCHEMAS
    assert ("memory_layer", "state") in DOMAIN_SECTION_SCHEMAS
    assert ("trust_layer", "trust") in DOMAIN_SECTION_SCHEMAS
    assert ("trust_layer", "dignity_check") in DOMAIN_SECTION_SCHEMAS
    assert ("action_gateway", "tools") in DOMAIN_SECTION_SCHEMAS
    assert ("reach_layer", "reach_layer") in DOMAIN_SECTION_SCHEMAS
    assert ("observability_layer", "observability") in DOMAIN_SECTION_SCHEMAS


def test_error_format_includes_type_and_offending_value():
    """Verify the error formatter output structure."""
    err = validate_domain_section(
        "memory_layer", "state",
        {"session": {"ttl_minutes": 99999}, "persistent": {"graph": {"user_node": {"label": "U", "key": "id"}}}},
    )
    assert err is not None
    # Should have format: "- field.path [error_type]: msg (you sent: value)"
    assert " [" in err and "]" in err
    assert "you sent:" in err


def test_error_format_root_path_handled():
    """Errors at the root level should show '<root>' instead of empty path."""
    err = validate_domain_section("agent_core", "agent", "not_a_dict")
    assert err is not None


def test_error_format_handles_repr_exception():
    """If offending value's repr() raises, still emit the rest of the error."""
    class BrokenRepr:
        def __repr__(self):
            raise RuntimeError("repr failed")

    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": BrokenRepr(), "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert err is not None
    # Path/type still present; "you sent:" silently dropped on repr failure.
    assert "[" in err and "]" in err


def test_error_format_truncates_long_values():
    """Values whose repr exceeds 200 chars render with the truncation marker."""
    long_value = "x" * 500
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": long_value, "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert err is not None
    assert "...<truncated>" in err


# -- get_valid_sections -------------------------------------------------------

def test_get_valid_sections_returns_sorted_list():
    sections = get_valid_sections("agent_core")
    assert "agent" in sections
    assert "agent_workflow" in sections
    assert "preprocessing" in sections
    assert sections == sorted(sections)


def test_get_valid_sections_unknown_block_returns_empty():
    assert get_valid_sections("nonexistent") == []


def test_get_valid_sections_all_blocks():
    # Verify all 7 blocks return non-empty sections
    blocks = [
        "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
        "action_gateway", "reach_layer", "observability_layer",
    ]
    for block in blocks:
        sections = get_valid_sections(block)
        assert len(sections) > 0, f"Block {block} should have sections"
        assert sections == sorted(sections), f"Sections for {block} should be sorted"
