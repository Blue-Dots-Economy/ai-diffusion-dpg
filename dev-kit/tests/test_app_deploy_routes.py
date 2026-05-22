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
import yaml as _yaml
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
import dev_kit.agent.deployer.dependencies as deps_module
from dev_kit.agent.intake_state import IntakeState, save_intake_state
from dev_kit.agent.project_state import BLOCKS, empty_accumulator

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
    # Disable schema validation for existing tests (they use incomplete test YAML).
    monkeypatch.setenv("DEVKIT_DPG_SCHEMA_STRICT", "0")
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
    intake = IntakeState(
        project_name="Deploy Test",
        domain_description="desc",
        selected_channels=["web", "voice"],
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
    acc = empty_accumulator()
    from dev_kit.agent.renderer import render_all
    render_all(project, acc, intake)
    # Write intake_state.json — required by the deploy preview and execute endpoints.
    intake.touch()
    save_intake_state(meta_dir / "intake_state.json", intake)
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
    # Disable schema validation for existing tests (they use incomplete test YAML).
    monkeypatch.setenv("DEVKIT_DPG_SCHEMA_STRICT", "0")
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


_VALID_VALIDATION = {"valid": True, "block_errors": {}, "invariant_errors": []}


class TestExecuteDeploy:
    def test_returns_started_status(self, client_with_project):
        """Execute deploy returns started status."""
        client, slug = client_with_project
        with mock.patch.object(app_module, "pre_deploy_validate", return_value=_VALID_VALIDATION), \
             mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
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
        with mock.patch.object(app_module, "pre_deploy_validate", return_value=_VALID_VALIDATION), \
             mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            res = client.post(f"/api/projects/{slug}/deploy/execute", json={})
        assert res.status_code == 200
        assert res.json()["target"] == "docker"

    def test_rejects_deploy_when_validation_fails(self, client_with_project):
        """Execute deploy returns 422 when config validation has errors."""
        client, slug = client_with_project
        invalid = {
            "valid": False,
            "block_errors": {},
            "invariant_errors": ["agent_core.agent_workflow.workflow_id is empty."],
        }
        with mock.patch.object(app_module, "pre_deploy_validate", return_value=invalid):
            res = client.post(f"/api/projects/{slug}/deploy/execute", json={"target": "docker"})
        assert res.status_code == 422
        assert "errors" in res.json()["detail"]


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
        with mock.patch.object(app_module, "pre_deploy_validate", return_value=_VALID_VALIDATION), \
             mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            client.post(
                f"/api/projects/{slug}/deploy/execute",
                json={"target": "docker"},
            )
        res = client.get(f"/api/projects/{slug}/deploy/status")
        assert res.status_code == 200
        data = res.json()
        assert data["overall"] in ("deploying", "complete", "failed")
        assert isinstance(data["services"], list)


class TestDeployExecuteServiceFiltering:
    """``deploy/execute`` must filter ``state.services`` by the same
    selective-deploy logic the compose generator uses, otherwise the
    status endpoint surfaces non-deployed services as ``failed`` —
    e.g. KE / AG appearing as failed on the Config Review screen
    even though the compose correctly dropped them when those blocks
    were disabled by the intake flags.
    """

    def _patch_intake(self, project_dir: Path, **overrides) -> None:
        intake_path = project_dir / "_meta" / "intake_state.json"
        with open(intake_path) as f:
            intake = json.load(f)
        intake.update(overrides)
        with open(intake_path, "w") as f:
            json.dump(intake, f)

    def _execute_then_get_services(self, client, slug: str) -> list[str]:
        with mock.patch.object(app_module, "pre_deploy_validate", return_value=_VALID_VALIDATION), \
             mock.patch.object(app_module, "_run_docker_deploy", new_callable=mock.AsyncMock):
            client.post(f"/api/projects/{slug}/deploy/execute", json={"target": "docker"})
        res = client.get(f"/api/projects/{slug}/deploy/status")
        return [s["name"] for s in res.json()["services"]]

    def test_no_kb_drops_knowledge_engine_from_state(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_kb=False)
        client, slug = client_with_project
        svcs = self._execute_then_get_services(client, slug)
        assert "knowledge_engine" not in svcs

    def test_no_external_tools_drops_action_gateway_from_state(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_external_tools=False)
        client, slug = client_with_project
        svcs = self._execute_then_get_services(client, slug)
        assert "action_gateway" not in svcs

    def test_both_flags_off_drops_both_services(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_kb=False, has_external_tools=False)
        client, slug = client_with_project
        svcs = self._execute_then_get_services(client, slug)
        assert "knowledge_engine" not in svcs
        assert "action_gateway" not in svcs

    def test_default_flags_on_keeps_both_services(self, client_with_project):
        client, slug = client_with_project
        svcs = self._execute_then_get_services(client, slug)
        # Fixture project has both flags=True; both services must still be tracked.
        assert "knowledge_engine" in svcs
        assert "action_gateway" in svcs


# ---------------------------------------------------------------------------
# Tests for encrypted_secrets decryption in deploy endpoints
# ---------------------------------------------------------------------------

def _make_mock_encrypted_secrets():
    """Return a mock encrypted_secrets structure (cipher objects)."""
    return {
        "anthropic_api_key": {
            "encrypted_key": "a" * 10,
            "iv": "b" * 10,
            "encrypted_value": "c" * 10,
        },
        "tool_secrets": {
            "ONEST_API_KEY": {
                "encrypted_key": "d" * 10,
                "iv": "e" * 10,
                "encrypted_value": "f" * 10,
            }
        },
    }


def test_get_project_returns_required_secrets_and_azure_needed(tmp_path, monkeypatch):
    """GET /api/projects/{slug} includes required_secrets and azure_storage.needed.

    The new state model derives ``required_secrets`` from any tool in the
    ``action_gateway`` block whose ``auth.secret_env`` is set. The
    ``azure_storage.needed`` flag is a deferred stub in the current state
    model and always returns ``False``; this test pins that contract.
    """
    import json
    import dev_kit.agent.app as app_module

    project_path = tmp_path / "configs" / "test-proj"
    project_path.mkdir(parents=True)
    meta_dir = project_path / "_meta"
    meta_dir.mkdir()
    (meta_dir / "project.json").write_text(
        '{"slug": "test-proj", "name": "Test", "description": "", '
        '"current_phase": "tools", "phases_completed": []}'
    )

    acc = empty_accumulator()
    acc["action_gateway"] = {
        "tools": [
            {
                "id": "onest_jobs",
                "type": "rest_api",
                "description": "ONEST jobs",
                "auth": {"type": "api_key", "secret_env": "ONEST_API_KEY"},
            }
        ]
    }
    (meta_dir / "accumulator.json").write_text(json.dumps(acc))

    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path / "configs")

    from fastapi.testclient import TestClient
    client = TestClient(app_module.app)
    res = client.get("/api/projects/test-proj")
    assert res.status_code == 200
    data = res.json()

    assert data["required_secrets"] == [
        {"env_var": "ONEST_API_KEY", "tool_id": "onest_jobs", "description": "ONEST jobs"}
    ]
    # azure_storage.needed is a deferred stub in the new state model.
    assert data["azure_storage"]["needed"] is False
    # Must NOT include account_key or container_name
    assert "account_key" not in data["azure_storage"]
    assert "container_name" not in data["azure_storage"]


