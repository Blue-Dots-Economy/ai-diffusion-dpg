"""Tests for deploy REST endpoints in dev_kit.agent.app.

Covers:
  - GET  /api/projects/{slug}/deploy/dpg-values
  - PUT  /api/projects/{slug}/deploy/dpg-values/{block}
  - GET  /api/projects/{slug}/deploy/dependencies
  - PUT  /api/projects/{slug}/deploy/dependencies/{service}
  - GET  /api/projects/{slug}/deploy/resource-presets
  - POST /api/projects/{slug}/deploy/resource-presets/{tier}
  - POST /api/projects/{slug}/deploy/validate-kubeconfig
  - POST /api/projects/{slug}/deploy/preview
  - POST /api/projects/{slug}/deploy/execute
  - GET  /api/projects/{slug}/deploy/status
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
import dev_kit.agent.deployer.dependencies as deps_module
from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator

# Stub YAML for infra services used in tests
_INFRA_STUB_YAML = {
    "redis": "image:\n  repository: redis\n  tag: '7-alpine'\nservice:\n  port: 6379\nresources:\n  requests:\n    cpu: 50m\n    memory: 64Mi\n  limits:\n    cpu: 100m\n    memory: 128Mi\npassword: ''\n",
    "memgraph": "image:\n  repository: memgraph/memgraph\n  tag: latest\nservice:\n  boltPort: 7687\n  httpPort: 7444\nresources:\n  requests:\n    cpu: 100m\n    memory: 256Mi\n  limits:\n    cpu: 500m\n    memory: 1Gi\npassword: ''\n",
    "otel-collector": "image:\n  repository: otel/opentelemetry-collector-contrib\n  tag: '0.96.0'\nresources:\n  requests:\n    cpu: 50m\n    memory: 64Mi\n  limits:\n    cpu: 100m\n    memory: 256Mi\n",
    "jaeger": "image:\n  repository: jaegertracing/all-in-one\n  tag: '1.55'\nresources:\n  requests:\n    cpu: 50m\n    memory: 64Mi\n  limits:\n    cpu: 100m\n    memory: 256Mi\n",
    "prometheus": "image:\n  repository: prom/prometheus\n  tag: v2.50.1\nresources:\n  requests:\n    cpu: 50m\n    memory: 64Mi\n  limits:\n    cpu: 100m\n    memory: 256Mi\n",
    "loki": "image:\n  repository: grafana/loki\n  tag: '2.9.4'\nresources:\n  requests:\n    cpu: 50m\n    memory: 64Mi\n  limits:\n    cpu: 100m\n    memory: 256Mi\n",
    "grafana": "image:\n  repository: grafana/grafana\n  tag: '10.3.3'\nresources:\n  requests:\n    cpu: 50m\n    memory: 128Mi\n  limits:\n    cpu: 200m\n    memory: 256Mi\nadminPassword: admin\n",
}


def _create_infra_tmp(tmp_path: Path) -> Path:
    """Create a temp infra Helm dir with stub values.yaml for all 7 services."""
    infra_tmp = tmp_path / "helm_infra"
    infra_tmp.mkdir(exist_ok=True)
    for chart_dir, content in _INFRA_STUB_YAML.items():
        chart_path = infra_tmp / chart_dir
        chart_path.mkdir(exist_ok=True)
        (chart_path / "values.yaml").write_text(content)
    return infra_tmp


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with CONFIGS_DIR, DPG_DIR, and HELM_INFRA_DIR redirected to tmp dirs."""
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
    return TestClient(app_module.app)


@pytest.fixture()
def project_slug():
    return "deploy-test"


@pytest.fixture()
def project_dir(tmp_path, project_slug):
    """Create a minimal project directory so the project exists."""
    project = tmp_path / project_slug
    project.mkdir()
    meta_dir = project / "_meta"
    meta_dir.mkdir()
    meta = {
        "slug": project_slug,
        "name": "Deploy Test",
        "description": "desc",
        "current_phase": "overview",
        "phases_completed": [],
    }
    (meta_dir / "project.json").write_text(json.dumps(meta))
    acc = ConfigAccumulator()
    from dev_kit.agent.renderer import render_all
    render_all(project, acc)
    return project


@pytest.fixture()
def client_with_project(tmp_path, monkeypatch, project_dir, project_slug):
    """Return a (TestClient, slug) tuple with a pre-created project."""
    dpg_tmp = tmp_path / "dpg"
    dpg_tmp.mkdir(exist_ok=True)
    for block in BLOCKS:
        (dpg_tmp / f"{block}.yaml").write_text(f"# {block} dpg stub\n")

    infra_tmp = _create_infra_tmp(tmp_path)

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "DPG_DIR", dpg_tmp)
    monkeypatch.setattr(deps_module, "HELM_INFRA_DIR", infra_tmp)
    app_module._engines.clear()
    return TestClient(app_module.app), project_slug


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/deploy/dpg-values
# ---------------------------------------------------------------------------


