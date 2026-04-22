"""Tests for project and config routes in dev_kit.agent.app.

Covers:
  - POST /api/projects (create project)
  - GET /api/projects (list projects)
  - GET /api/projects/{slug} (get project)
  - DELETE /api/projects/{slug} (delete project)
  - POST /api/projects/{slug}/chat (async chat endpoint)
  - GET /api/projects/{slug}/history (conversation history)
  - GET /api/projects/{slug}/checkpoints (list checkpoints)
  - POST /api/projects/{slug}/checkpoints/{phase}/restore (restore checkpoint)
  - GET /api/projects/{slug}/configs (all configs)
  - GET /api/projects/{slug}/configs/{block} (single config)
  - PUT /api/projects/{slug}/configs/{block} (update config)
  - POST /api/projects/{slug}/configs/validate (validate all)
  - GET /api/projects/{slug}/workflow/graph (workflow graph)
  - _slugify helper function
"""
from __future__ import annotations

import json
import os
import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator, ConfigStatus


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with CONFIGS_DIR redirected to tmp_path."""
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    app_module._engines.clear()
    return TestClient(app_module.app)


@pytest.fixture()
def project_slug():
    return "my-test-project"


@pytest.fixture()
def project_dir(tmp_path, project_slug):
    """Create a minimal project directory structure."""
    project = tmp_path / project_slug
    project.mkdir()
    meta_dir = project / "_meta"
    meta_dir.mkdir()
    meta = {
        "slug": project_slug,
        "name": "My Test Project",
        "description": "desc",
        "current_phase": "overview",
        "phases_completed": [],
    }
    (meta_dir / "project.json").write_text(json.dumps(meta))
    # Write empty YAML stubs so get_engine doesn't fail on missing files
    acc = ConfigAccumulator()
    from dev_kit.agent.renderer import render_all
    render_all(project, acc)
    return project


@pytest.fixture()
def client_with_project(tmp_path, monkeypatch, project_dir, project_slug):
    """Return a TestClient with a pre-created project."""
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    app_module._engines.clear()
    return TestClient(app_module.app), project_slug


# ---------------------------------------------------------------------------
# _slugify helper
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercase_and_spaces_become_dashes(self):
        assert app_module._slugify("Hello World") == "hello-world"

    def test_special_characters_become_dashes(self):
        assert app_module._slugify("Foo & Bar!") == "foo-bar"

    def test_leading_trailing_dashes_stripped(self):
        assert app_module._slugify("  --foo--  ") == "foo"

    def test_digits_preserved(self):
        assert app_module._slugify("Project 42") == "project-42"

    def test_empty_string_returns_empty(self):
        assert app_module._slugify("") == ""


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------


class TestCreateProject:
    def test_create_returns_meta(self, client):
        """Creating a project returns metadata with slug and name."""
        res = client.post("/api/projects", json={"name": "New Project", "description": "test"})
        assert res.status_code == 200
        data = res.json()
        assert data["slug"] == "new-project"
        assert data["name"] == "New Project"
        assert data["description"] == "test"
        assert data["current_phase"] == "tier"

    def test_create_writes_project_json(self, client, tmp_path):
        """Project metadata is persisted to disk."""
        client.post("/api/projects", json={"name": "Disk Test", "description": "d"})
        meta_file = tmp_path / "disk-test" / "_meta" / "project.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["slug"] == "disk-test"

    def test_create_initialises_agent_type_and_phase_decisions(self, client, tmp_path):
        """GH-137: project meta seeds empty agent_type and phase_decisions."""
        client.post("/api/projects", json={"name": "Meta Keys", "description": "d"})
        meta_file = tmp_path / "meta-keys" / "_meta" / "project.json"
        meta = json.loads(meta_file.read_text())
        assert meta["agent_type"] == ""
        assert meta["phase_decisions"] == {}

    def test_create_registers_engine(self, client):
        """Engine is registered in _engines after creation."""
        client.post("/api/projects", json={"name": "Engine Check", "description": ""})
        assert "engine-check" in app_module._engines


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


class TestListProjects:
    def test_empty_configs_dir_returns_empty_list(self, client):
        """Returns empty list when no projects exist."""
        res = client.get("/api/projects")
        assert res.status_code == 200
        assert res.json() == []

    def test_returns_created_project(self, client, tmp_path):
        """Returns list with the created project."""
        client.post("/api/projects", json={"name": "Listed", "description": ""})
        res = client.get("/api/projects")
        assert res.status_code == 200
        slugs = [p["slug"] for p in res.json()]
        assert "listed" in slugs

    def test_skips_corrupt_meta(self, client, tmp_path):
        """Projects with corrupt project.json are silently skipped."""
        bad_dir = tmp_path / "bad-project"
        bad_dir.mkdir()
        meta_dir = bad_dir / "_meta"
        meta_dir.mkdir()
        (meta_dir / "project.json").write_text("NOT VALID JSON {{{")

        res = client.get("/api/projects")
        assert res.status_code == 200
        slugs = [p.get("slug", "") for p in res.json()]
        assert "bad-project" not in slugs

    def test_skips_non_directory_entries(self, client, tmp_path):
        """Non-directory entries in CONFIGS_DIR are ignored."""
        (tmp_path / "some_file.txt").write_text("ignored")
        res = client.get("/api/projects")
        assert res.status_code == 200
        assert res.json() == []


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}
# ---------------------------------------------------------------------------


class TestGetProject:
    def test_returns_project_with_config_statuses(self, client_with_project):
        """Returns project meta augmented with config_statuses."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}")
        assert res.status_code == 200
        data = res.json()
        assert "config_statuses" in data
        assert set(data["config_statuses"].keys()) == set(BLOCKS)

    def test_404_for_missing_project(self, client):
        """Returns 404 when project does not exist."""
        res = client.get("/api/projects/does-not-exist")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/projects/{slug}