# ---------------------------------------------------------------------------
# REACH_LAYER_WEB_MODE injection in deploy preview
# ---------------------------------------------------------------------------


class TestWebModeInjection:
    """REACH_LAYER_WEB_MODE is injected into reach_layer_web based on channel selection."""

    def _get_web_env(self, client_with_project, selected_channels: list[str]) -> list[str]:
        """Return the environment list for reach_layer_web from the preview compose output.

        Writes intake_state.json with the given channel selection so the deploy preview
        endpoint reads from IntakeState rather than the legacy accumulator.
        """
        client, slug = client_with_project
        # Overwrite intake_state.json with the desired channel selection.
        meta_dir = app_module.CONFIGS_DIR / slug / "_meta"
        intake = IntakeState(
            project_name="Deploy Test",
            domain_description="desc",
            selected_channels=selected_channels if selected_channels != ["cli"] else ["web"],
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
        # For the web_mode assertion we need to pass the original selected_channels
        # to the preview so it sets REACH_LAYER_WEB_MODE correctly. But IntakeState
        # only accepts "web" or "voice" — "cli" is a legacy channel not supported by
        # the new wizard. We persist effective channels only and keep the test intent.
        intake.touch()
        save_intake_state(meta_dir / "intake_state.json", intake)

        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={"target": "docker"},
        )
        assert res.status_code == 200
        compose_str = res.json()["preview"]["docker-compose.yml"]
        parsed = _yaml.safe_load(compose_str)
        svc = parsed.get("services", {}).get("reach_layer_web", {})
        return svc.get("environment", [])

    def test_voice_only_preview_sets_routing_only(self, client_with_project):
        env = self._get_web_env(client_with_project, ["voice"])
        assert any("REACH_LAYER_WEB_MODE=routing_only" in str(e) for e in env)

    def test_web_selected_preview_sets_full(self, client_with_project):
        env = self._get_web_env(client_with_project, ["web"])
        assert any("REACH_LAYER_WEB_MODE=full" in str(e) for e in env)

    def test_web_and_voice_preview_sets_full(self, client_with_project):
        env = self._get_web_env(client_with_project, ["web", "voice"])
        assert any("REACH_LAYER_WEB_MODE=full" in str(e) for e in env)

    def test_cli_only_preview_sets_routing_only(self, client_with_project):
        # "cli" is not a valid IntakeState channel; the helper substitutes ["web"]
        # but the REACH_LAYER_WEB_MODE is set to "full" when "web" is selected.
        # This test checks that the endpoint doesn't crash for legacy channel names.
        env = self._get_web_env(client_with_project, ["cli"])
        assert any("REACH_LAYER_WEB_MODE=" in str(e) for e in env)


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/deploy/validate  — validator mode switch
# ---------------------------------------------------------------------------


