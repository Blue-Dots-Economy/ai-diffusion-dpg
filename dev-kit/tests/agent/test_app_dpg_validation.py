"""Tests for the deploy wizard's DPG Framework Values endpoint validation.

PUT /api/projects/{slug}/deploy/dpg-values/{block} must reject content that
fails Pydantic validation, not just YAML parse errors.
"""
import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
import dev_kit.agent.deployer.dependencies as deps_module
from dev_kit.agent.accumulator import BLOCKS


def _create_infra_tmp(tmp_path: Path) -> Path:
    """Create a temp infra Helm dir with stub values.yaml for all 7 services."""
    # Minimal infra stubs needed for the endpoint to work
    infra_tmp = tmp_path / "helm_infra"
    infra_tmp.mkdir(exist_ok=True)
    for service in ["redis", "memgraph", "otel-collector", "jaeger", "prometheus", "loki", "grafana"]:
        chart_path = infra_tmp / service
        chart_path.mkdir(exist_ok=True)
        (chart_path / "values.yaml").write_text(f"# {service} stub\n")
    return infra_tmp


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Return a TestClient with DPG_DIR redirected to tmp_path."""
    dpg_tmp = tmp_path / "dpg"
    dpg_tmp.mkdir()
    # Write stub YAML files for each block so reads don't return empty strings
    for block in BLOCKS:
        (dpg_tmp / f"{block}.yaml").write_text(f"# {block} dpg stub\n")

    infra_tmp = _create_infra_tmp(tmp_path)

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "DPG_DIR", dpg_tmp)
    monkeypatch.setattr(deps_module, "HELM_INFRA_DIR", infra_tmp)
    app_module._engines.clear()

    # Enable strict schema validation
    monkeypatch.setenv("DEVKIT_DPG_SCHEMA_STRICT", "1")

    return TestClient(app_module.app)


def test_invalid_yaml_returns_400(client):
    """Returns 400 when YAML syntax is invalid."""
    res = client.put(
        "/api/projects/test-slug/deploy/dpg-values/agent_core",
        json={"content": "this is: not valid: yaml: ::"},
    )
    assert res.status_code == 400
    assert "Invalid YAML" in res.json()["detail"]


def test_unknown_block_returns_400(client):
    """Returns 400 when block name is not recognised."""
    res = client.put(
        "/api/projects/test-slug/deploy/dpg-values/bogus_block",
        json={"content": "key: value\n"},
    )
    assert res.status_code == 400


def test_schema_violation_returns_400(client):
    """Port out of range fails Pydantic validation."""
    res = client.put(
        "/api/projects/test-slug/deploy/dpg-values/memory_layer",
        json={
            "content": (
                "server:\n"
                "  host: 0.0.0.0\n"
                "  port: 99999\n"
                "redis:\n"
                "  host: redis\n"
                "memgraph:\n"
                "  uri: bolt://memgraph\n"
                "  user: memgraph\n"
                "observability:\n"
                "  otel:\n"
                "    collector_endpoint: http://otel\n"
            )
        },
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "server.port" in detail or "port" in detail


def test_valid_yaml_accepted(client):
    """Valid YAML that passes schema validation is accepted."""
    valid = (
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8002\n"
        "redis:\n"
        "  host: redis\n"
        "  port: 6379\n"
        "memgraph:\n"
        "  uri: bolt://memgraph:7687\n"
        "  user: memgraph\n"
        "observability:\n"
        "  otel:\n"
        "    collector_endpoint: http://otelcol:4317\n"
    )
    res = client.put(
        "/api/projects/test-slug/deploy/dpg-values/memory_layer",
        json={"content": valid},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}