class TestGetDpgValues:
    def test_returns_all_7_blocks(self, client_with_project):
        """Returns exactly 7 items — one per DPG block."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dpg-values")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == len(BLOCKS)

    def test_each_item_has_block_and_content(self, client_with_project):
        """Each item has block and content keys."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dpg-values")
        assert res.status_code == 200
        for item in res.json():
            assert "block" in item
            assert "content" in item

    def test_block_names_match_blocks_constant(self, client_with_project):
        """Block names in response match the BLOCKS constant exactly."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dpg-values")
        assert res.status_code == 200
        returned_blocks = {item["block"] for item in res.json()}
        assert returned_blocks == set(BLOCKS)

    def test_content_is_string(self, client_with_project):
        """Content field is always a string, even if file is empty."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dpg-values")
        assert res.status_code == 200
        for item in res.json():
            assert isinstance(item["content"], str)


# ---------------------------------------------------------------------------
# PUT /api/projects/{slug}/deploy/dpg-values/{block}
# ---------------------------------------------------------------------------


class TestUpdateDpgValue:
    def test_update_valid_block_returns_ok(self, client_with_project):
        """Valid block update returns status ok."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/deploy/dpg-values/agent_core",
            json={"content": "agent:\n  primary_model: test-model\n"},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_400_for_unknown_block(self, client_with_project):
        """Returns 400 when block name is not recognised."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/deploy/dpg-values/not_a_block",
            json={"content": "key: value\n"},
        )
        assert res.status_code == 400

    def test_update_writes_content_to_disk(self, client_with_project, tmp_path):
        """Updated content is persisted to the DPG_DIR file."""
        client, slug = client_with_project
        new_content = "# updated agent_core content\nagent:\n  primary_model: new-model\n"
        client.put(
            f"/api/projects/{slug}/deploy/dpg-values/agent_core",
            json={"content": new_content},
        )
        written = (tmp_path / "dpg" / "agent_core.yaml").read_text()
        assert written == new_content


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/deploy/dependencies
# ---------------------------------------------------------------------------


class TestGetDependencies:
    def test_returns_7_services(self, client_with_project):
        """Returns exactly 7 infrastructure services."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dependencies")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 7

    def test_each_service_has_config_and_defaults(self, client_with_project):
        """Each service entry has config and defaults keys."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dependencies")
        assert res.status_code == 200
        for _name, info in res.json().items():
            assert "config" in info
            assert "defaults" in info

    def test_known_services_present(self, client_with_project):
        """Known service names (redis, grafana) are present in response."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/dependencies")
        assert res.status_code == 200
        keys = set(res.json().keys())
        assert "redis" in keys
        assert "grafana" in keys


# ---------------------------------------------------------------------------
# PUT /api/projects/{slug}/deploy/dependencies/{service}
# ---------------------------------------------------------------------------


class TestUpdateDependency:
    def test_update_valid_service_returns_ok(self, client_with_project):
        """Valid service update returns status ok (writes to tmp dir, not real files)."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/deploy/dependencies/redis",
            json={"content": "password: secret\n"},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_400_for_unknown_service(self, client_with_project):
        """Returns 400 when service name is not recognised."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/deploy/dependencies/unknown_service",
            json={"content": "key: value\n"},
        )
        assert res.status_code == 400

    def test_400_for_invalid_yaml(self, client_with_project):
        """Returns 400 when YAML content is malformed."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/deploy/dependencies/redis",
            json={"content": ":::not yaml:::\n  - {"},
        )
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/deploy/resource-presets
# ---------------------------------------------------------------------------


class TestGetResourcePresets:
    def test_returns_3_tiers(self, client_with_project):
        """Returns exactly 3 preset tiers."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/resource-presets")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 3

    def test_tier_names_are_low_medium_high(self, client_with_project):
        """Tier names are low, medium, and high."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/resource-presets")
        assert res.status_code == 200
        assert set(res.json().keys()) == {"low", "medium", "high"}

    def test_each_tier_has_7_blocks(self, client_with_project):
        """Each tier contains resources for all 7 DPG blocks."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/deploy/resource-presets")
        assert res.status_code == 200
        for tier_name, tier_data in res.json().items():
            assert len(tier_data) == 7, f"Tier '{tier_name}' has {len(tier_data)} blocks, expected 7"


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/deploy/resource-presets/{tier}
# ---------------------------------------------------------------------------


class TestApplyResourcePreset:
    def test_apply_low_preset_returns_resources(self, client_with_project):
        """Applying 'low' preset returns resource map for all 7 blocks."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/resource-presets/low")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 7
        # Each block should have requests and limits
        for block_name, spec in data.items():
            assert "requests" in spec
            assert "limits" in spec

    def test_apply_medium_preset_returns_resources(self, client_with_project):
        """Applying 'medium' preset returns resource map for all 7 blocks."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/resource-presets/medium")
        assert res.status_code == 200
        assert len(res.json()) == 7

    def test_apply_high_preset_returns_resources(self, client_with_project):
        """Applying 'high' preset returns resource map for all 7 blocks."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/resource-presets/high")
        assert res.status_code == 200
        assert len(res.json()) == 7

    def test_400_for_invalid_preset_tier(self, client_with_project):
        """Returns 400 when tier name is not recognised."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/resource-presets/ultra")
        assert res.status_code == 400

    def test_400_for_empty_tier_name(self, client_with_project):
        """Returns 400 when tier name is empty or nonsensical."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/resource-presets/")
        # FastAPI routing: empty segment is a 404 or 400
        assert res.status_code in (400, 404, 405)


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/deploy/validate-kubeconfig
# ---------------------------------------------------------------------------


