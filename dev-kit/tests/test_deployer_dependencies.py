import pytest
import yaml
from dev_kit.agent.deployer.dependencies import (
    INFRA_SERVICES, get_defaults, get_service_config, update_service_config,
)


def test_infra_services_has_seven_entries():
    assert len(INFRA_SERVICES) == 7


def test_get_defaults_returns_all_services():
    defaults = get_defaults()
    assert "redis" in defaults
    assert "memgraph" in defaults
    assert "otel_collector" in defaults
    assert "jaeger" in defaults
    assert "prometheus" in defaults
    assert "loki" in defaults
    assert "grafana" in defaults


def test_each_default_has_image_and_resources():
    for name, cfg in get_defaults().items():
        assert "image" in cfg, f"{name} missing image"
        assert "resources" in cfg, f"{name} missing resources"


def test_get_service_config_returns_yaml_string():
    result = get_service_config("redis")
    parsed = yaml.safe_load(result)
    assert parsed["image"]["repository"] == "redis"


def test_get_service_config_unknown():
    with pytest.raises(ValueError, match="Unknown"):
        get_service_config("unknown_service")


def test_update_service_config():
    new_yaml = yaml.dump({"image": {"repository": "redis", "tag": "6-alpine"}, "resources": {}})
    update_service_config("redis", new_yaml)
    result = yaml.safe_load(get_service_config("redis"))
    assert result["image"]["tag"] == "6-alpine"