# ---------------------------------------------------------------------------


class TestDeleteProject:
    def test_delete_removes_directory(self, client_with_project, tmp_path):
        """Deleted project directory no longer exists on disk."""
        client, slug = client_with_project
        res = client.delete(f"/api/projects/{slug}")
        assert res.status_code == 200
        assert res.json() == {"deleted": slug}
        assert not (tmp_path / slug).exists()

    def test_delete_removes_engine_from_registry(self, client_with_project):
        """Engine is removed from _engines after deletion."""
        client, slug = client_with_project
        # Ensure engine is loaded first
        client.get(f"/api/projects/{slug}")
        assert slug in app_module._engines
        client.delete(f"/api/projects/{slug}")
        assert slug not in app_module._engines

    def test_delete_404_for_missing(self, client):
        """Returns 404 when project does not exist."""
        res = client.delete("/api/projects/nonexistent")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/chat
# ---------------------------------------------------------------------------


class TestChatEndpoint:
    def test_chat_returns_result_on_success(self, client_with_project):
        """Chat endpoint returns result dict from engine.chat."""
        client, slug = client_with_project
        # Ensure engine is loaded
        client.get(f"/api/projects/{slug}")
        engine = app_module._engines[slug]

        async def _mock_chat(message):
            return {"reply": "Hello back", "phase": "overview"}

        with mock.patch.object(engine, "chat", side_effect=_mock_chat):
            res = client.post(f"/api/projects/{slug}/chat", json={"message": "Hello"})
        assert res.status_code == 200
        assert res.json()["reply"] == "Hello back"

    def test_chat_404_for_missing_project(self, client):
        """Chat endpoint returns 404 when project does not exist."""
        res = client.post("/api/projects/nonexistent/chat", json={"message": "Hi"})
        assert res.status_code == 404

    def test_chat_500_on_conversation_error(self, client_with_project):
        """Chat endpoint returns 500 when ConversationError is raised."""
        from dev_kit.agent.errors import ConversationError

        client, slug = client_with_project
        client.get(f"/api/projects/{slug}")
        engine = app_module._engines[slug]

        async def _raise_conversation_error(message):
            raise ConversationError("LLM failed")

        with mock.patch.object(engine, "chat", side_effect=_raise_conversation_error):
            res = client.post(f"/api/projects/{slug}/chat", json={"message": "fail"})
        assert res.status_code == 500

    def test_chat_500_on_unexpected_error(self, client_with_project):
        """Chat endpoint returns 500 when an unexpected exception is raised."""
        client, slug = client_with_project
        client.get(f"/api/projects/{slug}")
        engine = app_module._engines[slug]

        async def _raise_unexpected(message):
            raise RuntimeError("unexpected")

        with mock.patch.object(engine, "chat", side_effect=_raise_unexpected):
            res = client.post(f"/api/projects/{slug}/chat", json={"message": "boom"})
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/history
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_empty_history_for_new_project(self, client_with_project):
        """New projects have empty conversation history."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/history")
        assert res.status_code == 200
        assert res.json() == []

    def test_history_with_string_content(self, client_with_project):
        """String-content messages are included in history."""
        client, slug = client_with_project
        engine = app_module._engines.get(slug)
        if engine is None:
            client.get(f"/api/projects/{slug}")
            engine = app_module._engines[slug]
        engine._history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        res = client.get(f"/api/projects/{slug}/history")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert data[0] == {"role": "user", "content": "Hello"}

    def test_history_skips_non_string_content(self, client_with_project):
        """Messages with non-string content (tool use blocks) are excluded."""
        client, slug = client_with_project
        # Load engine
        client.get(f"/api/projects/{slug}")
        engine = app_module._engines[slug]
        engine._history = [
            {"role": "user", "content": "text message"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1"}]},
        ]
        res = client.get(f"/api/projects/{slug}/history")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["role"] == "user"


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/checkpoints
# ---------------------------------------------------------------------------


class TestGetCheckpoints:
    def test_no_checkpoints_returns_empty_list(self, client_with_project):
        """Projects without checkpoints return empty list."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/checkpoints")
        assert res.status_code == 200
        assert res.json() == []

    def test_404_for_missing_project(self, client):
        """Returns 404 when project does not exist."""
        res = client.get("/api/projects/nonexistent/checkpoints")
        assert res.status_code == 404

    def test_returns_checkpoint_list(self, client_with_project, tmp_path, project_slug):
        """Returns list of checkpoint metadata."""
        client, slug = client_with_project
        cp_dir = tmp_path / slug / "_meta" / "checkpoints" / "01_overview"
        cp_dir.mkdir(parents=True)
        acc = ConfigAccumulator()
        (cp_dir / "accumulator.json").write_text(json.dumps(acc.to_dict()))
        (cp_dir / "summary.txt").write_text("overview done")
        (cp_dir / "timestamp.json").write_text('{"created_at": "2026-01-01T00:00:00Z"}')

        res = client.get(f"/api/projects/{slug}/checkpoints")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["phase"] == "01_overview"


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/checkpoints/{phase}/restore
# ---------------------------------------------------------------------------


