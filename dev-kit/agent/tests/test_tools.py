"""Tests for dev_kit.agent.tools.ToolHandler."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.tools import ToolHandler, TOOL_DEFINITIONS


class TestToolDefinitions:
    def test_all_tools_defined(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert names == {
            "set_project_meta", "set_agent_type", "skip_optional_phase",
            "update_config", "set_phase",
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
            "values": {
                "primary_model": "claude-haiku-4-5-20251001",
                "fallback_model": "claude-sonnet-4-6",
            },
        })
        assert acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert "ok" in result.lower() or "updated" in result.lower()


class TestToolHandlerUpdateConfigChannelGuards:
    """GH-137: update_config rejects the removed agent.channels / reach_layer.channels paths."""

    def _handler(self):
        acc = ConfigAccumulator()
        state = {"phase": "reach", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        return ToolHandler(acc, state), acc

    def test_rejects_agent_channels(self):
        handler, _acc = self._handler()
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "agent.channels",
            "values": {"voice": {"system_prompt_suffix": "x"}},
        })
        assert "error" in result.lower()
        assert "channels" in result.lower()

    def test_rejects_agent_channels_subpath(self):
        handler, _acc = self._handler()
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "agent.channels.voice",
            "values": {"system_prompt_suffix": "x"},
        })
        assert "error" in result.lower()

    def test_rejects_reach_layer_channels_for_agent_core(self):
        handler, _acc = self._handler()
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "reach_layer.channels",
            "values": {"voice": {"turn_assembler": {}}},
        })
        assert "error" in result.lower()

    def test_accepts_top_level_channels(self):
        handler, acc = self._handler()
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "channels",
            "values": {"voice": {"system_prompt_suffix": "short"}},
        })
        assert "error" not in result.lower() or "ok" in result.lower()
        assert acc.get_block("agent_core").get("channels", {}).get("voice", {}).get(
            "system_prompt_suffix"
        ) == "short"


class TestToolHandlerSetPhase:
    def test_updates_phase_in_state(self):
        acc = ConfigAccumulator()
        state = {"phase": "overview", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("set_phase", {"phase": "language"})
        assert state["phase_changed"] == "language"


class TestToolHandlerSetPhaseGating:
    """GH-137: set_phase honours SHEET_REQUIREMENTS and persists phase_decisions."""

    def _seed_meta(self, tmp_path, meta: dict) -> None:
        import json
        (tmp_path / "_meta").mkdir(parents=True, exist_ok=True)
        (tmp_path / "_meta" / "project.json").write_text(json.dumps(meta))

    def test_auto_skips_user_state_for_transactional(self, tmp_path):
        import json
        self._seed_meta(tmp_path, {"agent_type": "transactional", "phase_decisions": {}})
        state = {"phase": "memory", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(ConfigAccumulator(), state, project_path=tmp_path)
        result = handler.dispatch("set_phase", {"phase": "user_state"})
        assert "skip" in result.lower() or "trust" in result.lower()
        meta = json.loads((tmp_path / "_meta" / "project.json").read_text())
        assert meta["phase_decisions"]["user_state"]["status"] == "not_applicable_for_type"

    def test_respects_answered_decision_when_advancing(self, tmp_path):
        self._seed_meta(tmp_path, {
            "agent_type": "conversational",
            "phase_decisions": {"memory": {"status": "answered", "timestamp": "x"}},
        })
        state = {"phase": "memory", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(ConfigAccumulator(), state, project_path=tmp_path)
        result = handler.dispatch("set_phase", {"phase": "user_state"})
        assert "ERROR" not in result
        assert state["phase_changed"] == "user_state"

    def test_skip_optional_records_decision_when_user_initiated(self, tmp_path):
        import json
        self._seed_meta(tmp_path, {"agent_type": "conversational", "phase_decisions": {}})
        state = {"phase": "knowledge", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(ConfigAccumulator(), state, project_path=tmp_path)
        handler.dispatch("skip_optional_phase", {"phase": "knowledge"})
        meta = json.loads((tmp_path / "_meta" / "project.json").read_text())
        assert meta["phase_decisions"]["knowledge"]["status"] == "skipped_by_user"

    def test_skip_optional_rejects_required_phase(self, tmp_path):
        self._seed_meta(tmp_path, {"agent_type": "conversational", "phase_decisions": {}})
        state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(ConfigAccumulator(), state, project_path=tmp_path)
        result = handler.dispatch("skip_optional_phase", {"phase": "overview"})
        assert "ERROR" in result


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


class TestToolHandlerSetAgentType:
    """GH-137: set_agent_type tool writes agent_type to project meta."""

    def test_writes_meta_on_disk(self, tmp_path):
        import json
        acc = ConfigAccumulator()
        state = {"phase": "tier", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        # Seed a minimal project.json so the writer merges instead of creating fresh.
        (tmp_path / "_meta").mkdir(parents=True)
        (tmp_path / "_meta" / "project.json").write_text(json.dumps({"slug": "p", "name": "P"}))
        handler = ToolHandler(acc, state, project_path=tmp_path)
        result = handler.dispatch("set_agent_type", {"type": "conversational"})
        assert "ok" in result.lower()
        meta = json.loads((tmp_path / "_meta" / "project.json").read_text())
        assert meta["agent_type"] == "conversational"

    def test_rejects_unknown_type(self):
        acc = ConfigAccumulator()
        state = {"phase": "tier", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(acc, state)
        result = handler.dispatch("set_agent_type", {"type": "hybrid"})
        assert "error" in result.lower() or "invalid" in result.lower()

    def test_tool_definition_present(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "set_agent_type" in names

    def test_writes_to_state_when_no_project_path(self):
        """Without a project_path, agent_type still lands in state['project_meta']."""
        acc = ConfigAccumulator()
        state = {"phase": "tier", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(acc, state)
        handler.dispatch("set_agent_type", {"type": "agentic"})
        assert state["project_meta"]["agent_type"] == "agentic"


class TestToolHandlerFinalizeConfig:
    def test_sets_block_complete(self):
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "knowledge", {"blocks": {}})
        state = {"phase": "knowledge", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("finalize_config", {"block": "knowledge_engine"})
        assert acc.get_status("knowledge_engine") == ConfigStatus.COMPLETE
