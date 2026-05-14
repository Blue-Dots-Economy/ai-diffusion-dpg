"""Tests for the migrated /chat and /history endpoints (Task C.2)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import dev_kit.agent.app as app_mod
    configs = tmp_path / "configs"
    configs.mkdir()
    monkeypatch.setattr(app_mod, "CONFIGS_DIR", configs)
    return TestClient(app_mod.app), configs


def _create_project(c, name="chat-proj"):
    res = c.post("/api/projects", json={
        "name": name, "project_name": name,
        "domain_description": "test bot",
        "selected_channels": ["web"],
        "default_language": "english", "supported_languages": ["english"],
    })
    return res.json()["slug"]


def test_chat_404_on_missing_project(client):
    c, _ = client
    res = c.post("/api/projects/does-not-exist/chat", json={"message": "hello"})
    assert res.status_code == 404


def test_chat_400_when_no_intake_state(client, monkeypatch):
    """Pre-existing project directory without intake_state.json yields 400."""
    c, configs = client
    legacy = configs / "legacy"
    (legacy / "_meta").mkdir(parents=True)
    res = c.post("/api/projects/legacy/chat", json={"message": "hi"})
    assert res.status_code == 400
    assert "older version" in res.json().get("detail", "")


def test_chat_delegates_to_phase_driver_and_returns_shape(client, monkeypatch):
    """Successful chat returns the wizard-shaped dict and triggers history append."""
    c, configs = client
    slug = _create_project(c)

    # Patch phase_driver.run_turn to bypass real LLM. Also let history.append_turn
    # run with the real implementation so we can verify the file write.
    import dev_kit.agent.app as app_mod

    def _fake_run_turn(user_message, project_slug, *, projects_root, llm_call):
        # Simulate the real run_turn's history-append behaviour using the actual
        # helper (the real run_turn does this inside).
        from dev_kit.agent.history import HistoryEntry, append_turn, utc_now_iso
        slug_root = projects_root / project_slug
        append_turn(slug_root, HistoryEntry(
            role="user", content=user_message,
            phase="tier", timestamp=utc_now_iso(),
        ))
        append_turn(slug_root, HistoryEntry(
            role="assistant", content="assistant reply",
            phase="tier", timestamp=utc_now_iso(),
        ))
        return "assistant reply"

    monkeypatch.setattr(app_mod.phase_driver, "run_turn", _fake_run_turn)
    res = c.post(f"/api/projects/{slug}/chat", json={"message": "hello"})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "assistant reply"
    assert body["phase"] in ("tier", "language", "knowledge", "memory", "user_state",
                             "trust", "tools", "workflow", "observability", "reach", "review")
    assert body["config_updates"] == []
    assert body["checkpoint_created"] is None
    assert body["graph"] == {}
    # And history.jsonl now exists with the two entries.
    history_path = configs / slug / "_meta" / "history.jsonl"
    assert history_path.exists()
    lines = [json.loads(l) for l in history_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0]["role"] == "user"
    assert lines[1]["role"] == "assistant"


def test_chat_500_when_phase_driver_raises(client, monkeypatch):
    c, _ = client
    slug = _create_project(c)
    import dev_kit.agent.app as app_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("phase driver exploded")

    monkeypatch.setattr(app_mod.phase_driver, "run_turn", _boom)
    res = c.post(f"/api/projects/{slug}/chat", json={"message": "x"})
    assert res.status_code == 500


def test_history_404_on_missing_project(client):
    c, _ = client
    res = c.get("/api/projects/does-not-exist/history")
    assert res.status_code == 404


def test_history_empty_when_no_chat_yet(client):
    c, _ = client
    slug = _create_project(c)
    res = c.get(f"/api/projects/{slug}/history")
    assert res.status_code == 200
    assert res.json() == []


def test_history_returns_entries_in_order(client):
    c, configs = client
    slug = _create_project(c)
    # Hand-write history.jsonl to bypass the chat path.
    h = configs / slug / "_meta" / "history.jsonl"
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text(
        '{"role":"user","content":"hi","phase":"tier","timestamp":"t1"}\n'
        '{"role":"assistant","content":"hello","phase":"tier","timestamp":"t2"}\n'
    )
    res = c.get(f"/api/projects/{slug}/history")
    assert res.status_code == 200
    body = res.json()
    assert body == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
