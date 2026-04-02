"""Tests for dev_kit.schema validate_partial."""
import pytest
from dev_kit.schema import validate_partial


class TestValidatePartial:
    def test_empty_dict_returns_no_errors(self):
        assert validate_partial("trust_layer", {}) == []

    def test_valid_trust_config_returns_no_errors(self):
        data = {
            "server": {"host": "0.0.0.0", "port": 8003},
            "trust": {
                "input_rules": {"blocked_phrases": ["spam"]},
                "output_rules": {"blocked_phrases": []},
            },
        }
        assert validate_partial("trust_layer", data) == []

    def test_type_error_is_reported(self):
        # blocked_phrases must be a list, not a string
        data = {"trust": {"input_rules": {"blocked_phrases": "not-a-list"}}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0

    def test_missing_required_field_is_not_reported(self):
        # AgentCoreConfig has many required fields — missing ones must be ignored
        data = {"agent": {"primary_model": "claude-haiku-4-5-20251001"}}
        errors = validate_partial("agent_core", data)
        assert errors == []

    def test_unknown_block_returns_error(self):
        errors = validate_partial("bogus_block", {})
        assert len(errors) == 1
        assert "Unknown block" in errors[0]

    def test_nested_type_error_is_reported(self):
        # port must be int
        data = {"server": {"host": "0.0.0.0", "port": "not-an-int"}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0
