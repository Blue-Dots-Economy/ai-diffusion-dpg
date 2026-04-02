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