class TestValidateKubeconfig:
    def test_valid_kubeconfig_returns_cluster_info(self, client_with_project):
        """A valid kubeconfig returns cluster info dict."""
        client, slug = client_with_project
        mock_result = {"cluster": "test-cluster", "valid": True}

        async def _mock_validate(content):
            return mock_result

        with mock.patch(
            "dev_kit.agent.deployer.kubeconfig.validate_kubeconfig",
            side_effect=_mock_validate,
        ):
            res = client.post(
                f"/api/projects/{slug}/deploy/validate-kubeconfig",
                json={"content": "apiVersion: v1\nclusters: []\n"},
            )
        assert res.status_code == 200
        assert res.json() == mock_result

    def test_invalid_kubeconfig_returns_400(self, client_with_project):
        """Invalid kubeconfig returns 400."""
        client, slug = client_with_project

        async def _raise(content):
            raise ValueError("Invalid kubeconfig: missing clusters")

        with mock.patch("dev_kit.agent.deployer.kubeconfig.validate_kubeconfig", side_effect=_raise):
            res = client.post(
                f"/api/projects/{slug}/deploy/validate-kubeconfig",
                json={"content": "not-valid"},
            )
        assert res.status_code == 400


async def _async_return(return_value, *_args, **_kwargs):
    """Return return_value as a coroutine, ignoring all arguments."""
    return return_value


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/deploy/preview
# ---------------------------------------------------------------------------


class TestGetDeployPreview:
    def test_docker_target_returns_compose_preview(self, client_with_project):
        """Docker target returns docker-compose.yml in preview."""
        client, slug = client_with_project
        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={"target": "docker"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["target"] == "docker"
        assert "docker-compose.yml" in data["preview"]

    def test_compose_content_is_non_empty_yaml(self, client_with_project):
        """Generated docker-compose.yml is a non-empty YAML string."""
        import yaml as _yaml

        client, slug = client_with_project
        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={"target": "docker"},
        )
        assert res.status_code == 200
        compose_str = res.json()["preview"]["docker-compose.yml"]
        parsed = _yaml.safe_load(compose_str)
        assert isinstance(parsed, dict)
        assert "services" in parsed

    def test_kubernetes_target_returns_preview_dict(self, client_with_project):
        """Kubernetes target returns preview dict with all 14 services."""
        async def _mock_run_helm(cmd):
            return {"success": True, "stdout": "# mocked template output\n", "stderr": ""}

        client, slug = client_with_project
        with mock.patch("dev_kit.agent.deployer.helm.run_helm_command", side_effect=_mock_run_helm):
            res = client.post(
                f"/api/projects/{slug}/deploy/preview",
                json={"target": "kubernetes"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["target"] == "kubernetes"
        assert isinstance(data["preview"], dict)
        assert len(data["preview"]) == 14

    def test_default_target_is_docker(self, client_with_project):
        """Omitting target defaults to docker."""
        client, slug = client_with_project
        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={},
        )
        assert res.status_code == 200
        assert res.json()["target"] == "docker"


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/deploy/execute
# ---------------------------------------------------------------------------


class TestExecuteDeploy:
    def test_returns_started_status(self, client_with_project):
        """Execute deploy returns started status."""
        client, slug = client_with_project
        with mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            res = client.post(
                f"/api/projects/{slug}/deploy/execute",
                json={"target": "docker"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert data["target"] == "docker"

    def test_default_target_is_docker(self, client_with_project):
        """Omitting target defaults to docker."""
        client, slug = client_with_project
        with mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            res = client.post(f"/api/projects/{slug}/deploy/execute", json={})
        assert res.status_code == 200
        assert res.json()["target"] == "docker"


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/deploy/status
# ---------------------------------------------------------------------------


class TestGetDeployStatus:
    def test_returns_idle_when_no_deployment(self, client_with_project):
        """Status endpoint returns idle when no deployment is active."""
        from dev_kit.agent.deployer.state import clear_state

        client, slug = client_with_project
        clear_state(slug)
        res = client.get(f"/api/projects/{slug}/deploy/status")
        assert res.status_code == 200
        data = res.json()
        assert data["overall"] == "idle"
        assert data["services"] == []

    def test_returns_deploying_after_execute(self, client_with_project):
        """Status endpoint returns deploying state after execute is called."""
        client, slug = client_with_project
        with mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            client.post(
                f"/api/projects/{slug}/deploy/execute",
                json={"target": "docker"},
            )
        res = client.get(f"/api/projects/{slug}/deploy/status")
        assert res.status_code == 200
        data = res.json()
        assert data["overall"] in ("deploying", "complete", "failed")
        assert isinstance(data["services"], list)
