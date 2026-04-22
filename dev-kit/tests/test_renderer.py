"""Tests for dev_kit.agent.renderer.

Covers:
  - _sync_agent_core_intents: merges workflow intents into NLU list
  - render_block: agent_core block gets intents auto-synced before write
  - render_block: non-agent_core blocks are not affected
  - render_all: all blocks written; statuses returned
"""
from __future__ import annotations

import os

import pytest
import yaml

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.renderer import _sync_agent_core_intents, render_all, render_block


# ---------------------------------------------------------------------------
# _sync_agent_core_intents — unit tests
# ---------------------------------------------------------------------------


def _make_agent_core_data(
    nlu_intents: list[str],
    subagent_valid_intents: list[list[str]],
    global_intents: list[str] | None = None,
) -> dict:
    """Build a minimal agent_core data dict for testing."""
    subagents = [
        {"id": f"sa_{i}", "valid_intents": vi}
        for i, vi in enumerate(subagent_valid_intents)
    ]
    workflow: dict = {"subagents": subagents}
    if global_intents is not None:
        workflow["global_intents"] = global_intents

    return {
        "preprocessing": {
            "nlu_processor": {"intents": list(nlu_intents)}
        },
        "agent_workflow": workflow,
    }


class TestSyncAgentCoreIntents:
    def test_no_op_when_all_intents_present(self) -> None:
        """No intents added when NLU list already covers the workflow."""
        data = _make_agent_core_data(
            nlu_intents=["greeting", "report_problem", "end_session"],
            subagent_valid_intents=[["greeting", "report_problem"]],
            global_intents=["end_session"],
        )
        result = _sync_agent_core_intents(data)
        assert result["preprocessing"]["nlu_processor"]["intents"] == [
            "greeting", "report_problem", "end_session"
        ]

    def test_missing_subagent_intents_are_appended(self) -> None:
        """Intents in subagent valid_intents but not in NLU list are added."""
        data = _make_agent_core_data(
            nlu_intents=["greeting"],
            subagent_valid_intents=[["greeting", "report_problem", "ask_scheme"]],
        )
        result = _sync_agent_core_intents(data)
        intent_set = set(result["preprocessing"]["nlu_processor"]["intents"])
        assert "report_problem" in intent_set
        assert "ask_scheme" in intent_set
        assert "greeting" in intent_set  # original preserved

    def test_missing_global_intents_are_appended(self) -> None:
        """Intents in global_intents but absent from NLU list are added."""
        data = _make_agent_core_data(
            nlu_intents=["greeting"],
            subagent_valid_intents=[["greeting"]],
            global_intents=["escalate_to_human", "end_session"],
        )
        result = _sync_agent_core_intents(data)
        intent_set = set(result["preprocessing"]["nlu_processor"]["intents"])
        assert "escalate_to_human" in intent_set
        assert "end_session" in intent_set

    def test_other_sentinel_excluded(self) -> None:
        """The catch-all 'other' is never added to the NLU intents list."""
        data = _make_agent_core_data(
            nlu_intents=["greeting"],
            subagent_valid_intents=[["greeting", "other"]],
            global_intents=["other"],
        )
        result = _sync_agent_core_intents(data)
        assert "other" not in result["preprocessing"]["nlu_processor"]["intents"]

    def test_no_workflow_key_returns_data_unchanged(self) -> None:
        """Data without agent_workflow is returned unchanged."""
        data = {"preprocessing": {"nlu_processor": {"intents": ["greeting"]}}}
        result = _sync_agent_core_intents(data)
        assert result == data

    def test_empty_workflow_returns_data_unchanged(self) -> None:
        """Empty agent_workflow dict is a no-op."""
        data = {
            "preprocessing": {"nlu_processor": {"intents": ["greeting"]}},
            "agent_workflow": {},
        }
        result = _sync_agent_core_intents(data)
        assert result["preprocessing"]["nlu_processor"]["intents"] == ["greeting"]

    def test_missing_preprocessing_section_created(self) -> None:
        """preprocessing and nlu_processor are created if absent."""
        data = {
            "agent_workflow": {
                "subagents": [{"id": "sa", "valid_intents": ["report_problem"]}]
            }
        }
        result = _sync_agent_core_intents(data)
        assert "report_problem" in result["preprocessing"]["nlu_processor"]["intents"]

    def test_missing_intents_key_in_nlu_created(self) -> None:
        """nlu_processor dict without 'intents' key is handled safely."""
        data = {
            "preprocessing": {"nlu_processor": {}},
            "agent_workflow": {
                "subagents": [{"id": "sa", "valid_intents": ["greeting"]}]
            },
        }
        result = _sync_agent_core_intents(data)
        assert "greeting" in result["preprocessing"]["nlu_processor"]["intents"]

    def test_added_intents_are_sorted(self) -> None:
        """New intents are appended in sorted order after existing ones."""
        data = _make_agent_core_data(
            nlu_intents=["greeting"],
            subagent_valid_intents=[["zebra_intent", "apple_intent"]],
        )
        result = _sync_agent_core_intents(data)
        intents = result["preprocessing"]["nlu_processor"]["intents"]
        # Original comes first; new ones appended in sorted order
        assert intents[0] == "greeting"
        assert intents[1:] == sorted(["apple_intent", "zebra_intent"])

    def test_no_duplicates_added(self) -> None:
        """Intents already present in the NLU list are not duplicated."""
        data = _make_agent_core_data(
            nlu_intents=["greeting", "report_problem"],
            subagent_valid_intents=[["greeting", "report_problem"]],
        )
        result = _sync_agent_core_intents(data)
        intents = result["preprocessing"]["nlu_processor"]["intents"]
        assert intents.count("greeting") == 1
        assert intents.count("report_problem") == 1


