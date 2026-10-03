"""Tests for turn_policy: merging of turn-lifecycle config (Agent Core block)."""

import logging

from src.turn_policy import (
    ON_DISCONNECT_ABORT,
    ON_DISCONNECT_CONTINUE,
    ON_NEW_INPUT_ABORT_AND_FOLD,
    ON_NEW_INPUT_REPLACE,
    TurnPolicy,
    resolve_session_idle_ttl_ms,
    resolve_turn_policy,
)


def _cfg(default_ta=None, channels=None):
    return {
        "reach_layer": {"turn_assembler": default_ta or {}},
        "channels": channels or {},
    }


class TestResolveTurnPolicy:

    def test_defaults_when_nothing_configured(self):
        policy = resolve_turn_policy({}, "bridge")
        assert policy == TurnPolicy()
        assert policy.on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
        assert policy.on_disconnect == ON_DISCONNECT_ABORT
        assert policy.drain_max_ms == 3000
        assert policy.fold_max_segments == 3
        assert policy.carryover_max_age_ms == 60000
        assert policy.undelivered_note == ""

    def test_reach_layer_default_applies(self):
        cfg = _cfg(default_ta={
            "interruption": {"drain_max_ms": 500},
            "carryover": {"undelivered_note": "NOTE"},
        })
        policy = resolve_turn_policy(cfg, "voice")
        assert policy.drain_max_ms == 500
        assert policy.undelivered_note == "NOTE"

    def test_channel_override_merges_per_subsection(self):
        cfg = _cfg(
            default_ta={"interruption": {"drain_max_ms": 500, "on_disconnect": "abort"}},
            channels={"bridge": {"turn_assembler": {
                "interruption": {"on_disconnect": "continue"},
                "fold": {"max_segments": 5},
            }}},
        )
        policy = resolve_turn_policy(cfg, "bridge")
        assert policy.on_disconnect == ON_DISCONNECT_CONTINUE
        assert policy.drain_max_ms == 500          # inherited, not reset
        assert policy.fold_max_segments == 5

    def test_unknown_channel_uses_defaults(self):
        cfg = _cfg(channels={"voice": {"turn_assembler": {"fold": {"max_segments": 9}}}})
        assert resolve_turn_policy(cfg, "nope").fold_max_segments == 3
        assert resolve_turn_policy(cfg, None).fold_max_segments == 3

    def test_invalid_enum_falls_back_with_warning(self, caplog):
        cfg = _cfg(default_ta={"interruption": {"on_new_input": "explode"}})
        with caplog.at_level(logging.WARNING):
            policy = resolve_turn_policy(cfg, "voice")
        assert policy.on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
        assert any("turn_policy.invalid_value" in r.message for r in caplog.records)

    def test_negative_numbers_fall_back(self):
        cfg = _cfg(default_ta={"fold": {"max_segments": -1},
                               "interruption": {"drain_max_ms": "x"}})
        policy = resolve_turn_policy(cfg, "voice")
        assert policy.fold_max_segments == 3
        assert policy.drain_max_ms == 3000

    def test_replace_is_accepted(self):
        cfg = _cfg(default_ta={"interruption": {"on_new_input": "replace"}})
        assert resolve_turn_policy(cfg, "voice").on_new_input == ON_NEW_INPUT_REPLACE

    def test_non_dict_sections_ignored(self):
        cfg = {"reach_layer": {"turn_assembler": {"fold": "bad"}}, "channels": {"voice": None}}
        assert resolve_turn_policy(cfg, "voice") == TurnPolicy()


class TestSessionIdleTtl:

    def test_default(self):
        assert resolve_session_idle_ttl_ms({}) == 1800000

    def test_configured(self):
        assert resolve_session_idle_ttl_ms(_cfg(default_ta={"session_idle_ttl_ms": 1000})) == 1000

    def test_invalid_falls_back(self):
        assert resolve_session_idle_ttl_ms(_cfg(default_ta={"session_idle_ttl_ms": -5})) == 1800000


import pytest
from pydantic import ValidationError

from src.schema.config import TurnAssemblerConfig


class TestRuntimeSchema:

    def test_defaults(self):
        ta = TurnAssemblerConfig()
        assert ta.interruption.on_new_input == "abort_and_fold"
        assert ta.interruption.on_disconnect == "abort"
        assert ta.interruption.drain_max_ms == 3000
        assert ta.fold.max_segments == 3
        assert ta.carryover.max_age_ms == 60000
        assert ta.carryover.undelivered_note == ""
        assert ta.session_idle_ttl_ms == 1800000

    def test_accepts_valid(self):
        ta = TurnAssemblerConfig.model_validate({
            "interruption": {"on_new_input": "replace", "on_disconnect": "continue",
                             "drain_max_ms": 0},
            "fold": {"max_segments": 0},
            "carryover": {"max_age_ms": 1, "undelivered_note": "x"},
            "session_idle_ttl_ms": 5,
        })
        assert ta.interruption.on_new_input == "replace"

    @pytest.mark.parametrize("payload", [
        {"interruption": {"on_new_input": "explode"}},
        {"interruption": {"on_disconnect": "maybe"}},
        {"interruption": {"drain_max_ms": -1}},
        {"fold": {"max_segments": -1}},
        {"carryover": {"max_age_ms": -1}},
        {"carryover": {"enabled": True}},          # removed from the design
        {"session_idle_ttl_ms": -1},
    ])
    def test_rejects_invalid(self, payload):
        with pytest.raises(ValidationError):
            TurnAssemblerConfig.model_validate(payload)
