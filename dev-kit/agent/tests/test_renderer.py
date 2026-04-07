"""Tests for dev_kit.agent.renderer."""
import pytest
import yaml
from pathlib import Path
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus, DRAFT_BLOCKS
from dev_kit.agent.renderer import render_all, render_block


class TestRenderBlock:
    def test_empty_block_writes_pending_file(self, tmp_path):
        acc = ConfigAccumulator()
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "no config" in content.lower() or content.strip().startswith("#")
        assert acc.get_status("trust_layer") == ConfigStatus.PENDING

    def test_draft_block_with_data_writes_draft_header(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "STATUS: draft" in content
        assert acc.get_status("trust_layer") == ConfigStatus.DRAFT

    def test_non_draft_block_with_data_no_draft_header(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "knowledge", {"blocks": {"glossary": {"enabled": True, "mappings": []}}})
        render_block(tmp_path, "knowledge_engine", acc)
        content = (tmp_path / "knowledge_engine.yaml").read_text()
        assert "STATUS: draft" not in content

    def test_written_yaml_is_parseable(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        parsed = yaml.safe_load(content)
        assert parsed is not None


class TestRenderAll:
    def test_creates_all_7_files(self, tmp_path):
        acc = ConfigAccumulator()
        render_all(tmp_path, acc)
        for block in ["agent_core", "knowledge_engine", "memory_layer",
                      "trust_layer", "action_gateway", "reach_layer", "observability_layer"]:
            assert (tmp_path / f"{block}.yaml").exists()

    def test_returns_status_dict_for_all_blocks(self, tmp_path):
        acc = ConfigAccumulator()
        statuses = render_all(tmp_path, acc)
        assert set(statuses.keys()) == {
            "agent_core", "knowledge_engine", "memory_layer",
            "trust_layer", "action_gateway", "reach_layer", "observability_layer",
        }
