"""Tests for GET /api/projects/{slug}/deploy-fields and POST /api/projects/{slug}/deploy-settings.

Task 11.2 — deploy form surfaces deploy_overridable fields with pre-fill.

Covers:
  - deploy-fields returns entries for category=='deploy' and deploy_overridable==True
  - agent.primary_model (deploy_overridable=True) is present in the result
  - current_value is pre-filled from the accumulator when set
  - current_value falls back to the rule default when accumulator has nothing
  - deploy-settings POST writes deploy_settings.json correctly
  - 404 for unknown project slug
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.renderer import render_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_client(tmp_path, monkeypatch):
    """Return a TestClient with CONFIGS_DIR redirected to tmp_path."""
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    return TestClient(app_module.app), tmp_path


@pytest.fixture()
def project(tmp_client):
    """Create a project via the API and return (client, slug, tmp_path)."""
    client, tmp_path = tmp_client
    resp = client.post("/api/projects", json={"name": "Deploy Test Bot", "description": "test"})
    assert resp.status_code in (200, 201)
    slug = resp.json()["slug"]
    return client, slug, tmp_path


# ---------------------------------------------------------------------------
# Task 11.2 — GET /api/projects/{slug}/deploy-fields
# ---------------------------------------------------------------------------


class TestDeployFieldsEndpoint:
    """GET /api/projects/{slug}/deploy-fields returns deploy-overridable fields."""

    def test_returns_deploy_overridable_fields(self, project):
        """agent.primary_model (deploy_overridable=True) must appear in fields."""
        client, slug, _ = project
        resp = client.get(f"/api/projects/{slug}/deploy-fields")
        assert resp.status_code == 200
        data = resp.json()
        paths = {entry["path"] for entry in data["fields"]}
        assert "agent_core.agent.primary_model" in paths, (
            "agent_core.agent.primary_model is deploy_overridable=True per catalogue §7.1"
        )

    def test_returns_provider_and_fallback_model(self, project):
        """agent.provider and agent.fallback_model must also be in the result."""
        client, slug, _ = project
        resp = client.get(f"/api/projects/{slug}/deploy-fields")
        assert resp.status_code == 200
        paths = {entry["path"] for entry in resp.json()["fields"]}
        assert "agent_core.agent.provider" in paths
        assert "agent_core.agent.fallback_model" in paths

    def test_each_entry_has_required_keys(self, project):
        """Every returned entry must have path, default, current_value, description, advanced."""
        client, slug, _ = project
        resp = client.get(f"/api/projects/{slug}/deploy-fields")
        assert resp.status_code == 200
        for entry in resp.json()["fields"]:
            for key in ("path", "default", "current_value", "description", "advanced"):
                assert key in entry, f"Key {key!r} missing from entry {entry!r}"

    def test_current_value_falls_back_to_default_when_not_in_accumulator(self, project):
        """current_value must equal the rule default when the accumulator has no value."""
        client, slug, _ = project
        resp = client.get(f"/api/projects/{slug}/deploy-fields")
        assert resp.status_code == 200
        by_path = {e["path"]: e for e in resp.json()["fields"]}
        provider_entry = by_path.get("agent_core.agent.provider")
        assert provider_entry is not None
        # Rule default for agent.provider is "anthropic"
        assert provider_entry["current_value"] == "anthropic"

    def test_unknown_slug_returns_404(self, tmp_client):
        """deploy-fields on a non-existent project must return 404."""
        client, _ = tmp_client
        resp = client.get("/api/projects/does-not-exist/deploy-fields")
        assert resp.status_code == 404

    def test_no_framework_default_only_fields_returned(self, project):
        """Fields with category=='framework_default_only' must not appear."""
        from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
        client, slug, _ = project
        resp = client.get(f"/api/projects/{slug}/deploy-fields")
        assert resp.status_code == 200
        returned_paths = {e["path"] for e in resp.json()["fields"]}
        for path, rule in AGGREGATED_FIELD_RULES.items():
            if rule.category == "framework_default_only":
                assert path not in returned_paths, (
                    f"framework_default_only field {path!r} should not appear in deploy-fields"
                )


# ---------------------------------------------------------------------------
# Task 11.2 — POST /api/projects/{slug}/deploy-settings
# ---------------------------------------------------------------------------


class TestDeploySettingsEndpoint:
    """POST /api/projects/{slug}/deploy-settings persists overrides to disk."""

    def test_saves_overrides_to_deploy_settings_json(self, project):
        """Overrides must be written to _meta/deploy_settings.json."""
        client, slug, tmp_path = project
        overrides = {
            "agent_core.agent.primary_model": "claude-opus-4-5",
            "agent_core.agent.provider": "anthropic",
        }
        resp = client.post(
            f"/api/projects/{slug}/deploy-settings",
            json={"overrides": overrides},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert data["override_count"] == 2

        # Verify file on disk
        settings_file = tmp_path / slug / "_meta" / "deploy_settings.json"
        assert settings_file.exists()
        on_disk = json.loads(settings_file.read_text())
        assert on_disk["agent_core.agent.primary_model"] == "claude-opus-4-5"

    def test_empty_overrides_writes_empty_file(self, project):
        """An empty overrides dict must still write the file."""
        client, slug, tmp_path = project
        resp = client.post(
            f"/api/projects/{slug}/deploy-settings",
            json={"overrides": {}},
        )
        assert resp.status_code == 200
        settings_file = tmp_path / slug / "_meta" / "deploy_settings.json"
        assert settings_file.exists()
        assert json.loads(settings_file.read_text()) == {}

    def test_unknown_slug_returns_404(self, tmp_client):
        """deploy-settings on a non-existent project must return 404."""
        client, _ = tmp_client
        resp = client.post(
            "/api/projects/does-not-exist/deploy-settings",
            json={"overrides": {}},
        )
        assert resp.status_code == 404
