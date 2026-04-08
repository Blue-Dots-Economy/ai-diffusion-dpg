import pytest
import yaml
from dev_kit.agent.deployer.compose import generate_compose


def test_generate_compose_has_all_services():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={"agent_core": {"limits": {"cpu": "1.0", "memory": "2G"}}},
        secrets={"anthropic_api_key": "sk-test"},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    assert "services" in parsed
    assert len(parsed["services"]) == 14


def test_generate_compose_agent_core_has_api_key():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={"anthropic_api_key": "sk-test"},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    agent_env = parsed["services"]["agent_core"].get("environment", [])
    assert any("ANTHROPIC_API_KEY" in str(e) for e in agent_env)


def test_generate_compose_has_network():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    assert "dpg_net" in parsed.get("networks", {})


def test_generate_compose_volume_mounts():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    ac_volumes = parsed["services"]["agent_core"].get("volumes", [])
    assert any("dpg.yaml" in str(v) for v in ac_volumes)
    assert any("domain.yaml" in str(v) for v in ac_volumes)
