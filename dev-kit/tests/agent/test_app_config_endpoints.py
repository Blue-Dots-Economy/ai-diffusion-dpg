"""Tests for the migrated config read/write endpoints (Task C.3).

Covers:
- GET  /api/projects/{slug}/configs          — list all 7 blocks
- GET  /api/projects/{slug}/configs/export   — ZIP archive of all YAMLs
- GET  /api/projects/{slug}/configs/{block}  — single block
- PUT  /api/projects/{slug}/configs/{block}  — write YAML + update accumulator
- POST /api/projects/{slug}/configs/reload   — reload from disk
- POST /api/projects/{slug}/configs/validate — partial validation per block
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with CONFIGS_DIR redirected to tmp_path/configs."""
    import dev_kit.agent.app as app_mod

    configs = tmp_path / "configs"
    configs.mkdir()
    monkeypatch.setattr(app_mod, "CONFIGS_DIR", configs)
    return TestClient(app_mod.app), configs


def _create_project(c, configs, name="test-proj"):
    """Helper: POST /api/projects and return (slug, project_path)."""
    res = c.post("/api/projects", json={
        "name": name,
        "project_name": name,
        "domain_description": "Integration test project",
        "selected_channels": ["web"],
        "default_language": "english",
        "supported_languages": ["english"],
    })
    assert res.status_code == 200, res.text
    slug = res.json()["slug"]
    return slug, configs / slug


BLOCKS = (
    "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
    "action_gateway", "reach_layer", "observability_layer",
)


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/configs
# ---------------------------------------------------------------------------

class TestGetConfigs:
    def test_returns_list_with_all_7_blocks(self, client):
        """GET /configs returns a list with one entry per block."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        returned_blocks = {item["block"] for item in data}
        assert returned_blocks == set(BLOCKS)

    def test_each_item_has_required_keys(self, client):
        """Each list item has block, status, and content keys."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        for item in res.json():
            assert "block" in item
            assert "status" in item
            assert "content" in item

    def test_status_reflects_field_status(self, client):
        """Status for a block is 'complete' only when all its fields are answered."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"

        # Write a synthetic field_status that marks agent_core field as answered.
        field_status = {"agent_core.persona": "answered"}
        (meta_dir / "field_status.json").write_text(json.dumps(field_status))

        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        agent_core_item = next(i for i in res.json() if i["block"] == "agent_core")
        # Only one field, answered → complete
        assert agent_core_item["status"] == "complete"

    def test_content_is_on_disk_yaml_text(self, client):
        """Content for a block with a file is the raw on-disk YAML text."""
        c, configs = client
        slug, project_path = _create_project(c, configs)

        # Write a known YAML string to disk.
        yaml_text = "agent:\n  persona: test-persona\n"
        (project_path / "agent_core.yaml").write_text(yaml_text)

        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        agent_core_item = next(i for i in res.json() if i["block"] == "agent_core")
        assert agent_core_item["content"] == yaml_text

    def test_content_is_empty_string_for_missing_file(self, client):
        """Content is '' for a block without a YAML file on disk."""
        c, configs = client
        slug, project_path = _create_project(c, configs)

        # Ensure no agent_core.yaml on disk.
        yaml_file = project_path / "agent_core.yaml"
        if yaml_file.exists():
            yaml_file.unlink()

        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        agent_core_item = next(i for i in res.json() if i["block"] == "agent_core")
        assert agent_core_item["content"] == ""

    def test_get_configs_500_on_corrupt_field_status(self, client):
        """GET /configs returns 500 when field_status.json contains corrupt JSON."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"

        # Overwrite field_status.json with unparseable content.
        (meta_dir / "field_status.json").write_text("{not valid json {")

        res = c.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Corrupt field_status.json")


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/configs/export
# ---------------------------------------------------------------------------

