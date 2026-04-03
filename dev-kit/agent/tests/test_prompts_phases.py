"""Tests for dev_kit.agent.prompts.phases."""
import pytest
from dev_kit.agent.prompts.phases import get_phase_addition


class TestGetPhaseAddition:
    """Test suite for get_phase_addition function."""

    def test_overview_phase_returns_empty_string(self):
        """Overview phase should return empty string."""
        result = get_phase_addition("overview")
        assert result == ""

    def test_language_phase_returns_non_empty_string(self):
        """Language phase should return non-empty schema context."""
        result = get_phase_addition("language")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Language" in result or "language" in result.lower()

    def test_knowledge_phase_returns_non_empty_string(self):
        """Knowledge phase should return non-empty schema context."""
        result = get_phase_addition("knowledge")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Knowledge" in result or "knowledge" in result.lower()

    def test_memory_phase_returns_non_empty_string(self):
        """Memory phase should return non-empty schema context."""
        result = get_phase_addition("memory")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Memory" in result or "memory" in result.lower()

    def test_trust_phase_returns_non_empty_string(self):
        """Trust phase should return non-empty schema context."""
        result = get_phase_addition("trust")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Trust" in result or "trust" in result.lower()

    def test_connectors_phase_returns_non_empty_string(self):
        """Connectors phase should return non-empty schema context."""
        result = get_phase_addition("connectors")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Connectors" in result or "connectors" in result.lower()

    def test_workflow_phase_returns_non_empty_string(self):
        """Workflow phase should return non-empty schema context."""
        result = get_phase_addition("workflow")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Workflow" in result or "workflow" in result.lower()

    def test_review_phase_returns_non_empty_string(self):
        """Review phase should return non-empty schema context."""
        result = get_phase_addition("review")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Review" in result or "review" in result.lower()

    def test_unknown_phase_returns_empty_string(self):
        """Unknown phase name should return empty string, not raise."""
        result = get_phase_addition("unknown_phase")
        assert result == ""

    def test_workflow_with_available_connectors_includes_connector_names(self):
        """Workflow phase with available_connectors should include connector names in output."""
        connectors = ["crm_api", "sms_service"]
        result = get_phase_addition("workflow", available_connectors=connectors)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "crm_api" in result
        assert "sms_service" in result

    def test_workflow_with_none_connectors_does_not_raise(self):
        """Workflow phase with available_connectors=None should not raise."""
        result = get_phase_addition("workflow", available_connectors=None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_workflow_with_empty_connectors_list(self):
        """Workflow phase with empty connectors list should return base workflow context."""
        result = get_phase_addition("workflow", available_connectors=[])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_phase_names_are_case_sensitive(self):
        """Phase names should be case-sensitive; 'Language' (capital L) should return empty."""
        result = get_phase_addition("Language")
        assert result == ""

    def test_all_phase_names_case_sensitive(self):
        """Verify case sensitivity for all known phase names."""
        known_phases = ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"]
        for phase in known_phases:
            capitalized = phase.capitalize()
            result = get_phase_addition(capitalized)
            assert result == "", f"Expected empty string for capitalized phase '{capitalized}', got: {result!r}"

    def test_return_type_is_always_string(self):
        """Return type should always be a string."""
        test_phases = ["overview", "language", "memory", "unknown", "workflow"]
        for phase in test_phases:
            result = get_phase_addition(phase)
            assert isinstance(result, str), f"Expected str for phase '{phase}', got {type(result)}"

    def test_workflow_with_multiple_connectors_in_output(self):
        """Workflow phase should include all connectors in the output."""
        connectors = ["api_1", "api_2", "api_3"]
        result = get_phase_addition("workflow", available_connectors=connectors)
        for connector in connectors:
            assert connector in result, f"Expected connector '{connector}' in workflow output"

    def test_language_phase_includes_schema_header(self):
        """Language phase should include a schema context header."""
        result = get_phase_addition("language")
        assert "Schema context" in result or "Language" in result

    def test_knowledge_phase_includes_glossary_format(self):
        """Knowledge phase should mention glossary format."""
        result = get_phase_addition("knowledge")
        assert "Glossary" in result or "glossary" in result.lower()

    def test_memory_phase_includes_session_schema(self):
        """Memory phase should mention session schema."""
        result = get_phase_addition("memory")
        assert "Session" in result or "session" in result.lower()