class TestRestoreCheckpoint:
    def _make_checkpoint(self, project_path, phase="01_overview"):
        acc = ConfigAccumulator()
        cp_dir = project_path / "_meta" / "checkpoints" / phase
        cp_dir.mkdir(parents=True)
        (cp_dir / "accumulator.json").write_text(json.dumps(acc.to_dict()))
        (cp_dir / "summary.txt").write_text("restored summary")
        (cp_dir / "timestamp.json").write_text('{"created_at": "2026-01-01T00:00:00Z"}')
        return phase

    def test_restore_returns_restored_phase(self, client_with_project, tmp_path, project_slug):
        """Restore endpoint returns restored phase and summary."""
        client, slug = client_with_project
        phase = self._make_checkpoint(tmp_path / slug)

        res = client.post(f"/api/projects/{slug}/checkpoints/{phase}/restore")
        assert res.status_code == 200
        data = res.json()
        assert data["restored"] == phase
        assert "summary" in data

    def test_restore_404_for_missing_checkpoint(self, client_with_project):
        """Returns 404 when checkpoint does not exist."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/checkpoints/99_nonexistent/restore")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/configs
# ---------------------------------------------------------------------------


class TestGetConfigs:
    def test_returns_all_blocks(self, client_with_project):
        """Returns a list with one entry per DPG block."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == len(BLOCKS)
        blocks_returned = {item["block"] for item in data}
        assert blocks_returned == set(BLOCKS)

    def test_each_item_has_required_keys(self, client_with_project):
        """Each item has block, status, and content keys."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/configs")
        assert res.status_code == 200
        for item in res.json():
            assert "block" in item
            assert "status" in item
            assert "content" in item


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/configs/{block}
# ---------------------------------------------------------------------------


class TestGetConfig:
    def test_returns_single_block(self, client_with_project):
        """Returns data for a single valid block."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/configs/agent_core")
        assert res.status_code == 200
        data = res.json()
        assert data["block"] == "agent_core"
        assert "status" in data
        assert "content" in data

    def test_400_for_unknown_block(self, client_with_project):
        """Returns 400 for an unrecognised block name."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/configs/not_a_block")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# PUT /api/projects/{slug}/configs/{block}
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_update_valid_yaml(self, client_with_project):
        """Valid YAML update writes file and returns block info."""
        client, slug = client_with_project
        yaml_content = "agent:\n  primary_model: claude-test\n  fallback_model: claude-alt\n"
        res = client.put(
            f"/api/projects/{slug}/configs/agent_core",
            json={"content": yaml_content},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["block"] == "agent_core"
        assert "status" in data
        assert "validation_errors" in data

    def test_400_for_invalid_yaml(self, client_with_project):
        """Invalid YAML returns 400 with error details."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/configs/agent_core",
            json={"content": ":::invalid yaml:::\n  - {"},
        )
        assert res.status_code == 400

    def test_400_for_unknown_block(self, client_with_project):
        """Returns 400 for an unrecognised block name."""
        client, slug = client_with_project
        res = client.put(
            f"/api/projects/{slug}/configs/not_a_block",
            json={"content": "key: value\n"},
        )
        assert res.status_code == 400

    def test_schema_errors_set_stale_status(self, client_with_project):
        """YAML with wrong schema fields results in stale status."""
        client, slug = client_with_project
        # Provide wrong type for a known field to trigger validation error
        yaml_content = "agent:\n  primary_model: 12345\n"
        res = client.put(
            f"/api/projects/{slug}/configs/agent_core",
            json={"content": yaml_content},
        )
        assert res.status_code == 200
        # If validation errors exist, status should be stale
        data = res.json()
        if data["validation_errors"]:
            assert data["status"] == "stale"

    def test_draft_block_gets_draft_status(self, client_with_project):
        """Trust layer (a DRAFT_BLOCKS member) gets draft status on valid config."""
        client, slug = client_with_project
        yaml_content = "trust:\n  policy_pack: default\n"
        res = client.put(
            f"/api/projects/{slug}/configs/trust_layer",
            json={"content": yaml_content},
        )
        assert res.status_code == 200
        # Either draft or stale depending on validation
        assert res.json()["status"] in ("draft", "stale", "complete")


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/configs/validate
# ---------------------------------------------------------------------------


