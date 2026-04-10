import pytest
from dev_kit.agent.deployer.helm import build_helm_command, build_template_command, DEPLOY_PHASES


def test_deploy_phases_has_six_phases():
    assert len(DEPLOY_PHASES) == 6


def test_build_helm_command_dpg_block():
    cmd = build_helm_command(
        chart_path="/charts/dpg/agent-core",
        release_name="agent-core",
        namespace="dpg-agent-core",
        kubeconfig_path="/tmp/kc",
        set_values={"anthropicApiKey": "sk-ant-test"},
        set_files={"dpgConfig": "/tmp/dpg.yaml", "domainConfig": "/tmp/domain.yaml"},
    )
    assert cmd[0] == "helm"
    assert "--namespace" in cmd
    assert "dpg-agent-core" in cmd
    assert "--set-file" in cmd
    assert "--kubeconfig" in cmd


def test_build_helm_command_infra():
    cmd = build_helm_command(
        chart_path="/charts/infra/redis",
        release_name="redis",
        namespace="dpg-redis",
        kubeconfig_path="/tmp/kc",
    )
    assert "redis" in cmd
    assert "--create-namespace" in cmd


def test_build_template_command():
    cmd = build_template_command(
        chart_path="/charts/dpg/agent-core",
        release_name="agent-core",
        set_values={"anthropicApiKey": "test"},
        set_files={"dpgConfig": "/tmp/dpg.yaml"},
    )
    assert cmd[1] == "template"
    assert "--set-file" in cmd


def test_build_helm_command_upgrade():
    cmd = build_helm_command(
        chart_path="/charts/dpg/agent-core",
        release_name="agent-core",
        namespace="dpg-agent-core",
        kubeconfig_path="/tmp/kc",
        upgrade=True,
    )
    assert "upgrade" in cmd
    assert "--install" in cmd
