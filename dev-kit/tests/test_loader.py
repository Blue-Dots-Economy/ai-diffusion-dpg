"""Tests for dev_kit.loader get_schema_descriptions."""
from dev_kit.loader import get_schema_descriptions


class TestGetSchemaDescriptions:
    def test_returns_dict_for_known_block(self):
        result = get_schema_descriptions("trust_layer")
        assert isinstance(result, dict)

    def test_returns_empty_dict_for_unknown_block(self):
        assert get_schema_descriptions("bogus") == {}

    def test_all_keys_and_values_are_strings(self):
        result = get_schema_descriptions("agent_core")
        assert all(isinstance(k, str) for k in result)
        assert all(isinstance(v, str) for v in result.values())

    def test_known_described_field_is_present(self):
        # primary_model has a description after Task 2
        result = get_schema_descriptions("agent_core")
        assert len(result) > 0

    def test_trust_layer_blocked_phrases_described(self):
        result = get_schema_descriptions("trust_layer")
        # blocked_phrases has a description after Task 2
        matching = {k: v for k, v in result.items() if "blocked_phrases" in k}
        assert len(matching) > 0