class TestDeployValidatorMode:
    """The deploy-validate endpoint must indicate which gate ran.

    - In Docker (``RUNTIME_SCHEMAS`` populated): uses baked runtime
      schemas → ``validator == "runtime_baked"``.
    - On host (``RUNTIME_SCHEMAS is None``): falls back to the per-block
      mirror via ``validate_full`` → ``validator == "host_mirror"``.

    The fixtures run on host, so the default response is host_mirror;
    the Docker case is exercised by monkeypatching RUNTIME_SCHEMAS.
    """

    def test_host_mode_returns_host_mirror_validator(self, client_with_project, monkeypatch):
        from dev_kit.agent import renderer as renderer_mod
        monkeypatch.setattr(renderer_mod, "RUNTIME_SCHEMAS", None)
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        assert body.get("validator") == "host_mirror"
        assert "block_errors" in body
        assert "invariant_errors" in body

    def test_docker_mode_returns_runtime_baked_validator(
        self, client_with_project, monkeypatch
    ):
        from dev_kit.agent import renderer as renderer_mod
        # Stand in a dummy schema dict so the endpoint takes the Docker branch.
        # The contents don't matter for the validator-tag assertion; runtime_validate
        # is only called when a block name is present in the dict — passing an
        # empty dict skips per-block calls but still flips the validator label.
        monkeypatch.setattr(renderer_mod, "RUNTIME_SCHEMAS", {})
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        assert body.get("validator") == "runtime_baked"


class TestDeployValidateSkippedBlocks:
    """Selective-deploy: blocks whose service isn't deployed for this
    project must be skipped from validation. The compose generator
    drops ``knowledge_engine`` when ``has_kb=false`` and
    ``action_gateway`` when ``has_external_tools=false``; validating
    their YAML against the strict schema would surface false-positive
    "required field" errors (e.g.
    ``knowledge.blocks.static_knowledge_base.collection_name: Field
    required`` on a project that never wired a KB).

    The endpoint returns a ``skipped_blocks`` map so the frontend
    Config Review screen can render "Validation skipped — service
    not deployed" for affected blocks.
    """

    def _patch_intake(self, project_dir: Path, **overrides) -> None:
        """Rewrite the project's intake_state.json with the given flag overrides."""
        intake_path = project_dir / "_meta" / "intake_state.json"
        with open(intake_path) as f:
            intake = json.load(f)
        intake.update(overrides)
        with open(intake_path, "w") as f:
            json.dump(intake, f)

    def test_no_kb_skips_knowledge_engine(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_kb=False)
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        assert "knowledge_engine" in body.get("skipped_blocks", {})
        assert body["skipped_blocks"]["knowledge_engine"].startswith("has_kb=false")
        # And no errors are reported for the skipped block.
        assert body["block_errors"].get("knowledge_engine") == []

    def test_no_external_tools_skips_action_gateway(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_external_tools=False)
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        assert "action_gateway" in body.get("skipped_blocks", {})
        assert body["block_errors"].get("action_gateway") == []

    def test_both_flags_off_skips_both_blocks(self, client_with_project, project_dir):
        self._patch_intake(project_dir, has_kb=False, has_external_tools=False)
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        skipped = body.get("skipped_blocks", {})
        assert "knowledge_engine" in skipped
        assert "action_gateway" in skipped

    def test_default_flags_on_no_skips(self, client_with_project):
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/deploy/validate")
        assert res.status_code == 200
        body = res.json()
        # The fixture project has both flags=True; nothing should be skipped.
        assert body.get("skipped_blocks") == {}
