"""Tests for the new validation hook + retry counter on ConfigAccumulator."""
import os
import pytest

from dev_kit.agent.accumulator import ConfigAccumulator


@pytest.fixture(autouse=True)
def enable_strict():
    """Tests run with strict validation enabled."""
    old = os.environ.get("DEVKIT_DPG_SCHEMA_STRICT")
    os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = "1"
    yield
    if old is None:
        os.environ.pop("DEVKIT_DPG_SCHEMA_STRICT", None)
    else:
        os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = old


def test_valid_update_returns_ok():
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert result == "OK"


def test_invalid_update_returns_validation_error():
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert result.startswith("VALIDATION_ERROR")
    assert "must be different" in result
    assert "attempt 1/" in result


def test_counter_increments_on_repeated_failures():
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    r1 = acc.update("agent_core", "agent", bad)
    r2 = acc.update("agent_core", "agent", bad)
    assert "attempt 1/" in r1
    assert "attempt 2/" in r2


def test_counter_caps_at_max():
    """The Mth failure returns VALIDATION_FAILED_AFTER and marks the section stale.

    Subsequent calls to the same section return VALIDATION_SECTION_STALE
    without re-validating — the loop safety net.
    """
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    r1 = acc.update("agent_core", "agent", bad)
    r2 = acc.update("agent_core", "agent", bad)
    r3 = acc.update("agent_core", "agent", bad)
    r4 = acc.update("agent_core", "agent", bad)
    assert "attempt 1/" in r1
    assert "attempt 2/" in r2
    assert "VALIDATION_FAILED_AFTER" in r3
    assert "VALIDATION_SECTION_STALE" in r4
    assert "DO NOT call update_config" in r4


def test_section_stale_blocks_value_change_attempts():
    """Even with a different (still-bad) value, a stale section is hard-rejected.

    Prevents the LLM from looping by varying the bad value to escape the
    retry counter.
    """
    acc = ConfigAccumulator()
    bad_a = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    bad_b = {"primary_model": "claude-haiku-4-5-20251001", "fallback_model": "claude-haiku-4-5-20251001"}
    for _ in range(3):
        acc.update("agent_core", "agent", bad_a)
    # Different bad payload — still stale, still rejected without validation.
    result = acc.update("agent_core", "agent", bad_b)
    assert "VALIDATION_SECTION_STALE" in result


def test_validation_does_not_pollute_state_on_failure():
    """Failed validation must not write the bad value into the accumulator.

    Single-validation refactor: validate-before-write means rejected
    payloads never enter self._data.
    """
    acc = ConfigAccumulator()
    # Submit a value that fails validation (matching primary == fallback).
    acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    # The agent_core block should still be empty — nothing written.
    assert acc.get_block("agent_core").get("agent", {}) == {}


def test_counter_resets_on_success():
    acc = ConfigAccumulator()
    acc.update("agent_core", "agent",
               {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"})
    # Now successful update — counter should reset
    ok = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert ok == "OK"
    # Subsequent failure starts at attempt 1, not 2
    fail = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert "attempt 1/" in fail


def test_counter_independent_per_section():
    acc = ConfigAccumulator()
    bad_agent = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    acc.update("agent_core", "agent", bad_agent)  # 1/3
    acc.update("agent_core", "agent", bad_agent)  # 2/3
    # Other section's counter is independent
    other = acc.update("knowledge_engine", "observability", {"domain": ""})
    assert "attempt 1/" in other


def test_reset_counters_on_new_turn():
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    for _ in range(3):
        acc.update("agent_core", "agent", bad)
    acc.reset_validation_attempts()
    fresh = acc.update("agent_core", "agent", bad)
    assert "attempt 1/" in fresh


def test_strict_mode_disabled_skips_validation():
    """With DEVKIT_DPG_SCHEMA_STRICT=0, invalid values pass through."""
    os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = "0"
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert result == "OK"


def test_max_attempts_env_override():
    """DEVKIT_VALIDATION_MAX_ATTEMPTS overrides the default of 3."""
    os.environ["DEVKIT_VALIDATION_MAX_ATTEMPTS"] = "1"
    try:
        acc = ConfigAccumulator()
        bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
        first = acc.update("agent_core", "agent", bad)
        assert "VALIDATION_FAILED_AFTER" in first  # cap is 1, immediate fallback
    finally:
        os.environ.pop("DEVKIT_VALIDATION_MAX_ATTEMPTS", None)
