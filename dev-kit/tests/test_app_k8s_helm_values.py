"""Tests for the Helm release values the dev-kit builds for DPG blocks.

Covers dev_kit.agent.app helpers used by the Kubernetes deploy and its preview:
  - _dpg_chart_path: chart directories under automation/helm/dpg-services/
  - _dpg_helm_values: --set / --set-file values per block, including the
    reach-layer parent chart (global config, web.* values, channel switches)
  - _selected_channels_for: channel selection read from the project's intake
  - _infra_helm_values: values files and passwords for the infra charts
  - _run_k8s_deploy: one helm release per service, in DEPLOY_PHASES order
  - _spawn_background / POST deploy/execute (kubernetes): background deploy task
"""
from __future__ import annotations

import asyncio
import os
import unittest.mock as mock
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
import dev_kit.agent.deployer.dependencies as deps_module
from dev_kit.agent.deployer.helm import DEPLOY_PHASES
from dev_kit.agent.deployer.state import DeployState
from dev_kit.agent.intake_state import IntakeState, save_intake_state
from dev_kit.agent.project_state import BLOCKS

_REPO_HELM = Path(__file__).resolve().parents[2] / "automation" / "helm"


@pytest.fixture
def config_dirs(tmp_path, monkeypatch):
    """Point DPG_DIR / CONFIGS_DIR at temp dirs holding one YAML per block."""
    dpg_dir = tmp_path / "dpg"
    configs_dir = tmp_path / "configs"
    project = configs_dir / "proj"
    dpg_dir.mkdir()
    project.mkdir(parents=True)
    for block in BLOCKS:
        (dpg_dir / f"{block}.yaml").write_text(f"# {block} defaults\n")
        (project / f"{block}.yaml").write_text(f"# {block} domain\n")
    monkeypatch.setattr(app_module, "DPG_DIR", dpg_dir)
    monkeypatch.setattr(app_module, "CONFIGS_DIR", configs_dir)
    return dpg_dir, project


# ---------------------------------------------------------------------------
# _dpg_chart_path
# ---------------------------------------------------------------------------


def test_chart_path_points_at_dpg_services():
    path = app_module._dpg_chart_path(Path("/h"), "agent_core")
    assert path == "/h/dpg-services/agent-core"


@pytest.mark.parametrize("block", BLOCKS)
def test_every_block_has_a_chart_in_the_repo(block):
    chart = Path(app_module._dpg_chart_path(_REPO_HELM, block))
    assert (chart / "Chart.yaml").is_file(), f"missing chart for {block}: {chart}"


# ---------------------------------------------------------------------------
# _dpg_helm_values: blocks other than reach_layer
# ---------------------------------------------------------------------------


def test_block_config_files_are_top_level(config_dirs):
    dpg_dir, project = config_dirs
    _, set_files = app_module._dpg_helm_values("agent_core", "proj", {}, {})
    assert set_files == {
        "dpgConfig": str(dpg_dir / "agent_core.yaml"),
        "domainConfig": str(project / "agent_core.yaml"),
    }


def test_missing_config_files_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DPG_DIR", tmp_path / "nope")
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path / "nope")
    set_values, set_files = app_module._dpg_helm_values("trust_layer", "proj", {}, {})
    assert set_files == {}
    assert set_values == {}


def test_llm_keys_and_resources(config_dirs):
    secrets = {"openai_api_key": "sk-o", "anthropic_api_key": "sk-a"}
    resources = {"agent_core": {"limits": {"cpu": "1", "memory": "2Gi"}, "requests": {"cpu": "200m"}}}
    set_values, _ = app_module._dpg_helm_values("agent_core", "proj", secrets, resources)
    assert set_values == {
        "openaiApiKey": "sk-o",
        "anthropicApiKey": "sk-a",
        "resources.limits.cpu": "1",
        "resources.limits.memory": "2Gi",
        "resources.requests.cpu": "200m",
    }


def test_action_gateway_tool_secrets_skip_empty_values(config_dirs):
    secrets = {"tool_secrets": {"ONEST_API_KEY": "k1", "UNSET_KEY": ""}}
    set_values, _ = app_module._dpg_helm_values("action_gateway", "proj", secrets, {})
    assert set_values == {"extraSecrets.ONEST_API_KEY": "k1"}


def test_memory_layer_infra_credentials(config_dirs):
    secrets = {"memgraph_password": "mg", "redis_password": "rd"}
    set_values, _ = app_module._dpg_helm_values("memory_layer", "proj", secrets, {})
    assert set_values["memgraph.password"] == "mg"
    assert set_values["redis.url"] == "redis://:rd@redis:6379/0"