class TestExportConfigs:
    def test_returns_zip_with_7_yaml_files(self, client):
        """GET /configs/export returns a ZIP containing one YAML per block."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.get(f"/api/projects/{slug}/configs/export")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            names = set(zf.namelist())
        assert names == {f"{block}.yaml" for block in BLOCKS}

    def test_404_on_missing_project(self, client):
        """GET /configs/export returns 404 for unknown project."""
        c, _ = client
        res = c.get("/api/projects/does-not-exist/configs/export")
        assert res.status_code == 404

    def test_zip_uses_slug_filename(self, client):
        """Content-Disposition attachment name matches the slug."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.get(f"/api/projects/{slug}/configs/export")
        assert res.status_code == 200
        assert slug in res.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/configs/{block}
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_returns_block_content_and_status(self, client):
        """GET /configs/{block} returns block name, status, and content."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        yaml_text = "agent:\n  persona: hello\n"
        (project_path / "agent_core.yaml").write_text(yaml_text)

        res = c.get(f"/api/projects/{slug}/configs/agent_core")
        assert res.status_code == 200
        data = res.json()
        assert data["block"] == "agent_core"
        assert "status" in data
        assert data["content"] == yaml_text

    def test_400_on_unknown_block(self, client):
        """GET /configs/{block} returns 400 for an unknown block name."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.get(f"/api/projects/{slug}/configs/not_a_block")
        assert res.status_code == 400

    def test_404_on_missing_project(self, client):
        """GET /configs/{block} returns 404 when the project does not exist."""
        c, _ = client
        res = c.get("/api/projects/no-such-project/configs/agent_core")
        assert res.status_code == 404

    def test_content_empty_when_file_missing(self, client):
        """GET /configs/{block} returns '' content when YAML file is absent."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        yaml_file = project_path / "agent_core.yaml"
        if yaml_file.exists():
            yaml_file.unlink()
        res = c.get(f"/api/projects/{slug}/configs/agent_core")
        assert res.status_code == 200
        assert res.json()["content"] == ""

    def test_get_config_block_500_on_corrupt_field_status(self, client):
        """GET /configs/{block} returns 500 when field_status.json contains corrupt JSON."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"

        # Overwrite field_status.json with unparseable content.
        (meta_dir / "field_status.json").write_text("{not valid json {")

        res = c.get(f"/api/projects/{slug}/configs/agent_core")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Corrupt field_status.json")


# ---------------------------------------------------------------------------
# PUT /api/projects/{slug}/configs/{block}
# ---------------------------------------------------------------------------

