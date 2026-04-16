"""Tests for dev_kit.agent.prompts.base.build_system_prompt."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.base import build_system_prompt


def _make_prompt(phase="overview", summaries=None, available_tools=None, name="TestProject", desc="A test"):
    acc = ConfigAccumulator()
    return build_system_prompt(
        project_name=name,
        project_description=desc,
        accumulator=acc,
        phase=phase,
        checkpoint_summaries=summaries or [],
        available_tools=available_tools,
    )


class TestBuildSystemPrompt:
    def test_contains_dpg_overview(self):
        """Output must include the DPG overview section."""
        prompt = _make_prompt()
        assert "DPG Configuration Assistant" in prompt

    def test_contains_project_context_when_name_given(self):
        """Project name and description must appear in the prompt."""
        prompt = _make_prompt(name="KarmaKitchen", desc="Food bank management")
        assert "KarmaKitchen" in prompt
        assert "Food bank management" in prompt

    def test_no_project_section_when_name_empty(self):
        """When project_name is empty, no Project section is injected."""
        prompt = build_system_prompt(
            project_name="",
            project_description="",
            accumulator=ConfigAccumulator(),
            phase="overview",
            checkpoint_summaries=[],
        )
        assert "## Project" not in prompt

    def test_contains_checkpoint_summaries(self):
        """Prior phase summaries must appear in the output."""
        prompt = _make_prompt(summaries=["Overview complete. User builds a chatbot."])
        assert "Overview complete. User builds a chatbot." in prompt

    def test_no_summaries_section_when_empty(self):
        """When no summaries are provided, the prior-phase summaries section is omitted."""
        prompt = _make_prompt(summaries=[])
        assert "Prior phase summaries" not in prompt

    def test_contains_current_phase(self):
        """Current phase name must appear in the output."""
        prompt = _make_prompt(phase="trust")
        assert "trust" in prompt

    def test_phase_addition_injected_for_known_phase(self):
        """A known phase must inject phase-specific schema context."""
        prompt = _make_prompt(phase="language")
        assert len(prompt) > len(_make_prompt(phase="overview"))

    def test_tools_injected_in_workflow_phase(self):
        """Available tools must appear in the workflow-phase prompt."""
        prompt = _make_prompt(phase="workflow", available_tools=["crm_api", "sms_gateway"])
        assert "crm_api" in prompt or "sms_gateway" in prompt

    def test_unknown_phase_produces_valid_prompt(self):
        """An unrecognised phase name must not raise — returns a prompt without phase addition."""
        prompt = _make_prompt(phase="totally_unknown_phase")
        assert "totally_unknown_phase" in prompt
