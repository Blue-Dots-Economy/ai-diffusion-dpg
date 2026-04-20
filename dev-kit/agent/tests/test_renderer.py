"""Tests for dev_kit.agent.renderer."""
import pytest
import yaml
from pathlib import Path
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus, DRAFT_BLOCKS
from dev_kit.agent.renderer import render_all, render_block, _strip_status_header


class TestRenderBlock:
    def test_empty_block_writes_pending_file(self, tmp_path):
        acc = ConfigAccumulator()
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "no config" in content.lower() or content.strip().startswith("#")
        assert acc.get_status("trust_layer") == ConfigStatus.PENDING

    def test_block_with_valid_data_writes_complete(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "STATUS: draft" not in content
        assert acc.get_status("trust_layer") == ConfigStatus.COMPLETE

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


class TestStripStatusHeader:
    def test_strips_single_comment_line(self):
        raw = "# STATUS: draft\nkey: value\n"
        assert _strip_status_header(raw) == "key: value\n"

    def test_strips_multi_line_comment_block(self):
        raw = "# STATUS: stale\n#   - error one\n#   - error two\nkey: value\n"
        assert _strip_status_header(raw) == "key: value\n"

    def test_no_header_returns_unchanged(self):
        raw = "key: value\n"
        assert _strip_status_header(raw) == "key: value\n"

    def test_empty_string_returns_empty(self):
        assert _strip_status_header("") == ""

    def test_does_not_strip_inline_comments(self):
        raw = "key: value  # inline comment\nnested:\n  sub: x\n"
        assert _strip_status_header(raw) == raw


class TestRenderBlockPreservesBody:
    def test_existing_file_body_not_reformatted(self, tmp_path):
        """render_block must not reformat an existing YAML file's body."""
        original_body = "agent:\n  primary_model: claude-haiku-4-5-20251001\n  fallback_model: claude-haiku-4-5-20251001\n"
        out = tmp_path / "agent_core.yaml"
        out.write_text(original_body)
        acc = ConfigAccumulator()
        acc._data["agent_core"] = yaml.safe_load(original_body)
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        render_block(tmp_path, "agent_core", acc)
        assert out.read_text() == original_body

    def test_existing_stale_header_removed_when_fixed(self, tmp_path):
        """Re-rendering a valid file removes the old stale header."""
        original_body = "agent:\n  primary_model: claude-haiku-4-5-20251001\n"
        out = tmp_path / "agent_core.yaml"
        out.write_text("# STATUS: stale — validation errors detected:\n#   - old error\n" + original_body)
        acc = ConfigAccumulator()
        acc._data["agent_core"] = yaml.safe_load(original_body)
        render_block(tmp_path, "agent_core", acc)
        content = out.read_text()
        assert "# STATUS:" not in content
        assert "agent:" in content

    def test_new_file_uses_yaml_dump(self, tmp_path):
        """When the file does not exist, yaml.dump output is used."""
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        assert (tmp_path / "trust_layer.yaml").exists()
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert yaml.safe_load(content) is not None

    def test_updated_accumulator_overwrites_file(self, tmp_path):
        """When accumulator data differs from the file, the file is rewritten."""
        original_body = "agent:\n  primary_model: claude-haiku-4-5-20251001\n"
        out = tmp_path / "agent_core.yaml"
        out.write_text(original_body)
        acc = ConfigAccumulator()
        # Accumulator has different data than what's on disk
        acc._data["agent_core"] = {"agent": {"primary_model": "claude-opus-4-7"}}
        render_block(tmp_path, "agent_core", acc)
        content = out.read_text()
        assert "claude-opus-4-7" in content
        assert "claude-haiku-4-5-20251001" not in content


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