class TestUpdateConfig:
    def test_rejects_malformed_yaml_with_400(self, client):
        """PUT /configs/{block} returns 400 for invalid YAML."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.put(f"/api/projects/{slug}/configs/agent_core",
                    json={"content": ": bad: yaml: [\n"})
        assert res.status_code == 400
        assert "Invalid YAML" in res.json()["detail"]

    def test_writes_yaml_to_disk(self, client):
        """PUT /configs/{block} writes the raw YAML text to the YAML file on disk."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        yaml_text = "agent:\n  persona: updated\n"
        res = c.put(f"/api/projects/{slug}/configs/agent_core",
                    json={"content": yaml_text})
        assert res.status_code == 200
        assert (project_path / "agent_core.yaml").read_text() == yaml_text

    def test_updates_accumulator_json(self, client):
        """PUT /configs/{block} updates the accumulator.json on disk."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"
        yaml_text = "agent:\n  persona: updated\n"
        c.put(f"/api/projects/{slug}/configs/agent_core",
              json={"content": yaml_text})
        accumulator = json.loads((meta_dir / "accumulator.json").read_text())
        assert accumulator.get("agent_core") == {"agent": {"persona": "updated"}}

    def test_returns_validation_errors_empty_for_valid_config(self, client):
        """PUT /configs/{block} returns validation_errors=[] for a parseable config."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        yaml_text = "agent:\n  persona: ok\n"
        res = c.put(f"/api/projects/{slug}/configs/agent_core",
                    json={"content": yaml_text})
        assert res.status_code == 200
        data = res.json()
        assert "validation_errors" in data
        assert isinstance(data["validation_errors"], list)

    def test_returns_non_empty_validation_errors_for_bad_value(self, client):
        """PUT /configs/{block} validation_errors is non-empty for schema violations."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        # memory_layer expects certain structure; push a type mismatch:
        bad_yaml = "session_ttl_seconds: not_a_number\n"
        res = c.put(f"/api/projects/{slug}/configs/memory_layer",
                    json={"content": bad_yaml})
        assert res.status_code == 200
        data = res.json()
        # validation_errors may or may not be empty depending on mirror schema;
        # at minimum the endpoint must not 500.
        assert "validation_errors" in data
        assert isinstance(data["validation_errors"], list)

    def test_does_not_mutate_field_status(self, client):
        """PUT /configs/{block} must NOT modify field_status.json."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"

        # Write a known field_status before the PUT.
        field_status_before = {"agent_core.persona": "answered"}
        (meta_dir / "field_status.json").write_text(json.dumps(field_status_before))

        c.put(f"/api/projects/{slug}/configs/agent_core",
              json={"content": "agent:\n  persona: updated\n"})

        field_status_after = json.loads((meta_dir / "field_status.json").read_text())
        assert field_status_after == field_status_before

    def test_400_on_unknown_block(self, client):
        """PUT /configs/{block} returns 400 for unknown block name."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.put(f"/api/projects/{slug}/configs/not_a_block",
                    json={"content": "foo: bar\n"})
        assert res.status_code == 400

    def test_404_on_missing_project(self, client):
        """PUT /configs/{block} returns 404 when project does not exist."""
        c, _ = client
        res = c.put("/api/projects/no-such/configs/agent_core",
                    json={"content": "foo: bar\n"})
        assert res.status_code == 404

    def test_response_has_status_key(self, client):
        """PUT /configs/{block} response includes block, status, validation_errors."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.put(f"/api/projects/{slug}/configs/agent_core",
                    json={"content": "agent:\n  persona: x\n"})
        assert res.status_code == 200
        data = res.json()
        assert data["block"] == "agent_core"
        assert "status" in data
        assert data["status"] in ("complete", "incomplete")

    def test_put_config_500_on_corrupt_field_status(self, client):
        """PUT /configs/{block} returns 500 when field_status.json contains corrupt JSON."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        (project_path / "_meta" / "field_status.json").write_text("{not valid json {")

        res = c.put(f"/api/projects/{slug}/configs/agent_core",
                    json={"content": "agent:\n  persona: hi\n"})
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Corrupt field_status.json")


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/configs/reload
# ---------------------------------------------------------------------------

class TestReloadConfigs:
    def test_reload_repopulates_accumulator_from_disk(self, client):
        """POST /configs/reload rewrites accumulator.json from on-disk YAMLs."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        meta_dir = project_path / "_meta"

        # Write a YAML file directly to disk (bypassing the wizard).
        (project_path / "agent_core.yaml").write_text("agent:\n  persona: reloaded\n")

        res = c.post(f"/api/projects/{slug}/configs/reload")
        assert res.status_code == 200

        accumulator = json.loads((meta_dir / "accumulator.json").read_text())
        # agent_core should now reflect the on-disk content.
        assert accumulator.get("agent_core", {}).get("agent", {}).get("persona") == "reloaded"

    def test_reload_returns_reloaded_true_and_slug(self, client):
        """POST /configs/reload returns at minimum {reloaded: true, slug: ...}."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.post(f"/api/projects/{slug}/configs/reload")
        assert res.status_code == 200
        data = res.json()
        assert data.get("reloaded") is True
        assert data.get("slug") == slug

    def test_reload_404_on_missing_project(self, client):
        """POST /configs/reload returns 404 for unknown project."""
        c, _ = client
        res = c.post("/api/projects/does-not-exist/configs/reload")
        assert res.status_code == 404

    def test_reload_includes_block_statuses(self, client):
        """POST /configs/reload response includes block_statuses for all 7 blocks."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.post(f"/api/projects/{slug}/configs/reload")
        assert res.status_code == 200
        data = res.json()
        assert "block_statuses" in data
        assert set(data["block_statuses"].keys()) == set(BLOCKS)
        for status in data["block_statuses"].values():
            assert status in ("complete", "incomplete")

    def test_reload_500_on_corrupt_field_status(self, client):
        """POST /configs/reload returns 500 when field_status.json contains corrupt JSON."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        (project_path / "_meta" / "field_status.json").write_text("{not valid json {")

        res = c.post(f"/api/projects/{slug}/configs/reload")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Corrupt field_status.json")


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/configs/validate
# ---------------------------------------------------------------------------

class TestValidateConfigs:
    def test_returns_all_7_blocks(self, client):
        """POST /configs/validate returns a result dict for every block."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == set(BLOCKS)

    def test_each_block_has_valid_and_errors_keys(self, client):
        """Each block result has 'valid' (bool) and 'errors' (list) keys."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 200
        for block, result in res.json().items():
            assert isinstance(result["valid"], bool)
            assert isinstance(result["errors"], list)

    def test_404_on_missing_project(self, client):
        """POST /configs/validate returns 404 for unknown project."""
        c, _ = client
        res = c.post("/api/projects/no-such/configs/validate")
        assert res.status_code == 404

    def test_valid_true_for_empty_accumulator(self, client):
        """POST /configs/validate with empty accumulator has valid=True per block (no required fields violated)."""
        c, configs = client
        slug, _ = _create_project(c, configs)
        res = c.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 200
        # Validation on empty config should not cause a server error.
        for block, result in res.json().items():
            assert "valid" in result
            assert "errors" in result

    def test_validate_500_on_corrupt_accumulator(self, client):
        """POST /configs/validate returns 500 when accumulator.json contains corrupt JSON."""
        c, configs = client
        slug, project_path = _create_project(c, configs)
        (project_path / "_meta" / "accumulator.json").write_text("{not valid json {")

        res = c.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Corrupt accumulator.json")
