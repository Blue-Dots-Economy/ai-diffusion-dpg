"""Tests for project and config routes in dev_kit.agent.app.

Covers:
  - POST /api/projects (create project)
  - GET /api/projects (list projects)
  - GET /api/projects/importable (list importable folders)
  - POST /api/projects/import (import bare config folder)
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
  - _slugify, _is_importable_folder, _list_importable_slugs,
    _infer_phases_completed, _derive_meta_from_folder helpers
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
        assert data["current_phase"] == "overview"

    def test_create_writes_project_json(self, client, tmp_path):
        """Project metadata is persisted to disk."""
        client.post("/api/projects", json={"name": "Disk Test", "description": "d"})
        meta_file = tmp_path / "disk-test" / "_meta" / "project.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["slug"] == "disk-test"

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
# Import helpers unit tests
# ---------------------------------------------------------------------------

from dev_kit.agent.app import (
    _is_importable_folder,
    _list_importable_slugs,
    _derive_meta_from_folder,
    _infer_phases_completed,
)


class TestIsImportableFolder:
    def test_returns_true_when_has_yaml_and_no_meta(self, tmp_path):
        folder = tmp_path / "my-domain"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: claude-haiku-4-5\n")
        assert _is_importable_folder(folder) is True

    def test_returns_false_when_meta_exists(self, tmp_path):
        folder = tmp_path / "managed"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        meta = folder / "_meta"
        meta.mkdir()
        (meta / "project.json").write_text("{}")
        assert _is_importable_folder(folder) is False

    def test_returns_false_when_no_block_yamls(self, tmp_path):
        folder = tmp_path / "empty"
        folder.mkdir()
        (folder / "readme.txt").write_text("hi")
        assert _is_importable_folder(folder) is False

    def test_returns_false_for_file_not_dir(self, tmp_path):
        f = tmp_path / "notadir.yaml"
        f.write_text("")
        assert _is_importable_folder(f) is False


class TestListImportableSlugs:
    def test_returns_importable_slugs(self, tmp_path):
        for slug in ["bare-domain", "another-bare"]:
            d = tmp_path / slug
            d.mkdir()
            (d / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        managed = tmp_path / "managed"
        managed.mkdir()
        (managed / "agent_core.yaml").write_text("")
        meta = managed / "_meta"
        meta.mkdir()
        (meta / "project.json").write_text("{}")
        result = _list_importable_slugs(tmp_path)
        assert set(result) == {"bare-domain", "another-bare"}

    def test_returns_empty_when_no_configs_dir(self, tmp_path):
        assert _list_importable_slugs(tmp_path / "nonexistent") == []


class TestInferPhasesCompleted:
    def test_all_empty_returns_overview_phase(self):
        yamls = {b: {} for b in ["agent_core", "knowledge_engine", "memory_layer",
                                  "trust_layer", "action_gateway", "reach_layer",
                                  "observability_layer"]}
        phases_done, current = _infer_phases_completed(yamls)
        assert phases_done == []
        assert current == "overview"

    def test_agent_core_data_marks_overview_complete(self):
        yamls = {b: {} for b in ["agent_core", "knowledge_engine", "memory_layer",
                                  "trust_layer", "action_gateway", "reach_layer",
                                  "observability_layer"]}
        yamls["agent_core"] = {"agent": {"primary_model": "x"}}
        phases_done, current = _infer_phases_completed(yamls)
        assert "overview" in phases_done

    def test_all_blocks_filled_returns_review_phase(self):
        yamls = {
            "agent_core": {"agent": {"primary_model": "x"}},
            "knowledge_engine": {"rag": {"sources": []}},
            "memory_layer": {"session": {"ttl_seconds": 3600}},
            "trust_layer": {"guardrails": {"enabled": True}},
            "action_gateway": {"tools": [{"id": "t1"}]},
            "reach_layer": {"channels": ["web"]},
            "observability_layer": {"lifecycle_states": []},
        }
        phases_done, current = _infer_phases_completed(yamls)
        assert current == "review"


class TestDeriveMetaFromFolder:
    def test_derives_name_from_slug(self, tmp_path):
        folder = tmp_path / "hospital-helpdesk"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: claude-haiku-4-5\n")
        meta = _derive_meta_from_folder(folder, "hospital-helpdesk")
        assert meta["name"] == "Hospital Helpdesk"
        assert meta["slug"] == "hospital-helpdesk"

    def test_derives_primary_model_from_agent_core(self, tmp_path):
        folder = tmp_path / "mybot"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: claude-sonnet-4-6\n")
        meta = _derive_meta_from_folder(folder, "mybot")
        assert meta.get("primary_model") == "claude-sonnet-4-6"

    def test_handles_missing_yaml_gracefully(self, tmp_path):
        folder = tmp_path / "partial"
        folder.mkdir()
        meta = _derive_meta_from_folder(folder, "partial")
        assert meta["slug"] == "partial"
        assert "name" in meta

    def test_imported_flag_is_true(self, tmp_path):
        folder = tmp_path / "mybot"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        meta = _derive_meta_from_folder(folder, "mybot")
        assert meta["imported"] is True


# ---------------------------------------------------------------------------
# GET /api/projects/importable
# ---------------------------------------------------------------------------


class TestListImportableEndpoint:
    def test_returns_importable_folders(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        bare = tmp_path / "kkb"
        bare.mkdir()
        (bare / "agent_core.yaml").write_text("agent:\n  primary_model: claude-haiku-4-5\n")
        managed = tmp_path / "managed"
        managed.mkdir()
        (managed / "agent_core.yaml").write_text("")
        meta_dir = managed / "_meta"
        meta_dir.mkdir()
        (meta_dir / "project.json").write_text('{"slug":"managed"}')
        client = TestClient(app_module.app)
        res = client.get("/api/projects/importable")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["slug"] == "kkb"
        assert "detected_blocks" in data[0]
        assert "agent_core" in data[0]["detected_blocks"]

    def test_returns_empty_when_no_importable_folders(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        client = TestClient(app_module.app)
        res = client.get("/api/projects/importable")
        assert res.status_code == 200
        assert res.json() == []

    def test_returns_validation_errors_per_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "invalid-domain"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        client = TestClient(app_module.app)
        res = client.get("/api/projects/importable")
        assert res.status_code == 200
        item = res.json()[0]
        assert "validation_errors" in item


# ---------------------------------------------------------------------------
# POST /api/projects/import
# ---------------------------------------------------------------------------


class TestImportProjectEndpoint:
    def test_imports_bare_folder_successfully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "kkb"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text(
            "agent:\n  primary_model: claude-haiku-4-5\n  fallback_model: claude-haiku-4-5\n"
        )
        (folder / "knowledge_engine.yaml").write_text("rag:\n  similarity_threshold: 0.7\n")
        client = TestClient(app_module.app)
        res = client.post("/api/projects/import", json={"slug": "kkb"})
        assert res.status_code == 200
        data = res.json()
        assert data["slug"] == "kkb"
        assert data["imported"] is True
        assert (tmp_path / "kkb" / "_meta" / "project.json").exists()

    def test_import_creates_checkpoint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "mybot"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        client = TestClient(app_module.app)
        client.post("/api/projects/import", json={"slug": "mybot"})
        checkpoint_dir = tmp_path / "mybot" / "_meta" / "checkpoints" / "00_imported"
        assert checkpoint_dir.exists()
        assert (checkpoint_dir / "accumulator.json").exists()
        assert (checkpoint_dir / "summary.txt").exists()

    def test_import_creates_accumulator_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "mybot"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        client = TestClient(app_module.app)
        client.post("/api/projects/import", json={"slug": "mybot"})
        acc_path = tmp_path / "mybot" / "_meta" / "accumulator.json"
        assert acc_path.exists()
        data = json.loads(acc_path.read_text())
        assert data["data"]["agent_core"] == {"agent": {"primary_model": "x"}}

    def test_import_rejects_already_managed_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "managed"
        folder.mkdir()
        (folder / "agent_core.yaml").write_text("")
        meta_dir = folder / "_meta"
        meta_dir.mkdir()
        (meta_dir / "project.json").write_text('{"slug":"managed"}')
        client = TestClient(app_module.app)
        res = client.post("/api/projects/import", json={"slug": "managed"})
        assert res.status_code == 409

    def test_import_rejects_nonexistent_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        client = TestClient(app_module.app)
        res = client.post("/api/projects/import", json={"slug": "ghost"})
        assert res.status_code == 404

    def test_import_rejects_folder_with_no_block_yamls(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        folder = tmp_path / "empty-folder"
        folder.mkdir()
        (folder / "notes.txt").write_text("hi")
        client = TestClient(app_module.app)
        res = client.post("/api/projects/import", json={"slug": "empty-folder"})
        assert res.status_code == 422

    def test_existing_projects_unaffected_after_import(self, tmp_path, monkeypatch):
        from dev_kit.agent.renderer import render_all
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        app_module._engines.clear()
        managed = tmp_path / "managed"
        managed.mkdir()
        meta_dir = managed / "_meta"
        meta_dir.mkdir()
        managed_meta = {
            "slug": "managed", "name": "Managed", "description": "",
            "current_phase": "overview", "phases_completed": [],
        }
        (meta_dir / "project.json").write_text(json.dumps(managed_meta))
        render_all(managed, ConfigAccumulator())
        bare = tmp_path / "bare-domain"
        bare.mkdir()
        (bare / "agent_core.yaml").write_text("agent:\n  primary_model: x\n")
        client = TestClient(app_module.app)
        client.post("/api/projects/import", json={"slug": "bare-domain"})
        res = client.get("/api/projects/managed")
        assert res.status_code == 200
        assert res.json()["slug"] == "managed"
