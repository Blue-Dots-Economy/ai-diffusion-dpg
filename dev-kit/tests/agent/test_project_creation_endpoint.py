"""Tests for POST /api/projects — Task 11.1 (5 intake fields on project creation).

Covers:
  - intake_state.json is written with form values after project creation
  - All 7 binary flags default to False
  - current_phase.txt is initialised to "tier"
  - accumulator.json is initialised with empty per-block dicts
  - Old ``{name, description}`` shape still works (backwards compatibility)
  - effective_project_name / effective_domain_description fallback logic
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")

import dev_kit.agent.app as app_module
from dev_kit.agent.project_state import BLOCKS
from dev_kit.agent.intake_state import load_intake_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with CONFIGS_DIR redirected to tmp_path."""
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# Task 11.1 — intake fields written on project creation
# ---------------------------------------------------------------------------


class TestProjectCreationIntakeState:
    """POST /api/projects writes intake_state.json with 5 form fields."""

    def test_intake_state_written_with_all_fields(self, client, tmp_path):
        """Creating a project with all 5 intake fields persists them correctly."""
        payload = {
            "name": "Test Bot",
            "description": "Helps users do X.",
            "project_name": "Test Bot",
            "domain_description": "Helps users do X.",
            "selected_channels": ["web"],
            "default_language": "english",
            "supported_languages": ["english", "hindi"],
        }
        resp = client.post("/api/projects", json=payload)
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]

        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        assert state.project_name == "Test Bot"
        assert state.domain_description == "Helps users do X."
        assert state.selected_channels == ["web"]
        assert state.default_language == "english"
        assert state.supported_languages == ["english", "hindi"]

    def test_seven_binary_flags_default_false(self, client, tmp_path):
        """All 7 binary flags must be False after project creation."""
        resp = client.post("/api/projects", json={
            "name": "Flag Check Bot",
            "description": "",
        })
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]

        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        assert state.has_kb is False
        assert state.has_external_tools is False
        assert state.is_multi_turn is False
        assert state.needs_persistent_user_data is False
        assert state.is_companion_style is False
        assert state.needs_consent is False
        assert state.has_hitl is False
        assert state.completed is False

    def test_current_phase_initialised_to_tier(self, client, tmp_path):
        """current_phase.txt must be written with value 'tier'."""
        resp = client.post("/api/projects", json={"name": "Phase Test", "description": ""})
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]

        phase_file = tmp_path / slug / "_meta" / "current_phase.txt"
        assert phase_file.exists(), "current_phase.txt was not created"
        assert phase_file.read_text().strip() == "tier"

    def test_accumulator_json_initialised_with_empty_blocks(self, client, tmp_path):
        """accumulator.json must exist with empty dicts for all 7 blocks."""
        resp = client.post("/api/projects", json={"name": "Acc Init Test", "description": ""})
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]

        acc_file = tmp_path / slug / "_meta" / "accumulator.json"
        assert acc_file.exists(), "accumulator.json was not created"
        acc = json.loads(acc_file.read_text())
        for block in BLOCKS:
            assert block in acc, f"Block {block!r} missing from accumulator.json"
            assert acc[block] == {}, f"Block {block!r} should be empty dict initially"

    def test_legacy_shape_still_works(self, client, tmp_path):
        """Old ``{name, description}`` shape must still create a valid project."""
        resp = client.post("/api/projects", json={"name": "Legacy Bot", "description": "old shape"})
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]
        assert slug == "legacy-bot"

        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        # Falls back to name/description
        assert state.project_name == "Legacy Bot"
        assert state.domain_description == "old shape"
        # Defaults
        assert state.selected_channels == ["web"]
        assert state.default_language == "english"

    def test_project_name_overrides_name_for_slug(self, client, tmp_path):
        """When project_name differs from name, project_name drives the slug."""
        resp = client.post("/api/projects", json={
            "name": "fallback",
            "description": "",
            "project_name": "Preferred Name",
        })
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["slug"] == "preferred-name"
        assert data["name"] == "Preferred Name"

    def test_voice_channel_accepted(self, client, tmp_path):
        """selected_channels=['voice'] must be stored correctly."""
        resp = client.post("/api/projects", json={
            "name": "Voice Bot",
            "description": "",
            "selected_channels": ["voice"],
        })
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]
        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        assert state.selected_channels == ["voice"]

    def test_multi_channel_accepted(self, client, tmp_path):
        """selected_channels=['web', 'voice'] must be stored correctly."""
        resp = client.post("/api/projects", json={
            "name": "Multi Channel Bot",
            "description": "",
            "selected_channels": ["web", "voice"],
        })
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]
        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        assert sorted(state.selected_channels) == ["voice", "web"]

    def test_intake_state_has_updated_at_timestamp(self, client, tmp_path):
        """intake_state.json must carry a non-empty updated_at timestamp."""
        resp = client.post("/api/projects", json={"name": "Timestamp Check", "description": ""})
        assert resp.status_code in (200, 201)
        slug = resp.json()["slug"]
        state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
        assert state.updated_at, "updated_at should be set after creation"
