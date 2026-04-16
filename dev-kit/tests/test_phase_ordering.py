"""
tests/test_phase_ordering.py

Tests for the connectors→tools rename: PHASES list, set_phase handler,
build_system_prompt signature, get_phase_addition, and ConversationEngine
prompt building.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dev_kit.agent.accumulator import PHASES, ConfigAccumulator
from dev_kit.agent.prompts.base import build_system_prompt
from dev_kit.agent.prompts.phases import get_phase_addition
from dev_kit.agent.tools import ToolHandler


# ---------------------------------------------------------------------------
# 1. PHASES list correctness
# ---------------------------------------------------------------------------


def test_phases_contains_tools_at_index_5():
    """PHASES[5] must be 'tools' after the connectors→tools rename."""
    assert PHASES[5] == "tools"


def test_phases_does_not_contain_connectors():
    """'connectors' must not appear anywhere in PHASES after the rename."""
    assert "connectors" not in PHASES


def test_phases_has_ten_entries():
    """PHASES must still contain exactly 10 phases."""
    assert len(PHASES) == 10


# ---------------------------------------------------------------------------
# 2. set_phase handler — unknown / old phase name
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_at_trust():
    return {"phase": "trust", "phase_changed": None, "rollback_to": None, "project_meta": {}}


@pytest.fixture()
def handler_at_trust(state_at_trust):
    acc = ConfigAccumulator()
    return ToolHandler(acc, state_at_trust), state_at_trust


def test_set_phase_rejects_connectors_as_unknown(handler_at_trust):
    """set_phase('connectors') should not advance — it is no longer a valid phase."""
    handler, state = handler_at_trust
    result = handler.dispatch("set_phase", {"phase": "connectors"})
    # 'connectors' is not in PHASES so requested_idx == -1 < current_idx,
    # which hits the "cannot go back" error branch.
    assert "ERROR" in result
    assert state["phase_changed"] is None


# ---------------------------------------------------------------------------
# 3. set_phase sequential transitions involving 'tools'
# ---------------------------------------------------------------------------


def test_set_phase_trust_to_tools(handler_at_trust):
    """set_phase('tools') is the correct next step after 'trust'."""
    handler, state = handler_at_trust
    result = handler.dispatch("set_phase", {"phase": "tools"})
    assert "tools" in result
    assert state["phase_changed"] == "tools"


def test_set_phase_tools_to_workflow():
    """set_phase('workflow') is the correct next step after 'tools'."""
    state = {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    acc = ConfigAccumulator()
    handler = ToolHandler(acc, state)
    result = handler.dispatch("set_phase", {"phase": "workflow"})
    assert "workflow" in result
    assert state["phase_changed"] == "workflow"


# ---------------------------------------------------------------------------
# 4. build_system_prompt accepts available_tools kwarg
# ---------------------------------------------------------------------------


def test_build_system_prompt_accepts_available_tools_kwarg():
    """build_system_prompt must accept available_tools and not raise TypeError."""
    acc = ConfigAccumulator()
    result = build_system_prompt(
        project_name="Test",
        project_description="A test project",
        accumulator=acc,
        phase="workflow",
        checkpoint_summaries=[],
        available_tools=["my_tool", "another_tool"],
    )
    assert isinstance(result, str)
    assert len(result) > 0


def test_build_system_prompt_no_available_connectors_kwarg():
    """build_system_prompt must NOT accept available_connectors — removed kwarg."""
    acc = ConfigAccumulator()
    import inspect
    sig = inspect.signature(build_system_prompt)
    assert "available_connectors" not in sig.parameters
    assert "available_tools" in sig.parameters


# ---------------------------------------------------------------------------
# 5. get_phase_addition — available_tools wiring
# ---------------------------------------------------------------------------


def test_get_phase_addition_workflow_includes_tool_id():
    """get_phase_addition('workflow', available_tools=['my_tool']) includes 'my_tool'."""
    result = get_phase_addition("workflow", available_tools=["my_tool"])
    assert "my_tool" in result


def test_get_phase_addition_workflow_none_tools_returns_nonempty():
    """get_phase_addition('workflow', available_tools=None) returns non-empty string."""
    result = get_phase_addition("workflow", available_tools=None)
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_phase_addition_tools_phase_returns_nonempty():
    """get_phase_addition('tools') returns a non-empty guidance string."""
    result = get_phase_addition("tools")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_phase_addition_tools_phase_does_not_reference_connectors_phase():
    """The 'tools' phase addition should not reference the old 'connectors' terminology as a phase name."""
    result = get_phase_addition("tools")
    # The word 'connectors' may still appear naturally in descriptions, but the phase name
    # reference "connectors phase" in the old sense should not guide the LLM to that name.
    assert "set_phase('connectors')" not in result


# ---------------------------------------------------------------------------
# 6. ConversationEngine._build_system_prompt uses get_action_gateway_tools()
# ---------------------------------------------------------------------------


def test_conversation_engine_build_system_prompt_uses_get_action_gateway_tools():
    """ConversationEngine._build_system_prompt should call get_action_gateway_tools()."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    # Import here to avoid triggering full module initialisation side-effects
    from dev_kit.agent.conversation import ConversationEngine

    mock_client = MagicMock()

    with patch.object(ConversationEngine, "_load", return_value=None):
        engine = ConversationEngine.__new__(ConversationEngine)
        engine._project_path = Path("/tmp/fake_project")
        engine._client = mock_client
        engine._history = []
        engine._state = {
            "phase": "workflow",
            "phase_changed": None,
            "rollback_to": None,
            "project_meta": {"name": "Test", "description": "desc"},
        }
        mock_acc = MagicMock(spec=ConfigAccumulator)
        mock_acc.get_action_gateway_tools.return_value = [{"id": "tool_a"}, {"id": "tool_b"}]
        mock_acc.summary.return_value = "mock summary"
        engine.accumulator = mock_acc
        engine._tool_handler = ToolHandler(ConfigAccumulator(), engine._state)

        with patch("dev_kit.agent.conversation.list_checkpoints", return_value=[]):
            prompt = engine._build_system_prompt()

    mock_acc.get_action_gateway_tools.assert_called_once()
    assert "tool_a" in prompt or "tool_b" in prompt
