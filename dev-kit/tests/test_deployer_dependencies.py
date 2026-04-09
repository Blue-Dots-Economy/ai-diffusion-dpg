import pytest
import yaml
from unittest.mock import patch
from pathlib import Path
import tempfile
import shutil
from dev_kit.agent.deployer.dependencies import (
    SERVICE_CHART_MAP, get_defaults, get_service_config, get_service_names,
    update_service_config,
)


def test_service_chart_map_has_seven_entries():
    assert len(SERVICE_CHART_MAP) == 7


def test_get_service_names_returns_all():
    names = get_service_names()
    for svc in ["redis", "memgraph", "otel_collector", "jaeger", "prometheus", "loki", "grafana"]:
        assert svc in names


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
    """Test update writes to disk using a temp directory to avoid corrupting real files."""
    import dev_kit.agent.deployer.dependencies as deps_mod

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # Create a fake redis chart dir with a values.yaml
        redis_dir = tmp_dir / "redis"
        redis_dir.mkdir()
        original_yaml = yaml.dump({"image": {"repository": "redis", "tag": "7-alpine"}, "resources": {}})
        (redis_dir / "values.yaml").write_text(original_yaml)

        # Patch HELM_INFRA_DIR to point to tmp
        with patch.object(deps_mod, "HELM_INFRA_DIR", tmp_dir):
            new_yaml = yaml.dump({"image": {"repository": "redis", "tag": "6-alpine"}, "resources": {}})
            update_service_config("redis", new_yaml)
            result = yaml.safe_load(get_service_config("redis"))
            assert result["image"]["tag"] == "6-alpine"
    finally:
        shutil.rmtree(tmp_dir)
