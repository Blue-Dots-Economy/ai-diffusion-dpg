"""Tests for GET /api/projects/{slug}/field-status.

Task 11.3 — chat UI reads field_status.json for phase progress.

Covers:
  - Returns persisted status dict when field_status.json exists
  - Returns empty dict when file is absent (new project, wizard not started)
  - 404 for unknown project slug
  - All four valid status values are returned correctly
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.field_status import save_field_status


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
    resp = client.post("/api/projects", json={"name": "Status Test Bot", "description": ""})
    assert resp.status_code in (200, 201)
    slug = resp.json()["slug"]
    return client, slug, tmp_path


# ---------------------------------------------------------------------------
# Task 11.3 — GET /api/projects/{slug}/field-status
# ---------------------------------------------------------------------------


class TestFieldStatusEndpoint:
    """GET /api/projects/{slug}/field-status returns field_status.json contents."""

    def test_returns_persisted_status(self, project):
        """Endpoint must return the dict written to field_status.json."""
        client, slug, tmp_path = project
        # Write a field_status.json directly
        status = {
            "agent_core.agent.primary_model": "answered",
            "agent_core.agent.provider": "answered",
            "agent_core.conversation.persona": "pending",
        }
        save_field_status(tmp_path / slug / "_meta" / "field_status.json", status)

        resp = client.get(f"/api/projects/{slug}/field-status")
        assert resp.status_code == 200
        assert resp.json()["agent_core.agent.primary_model"] == "answered"
        assert resp.json()["agent_core.conversation.persona"] == "pending"

    def test_returns_empty_dict_when_file_absent(self, project):
        """When field_status.json does not exist, endpoint returns empty dict."""
        client, slug, tmp_path = project
        # Ensure file is absent (may have been created by other fixture code)
        status_file = tmp_path / slug / "_meta" / "field_status.json"
        if status_file.exists():
            status_file.unlink()

        resp = client.get(f"/api/projects/{slug}/field-status")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_all_four_status_values_round_trip(self, project):
        """All four valid status values must survive a write-then-read cycle."""
        client, slug, tmp_path = project
        status = {
            "agent_core.agent.primary_model": "answered",
            "agent_core.agent.fallback_model": "pending",
            "trust_layer.content.blocked_topics": "needs_re_asking",
            "knowledge_engine.retrieval.top_k": "not_applicable",
        }
        save_field_status(tmp_path / slug / "_meta" / "field_status.json", status)

        resp = client.get(f"/api/projects/{slug}/field-status")
        assert resp.status_code == 200
        result = resp.json()
        assert result["agent_core.agent.primary_model"] == "answered"
        assert result["agent_core.agent.fallback_model"] == "pending"
        assert result["trust_layer.content.blocked_topics"] == "needs_re_asking"
        assert result["knowledge_engine.retrieval.top_k"] == "not_applicable"

    def test_unknown_slug_returns_404(self, tmp_client):
        """field-status on a non-existent project must return 404."""
        client, _ = tmp_client
        resp = client.get("/api/projects/does-not-exist/field-status")
        assert resp.status_code == 404

    def test_larger_status_file_returns_full_dict(self, project):
        """A realistic field_status.json with many entries is returned in full."""
        client, slug, tmp_path = project
        # Build a larger status dict covering all blocks
        from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
        status = {}
        for path, rule in list(AGGREGATED_FIELD_RULES.items())[:20]:
            status[path] = "pending"
        save_field_status(tmp_path / slug / "_meta" / "field_status.json", status)

        resp = client.get(f"/api/projects/{slug}/field-status")
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == len(status)
        for path in status:
            assert result[path] == "pending"
