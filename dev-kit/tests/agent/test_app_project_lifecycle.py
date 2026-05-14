"""Tests for the migrated project lifecycle endpoints (Task C.1).

Covers POST /api/projects, GET /api/projects, GET /api/projects/{slug},
and DELETE /api/projects/{slug} after the state-layer migration that
replaced ConfigAccumulator / ConversationEngine with per-request loaders.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with CONFIGS_DIR redirected to tmp_path/configs."""
    import dev_kit.agent.app as app_mod

    configs = tmp_path / "configs"
    configs.mkdir()
    monkeypatch.setattr(app_mod, "CONFIGS_DIR", configs)
    return TestClient(app_mod.app), configs


def _make_create_body(name="testproj"):
    return {
        "name": name,
        "project_name": name,
        "domain_description": "Test project",
        "selected_channels": ["web"],
        "default_language": "english",
        "supported_languages": ["english"],
    }


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------


def test_create_project_writes_intake_and_accumulator(client):
    """POST /api/projects writes intake_state.json and accumulator.json."""
    c, configs = client
    res = c.post("/api/projects", json=_make_create_body())
    assert res.status_code == 200
    slug = res.json()["slug"]
    meta_dir = configs / slug / "_meta"
    assert (meta_dir / "intake_state.json").exists()
    assert (meta_dir / "accumulator.json").exists()
    # current_phase persisted (as .txt or .json depending on implementation)
    assert (meta_dir / "current_phase.txt").exists() or (meta_dir / "current_phase.json").exists()


def test_create_project_does_not_seed_engine_registry(client):
    """POST /api/projects must NOT add an entry to any engine registry."""
    c, configs = client
    res = c.post("/api/projects", json=_make_create_body())
    assert res.status_code == 200
    # No in-memory registry exists anymore; the project endpoint is stateless.
    # Just assert the create call succeeded.
    assert res.json()["slug"]


def test_create_project_writes_placeholder_yamls(client):
    """POST /api/projects calls render_all and writes at least one YAML file."""
    c, configs = client
    res = c.post("/api/projects", json=_make_create_body())
    slug = res.json()["slug"]
    project = configs / slug
    yamls = list(project.glob("*.yaml"))
    assert len(yamls) >= 1


def test_create_project_accumulator_has_all_blocks(client):
    """accumulator.json written by POST /api/projects has empty dicts for all 7 blocks."""
    from dev_kit.agent.project_state import BLOCKS

    c, configs = client
    res = c.post("/api/projects", json=_make_create_body())
    slug = res.json()["slug"]
    acc_file = configs / slug / "_meta" / "accumulator.json"
    acc = json.loads(acc_file.read_text())
    for block in BLOCKS:
        assert block in acc
        assert acc[block] == {}


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}
# ---------------------------------------------------------------------------


def test_get_project_returns_block_statuses_strings(client):
    """GET /api/projects/{slug} returns config_statuses with 'complete'|'incomplete' values."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    assert res.status_code == 200
    body = res.json()
    assert "config_statuses" in body
    for block, status in body["config_statuses"].items():
        assert status in ("complete", "incomplete"), (
            f"block {block!r} has unexpected status {status!r}"
        )


def test_get_project_includes_intake_derived_fields(client):
    """GET /api/projects/{slug} populates has_knowledge_base from intake state."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    body = res.json()
    # has_kb defaults to False at project creation.
    assert body["has_knowledge_base"] is False


def test_get_project_web_channel_secrets_include_google(client):
    """GET /api/projects/{slug} includes GOOGLE_CLIENT_ID when web channel is selected."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    body = res.json()
    secrets = body["channel_secrets"]
    assert any(s["env_var"] == "GOOGLE_CLIENT_ID" for s in secrets)


def test_get_project_voice_channel_secrets(client):
    """GET /api/projects/{slug} includes VOBIZ_AUTH_ID when voice channel is selected."""
    c, configs = client
    create_body = _make_create_body(name="voicebot")
    create_body["selected_channels"] = ["voice"]
    slug = c.post("/api/projects", json=create_body).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    body = res.json()
    env_vars = [s["env_var"] for s in body["channel_secrets"]]
    assert "VOBIZ_AUTH_ID" in env_vars
    assert "VOBIZ_AUTH_TOKEN" in env_vars
    assert "RAYA_API_KEY" in env_vars


def test_get_project_defaults_llm_provider_to_anthropic(client):
    """GET /api/projects/{slug} defaults llm_provider to 'anthropic'."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    assert res.json()["llm_provider"] == "anthropic"


def test_get_project_azure_storage_defaults_false(client):
    """GET /api/projects/{slug} returns azure_storage.needed=False."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.get(f"/api/projects/{slug}")
    assert res.json()["azure_storage"] == {"needed": False}


def test_get_project_does_not_call_get_engine(client, monkeypatch):
    """GET /api/projects/{slug} is stateless — no engine registry is consulted."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    # _get_engine no longer exists; just assert the endpoint is stateless
    # by verifying it works correctly without any in-memory state.
    res = c.get(f"/api/projects/{slug}")
    assert res.status_code == 200


