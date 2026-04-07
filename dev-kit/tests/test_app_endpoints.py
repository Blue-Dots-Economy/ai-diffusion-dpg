"""Tests for new endpoints in dev_kit.agent.app.

Tests for:
  - GET /api/projects/{slug}/configs/export (ZIP download)
  - GET /api/projects/{slug}/checkpoints/{phase}/preview (checkpoint preview)
  - GET /api/schemas/{block} (schema descriptions)
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

# Ensure ANTHROPIC_API_KEY is set before importing the app module
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.accumulator import BLOCKS


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with CONFIGS_DIR redirected to tmp_path."""
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    # Clear the engine registry so previous test state doesn't leak
    app_module._engines.clear()
    return TestClient(app_module.app)


@pytest.fixture()
def project_path(tmp_path):
    """Create a minimal project directory and return its path."""
    slug = "test-project"
    project = tmp_path / slug
    project.mkdir()
    meta_dir = project / "_meta"
    meta_dir.mkdir()
    (meta_dir / "project.json").write_text(
        json.dumps({"name": "Test", "description": "", "created_at": "2026-01-01T00:00:00Z"})
    )
    return project


# ---------------------------------------------------------------------------
# Endpoint 1: Export ZIP
# ---------------------------------------------------------------------------


class TestExportConfigsZip:
    def test_export_configs_returns_zip(self, client, tmp_path, project_path):
        """Status 200, content-type application/zip, ZIP contains agent_core.yaml."""
        slug = project_path.name

        # Write one real config file
        (project_path / "agent_core.yaml").write_text("agent:\n  primary_model: claude-test\n")

        response = client.get(f"/api/projects/{slug}/configs/export")
        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]

        # Unpack the ZIP and verify agent_core.yaml is present
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = zf.namelist()
        assert "agent_core.yaml" in names
        assert "attachment" in response.headers.get("content-disposition", "")
        assert f"{slug}-configs.zip" in response.headers.get("content-disposition", "")

    def test_export_includes_placeholder_for_missing_block(self, client, tmp_path, project_path):
        """Blocks without files on disk get a placeholder comment in the ZIP."""
        slug = project_path.name

        # Write only agent_core.yaml — all other blocks are missing
        (project_path / "agent_core.yaml").write_text("agent:\n  primary_model: claude-test\n")

        response = client.get(f"/api/projects/{slug}/configs/export")
        assert response.status_code == 200

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            # knowledge_engine.yaml should exist as a placeholder
            assert "knowledge_engine.yaml" in zf.namelist()
            content = zf.read("knowledge_engine.yaml").decode()
        assert "knowledge_engine.yaml" in content
        assert "not yet configured" in content


# ---------------------------------------------------------------------------
# Endpoint 2: Checkpoint preview
# ---------------------------------------------------------------------------


class TestCheckpointPreview:
    def _create_checkpoint(self, project_path: object) -> str:
        """Create a minimal checkpoint and return the phase name."""
        phase = "01_overview"
        cp_dir = project_path / "_meta" / "checkpoints" / phase
        cp_dir.mkdir(parents=True)

        acc_data = {
            "data": {b: {} for b in BLOCKS},
            "statuses": {b: "pending" for b in BLOCKS},
        }
        # Give agent_core actual data and mark it complete
        acc_data["data"]["agent_core"] = {"server": {"host": "0.0.0.0"}}
        acc_data["statuses"]["agent_core"] = "complete"

        (cp_dir / "accumulator.json").write_text(json.dumps(acc_data))
        (cp_dir / "summary.txt").write_text("summary")
        (cp_dir / "timestamp.json").write_text('{"created_at": "2026-01-01T00:00:00Z"}')
        return phase

    def test_checkpoint_preview_returns_configs(self, client, tmp_path, monkeypatch, project_path):
        """Returns list with correct block/status/content shape."""
        slug = project_path.name
        phase = self._create_checkpoint(project_path)

        response = client.get(f"/api/projects/{slug}/checkpoints/{phase}/preview")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == len(BLOCKS)

        # Verify shape of each item
        for item in data:
            assert "block" in item
            assert "status" in item
            assert "content" in item
            assert item["block"] in BLOCKS

        # agent_core should be complete with YAML content
        agent_core_item = next(item for item in data if item["block"] == "agent_core")
        assert agent_core_item["status"] == "complete"
        assert "host" in agent_core_item["content"]

        # memory_layer should be pending with empty content
        memory_item = next(item for item in data if item["block"] == "memory_layer")
        assert memory_item["status"] == "pending"

    def test_checkpoint_preview_404_for_missing_phase(self, client, tmp_path, monkeypatch, project_path):
        """Returns 404 when checkpoint does not exist."""
        slug = project_path.name

        response = client.get(f"/api/projects/{slug}/checkpoints/99_nonexistent/preview")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Endpoint 3: Schema descriptions
# ---------------------------------------------------------------------------


class TestSchemaDescriptions:
    def test_schema_descriptions_returns_key_map(self, client):
        """reach_layer returns a descriptions dict with known keys."""
        response = client.get("/api/schemas/reach_layer")
        assert response.status_code == 200

        data = response.json()
        assert data["block"] == "reach_layer"
        assert isinstance(data["descriptions"], dict)
        # The reach_layer template has app_name with a comment
        assert "app_name" in data["descriptions"]
        assert len(data["descriptions"]["app_name"]) > 0

    def test_schema_descriptions_empty_for_unknown_block(self, client):
        """Returns empty descriptions dict (not 404) for a nonexistent block."""
        response = client.get("/api/schemas/nonexistent_block")
        assert response.status_code == 200

        data = response.json()
        assert data["block"] == "nonexistent_block"
        assert data["descriptions"] == {}


def test_export_configs_404_for_missing_project(client):
    """Export endpoint returns 404 when the project does not exist."""
    res = client.get("/api/projects/nonexistent-project-xyz/configs/export")
    assert res.status_code == 404


def test_checkpoint_preview_corrupt_accumulator(client, tmp_path, monkeypatch):
    """Preview endpoint returns 500 or 404 when accumulator.json is corrupt JSON."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)

    slug = "test-corrupt"
    project_path = tmp_path / slug
    project_path.mkdir()
    (project_path / "_meta").mkdir()
    (project_path / "_meta" / "project.json").write_text(
        '{"name": "Test", "description": "", "created_at": "2026-01-01T00:00:00Z"}'
    )
    phase = "01_overview"
    cp_dir = project_path / "_meta" / "checkpoints" / phase
    cp_dir.mkdir(parents=True)
    (cp_dir / "accumulator.json").write_text("NOT VALID JSON {{{{")
    (cp_dir / "summary.txt").write_text("summary")
    (cp_dir / "timestamp.json").write_text('{"created_at": "2026-01-01T00:00:00Z"}')

    res = client.get(f"/api/projects/{slug}/checkpoints/{phase}/preview")
    assert res.status_code in (404, 500)  # endpoint handles corrupt JSON
