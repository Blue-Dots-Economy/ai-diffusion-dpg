"""Tests for dev_kit.agent.conversation.ConversationEngine."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dev_kit.agent.conversation import ConversationEngine
from dev_kit.agent.errors import ConversationError


def test_model_read_from_env(monkeypatch):
    """ConversationEngine must not hardcode the model name."""
    import importlib
    import dev_kit.agent.conversation as conv_module
    monkeypatch.setenv("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
    importlib.reload(conv_module)
    assert conv_module._MODEL == "claude-haiku-4-5-20251001"
    # Restore
    monkeypatch.delenv("DEVKIT_MODEL", raising=False)
    importlib.reload(conv_module)


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


class TestConversationEngineChatLegacyProjectRejected:
    """Tests that legacy projects (no intake_state.json) are rejected cleanly."""

    @pytest.mark.asyncio
    async def test_chat_raises_for_legacy_project(self, project_path, mock_client):
        """chat() must raise ConversationError when intake_state.json is absent."""
        engine = ConversationEngine(project_path, mock_client)
        with pytest.raises(ConversationError, match="older version"):
            await engine.chat("I want to build a jobs assistant")

    @pytest.mark.asyncio
    async def test_chat_legacy_error_does_not_modify_history(self, project_path, mock_client):
        """History must not be modified when chat() raises for a legacy project."""
        engine = ConversationEngine(project_path, mock_client)
        history_len_before = len(engine._history)
        with pytest.raises(ConversationError):
            await engine.chat("Hello")
        assert len(engine._history) == history_len_before


class TestConversationEnginePersistence:
    def test_engine_loads_existing_accumulator(self, project_path, mock_client):
        """_load() must restore a pre-seeded accumulator from disk."""
        from dev_kit.agent.accumulator import ConfigAccumulator
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["preloaded"]}})
        (project_path / "_meta" / "accumulator.json").write_text(
            json.dumps(acc.to_dict())
        )
        engine = ConversationEngine(project_path, mock_client)
        assert engine.accumulator.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["preloaded"]

    def test_load_handles_corrupt_accumulator_json(self, project_path, mock_client):
        """_load() must not crash on a corrupt accumulator.json — falls back to empty accumulator."""
        (project_path / "_meta" / "accumulator.json").write_text("NOT VALID JSON {{{{")
        # Should not raise
        engine = ConversationEngine(project_path, mock_client)
        assert engine.accumulator is not None
        assert engine.accumulator.get_block("trust_layer") == {}

    def test_load_handles_corrupt_project_json(self, project_path, mock_client):
        """_load() must not crash on a corrupt project.json — falls back to default phase."""
        (project_path / "_meta" / "project.json").write_text("{broken")
        engine = ConversationEngine(project_path, mock_client)
        assert engine._state["phase"] == "tier"
