"""Tests for the Helm release values the dev-kit builds for DPG blocks.

Covers dev_kit.agent.app helpers used by the Kubernetes deploy and its preview:
  - _dpg_chart_path: chart directories under automation/helm/dpg-services/
  - _dpg_helm_values: --set / --set-file values per block, including the
    reach-layer parent chart (global config, web.* values, channel switches)
  - _selected_channels_for: channel selection read from the project's intake
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
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