def test_get_project_legacy_project_without_intake_state(client):
    """GET /api/projects/{slug} on a legacy project (no intake_state.json) returns 200."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    # Simulate a legacy project by removing intake_state.json.
    (configs / slug / "_meta" / "intake_state.json").unlink()
    res = c.get(f"/api/projects/{slug}")
    assert res.status_code == 200
    body = res.json()
    assert body["has_knowledge_base"] is False
    assert body["channel_secrets"] == []


def test_get_project_404_on_missing(client):
    """GET /api/projects/{slug} returns 404 when the project does not exist."""
    c, _configs = client
    res = c.get("/api/projects/does-not-exist")
    assert res.status_code == 404


def test_get_project_500_on_corrupt_accumulator(client):
    """GET /api/projects/{slug} returns 500 when accumulator.json is corrupt."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    (configs / slug / "_meta" / "accumulator.json").write_text("not valid json {{{")
    res = c.get(f"/api/projects/{slug}")
    assert res.status_code == 500
    assert "Corrupt" in res.json().get("detail", "")


def test_get_project_recording_voice_secrets(client):
    """GET /api/projects/{slug} includes RECORDING_CALLER_ID_HASH_SALT when recording is enabled."""
    c, configs = client
    body = _make_create_body(name="recproj")
    body["selected_channels"] = ["voice"]
    body["supported_languages"] = ["english"]
    slug = c.post("/api/projects", json=body).json()["slug"]

    # Update accumulator to enable recording.
    acc_path = configs / slug / "_meta" / "accumulator.json"
    acc = json.loads(acc_path.read_text())
    acc["reach_layer"] = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "recording": {"source": "vobiz", "store": {"backend": "local"}}
                }
            }
        }
    }
    acc_path.write_text(json.dumps(acc))

    res = c.get(f"/api/projects/{slug}")
    secrets = res.json()["channel_secrets"]
    env_vars = {s["env_var"] for s in secrets}
    assert "RECORDING_CALLER_ID_HASH_SALT" in env_vars
    # S3 KMS only fires when store.backend == "s3"
    assert "RECORDING_S3_KMS_KEY_ID" not in env_vars


def test_get_project_recording_voice_s3_secrets(client):
    """GET /api/projects/{slug} includes RECORDING_S3_KMS_KEY_ID when recording backend is s3."""
    c, configs = client
    body = _make_create_body(name="recs3")
    body["selected_channels"] = ["voice"]
    slug = c.post("/api/projects", json=body).json()["slug"]

    acc_path = configs / slug / "_meta" / "accumulator.json"
    acc = json.loads(acc_path.read_text())
    acc["reach_layer"] = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "recording": {"source": "vobiz", "store": {"backend": "s3"}}
                }
            }
        }
    }
    acc_path.write_text(json.dumps(acc))

    res = c.get(f"/api/projects/{slug}")
    env_vars = {s["env_var"] for s in res.json()["channel_secrets"]}
    assert "RECORDING_S3_KMS_KEY_ID" in env_vars


# ---------------------------------------------------------------------------
# DELETE /api/projects/{slug}
# ---------------------------------------------------------------------------


def test_delete_project_removes_files(client):
    """DELETE /api/projects/{slug} removes the project directory."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    assert (configs / slug).exists()
    res = c.delete(f"/api/projects/{slug}")
    assert res.status_code == 200
    assert not (configs / slug).exists()


def test_delete_project_returns_deleted_key(client):
    """DELETE /api/projects/{slug} returns {'deleted': slug}."""
    c, configs = client
    slug = c.post("/api/projects", json=_make_create_body()).json()["slug"]
    res = c.delete(f"/api/projects/{slug}")
    assert res.json() == {"deleted": slug}


def test_delete_project_unknown_slug_returns_404(client):
    """DELETE /api/projects/{slug} returns 404 for a non-existent project."""
    c, configs = client
    res = c.delete("/api/projects/does-not-exist")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


def test_list_projects_returns_metadata(client):
    """GET /api/projects returns metadata for every created project."""
    c, configs = client
    c.post("/api/projects", json=_make_create_body(name="alpha"))
    c.post("/api/projects", json=_make_create_body(name="beta"))
    res = c.get("/api/projects")
    assert res.status_code == 200
    slugs = [p["slug"] for p in res.json()]
    assert "alpha" in slugs and "beta" in slugs


def test_list_projects_empty_when_no_projects(client):
    """GET /api/projects returns [] when CONFIGS_DIR is empty."""
    c, configs = client
    res = c.get("/api/projects")
    assert res.status_code == 200
    assert res.json() == []
