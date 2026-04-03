"""Tests for dev_kit.agent.app FastAPI routes."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    mock_anthropic = MagicMock()
    monkeypatch.setattr(app_module, "_anthropic_client", mock_anthropic)
    # Clear engine cache between tests
    app_module._engines.clear()
    from dev_kit.agent.app import app
    return TestClient(app)


class TestProjectRoutes:
    def test_create_project(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        resp = client.post("/api/projects", json={"name": "Test Project", "description": "A test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "slug" in data
        assert data["name"] == "Test Project"

    def test_list_projects_empty(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_existing_project(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "My App", "description": "desc"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}")
        assert resp.status_code == 200
        assert resp.json()["slug"] == slug

    def test_get_nonexistent_project_returns_404(self, client):
        resp = client.get("/api/projects/does-not-exist")
        assert resp.status_code == 404

    def test_list_projects_skips_corrupt_metadata(self, client, tmp_path, monkeypatch):
        """GET /api/projects must return healthy projects even if one project.json is corrupt."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "Good", "description": "ok"})
        bad_dir = tmp_path / "bad-project" / "_meta"
        bad_dir.mkdir(parents=True)
        (bad_dir / "project.json").write_text("{NOT JSON}")
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()
        assert len(projects) == 1
        assert projects[0]["name"] == "Good"

    def test_delete_project(self, client, tmp_path, monkeypatch):
        """DELETE /api/projects/{slug} must remove the project directory and engine cache."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "ToDelete", "description": "x"})
        slug = client.get("/api/projects").json()[0]["slug"]
        resp = client.delete(f"/api/projects/{slug}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == slug
        assert client.get("/api/projects").json() == []
        assert slug not in app_module._engines


class TestConfigRoutes:
    def test_get_configs_returns_7_blocks(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/configs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 7

    def test_get_single_config(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/configs/trust_layer")
        assert resp.status_code == 200
        assert "content" in resp.json()

    def test_update_config_rejects_invalid_yaml(self, client, tmp_path, monkeypatch):
        """PUT /configs/{block} must return 400 for unparseable YAML without overwriting the file."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        slug = client.get("/api/projects").json()[0]["slug"]
        config_file = tmp_path / slug / "trust_layer.yaml"
        original_content = config_file.read_text() if config_file.exists() else ""
        resp = client.put(f"/api/projects/{slug}/configs/trust_layer", json={"content": "key: [unclosed"})
        assert resp.status_code == 400
        current_content = config_file.read_text() if config_file.exists() else ""
        assert current_content == original_content

    def test_update_config_sets_stale_on_schema_errors(self, client, tmp_path, monkeypatch):
        """PUT /configs/{block} must set status=stale when YAML is valid but schema-invalid."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        slug = client.get("/api/projects").json()[0]["slug"]
        # primary_model must be a string; passing an int triggers a validation error
        resp = client.put(
            f"/api/projects/{slug}/configs/agent_core",
            json={"content": "agent:\n  primary_model: 999\n  fallback_model: x\n"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "stale"

    def test_get_config_unknown_block_returns_400(self, client, tmp_path, monkeypatch):
        """GET /configs/{block} with an unknown block name must return 400."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        slug = client.get("/api/projects").json()[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/configs/not_a_block")
        assert resp.status_code == 400


class TestCheckpointRoutes:
    def test_list_checkpoints_empty(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/checkpoints")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_restore_checkpoint_unknown_phase_returns_404(self, client, tmp_path, monkeypatch):
        """POST /checkpoints/{phase}/restore must return 404 for a non-existent checkpoint."""
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        slug = client.get("/api/projects").json()[0]["slug"]
        resp = client.post(f"/api/projects/{slug}/checkpoints/99_ghost/restore")
        assert resp.status_code == 404
