"""Tests for IntakeState-driven service gating in deploy preview and execute.

Verifies that when IntakeState.has_kb=False or has_external_tools=False the
corresponding services are stripped from the generated docker-compose output,
and that deploy preview sets REACH_LAYER_WEB_MODE correctly.

See:
  docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8
  dev-kit/dev_kit/agent/app.py — get_deploy_preview / execute_deploy
"""
from __future__ import annotations

import json
import os

import pytest
import yaml as _yaml
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.intake_state import IntakeState, save_intake_state
from dev_kit.agent.project_state import BLOCKS, empty_accumulator
from dev_kit.agent.renderer import render_all


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_intake(**overrides) -> IntakeState:
    """Build a default IntakeState suitable for most tests."""
    base = dict(
        project_name="Test Bot",
        domain_description="A test deployment.",
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        has_kb=True,
        has_external_tools=True,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
    )
    base.update(overrides)
    return IntakeState(**base)


def _write_intake(project_path, intake: IntakeState) -> None:
    """Write intake_state.json to <project_path>/_meta/."""
    meta_dir = project_path / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    intake.touch()
    save_intake_state(meta_dir / "intake_state.json", intake)


def _build_project(tmp_path, slug: str, intake: IntakeState):
    """Create a minimal project directory with project.json, YAMLs, and intake_state.json."""
    project = tmp_path / slug
    project.mkdir(parents=True, exist_ok=True)
    meta_dir = project / "_meta"
    meta_dir.mkdir(exist_ok=True)
    meta = {
        "slug": slug,
        "name": intake.project_name,
        "description": intake.domain_description,
        "current_phase": "overview",
        "phases_completed": [],
    }
    (meta_dir / "project.json").write_text(json.dumps(meta))
    acc = empty_accumulator()
    render_all(project, acc, intake)
    _write_intake(project, intake)
    return project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_client(tmp_path, monkeypatch):
    """TestClient with CONFIGS_DIR and DPG_DIR redirected to tmp dirs."""
    dpg_tmp = tmp_path / "dpg"
    dpg_tmp.mkdir()
    for block in BLOCKS:
        (dpg_tmp / f"{block}.yaml").write_text(f"# {block} dpg stub\n")

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "DPG_DIR", dpg_tmp)
    monkeypatch.setenv("DEVKIT_DPG_SCHEMA_STRICT", "0")
    return TestClient(app_module.app), tmp_path


# ---------------------------------------------------------------------------
# Helper: parse compose YAML from preview response
# ---------------------------------------------------------------------------


