"""Tests for the new render_all signature (no ConfigAccumulator dependency).

Covers:
  - Normal execution: YAML written per block, statuses all "complete"
  - Empty accumulator: placeholder files written, statuses "complete"
  - Single block with data: YAML file written with correct content
  - Mirror-schema warnings: advisory comment written, still returns "complete"
  - agent_core intents sync still fires through render_all
  - None project_path raises ValueError
  - Return values are always "complete" or "failed"
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.project_state import BLOCKS, empty_accumulator
from dev_kit.agent.renderer import render_all


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intake(**overrides) -> IntakeState:
    """Build a minimal IntakeState for testing."""
    defaults = dict(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="Test",
        project_name="testproj",
    )
    defaults.update(overrides)
    return IntakeState(**defaults)


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


class TestRenderAllNormal:
    def test_writes_yaml_per_block(self, tmp_path: Path) -> None:
        """render_all writes at least one YAML file when a block has data."""
        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        acc["agent_core"] = {"agent": {"primary_model": "claude-sonnet-4-5"}}
        statuses = render_all(project, acc, _intake())
        yaml_files = list(project.glob("*.yaml"))
        assert len(yaml_files) >= 1
        assert all(v in ("complete", "failed") for v in statuses.values())

    def test_returns_status_for_all_blocks(self, tmp_path: Path) -> None:
        """render_all returns a status for every known block."""
        acc = empty_accumulator()
        statuses = render_all(tmp_path, acc, _intake())
        assert set(statuses.keys()) == set(BLOCKS)

    def test_all_files_created(self, tmp_path: Path) -> None:
        """render_all creates exactly one YAML file per block."""
        acc = empty_accumulator()
        render_all(tmp_path, acc, _intake())
        for block in BLOCKS:
            assert (tmp_path / f"{block}.yaml").exists(), f"Missing {block}.yaml"

    def test_all_statuses_complete_on_empty_accumulator(self, tmp_path: Path) -> None:
        """Empty accumulator produces placeholder files and 'complete' status for all."""
        acc = empty_accumulator()
        statuses = render_all(tmp_path, acc, _intake())
        assert all(v == "complete" for v in statuses.values())

    def test_block_data_written_as_valid_yaml(self, tmp_path: Path) -> None:
        """A block with data produces a parseable YAML file with correct content."""
        acc = empty_accumulator()
        acc["knowledge_engine"] = {"retrieval": {"top_k": 5}}
        render_all(tmp_path, acc, _intake())
        parsed = yaml.safe_load((tmp_path / "knowledge_engine.yaml").read_text())
        assert parsed == {"retrieval": {"top_k": 5}}

    def test_status_complete_for_block_with_data(self, tmp_path: Path) -> None:
        """A block with valid data returns 'complete'."""
        acc = empty_accumulator()
        acc["knowledge_engine"] = {"retrieval": {"top_k": 5}}
        statuses = render_all(tmp_path, acc, _intake())
        assert statuses["knowledge_engine"] == "complete"

    def test_project_dir_created_if_absent(self, tmp_path: Path) -> None:
        """render_all creates the project directory if it does not exist yet."""
        project = tmp_path / "new_project"
        assert not project.exists()
        acc = empty_accumulator()
        render_all(project, acc, _intake())
        assert project.exists()

    def test_deploy_settings_accepted_but_ignored(self, tmp_path: Path) -> None:
        """deploy_settings kwarg is accepted without error (reserved for Phase 9)."""
        acc = empty_accumulator()
        statuses = render_all(tmp_path, acc, _intake(), deploy_settings={"some": "overlay"})
        assert set(statuses.keys()) == set(BLOCKS)


# ---------------------------------------------------------------------------
# Empty-block placeholder
# ---------------------------------------------------------------------------


class TestRenderAllEmptyBlock:
    def test_empty_block_writes_placeholder(self, tmp_path: Path) -> None:
        """An empty block writes a comment placeholder instead of empty YAML."""
        acc = empty_accumulator()
        render_all(tmp_path, acc, _intake())
        content = (tmp_path / "agent_core.yaml").read_text()
        assert "no config generated yet" in content

    def test_empty_block_returns_complete(self, tmp_path: Path) -> None:
        """An empty block is not a failure — it returns 'complete'."""
        acc = empty_accumulator()
        statuses = render_all(tmp_path, acc, _intake())
        assert statuses["agent_core"] == "complete"

    def test_placeholder_file_is_not_valid_yaml_data(self, tmp_path: Path) -> None:
        """The placeholder file parses to None/empty — not a config dict."""
        acc = empty_accumulator()
        render_all(tmp_path, acc, _intake())
        raw = (tmp_path / "trust_layer.yaml").read_text()
        lines = [l for l in raw.splitlines() if not l.startswith("#")]
        parsed = yaml.safe_load("\n".join(lines)) or {}
        assert parsed == {}


# ---------------------------------------------------------------------------
# agent_core-specific cleanups fire through render_all
# ---------------------------------------------------------------------------


class TestRenderAllAgentCoreCleanups:
    def test_missing_intents_synced_in_written_file(self, tmp_path: Path) -> None:
        """render_all auto-adds missing workflow intents to the NLU list."""
        acc = empty_accumulator()
        acc["agent_core"] = {
            "agent": {"primary_model": "claude-haiku-4-5"},
            "preprocessing": {"nlu_processor": {"intents": ["greeting"]}},
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
        }
        render_all(tmp_path, acc, _intake())
        written = yaml.safe_load((tmp_path / "agent_core.yaml").read_text())
        intents = written["preprocessing"]["nlu_processor"]["intents"]
        assert "report_problem" in intents
        assert "end_session" in intents
        assert "greeting" in intents

    def test_other_sentinel_not_written(self, tmp_path: Path) -> None:
        """'other' catch-all never appears in the written NLU intents list."""
        acc = empty_accumulator()
        acc["agent_core"] = {
            "agent": {"primary_model": "claude-haiku-4-5"},
            "preprocessing": {"nlu_processor": {"intents": ["greeting"]}},
            "agent_workflow": {
                "subagents": [
                    {"id": "sa", "is_start": True, "valid_intents": ["greeting", "other"]}
                ],
                "global_intents": ["other"],
                "workflow_id": "t",
                "version": "1.0",
            },
        }
        render_all(tmp_path, acc, _intake())
        written = yaml.safe_load((tmp_path / "agent_core.yaml").read_text())
        assert "other" not in written["preprocessing"]["nlu_processor"]["intents"]

    def test_non_agent_core_block_not_modified(self, tmp_path: Path) -> None:
        """Intent sync does not apply to blocks other than agent_core."""
        acc = empty_accumulator()
        acc["knowledge_engine"] = {"retrieval": {"top_k": 5}}
        render_all(tmp_path, acc, _intake())
        written = yaml.safe_load((tmp_path / "knowledge_engine.yaml").read_text())
        assert written == {"retrieval": {"top_k": 5}}


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestRenderAllInputValidation:
    def test_none_project_path_raises_valueerror(self) -> None:
        """Passing None as project_path raises ValueError immediately."""
        acc = empty_accumulator()
        with pytest.raises(ValueError, match="project_path"):
            render_all(None, acc, _intake())  # type: ignore[arg-type]

    def test_plain_dict_accumulator_accepted(self, tmp_path: Path) -> None:
        """A plain dict (not ConfigAccumulator) is accepted as accumulator."""
        acc = {"agent_core": {"agent": {"primary_model": "x"}}}
        # Missing blocks are treated as empty — no KeyError expected.
        statuses = render_all(tmp_path, acc, _intake())
        assert "agent_core" in statuses

    def test_internal_keys_stripped(self, tmp_path: Path) -> None:
        """Keys prefixed with _ in block data are stripped before writing."""
        acc = empty_accumulator()
        acc["knowledge_engine"] = {"retrieval": {"top_k": 3}, "_internal": "secret"}
        render_all(tmp_path, acc, _intake())
        written = yaml.safe_load((tmp_path / "knowledge_engine.yaml").read_text())
        assert "_internal" not in written
        assert written == {"retrieval": {"top_k": 3}}

    def test_render_all_raises_on_non_dict_block_value(self, tmp_path: Path) -> None:
        """A non-dict block value in the accumulator raises ValueError."""
        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        acc["agent_core"] = "not_a_dict"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="must be a dict"):
            render_all(project, acc, _intake())

    def test_render_all_raises_on_none_accumulator(self, tmp_path: Path) -> None:
        """Passing None as accumulator raises ValueError immediately."""
        project = tmp_path / "proj"
        with pytest.raises(ValueError, match="accumulator is required"):
            render_all(project, None, _intake())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mirror-schema warning header
# ---------------------------------------------------------------------------


class TestRenderAllWarningHeader:
    def test_mirror_warning_writes_warnings_header_once(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """validate_partial returning >=2 errors yields a single '# WARNINGS:' header."""
        from dev_kit.agent import renderer

        monkeypatch.setattr(
            renderer,
            "validate_partial",
            lambda block, data: ["bad field x", "bad field y"]
            if block == "agent_core"
            else [],
        )
        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        acc["agent_core"] = {"agent": {"primary_model": "claude-sonnet-4-5"}}
        statuses = render_all(project, acc, _intake())
        assert statuses["agent_core"] == "complete"
        body = (project / "agent_core.yaml").read_text()
        assert body.count("# WARNINGS:") == 1
        assert "#   - bad field x" in body
        assert "#   - bad field y" in body


class TestReachLayerWrap:
    """reach_layer renders under a top-level `reach_layer:` key.

    FIELD_RULES paths for the reach block are flat
    (`channels.web.ui.app_name`, `common.observability.domain`) so the
    accumulator stores those keys flat as well. The runtime
    `MergedConfig` for reach_layer expects a top-level `reach_layer:`
    wrapper — the renderer must add it. Tour Pal regression: a fresh
    project's reach_layer.yaml had top-level `channels:` and `common:`
    keys, producing two "Unknown section" warnings and YAML the runtime
    would reject at boot.
    """

    def test_reach_layer_yaml_has_top_level_wrapper(self, tmp_path: Path) -> None:
        """Even with only the skeleton+derived fields written, the YAML
        must start with `reach_layer:` (not `channels:` / `common:`).
        """
        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        # Mimic the state after build_skeleton + apply_derived_fields
        # for a `selected_channels=["web", "voice"]` project.
        acc["reach_layer"] = {
            "channels": {
                "voice": {"agent_core": {"timeout_ms": 15000}},
                "web": {
                    "ui": {
                        "storage_key": "tour_pal_user_id",
                        "theme_storage_key": "tour_pal_theme",
                    }
                },
            },
            "common": {"observability": {"domain": "tour_pal"}},
        }
        render_all(project, acc, _intake(selected_channels=["web", "voice"]))
        body = (project / "reach_layer.yaml").read_text()
        assert "# WARNINGS:" not in body, (
            f"expected no validate_partial warnings; got:\n{body}"
        )
        parsed = yaml.safe_load(
            "\n".join(line for line in body.splitlines() if not line.startswith("#"))
        )
        assert list(parsed.keys()) == ["reach_layer"], (
            f"reach_layer.yaml must start with `reach_layer:` wrapper; "
            f"got top-level keys: {list(parsed.keys())}"
        )
        assert parsed["reach_layer"]["channels"]["voice"]["agent_core"]["timeout_ms"] == 15000
        assert parsed["reach_layer"]["common"]["observability"]["domain"] == "tour_pal"

    def test_reach_layer_idempotent_wrap(self, tmp_path: Path) -> None:
        """If the accumulator was loaded back already wrapped (e.g. by
        load_block_from_file's symmetric unwrap fallback), the renderer
        does NOT double-wrap.
        """
        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        # Already wrapped — pretend a caller skipped the unwrap.
        acc["reach_layer"] = {
            "reach_layer": {"common": {"observability": {"domain": "foo"}}}
        }
        render_all(project, acc, _intake())
        body = (project / "reach_layer.yaml").read_text()
        parsed = yaml.safe_load(
            "\n".join(line for line in body.splitlines() if not line.startswith("#"))
        )
        # Single wrapper, not nested.
        assert list(parsed.keys()) == ["reach_layer"]
        assert "reach_layer" not in parsed["reach_layer"], (
            "double-wrap detected — _prepare_block_data must skip when "
            "the wrapper is already present"
        )

    def test_load_block_from_file_unwraps_reach_layer(self, tmp_path: Path) -> None:
        """Round-trip: render writes wrapped YAML, load reads it flat."""
        from dev_kit.agent.renderer import load_block_from_file

        project = tmp_path / "proj"
        project.mkdir()
        acc = empty_accumulator()
        acc["reach_layer"] = {
            "common": {"observability": {"domain": "tour_pal"}},
        }
        render_all(project, acc, _intake())
        loaded = load_block_from_file(project, "reach_layer")
        # Wrapper must be stripped — accumulator-shape is flat.
        assert "reach_layer" not in loaded, (
            f"load_block_from_file must unwrap; got {loaded!r}"
        )
        assert loaded["common"]["observability"]["domain"] == "tour_pal"
