"""End-to-end integration tests for the tools-phase → workflow pipeline."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator, PHASES
from dev_kit.agent.tools import ToolHandler
from dev_kit.agent.prompts.phases import get_phase_addition
from dev_kit.agent.prompts.base import build_system_prompt


class TestToolsPhaseToWorkflowPipeline:
    """Test the full pipeline: add tools → accumulator → workflow prompt."""

    def _make_handler(self):
        acc = ConfigAccumulator()
        state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(acc, state)
        return acc, state, handler

    def test_add_rest_api_tool_then_appears_in_workflow_prompt(self):
        """Tool added in tools phase should appear in workflow phase prompt."""
        acc, state, handler = self._make_handler()
        handler.dispatch("add_rest_api_tool", {
            "id": "job_search",
            "category": "read",
            "description": "Search job listings",
            "base_url": "https://api.jobs.example.com",
            "auth_type": "none",
            "endpoints": [{"name": "search", "method": "GET", "path": "/search", "params": []}],
        })
        tool_ids = [t["id"] for t in acc.get_action_gateway_tools()]
        addition = get_phase_addition("workflow", available_tools=tool_ids)
        assert "job_search" in addition

    def test_add_mcp_tool_then_appears_in_workflow_prompt(self):
        """MCP adapter registered in tools phase should appear in workflow phase prompt."""
        acc, state, handler = self._make_handler()
        handler.dispatch("add_mcp_tool", {
            "id": "knowledge_retrieval",
            "category": "read",
            "description": "Retrieve domain knowledge",
            "mcp_server_url": "https://mcp.example.com",
        })
        tool_ids = [t["id"] for t in acc.get_action_gateway_tools()]
        addition = get_phase_addition("workflow", available_tools=tool_ids)
        assert "knowledge_retrieval" in addition

    def test_auto_sync_rest_api_tool_to_agent_core_connectors(self):
        """add_rest_api_tool auto-creates connector in agent_core.connectors."""
        acc, state, handler = self._make_handler()
        handler.dispatch("add_rest_api_tool", {
            "id": "weather_api",
            "category": "read",
            "description": "Get weather data",
            "base_url": "https://weather.example.com",
            "auth_type": "api_key",
            "auth_header": "X-API-KEY",
            "auth_secret_env": "WEATHER_API_KEY",
            "endpoints": [{
                "name": "get_weather",
                "method": "GET",
                "path": "/current",
                "params": [
                    {"name": "city", "source": "agent", "type": "string", "required": True, "description": "City name"},
                    {"name": "units", "source": "static", "type": "string", "value": "metric"},
                ],
            }],
        })
        connectors = acc.get_block("agent_core").get("connectors", {}).get("read", [])
        assert len(connectors) == 1
        assert connectors[0]["name"] == "weather_api"
        # Static params should NOT be in LLM-facing schema
        props = connectors[0]["input_schema"]["properties"]
        assert "city" in props
        assert "units" not in props

    def test_mcp_tool_does_not_create_agent_core_connector(self):
        """add_mcp_tool must NOT create a connector in agent_core.connectors.

        MCP tool schemas come from the server at runtime. Subagents reference
        MCP tools by namespaced names ('{adapter_id}.{tool_name}'), not via
        agent_core connector entries.
        """
        acc, state, handler = self._make_handler()
        handler.dispatch("add_mcp_tool", {
            "id": "doc_search",
            "category": "read",
            "description": "Search documents",
            "mcp_server_url": "https://mcp.example.com",
        })
        connectors = acc.get_block("agent_core").get("connectors", {}).get("read", [])
        assert len(connectors) == 0, (
            "MCP tools must not create agent_core connectors — "
            "schemas are discovered from the server at runtime."
        )

    def test_multiple_tools_all_appear_in_summary(self):
        """After adding multiple tools, summary() shows all tool IDs."""
        acc, state, handler = self._make_handler()
        handler.dispatch("add_rest_api_tool", {
            "id": "tool_a", "category": "read", "description": "Tool A",
            "base_url": "https://a.example.com", "auth_type": "none",
            "endpoints": [{"name": "go", "method": "GET", "path": "/go"}],
        })
        handler.dispatch("add_mcp_tool", {
            "id": "tool_b", "category": "write", "description": "Tool B",
            "mcp_server_url": "https://mcp.example.com",
        })
        summary = acc.summary()
        assert "tool_a" in summary
        assert "tool_b" in summary

    def test_build_system_prompt_includes_tool_ids_in_workflow_phase(self):
        """build_system_prompt in workflow phase includes configured tool IDs."""
        acc, state, handler = self._make_handler()
        handler.dispatch("add_rest_api_tool", {
            "id": "my_tool", "category": "read", "description": "My Tool",
            "base_url": "https://api.example.com", "auth_type": "none",
            "endpoints": [{"name": "call", "method": "POST", "path": "/call"}],
        })
        available_tools = [t["id"] for t in acc.get_action_gateway_tools()]
        prompt = build_system_prompt(
            project_name="Test",
            project_description="Test project",
            accumulator=acc,
            phase="workflow",
            checkpoint_summaries=[],
            available_tools=available_tools,
        )
        assert "my_tool" in prompt


class TestReachChannelPhaseFlow:
    """Test the reach channel selection and per-channel config flow."""

    def _make_handler(self):
        acc = ConfigAccumulator()
        state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(acc, state)
        return acc, state, handler

    def test_set_reach_channels_stored_in_accumulator(self):
        """set_reach_channels stores selected channels in reach_layer."""
        acc, state, handler = self._make_handler()
        result = handler.dispatch("set_reach_channels", {"channels": ["web", "cli"]})
        assert "web" in result and "cli" in result
        assert acc._data["reach_layer"]["_selected_channels"] == ["web", "cli"]

    def test_reach_phase_prompt_mentions_channel_adapters(self):
        """GH-137: Reach phase prompt frames channel adapters at pedagogy level."""
        addition = get_phase_addition("reach")
        assert "channel" in addition.lower()

    def test_reach_phase_prompt_mentions_voice_and_web(self):
        """GH-137: Reach phase prompt covers voice and web adapters."""
        addition = get_phase_addition("reach")
        assert "voice" in addition.lower()
        assert "web" in addition.lower()

    def test_update_config_web_ui_stored_correctly(self):
        """Updating web UI via update_config writes to correct path."""
        acc, state, handler = self._make_handler()
        result = handler.dispatch("update_config", {
            "block": "reach_layer",
            "section": "reach_layer.channels.web.ui",
            "values": {"app_name": "KKB", "app_icon": "💼"},
        })
        assert "ERROR" not in result
        assert acc.get_block("reach_layer")["reach_layer"]["channels"]["web"]["ui"]["app_name"] == "KKB"

    def test_update_config_cli_channel_stored_correctly(self):
        """Updating CLI channel via update_config writes to correct path."""
        acc, state, handler = self._make_handler()
        result = handler.dispatch("update_config", {
            "block": "reach_layer",
            "section": "reach_layer.channels.cli",
            "values": {"prompt": "You: ", "agent_prefix": "Agent: "},
        })
        assert "ERROR" not in result
        assert acc.get_block("reach_layer")["reach_layer"]["channels"]["cli"]["prompt"] == "You: "


class TestPhaseSequenceWithNewTools:
    """Test that phase ordering enforces the new tools phase position."""

    def _make_handler(self, phase="trust"):
        acc = ConfigAccumulator()
        state = {"phase": phase, "phase_changed": None, "rollback_to": None, "project_meta": {}}
        handler = ToolHandler(acc, state)
        return acc, state, handler

    def test_tools_is_phase_8_in_sequence(self):
        assert PHASES[7] == "tools"

    def test_cannot_skip_tools_to_workflow(self):
        acc, state, handler = self._make_handler(phase="trust")
        result = handler.dispatch("set_phase", {"phase": "workflow"})
        assert "ERROR" in result
        assert "tools" in result

    def test_trust_advances_to_tools(self):
        acc, state, handler = self._make_handler(phase="trust")
        result = handler.dispatch("set_phase", {"phase": "tools"})
        assert "ERROR" not in result
        assert state["phase_changed"] == "tools"

    def test_tools_advances_to_workflow(self):
        acc, state, handler = self._make_handler(phase="tools")
        result = handler.dispatch("set_phase", {"phase": "workflow"})
        assert "ERROR" not in result
        assert state["phase_changed"] == "workflow"
