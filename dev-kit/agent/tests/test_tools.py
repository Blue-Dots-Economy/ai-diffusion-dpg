"""Tests for dev_kit.agent.tools.ToolHandler."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.tools import ToolHandler, TOOL_DEFINITIONS


class TestToolDefinitions:
    def test_all_tools_defined(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert names == {
            "set_project_meta", "update_config", "set_phase",
            "create_subagent", "update_subagent", "add_routing_rule",
            "update_routing_rule", "remove_subagent",
            "finalize_config", "rollback_to_checkpoint",
            "parse_openapi_spec", "fetch_openapi_spec_from_url",
            "add_rest_api_tool", "set_response_transformation",
            "discover_mcp_tools", "add_mcp_tool", "set_reach_channels",
            "declare_azure_storage",
        }

    def test_each_tool_has_required_keys(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool


class TestToolHandlerUpdateConfig:
    def test_updates_accumulator(self):
        acc = ConfigAccumulator()
        state = {"phase": "language", "phase_changed": None}
        handler = ToolHandler(acc, state)
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "agent",
            "values": {"primary_model": "claude-haiku-4-5-20251001"},
        })
        assert acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert "ok" in result.lower() or "updated" in result.lower()


class TestToolHandlerSetPhase:
    def test_updates_phase_in_state(self):
        acc = ConfigAccumulator()
        state = {"phase": "overview", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("set_phase", {"phase": "language"})
        assert state["phase_changed"] == "language"


class TestToolHandlerSubagents:
    def test_create_subagent(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting",
            "name": "Greeting",
            "description": "Entry point",
            "system_prompt": "Welcome the user",
            "is_start": True,
            "is_terminal": False,
            "valid_intents": ["greeting"],
            "tools": [],
        })
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert subagents[0]["id"] == "greeting"

    def test_create_duplicate_subagent_returns_message(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        sa = {"id": "greeting", "name": "Greeting", "description": "x", "system_prompt": "y", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": []}
        handler.dispatch("create_subagent", sa)
        result = handler.dispatch("create_subagent", sa)
        assert "already exists" in result.lower() or "use update_subagent" in result.lower()

    def test_remove_subagent(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting", "name": "G", "description": "x",
            "system_prompt": "y", "is_start": True, "is_terminal": False,
            "valid_intents": [], "tools": [],
        })
        handler.dispatch("remove_subagent", {"id": "greeting"})
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"] == []

    def test_add_routing_rule(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting", "name": "G", "description": "x",
            "system_prompt": "y", "is_start": True, "is_terminal": False,
            "valid_intents": [], "tools": [],
        })
        handler.dispatch("add_routing_rule", {
            "from_subagent_id": "greeting",
            "intent": "consent_granted",
            "next_subagent_id": "profile",
        })
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0]["intent"] == "consent_granted"


def test_set_project_meta_merges_not_replaces():
    """_handle_set_project_meta must merge inputs into existing state, not replace it."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {
        "phase": "overview",
        "phase_changed": None,
        "rollback_to": None,
        "project_meta": {
            "slug": "test",
            "name": "Old Name",
            "current_phase": "overview",
            "phases_completed": ["overview"],
        },
    }
    handler = ToolHandler(ConfigAccumulator(), state)
    handler.dispatch("set_project_meta", {"name": "New Name", "description": "A desc"})
    meta = state["project_meta"]
    assert meta["phases_completed"] == ["overview"]
    assert meta["name"] == "New Name"
    assert meta["slug"] == "test"


def test_remove_subagent_returns_error_for_unknown_id():
    """_handle_remove_subagent must return an error string if the ID is not found."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(ConfigAccumulator(), state)
    result = handler.dispatch("remove_subagent", {"id": "nonexistent"})
    assert "not found" in result.lower() or "error" in result.lower()


def test_dispatch_unknown_tool_raises_value_error():
    """dispatch() must raise ValueError for an unrecognised tool name."""
    import pytest
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(ConfigAccumulator(), state)
    with pytest.raises(ValueError, match="Unknown tool"):
        handler.dispatch("totally_made_up_tool", {})


class TestToolHandlerFinalizeConfig:
    def test_sets_block_complete(self):
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "knowledge", {"blocks": {}})
        state = {"phase": "knowledge", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("finalize_config", {"block": "knowledge_engine"})
        assert acc.get_status("knowledge_engine") == ConfigStatus.COMPLETE
