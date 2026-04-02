"""Tests for dev_kit.agent.checkpoints."""
import json
import pytest
from pathlib import Path
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.checkpoints import save_checkpoint, restore_checkpoint, list_checkpoints, build_summary


class TestSaveAndRestore:
    def test_save_creates_checkpoint_directory(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        history = [{"role": "user", "content": "hello"}]
        save_checkpoint(tmp_path, "05_trust", acc, history)
        assert (tmp_path / "_meta" / "checkpoints" / "05_trust").is_dir()

    def test_save_writes_all_files(self, tmp_path):
        acc = ConfigAccumulator()
        save_checkpoint(tmp_path, "01_overview", acc, [])
        checkpoint_dir = tmp_path / "_meta" / "checkpoints" / "01_overview"
        assert (checkpoint_dir / "accumulator.json").exists()
        assert (checkpoint_dir / "history.json").exists()
        assert (checkpoint_dir / "summary.txt").exists()
        assert (checkpoint_dir / "timestamp.json").exists()

    def test_restore_recovers_accumulator_state(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        save_checkpoint(tmp_path, "02_language", acc, [])
        restored_acc, summary = restore_checkpoint(tmp_path, "02_language")
        assert restored_acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert restored_acc.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_restore_recovers_history(self, tmp_path):
        acc = ConfigAccumulator()
        history = [{"role": "user", "content": "tell me about ITI workers"}]
        save_checkpoint(tmp_path, "01_overview", acc, history)
        restored_acc, summary = restore_checkpoint(tmp_path, "01_overview")
        assert isinstance(summary, str)

    def test_restore_missing_checkpoint_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            restore_checkpoint(tmp_path, "01_overview")


class TestListCheckpoints:
    def test_empty_project_returns_empty_list(self, tmp_path):
        assert list_checkpoints(tmp_path) == []

    def test_lists_saved_checkpoints_in_order(self, tmp_path):
        acc = ConfigAccumulator()
        save_checkpoint(tmp_path, "01_overview", acc, [])
        save_checkpoint(tmp_path, "02_language", acc, [])
        checkpoints = list_checkpoints(tmp_path)
        assert len(checkpoints) == 2
        assert checkpoints[0]["phase"] == "01_overview"
        assert checkpoints[1]["phase"] == "02_language"


class TestBuildSummary:
    def test_returns_string_with_phase(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        summary = build_summary("02_language", acc)
        assert isinstance(summary, str)
        assert "02_language" in summary
