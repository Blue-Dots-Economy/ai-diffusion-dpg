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
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    for _ in range(3):
        acc.update("agent_core", "agent", bad)
    final = acc.update("agent_core", "agent", bad)
    assert "VALIDATION_FAILED_AFTER" in final


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
