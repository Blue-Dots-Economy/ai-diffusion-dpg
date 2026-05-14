"""Tests for ConversationEngine.chat delegation to phase_driver.run_turn.

Task 12.1 wired ``conversation.py`` so the new deterministic wizard path
(detected by presence of ``_meta/intake_state.json``) routes through
``phase_driver.run_turn``, while older projects fall back to the legacy
LLM-orchestrated path. These tests cover both branches by patching the
phase_driver and the Anthropic client.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from dev_kit.agent import phase_driver
from dev_kit.agent.conversation import ConversationEngine
from dev_kit.agent.intake_state import IntakeState, save_intake_state


def _make_intake_state(slug_root: Path) -> None:
    """Write a minimal IntakeState to ``slug_root/_meta/intake_state.json``."""
    intake = IntakeState(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="test domain",
        project_name="test-project",
    )
    save_intake_state(slug_root / "_meta" / "intake_state.json", intake)


def _make_engine(tmp_path: Path, slug: str = "demo") -> ConversationEngine:
    """Build a ConversationEngine pointed at a fresh project under ``tmp_path``."""
    project_path = tmp_path / "projects" / slug
    (project_path / "_meta").mkdir(parents=True, exist_ok=True)
    fake_client = mock.MagicMock()  # AsyncAnthropic stand-in; legacy path is patched separately.
    return ConversationEngine(project_path, fake_client)


# ---------------------------------------------------------------------------
# New-wizard delegation
# ---------------------------------------------------------------------------


def test_chat_with_intake_state_delegates_to_phase_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When intake_state.json is present, chat() delegates to phase_driver.run_turn."""
    engine = _make_engine(tmp_path)
    _make_intake_state(engine._project_path)

    captured: dict[str, Any] = {}

    def _fake_run_turn(user_message, project_slug, *, projects_root, llm_call):
        captured["user_message"] = user_message
        captured["project_slug"] = project_slug
        captured["projects_root"] = projects_root
        captured["llm_call"] = llm_call
        return "hello from phase_driver"

    monkeypatch.setattr(phase_driver, "run_turn", _fake_run_turn)

    result = asyncio.run(engine.chat("hello"))

    assert result["reply"] == "hello from phase_driver"
    assert captured["user_message"] == "hello"
    assert captured["project_slug"] == "demo"
    assert captured["projects_root"] == engine._project_path.parent
    assert callable(captured["llm_call"])


def test_chat_without_intake_state_raises_conversation_error(
    tmp_path: Path,
) -> None:
    """When intake_state.json is absent, chat() raises ConversationError (legacy projects unsupported)."""
    from dev_kit.agent.errors import ConversationError

    engine = _make_engine(tmp_path)
    # No intake_state.json written — should raise immediately.

    with pytest.raises(ConversationError, match="older version"):
        asyncio.run(engine.chat("hello"))


def test_chat_appends_to_history_in_new_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful new-wizard chat appends user + assistant entries to _history."""
    engine = _make_engine(tmp_path)
    _make_intake_state(engine._project_path)
    assert engine._history == []

    monkeypatch.setattr(
        phase_driver,
        "run_turn",
        lambda *a, **kw: "assistant reply",
    )

    asyncio.run(engine.chat("user input"))

    assert len(engine._history) == 2
    assert engine._history[0] == {"role": "user", "content": "user input"}
    assert engine._history[1] == {"role": "assistant", "content": "assistant reply"}


def test_chat_new_wizard_returns_current_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The returned dict reports the project's current_phase after the turn."""
    engine = _make_engine(tmp_path)
    _make_intake_state(engine._project_path)
    # Persist a non-default current_phase to exercise the read.
    phase_driver.save_current_phase(engine._project_path, "trust")

    monkeypatch.setattr(phase_driver, "run_turn", lambda *a, **kw: "ok")

    result = asyncio.run(engine.chat("any"))
    assert result["phase"] == "trust"


def test_chat_new_wizard_wraps_errors_in_conversation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_turn raising bubbles up as a ConversationError (not a raw exception)."""
    from dev_kit.agent.errors import ConversationError

    engine = _make_engine(tmp_path)
    _make_intake_state(engine._project_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("phase driver exploded")

    monkeypatch.setattr(phase_driver, "run_turn", _boom)

    with pytest.raises(ConversationError, match="phase_driver.run_turn failed"):
        asyncio.run(engine.chat("anything"))


def test_chat_new_wizard_logs_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Entering the new-wizard path emits a conversation.chat.new_wizard log entry."""
    engine = _make_engine(tmp_path)
    _make_intake_state(engine._project_path)

    monkeypatch.setattr(phase_driver, "run_turn", lambda *a, **kw: "ok")

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.conversation"):
        asyncio.run(engine.chat("hi"))

    assert any(
        getattr(rec, "operation", None) == "conversation.chat.new_wizard"
        for rec in caplog.records
    )