def test_knowledge_engine_azure_and_upload_chain(config_dirs):
    secrets = {
        "azure_storage_account": "acc",
        "azure_storage_key": "key",
        "azure_container_name": "c",
        "reach_to_ke_api_key": "r2k",
        "ke_to_devkit_api_key": "k2d",
        "ke_devkit_callback_url": "http://dk:8080",
    }
    set_values, _ = app_module._dpg_helm_values("knowledge_engine", "proj", secrets, {})
    assert set_values == {
        "azure.storageAccount": "acc",
        "azure.storageKey": "key",
        "azure.containerName": "c",
        "uploadAuth.reachToKeApiKey": "r2k",
        "uploadAuth.keToDevkitApiKey": "k2d",
        "uploadAuth.devkitCallbackUrl": "http://dk:8080",
    }


def test_node_port_flag_is_ignored_for_other_blocks(config_dirs):
    set_values, _ = app_module._dpg_helm_values("agent_core", "proj", {}, {}, expose_reach_node_port=True)
    assert not any("service" in key for key in set_values)


# ---------------------------------------------------------------------------
# _dpg_helm_values: reach_layer parent chart
# ---------------------------------------------------------------------------


def test_reach_config_goes_to_globals(config_dirs):
    dpg_dir, project = config_dirs
    _, set_files = app_module._dpg_helm_values("reach_layer", "proj", {}, {})
    assert set_files == {
        "global.dpgConfig": str(dpg_dir / "reach_layer.yaml"),
        "global.domainConfig": str(project / "reach_layer.yaml"),
    }


def test_reach_web_values_are_prefixed(config_dirs):
    secrets = {"devkit_to_reach_api_key": "d2r", "reach_to_ke_api_key": "r2k", "ke_internal_url": "http://ke:8001"}
    resources = {"reach_layer": {"limits": {"memory": "1Gi"}, "requests": {"cpu": "100m"}}}
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", secrets, resources)
    assert set_values == {
        "web.uploadAuth.devkitToReachApiKey": "d2r",
        "web.uploadAuth.reachToKeApiKey": "r2k",
        "web.uploadAuth.keInternalUrl": "http://ke:8001",
        "web.resources.limits.memory": "1Gi",
        "web.resources.requests.cpu": "100m",
    }


def test_reach_node_port_only_when_exposed(config_dirs):
    without, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {})
    with_port, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {}, expose_reach_node_port=True)
    assert "web.service.type" not in without
    assert with_port["web.service.type"] == "NodePort"
    assert with_port["web.service.nodePort"] == "30805"


def test_reach_without_channel_selection_deploys_web_only(config_dirs):
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {}, selected_channels=None)
    assert not any(key.endswith(".enabled") for key in set_values)
    assert "web.webMode" not in set_values


@pytest.mark.parametrize(
    "selected, expected_enabled, web_mode",
    [
        (["web"], set(), "full"),
        (["web", "voice"], {"voice.enabled"}, "full"),
        (["web", "voice", "mcp", "cli"], {"voice.enabled", "mcp.enabled"}, "full"),
        (["voice"], {"voice.enabled"}, "routing_only"),
        ([], set(), "routing_only"),
    ],
)
def test_reach_channels_follow_selection(config_dirs, selected, expected_enabled, web_mode):
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {}, selected_channels=selected)
    enabled = {key for key, value in set_values.items() if key.endswith(".enabled") and value == "true"}
    assert enabled == expected_enabled
    assert set_values["web.webMode"] == web_mode
    assert "bridge.enabled" not in set_values


def test_reach_bridge_enabled_when_domain_declares_it(config_dirs):
    _, project = config_dirs
    (project / "agent_core.yaml").write_text("channels:\n  bridge:\n    system_prompt_suffix: ''\n")
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {}, selected_channels=["web"])
    assert set_values["bridge.enabled"] == "true"


def test_reach_bridge_stays_off_for_other_channels(config_dirs):
    _, project = config_dirs
    (project / "agent_core.yaml").write_text("channels:\n  mcp: {}\n")
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {})
    assert "bridge.enabled" not in set_values


def test_reach_bridge_stays_off_for_unreadable_domain_config(config_dirs):
    _, project = config_dirs
    (project / "agent_core.yaml").write_text("channels: [unclosed\n")
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {})
    assert "bridge.enabled" not in set_values


def test_bridge_rule_does_not_apply_to_other_blocks(config_dirs):
    _, project = config_dirs
    (project / "agent_core.yaml").write_text("channels:\n  bridge: {}\n")
    set_values, _ = app_module._dpg_helm_values("agent_core", "proj", {}, {})
    assert "bridge.enabled" not in set_values


# ---------------------------------------------------------------------------
# _selected_channels_for
# ---------------------------------------------------------------------------