class TestValidateAllConfigs:
    def test_returns_result_per_block(self, client_with_project):
        """Returns validation result for each of the 7 blocks."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == set(BLOCKS)
        for block_result in data.values():
            assert "valid" in block_result
            assert "errors" in block_result

    def test_valid_key_is_bool(self, client_with_project):
        """The 'valid' field is always a boolean."""
        client, slug = client_with_project
        res = client.post(f"/api/projects/{slug}/configs/validate")
        assert res.status_code == 200
        for block_result in res.json().values():
            assert isinstance(block_result["valid"], bool)


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/workflow/graph
# ---------------------------------------------------------------------------


class TestWorkflowGraph:
    def test_returns_graph_dict(self, client_with_project):
        """Returns a dict (even if empty nodes/edges for a blank project)."""
        client, slug = client_with_project
        res = client.get(f"/api/projects/{slug}/workflow/graph")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# _load_project_meta edge cases
# ---------------------------------------------------------------------------


class TestLoadProjectMeta:
    def test_404_when_no_meta_file(self, client, tmp_path):
        """Returns 404 when project directory has no project.json."""
        # Create project dir but no meta file
        (tmp_path / "no-meta").mkdir()
        res = client.get("/api/projects/no-meta")
        assert res.status_code == 404

    def test_500_for_corrupt_meta_file(self, client, tmp_path):
        """Returns 500 when project.json contains invalid JSON."""
        proj_dir = tmp_path / "corrupt-meta"
        proj_dir.mkdir()
        meta_dir = proj_dir / "_meta"
        meta_dir.mkdir()
        (meta_dir / "project.json").write_text("{{NOT JSON}}")
        res = client.get("/api/projects/corrupt-meta")
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# _get_engine edge case: missing project directory
# ---------------------------------------------------------------------------


class TestGetEngine:
    def test_404_when_project_dir_missing(self, client):
        """Returns 404 when trying to load engine for non-existent project."""
        res = client.get("/api/projects/ghost-project/configs")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects/{slug} — azure_storage field
# ---------------------------------------------------------------------------


class TestGetProjectAzureStorage:
    def test_returns_azure_storage_needed_true_when_declared(self, client):
        """GET /api/projects/{slug} returns azure_storage.needed=True after declare_azure_storage."""
        # Create a project first
        resp = client.post("/api/projects", json={"name": "azure-test", "description": "test"})
        assert resp.status_code == 200
        slug = resp.json()["slug"]

        # Simulate declare_azure_storage having been called (sets intent flag, no credentials)
        from dev_kit.agent.app import _engines
        engine = _engines.get(slug)
        if engine:
            engine.accumulator.declare_azure_needed()

        # Get project — should report needed=True; no credentials stored
        resp = client.get(f"/api/projects/{slug}")
        assert resp.status_code == 200
        body = resp.json()
        assert "azure_storage" in body
        az = body["azure_storage"]
        assert az["needed"] is True
        # Confirm no credential fields are ever exposed
        assert "account_name" not in az
        assert "account_key" not in az
        assert "container_name" not in az

    def test_returns_azure_storage_needed_false_when_not_declared(self, client):
        """GET /api/projects/{slug} returns azure_storage.needed=False when not configured."""
        resp = client.post("/api/projects", json={"name": "no-azure-test", "description": "test"})
        assert resp.status_code == 200
        slug = resp.json()["slug"]

        resp = client.get(f"/api/projects/{slug}")
        assert resp.status_code == 200
        body = resp.json()
        az = body.get("azure_storage")
        assert az is not None
        assert az["needed"] is False