# ---------------------------------------------------------------------------
# render_block — integration tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def accumulator_with_agent_core() -> ConfigAccumulator:
    """Accumulator pre-loaded with a minimal agent_core block."""
    acc = ConfigAccumulator()
    acc.update(
        "agent_core",
        "",
        {
            "agent": {"primary_model": "claude-haiku-4-5-20251001"},
            "preprocessing": {
                "nlu_processor": {
                    "model": "claude-haiku-4-5-20251001",
                    "intents": ["greeting"],
                }
            },
            "agent_workflow": {
                "subagents": [
                    {
                        "id": "start",
                        "is_start": True,
                        "valid_intents": ["greeting", "report_problem"],
                    }
                ],
                "global_intents": ["end_session"],
                "workflow_id": "test",
                "version": "1.0",
            },
        },
    )
    return acc


class TestRenderBlock:
    def test_agent_core_intents_synced_in_written_file(
        self, tmp_path, accumulator_with_agent_core
    ) -> None:
        """render_block writes agent_core YAML with all workflow intents included."""
        render_block(tmp_path, "agent_core", accumulator_with_agent_core)
        written = yaml.safe_load((tmp_path / "agent_core.yaml").read_text())
        intents = written["preprocessing"]["nlu_processor"]["intents"]
        assert "report_problem" in intents
        assert "end_session" in intents
        assert "greeting" in intents

    def test_other_sentinel_not_written(self, tmp_path) -> None:
        """'other' must not appear in the written NLU intents list."""
        acc = ConfigAccumulator()
        acc.update(
            "agent_core",
            "",
            {
                "agent": {"primary_model": "claude-haiku-4-5-20251001"},
                "preprocessing": {"nlu_processor": {"intents": ["greeting"]}},
                "agent_workflow": {
                    "subagents": [
                        {"id": "sa", "is_start": True, "valid_intents": ["greeting", "other"]}
                    ],
                    "global_intents": ["other"],
                    "workflow_id": "t",
                    "version": "1.0",
                },
            },
        )
        render_block(tmp_path, "agent_core", acc)
        written = yaml.safe_load((tmp_path / "agent_core.yaml").read_text())
        assert "other" not in written["preprocessing"]["nlu_processor"]["intents"]

    def test_non_agent_core_block_not_modified(self, tmp_path) -> None:
        """render_block does not apply intent sync to non-agent_core blocks."""
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "", {"retrieval": {"top_k": 5}})
        render_block(tmp_path, "knowledge_engine", acc)
        written = yaml.safe_load((tmp_path / "knowledge_engine.yaml").read_text())
        assert written == {"retrieval": {"top_k": 5}}

    def test_empty_block_writes_placeholder(self, tmp_path) -> None:
        """render_block writes a placeholder comment for an empty block."""
        acc = ConfigAccumulator()
        render_block(tmp_path, "agent_core", acc)
        content = (tmp_path / "agent_core.yaml").read_text()
        assert "no config generated yet" in content
        assert acc.get_status("agent_core") == ConfigStatus.PENDING


# ---------------------------------------------------------------------------
# render_all — smoke test
# ---------------------------------------------------------------------------


class TestRenderAll:
    def test_returns_status_for_all_blocks(self, tmp_path) -> None:
        """render_all returns a status dict containing every block name."""
        from dev_kit.agent.accumulator import BLOCKS

        acc = ConfigAccumulator()
        statuses = render_all(tmp_path, acc)
        assert set(statuses.keys()) == set(BLOCKS)

    def test_all_files_created(self, tmp_path) -> None:
        """render_all creates a YAML file for every block."""
        from dev_kit.agent.accumulator import BLOCKS

        acc = ConfigAccumulator()
        render_all(tmp_path, acc)
        for block in BLOCKS:
            assert (tmp_path / f"{block}.yaml").exists()
