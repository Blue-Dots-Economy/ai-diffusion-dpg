"""Tests for dev_kit.agent.conversation.ConversationEngine."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dev_kit.agent.conversation import ConversationEngine


def _make_text_response(text: str):
    """Build a mock Anthropic message response with stop_reason=end_turn."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_1"):
    """Build a mock Anthropic message response with stop_reason=tool_use."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


@pytest.fixture
def project_path(tmp_path):
    p = tmp_path / "test_project"
    p.mkdir()
    meta = p / "_meta"
    meta.mkdir()
    (p / "_meta" / "project.json").write_text(json.dumps({
        "slug": "test_project",
        "name": "Test",
        "description": "A test project",
        "current_phase": "overview",
        "phases_completed": [],
    }))
    return p


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


class TestConversationEngineChat:
    @pytest.mark.asyncio
    async def test_chat_returns_reply(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Hello! Tell me about your project.")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("I want to build a jobs assistant")
        assert "reply" in result
        assert len(result["reply"]) > 0

    @pytest.mark.asyncio
    async def test_chat_includes_phase_in_response(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Great idea!")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Hello")
        assert "phase" in result
        assert result["phase"] == "overview"

    @pytest.mark.asyncio
    async def test_chat_dispatches_tool_use(self, project_path, mock_client):
        # First call returns tool_use, second call returns text
        tool_response = _make_tool_use_response(
            "update_config",
            {"block": "trust_layer", "section": "trust", "values": {"input_rules": {"blocked_phrases": ["spam"]}}},
        )
        text_response = _make_text_response("Config updated!")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Add spam to blocked phrases")
        assert result["reply"] == "Config updated!"
        assert engine.accumulator.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["spam"]

    @pytest.mark.asyncio
    async def test_chat_advances_phase_on_set_phase_tool(self, project_path, mock_client):
        tool_response = _make_tool_use_response("set_phase", {"phase": "language"})
        text_response = _make_text_response("Moving to language configuration.")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Let's move on")
        assert result["phase"] == "language"

    @pytest.mark.asyncio
    async def test_chat_includes_graph_in_response(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Sure.")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Hello")
        assert "graph" in result
        assert "nodes" in result["graph"]
        assert "edges" in result["graph"]

    @pytest.mark.asyncio
    async def test_history_grows_with_each_turn(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Hello!")
        engine = ConversationEngine(project_path, mock_client)
        await engine.chat("Hi")
        await engine.chat("Tell me more")
        assert len(engine._history) == 4  # 2 user + 2 assistant


class TestConversationEnginePersistence:
    @pytest.mark.asyncio
    async def test_accumulator_persisted_after_tool_call(self, project_path, mock_client):
        tool_response = _make_tool_use_response(
            "update_config",
            {"block": "trust_layer", "section": "trust", "values": {"input_rules": {"blocked_phrases": ["x"]}}},
        )
        text_response = _make_text_response("Done.")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        await engine.chat("Add blocked phrase")
        assert (project_path / "_meta" / "accumulator.json").exists()

    @pytest.mark.asyncio
    async def test_engine_loads_existing_accumulator(self, project_path, mock_client):
        # Pre-seed an accumulator file
        from dev_kit.agent.accumulator import ConfigAccumulator
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["preloaded"]}})
        (project_path / "_meta" / "accumulator.json").write_text(
            json.dumps(acc.to_dict())
        )
        engine = ConversationEngine(project_path, mock_client)
        assert engine.accumulator.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["preloaded"]