def _preview_compose(client: TestClient, slug: str) -> dict:
    """POST /deploy/preview and return the parsed compose document."""
    res = client.post(
        f"/api/projects/{slug}/deploy/preview",
        json={"target": "docker"},
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    compose_str = res.json()["preview"]["docker-compose.yml"]
    return _yaml.safe_load(compose_str)


# ---------------------------------------------------------------------------
# Service inclusion / exclusion based on IntakeState flags
# ---------------------------------------------------------------------------


class TestIntakeStateServiceGating:
    """Deploy preview strips or retains services based on IntakeState flags."""

    def test_has_kb_false_removes_knowledge_engine(self, tmp_client):
        """knowledge_engine must not appear in compose when has_kb=False."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=False, has_external_tools=True)
        _build_project(tmp_path, "test-no-kb", intake)
        compose = _preview_compose(client, "test-no-kb")
        services = compose.get("services", {})
        assert "knowledge_engine" not in services, (
            "knowledge_engine must be stripped when has_kb=False"
        )

    def test_has_kb_true_retains_knowledge_engine(self, tmp_client):
        """knowledge_engine must appear in compose when has_kb=True."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=True, has_external_tools=True)
        _build_project(tmp_path, "test-with-kb", intake)
        compose = _preview_compose(client, "test-with-kb")
        services = compose.get("services", {})
        assert "knowledge_engine" in services, (
            "knowledge_engine must be retained when has_kb=True"
        )

    def test_has_external_tools_false_removes_action_gateway(self, tmp_client):
        """action_gateway must not appear in compose when has_external_tools=False."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=True, has_external_tools=False)
        _build_project(tmp_path, "test-no-ag", intake)
        compose = _preview_compose(client, "test-no-ag")
        services = compose.get("services", {})
        assert "action_gateway" not in services, (
            "action_gateway must be stripped when has_external_tools=False"
        )

    def test_has_external_tools_true_retains_action_gateway(self, tmp_client):
        """action_gateway must appear in compose when has_external_tools=True."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=True, has_external_tools=True)
        _build_project(tmp_path, "test-with-ag", intake)
        compose = _preview_compose(client, "test-with-ag")
        services = compose.get("services", {})
        assert "action_gateway" in services, (
            "action_gateway must be retained when has_external_tools=True"
        )

    def test_no_kb_no_tools_web_only_strips_both(self, tmp_client):
        """knowledge_engine and action_gateway both absent when both flags are False."""
        client, tmp_path = tmp_client
        intake = _make_intake(
            has_kb=False,
            has_external_tools=False,
            selected_channels=["web"],
        )
        _build_project(tmp_path, "test-lean", intake)
        compose = _preview_compose(client, "test-lean")
        services = compose.get("services", {})
        assert "knowledge_engine" not in services, "knowledge_engine must be stripped"
        assert "action_gateway" not in services, "action_gateway must be stripped"

    def test_voice_not_selected_removes_reach_layer_voice_and_ngrok(self, tmp_client):
        """reach_layer_voice and ngrok must be absent when voice is not selected."""
        client, tmp_path = tmp_client
        intake = _make_intake(selected_channels=["web"])
        _build_project(tmp_path, "test-web-only", intake)
        compose = _preview_compose(client, "test-web-only")
        services = compose.get("services", {})
        assert "reach_layer_voice" not in services
        assert "ngrok" not in services

    def test_voice_selected_retains_reach_layer_voice(self, tmp_client):
        """reach_layer_voice must be present when voice is in selected_channels."""
        client, tmp_path = tmp_client
        intake = _make_intake(selected_channels=["web", "voice"])
        _build_project(tmp_path, "test-voice", intake)
        compose = _preview_compose(client, "test-voice")
        services = compose.get("services", {})
        assert "reach_layer_voice" in services

    def test_dev_kit_is_always_removed(self, tmp_client):
        """dev_kit service must never appear in the generated compose output."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=True, has_external_tools=True, selected_channels=["web", "voice"])
        _build_project(tmp_path, "test-no-devkit", intake)
        compose = _preview_compose(client, "test-no-devkit")
        assert "dev_kit" not in compose.get("services", {})

    def test_reach_layer_web_always_present(self, tmp_client):
        """reach_layer_web must remain even when 'web' is not in selected_channels."""
        client, tmp_path = tmp_client
        intake = _make_intake(selected_channels=["voice"])
        _build_project(tmp_path, "test-voice-only", intake)
        compose = _preview_compose(client, "test-voice-only")
        assert "reach_layer_web" in compose.get("services", {})


# ---------------------------------------------------------------------------
# REACH_LAYER_WEB_MODE injection
# ---------------------------------------------------------------------------


class TestWebModeInjection:
    """REACH_LAYER_WEB_MODE env var is set correctly based on selected_channels."""

    def _web_env(self, client, slug):
        compose = _preview_compose(client, slug)
        svc = compose.get("services", {}).get("reach_layer_web", {})
        return svc.get("environment", [])

    def test_web_selected_sets_full(self, tmp_client):
        """REACH_LAYER_WEB_MODE=full when 'web' is in selected_channels."""
        client, tmp_path = tmp_client
        intake = _make_intake(selected_channels=["web"])
        _build_project(tmp_path, "test-web-mode-full", intake)
        env = self._web_env(client, "test-web-mode-full")
        assert any("REACH_LAYER_WEB_MODE=full" in str(e) for e in env)

    def test_voice_only_sets_routing_only(self, tmp_client):
        """REACH_LAYER_WEB_MODE=routing_only when 'web' is not in selected_channels."""
        client, tmp_path = tmp_client
        intake = _make_intake(selected_channels=["voice"])
        _build_project(tmp_path, "test-web-mode-routing", intake)
        env = self._web_env(client, "test-web-mode-routing")
        assert any("REACH_LAYER_WEB_MODE=routing_only" in str(e) for e in env)


# ---------------------------------------------------------------------------
# depends_on stripping
# ---------------------------------------------------------------------------


class TestDependsOnStripping:
    """depends_on references to removed services are stripped to avoid compose errors."""

    def test_no_dangling_depends_on_refs(self, tmp_client):
        """After service removal, no remaining service depends_on a removed service."""
        client, tmp_path = tmp_client
        intake = _make_intake(has_kb=False, has_external_tools=False, selected_channels=["web"])
        _build_project(tmp_path, "test-nodeps", intake)
        compose = _preview_compose(client, "test-nodeps")
        services = compose.get("services", {})
        removed = {"knowledge_engine", "action_gateway", "reach_layer_voice", "ngrok", "dev_kit"}
        for svc_name, svc in services.items():
            deps = svc.get("depends_on")
            if deps is None:
                continue
            if isinstance(deps, list):
                bad = [d for d in deps if d in removed]
            elif isinstance(deps, dict):
                bad = [k for k in deps if k in removed]
            else:
                bad = []
            assert not bad, (
                f"Service '{svc_name}' still depends_on removed service(s): {bad}"
            )


# ---------------------------------------------------------------------------
# Legacy project without intake_state.json returns 400
# ---------------------------------------------------------------------------


class TestLegacyProjectWithoutIntakeState:
    """Deploy preview returns 400 for projects missing intake_state.json."""

    def test_missing_intake_state_returns_400(self, tmp_client):
        """POST /deploy/preview returns 400 when intake_state.json is absent."""
        client, tmp_path = tmp_client
        slug = "legacy-project"
        project = tmp_path / slug
        project.mkdir()
        meta_dir = project / "_meta"
        meta_dir.mkdir()
        (meta_dir / "project.json").write_text(
            json.dumps({
                "slug": slug, "name": "Legacy", "description": "",
                "current_phase": "overview", "phases_completed": [],
            })
        )
        acc = empty_accumulator()
        render_all(project, acc, _make_intake())
        # Intentionally do NOT write intake_state.json.

        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={"target": "docker"},
        )
        assert res.status_code == 400
        assert "intake_state.json" in res.json().get("detail", "")