def test_selected_channels_from_intake(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    meta = tmp_path / "proj" / "_meta"
    meta.mkdir(parents=True)
    intake = IntakeState(
        project_name="Helm Values Test",
        domain_description="desc",
        selected_channels=["web", "voice"],
        default_language="english",
        supported_languages=["english"],
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
    )
    intake.touch()
    save_intake_state(meta / "intake_state.json", intake)
    assert app_module._selected_channels_for("proj") == ["web", "voice"]


def test_selected_channels_none_without_intake(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    assert app_module._selected_channels_for("proj") is None


# ---------------------------------------------------------------------------
# _infra_helm_values
# ---------------------------------------------------------------------------


@pytest.fixture
def infra_dir(tmp_path, monkeypatch):
    """Point HELM_INFRA_DIR at a temp dir holding a values.yaml per infra chart."""
    infra = tmp_path / "infra"
    for chart in ("redis", "memgraph", "otel-collector", "jaeger", "prometheus", "loki", "grafana"):
        (infra / chart).mkdir(parents=True)
        (infra / chart / "values.yaml").write_text("image: {}\n")
    monkeypatch.setattr(deps_module, "HELM_INFRA_DIR", infra)
    return infra


def test_infra_values_file_is_the_chart_values_yaml(infra_dir):
    set_values, values_files = app_module._infra_helm_values("otel_collector", {})
    assert values_files == [str(infra_dir / "otel-collector" / "values.yaml")]
    assert set_values == {}


@pytest.mark.parametrize(
    "service, secret, key",
    [
        ("redis", "redis_password", "password"),
        ("memgraph", "memgraph_password", "password"),
        ("grafana", "grafana_admin_password", "adminPassword"),
    ],
)
def test_infra_passwords(infra_dir, service, secret, key):
    set_values, _ = app_module._infra_helm_values(service, {secret: "pw"})
    assert set_values == {key: "pw"}


def test_infra_without_values_file_or_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(deps_module, "HELM_INFRA_DIR", tmp_path / "missing")
    set_values, values_files = app_module._infra_helm_values("loki", {"redis_password": "pw"})
    assert (set_values, values_files) == ({}, [])


# ---------------------------------------------------------------------------
# _run_k8s_deploy
# ---------------------------------------------------------------------------


def _flags(cmd: list[str], flag: str) -> dict[str, str]:
    """Collect ``flag key=value`` pairs from a helm command line."""
    out = {}
    for i, part in enumerate(cmd[:-1]):
        if part == flag:
            key, _, value = cmd[i + 1].partition("=")
            out[key] = value
    return out


def _run_deploy(tmp_path, monkeypatch, helm_result, **kwargs):
    """Run _run_k8s_deploy with run_helm_command mocked; return (state, commands)."""
    monkeypatch.setattr(app_module, "HELM_BASE", tmp_path / "helm")
    commands: list[list[str]] = []

    async def fake_run(cmd):
        commands.append(cmd)
        return helm_result(cmd)

    state = DeployState("kubernetes")
    with mock.patch("dev_kit.agent.deployer.helm.run_helm_command", side_effect=fake_run):
        asyncio.run(app_module._run_k8s_deploy("proj", state, {}, {}, "apiVersion: v1\n", "dpg-test", **kwargs))
    return state, commands


def _ok(cmd):
    return {"success": True, "stdout": "", "stderr": ""}


def test_deploy_installs_every_service_in_phase_order(tmp_path, monkeypatch, config_dirs, infra_dir):
    state, commands = _run_deploy(tmp_path, monkeypatch, _ok)
    expected = [svc for phase in DEPLOY_PHASES for svc in phase["services"]]
    releases = [cmd[cmd.index("--install") + 1] for cmd in commands]
    assert releases == [svc.replace("_", "-") for svc in expected]
    assert state.overall == "complete"
    assert all(entry["status"] == "running" for entry in state.services.values())
    assert state.namespace == "dpg-test"
    assert Path(state.kubeconfig_path).read_text() == "apiVersion: v1\n"


def test_deploy_chart_paths(tmp_path, monkeypatch, config_dirs, infra_dir):
    _, commands = _run_deploy(tmp_path, monkeypatch, _ok)
    charts = {cmd[cmd.index("--install") + 1]: cmd[cmd.index("--install") + 2] for cmd in commands}
    assert charts["agent-core"] == str(tmp_path / "helm" / "dpg-services" / "agent-core")
    assert charts["reach-layer"] == str(tmp_path / "helm" / "dpg-services" / "reach-layer")
    assert charts["otel-collector"] == str(tmp_path / "helm" / "infra" / "otel-collector")


def test_deploy_reach_release_values(tmp_path, monkeypatch, config_dirs, infra_dir):
    _, commands = _run_deploy(tmp_path, monkeypatch, _ok, selected_channels=["web", "voice"])
    reach = next(cmd for cmd in commands if "reach-layer" in cmd)
    values = _flags(reach, "--set")
    assert values["web.service.type"] == "NodePort"
    assert values["web.service.nodePort"] == "30805"
    assert values["voice.enabled"] == "true"
    assert set(_flags(reach, "--set-file")) == {"global.dpgConfig", "global.domainConfig"}


def test_deploy_infra_release_uses_values_file(tmp_path, monkeypatch, config_dirs, infra_dir):
    _, commands = _run_deploy(tmp_path, monkeypatch, _ok)
    redis = next(cmd for cmd in commands if cmd[cmd.index("--install") + 1] == "redis")
    assert redis[redis.index("-f") + 1] == str(infra_dir / "redis" / "values.yaml")


def test_deploy_marks_a_failed_release(tmp_path, monkeypatch, config_dirs, infra_dir):
    def fail_agent_core(cmd):
        if "agent-core" in cmd:
            return {"success": False, "stdout": "", "stderr": "Error: boom"}
        return _ok(cmd)

    state, commands = _run_deploy(tmp_path, monkeypatch, fail_agent_core)
    assert state.services["agent_core"]["status"] == "failed"
    assert state.services["trust_layer"]["status"] == "running"
    assert state.overall == "failed"
    assert len(commands) == sum(len(phase["services"]) for phase in DEPLOY_PHASES)


def test_deploy_exception_fails_the_deploy(tmp_path, monkeypatch, config_dirs, infra_dir):
    def explode(cmd):
        raise RuntimeError("helm missing")

    state, commands = _run_deploy(tmp_path, monkeypatch, explode)
    assert state.overall == "failed"
    assert len(commands) == 1


# ---------------------------------------------------------------------------
# _spawn_background and POST deploy/execute (kubernetes)
# ---------------------------------------------------------------------------


def test_spawn_background_keeps_task_until_done():
    async def scenario():
        gate = asyncio.Event()

        async def work():
            await gate.wait()

        task = app_module._spawn_background(work())
        held_while_running = task in app_module._BACKGROUND_TASKS
        gate.set()
        await task
        await asyncio.sleep(0)
        return held_while_running, task in app_module._BACKGROUND_TASKS

    assert asyncio.run(scenario()) == (True, False)


def test_execute_kubernetes_passes_selected_channels(tmp_path, monkeypatch, config_dirs):
    from fastapi.testclient import TestClient

    _, project = config_dirs
    meta = project / "_meta"
    meta.mkdir()
    (meta / "project.json").write_text('{"slug": "proj", "name": "Proj", "current_phase": "overview", "phases_completed": []}')
    intake = IntakeState(
        project_name="Proj",
        domain_description="desc",
        selected_channels=["web", "mcp"],
        default_language="english",
        supported_languages=["english"],
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
    )
    intake.touch()
    save_intake_state(meta / "intake_state.json", intake)

    valid = {"valid": True, "block_errors": {}, "invariant_errors": []}
    with mock.patch.object(app_module, "pre_deploy_validate", return_value=valid), \
         mock.patch.object(app_module, "_run_k8s_deploy", new_callable=mock.AsyncMock) as deploy:
        res = TestClient(app_module.app).post(
            "/api/projects/proj/deploy/execute",
            json={"target": "kubernetes", "kubeconfig": "apiVersion: v1\n", "namespace": "dpg-test"},
        )
    assert res.status_code == 200
    assert res.json() == {"status": "started", "target": "kubernetes"}
    args, kwargs = deploy.call_args
    assert args[0] == "proj"
    assert args[4:6] == ("apiVersion: v1\n", "dpg-test")
    assert kwargs["selected_channels"] == ["web", "mcp"]


def test_reach_bridge_stays_off_without_domain_agent_core(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DPG_DIR", tmp_path / "dpg")
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path / "configs")
    assert app_module._domain_declares_channel("proj", "bridge") is False
    set_values, _ = app_module._dpg_helm_values("reach_layer", "proj", {}, {})
    assert "bridge.enabled" not in set_values


def test_destroy_runs_teardown_in_background(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    with mock.patch.object(app_module, "_run_docker_destroy", new_callable=mock.AsyncMock) as destroy:
        res = TestClient(app_module.app).post("/api/projects/never-deployed/destroy", json={"remove_volumes": True})
    assert res.status_code == 200
    assert res.json() == {"status": "started"}
    destroy.assert_called_once_with("never-deployed", None, "dpg-never-deployed", True)
