"""Tests for phase_driver.run_turn — the wizard's single shared turn-runner.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §6.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
from dev_kit.agent.field_status import save_field_status
from dev_kit.agent.intake_state import IntakeState, save_intake_state
from dev_kit.agent.phase_driver import (
    LLMResponse,
    ToolCall,
    collect_pending_fields,
    cross_phase_references,
    load_accumulator,
    load_current_phase,
    render_pydantic_classes,
    run_turn,
    save_accumulator,
    save_current_phase,
)
from dev_kit.agent.skeleton import BLOCKS


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_intake(**overrides) -> IntakeState:
    base = dict(
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
    base.update(overrides)
    return IntakeState(**base)


def _setup_project(
    tmp_path: Path,
    *,
    slug: str = "demo",
    intake: IntakeState | None = None,
    accumulator: dict[str, dict] | None = None,
    field_status: dict[str, str] | None = None,
    current_phase: str | None = "tier",
) -> Path:
    """Lay out a minimal valid project tree under ``tmp_path/projects/<slug>``.

    Returns the projects_root path the driver should be pointed at.
    """
    projects_root = tmp_path / "projects"
    slug_root = projects_root / slug
    meta = slug_root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)

    if intake is None:
        intake = _make_intake()
    save_intake_state(meta / "intake_state.json", intake)

    if accumulator is not None:
        save_accumulator(slug_root, accumulator)

    if field_status is not None:
        save_field_status(meta / "field_status.json", field_status)

    if current_phase is not None:
        save_current_phase(slug_root, current_phase)

    return projects_root


def _fake_llm(text: str = "ok", tool_calls: list[ToolCall] | None = None):
    """Return a callable that records its args and returns a canned LLMResponse."""
    captured: dict[str, Any] = {"system_prompt": None, "user_message": None, "calls": 0}

    def _call(system_prompt: str, user_message: str) -> LLMResponse:
        captured["system_prompt"] = system_prompt
        captured["user_message"] = user_message
        captured["calls"] += 1
        return LLMResponse(text=text, tool_calls=list(tool_calls or []))

    return _call, captured


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


def test_run_turn_loads_state_and_returns_response(tmp_path: Path) -> None:
    """run_turn returns the assistant's text from a fake LLM."""
    projects_root = _setup_project(tmp_path)
    fake, _ = _fake_llm(text="hello")

    response_text = run_turn(
        user_message="hi",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    assert response_text == "hello"


def test_run_turn_routes_update_intake(tmp_path: Path) -> None:
    """update_intake tool call mutates the persisted IntakeState."""
    projects_root = _setup_project(tmp_path)
    fake, _ = _fake_llm(tool_calls=[ToolCall("update_intake", {"field": "has_kb", "value": True})])

    run_turn(
        user_message="yes",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    saved = json.loads((projects_root / "demo" / "_meta" / "intake_state.json").read_text())
    assert saved["has_kb"] is True


def test_run_turn_routes_update_config(tmp_path: Path) -> None:
    """update_config tool call writes to accumulator and marks field answered."""
    intake = _make_intake()
    # Trust phase has unconditional chat fields like blocked_phrases.
    projects_root = _setup_project(
        tmp_path,
        intake=intake,
        field_status={"trust_layer.trust.input_rules.blocked_phrases": "pending"},
        current_phase="trust",
    )

    fake, _ = _fake_llm(
        tool_calls=[
            ToolCall(
                "update_config",
                {
                    "path": "trust_layer.trust.input_rules.blocked_phrases",
                    "value": ["badword"],
                },
            )
        ]
    )

    run_turn(
        user_message="ok",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    acc = load_accumulator(projects_root / "demo")
    assert acc["trust_layer"]["trust"]["input_rules"]["blocked_phrases"] == ["badword"]

    statuses = json.loads(
        (projects_root / "demo" / "_meta" / "field_status.json").read_text()
    )
    assert statuses["trust_layer.trust.input_rules.blocked_phrases"] == "answered"


def test_run_turn_advances_phase_when_complete(tmp_path: Path) -> None:
    """When all applicable chat fields are answered, the router advances the phase."""
    from dev_kit.agent.skeleton import eval_expr

    intake = _make_intake()
    # Build a field_status where every applicable language-phase chat field is "answered".
    # (has_kb=false so knowledge is skipped → next relevant phase after language is memory.)
    answered = {
        path: "answered"
        for path, rule in AGGREGATED_FIELD_RULES.items()
        if rule.category == "chat" and rule.phase == "language"
        and eval_expr(rule.applies_if, intake)
    }
    projects_root = _setup_project(
        tmp_path,
        intake=intake,
        field_status=answered,
        current_phase="language",
    )

    fake, _ = _fake_llm()
    run_turn(
        user_message="",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    new_phase = load_current_phase(projects_root / "demo")
    assert new_phase == "memory"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_run_turn_with_pending_fields_calls_phase_prompt(tmp_path: Path) -> None:
    """The injected llm_call sees the phase-specific system prompt header."""
    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        current_phase="trust",
    )
    fake, captured = _fake_llm()

    run_turn(
        user_message="please configure",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    assert captured["system_prompt"] is not None
    assert "# Phase: Trust" in captured["system_prompt"]


def test_run_turn_unsupported_tool_skipped(tmp_path: Path, caplog) -> None:
    """Unknown tool names are logged and skipped without crashing.

    Phase 7 expanded TOOL_HANDLERS to 8 tools; use a genuinely unknown name
    (not one of the 8 canonical tools) to verify the skip-and-log path.
    """
    projects_root = _setup_project(tmp_path)
    fake, _ = _fake_llm(tool_calls=[ToolCall("old_set_phase", {"phase": "tools"})])

    with caplog.at_level(logging.WARNING, logger="dev_kit.agent.phase_driver"):
        result = run_turn(
            user_message="something",
            project_slug="demo",
            projects_root=projects_root,
            llm_call=fake,
        )

    assert result == "ok"
    assert any(
        getattr(rec, "operation", None) == "phase_driver.tool_call_rejected"
        and getattr(rec, "tool", None) == "old_set_phase"
        for rec in caplog.records
    )


def test_run_turn_creates_current_phase_file_if_missing(tmp_path: Path) -> None:
    """A project without current_phase.txt defaults to the 'tier' phase."""
    projects_root = _setup_project(tmp_path, current_phase=None)
    # Confirm the file is not present.
    phase_file = projects_root / "demo" / "_meta" / "current_phase.txt"
    assert not phase_file.exists()
    assert load_current_phase(projects_root / "demo") == "tier"

    fake, captured = _fake_llm()
    run_turn(
        user_message="hi",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    # Tier phase produces a known header
    assert "# Phase: Tier intake chat" in captured["system_prompt"]


def test_run_turn_empty_pending_fields_in_phase(tmp_path: Path) -> None:
    """A phase whose fields are all not_applicable still builds a prompt without crashing.

    user_state phase is gated by is_companion_style=False → no chat fields apply.
    """
    intake = _make_intake(is_companion_style=False)
    projects_root = _setup_project(
        tmp_path,
        intake=intake,
        current_phase="user_state",
    )
    fake, captured = _fake_llm()

    result = run_turn(
        user_message="no",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    assert result == "ok"
    assert captured["system_prompt"]  # non-empty prompt returned


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_run_turn_missing_intake_state_raises(tmp_path: Path) -> None:
    """A project directory without intake_state.json raises FileNotFoundError."""
    projects_root = tmp_path / "projects"
    (projects_root / "demo" / "_meta").mkdir(parents=True)
    fake, _ = _fake_llm()

    with pytest.raises(FileNotFoundError):
        run_turn(
            user_message="hi",
            project_slug="demo",
            projects_root=projects_root,
            llm_call=fake,
        )


def test_run_turn_invalid_current_phase_raises(tmp_path: Path) -> None:
    """Writing an invalid phase name via save_current_phase raises ValueError."""
    projects_root = _setup_project(tmp_path, current_phase=None)
    slug_root = projects_root / "demo"

    with pytest.raises(ValueError):
        save_current_phase(slug_root, "bogus_phase")


def test_run_turn_update_config_validation_failure_does_not_crash(tmp_path: Path) -> None:
    """An update_config call with an invalid value does not abort the turn.

    blocked_message is str with min_length=1; empty string violates the
    schema. The handler should catch the ValueError, log a warning, and the
    accumulator should remain at the original valid state.
    """
    projects_root = _setup_project(
        tmp_path,
        accumulator={
            **{b: {} for b in BLOCKS},
            "agent_core": {"conversation": {"blocked_message": "original"}},
        },
        field_status={"agent_core.conversation.blocked_message": "pending"},
        current_phase="language",
    )
    fake, _ = _fake_llm(
        text="bad",
        tool_calls=[
            ToolCall(
                "update_config",
                {"path": "agent_core.conversation.blocked_message", "value": ""},
            )
        ],
    )

    result = run_turn(
        user_message="x",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    assert result == "bad"
    acc = load_accumulator(projects_root / "demo")
    assert acc["agent_core"]["conversation"]["blocked_message"] == "original"
    statuses = json.loads(
        (projects_root / "demo" / "_meta" / "field_status.json").read_text()
    )
    # Not marked answered — the write was rejected.
    assert statuses["agent_core.conversation.blocked_message"] == "pending"


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_collect_pending_fields_filters_correctly() -> None:
    """Only chat fields matching phase, applies_if, and status are returned."""
    intake = _make_intake(has_hitl=True)
    # Pick a known trust-phase chat field that applies when has_hitl=True.
    target = "trust_layer.trust.input_rules.escalation_topics"
    field_status = {target: "pending"}

    pending = collect_pending_fields("trust", intake, field_status)
    paths = {p for p, _ in pending}
    assert target in paths

    # Same field but status=answered → excluded.
    pending2 = collect_pending_fields("trust", intake, {target: "answered"})
    paths2 = {p for p, _ in pending2}
    assert target not in paths2

    # needs_re_asking is included.
    pending3 = collect_pending_fields("trust", intake, {target: "needs_re_asking"})
    paths3 = {p for p, _ in pending3}
    assert target in paths3

    # A different phase excludes the trust-phase target entirely.
    pending4 = collect_pending_fields("memory", intake, field_status)
    paths4 = {p for p, _ in pending4}
    assert target not in paths4


def test_collect_pending_fields_excludes_inapplicable(tmp_path: Path) -> None:
    """A chat field whose applies_if is False is not collected."""
    # has_hitl gates escalation_topics; when False, the field does not apply.
    intake = _make_intake(has_hitl=False)
    field_status = {"trust_layer.trust.input_rules.escalation_topics": "pending"}
    pending = collect_pending_fields("trust", intake, field_status)
    paths = {p for p, _ in pending}
    assert "trust_layer.trust.input_rules.escalation_topics" not in paths


def test_cross_phase_references_returns_empty_when_accumulator_empty() -> None:
    """An empty accumulator produces an empty references string."""
    acc = {b: {} for b in BLOCKS}
    assert cross_phase_references(acc) == ""


def test_cross_phase_references_includes_set_values() -> None:
    """Populated provider/primary_model surface in the references output."""
    acc = {b: {} for b in BLOCKS}
    acc["agent_core"] = {
        "agent": {
            "provider": "anthropic",
            "primary_model": "claude-sonnet-4-5",
        },
        "preprocessing": {
            "language_normalisation": {
                "default_language": "english",
                "supported_languages": ["english", "hindi"],
            },
        },
    }

    out = cross_phase_references(acc)
    assert "agent_core.agent.provider: anthropic" in out
    assert "agent_core.agent.primary_model: claude-sonnet-4-5" in out
    assert "default_language: english" in out
    assert "supported_languages" in out


def test_render_pydantic_classes_empty_returns_empty_string() -> None:
    """No pending fields → empty placeholder."""
    assert render_pydantic_classes([]) == ""


def test_render_pydantic_classes_lists_pending_paths() -> None:
    """When pending fields exist, the placeholder lists their paths."""
    rule = AGGREGATED_FIELD_RULES["trust_layer.trust.input_rules.blocked_phrases"]
    out = render_pydantic_classes(
        [("trust_layer.trust.input_rules.blocked_phrases", rule)]
    )
    assert "trust_layer.trust.input_rules.blocked_phrases" in out


# ---------------------------------------------------------------------------
# LLM injection
# ---------------------------------------------------------------------------


def test_llm_call_receives_system_prompt_and_user_message(tmp_path: Path) -> None:
    """The injected llm_call receives non-empty system_prompt and the verbatim user message."""
    projects_root = _setup_project(tmp_path)
    fake, captured = _fake_llm()

    run_turn(
        user_message="please continue",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    assert captured["calls"] == 1
    assert captured["user_message"] == "please continue"
    assert isinstance(captured["system_prompt"], str)
    assert captured["system_prompt"].strip()  # non-empty


# ---------------------------------------------------------------------------
# Persistence smoke tests
# ---------------------------------------------------------------------------


def test_load_accumulator_missing_returns_empty_skeleton(tmp_path: Path) -> None:
    """A project without accumulator.json gets an all-blocks-empty skeleton."""
    slug_root = tmp_path / "p"
    slug_root.mkdir()
    acc = load_accumulator(slug_root)
    assert set(acc.keys()) >= set(BLOCKS)
    for b in BLOCKS:
        assert acc[b] == {}


def test_load_accumulator_corrupt_returns_empty_skeleton(tmp_path: Path) -> None:
    """Corrupt accumulator JSON is treated as missing."""
    slug_root = tmp_path / "p"
    meta = slug_root / "_meta"
    meta.mkdir(parents=True)
    (meta / "accumulator.json").write_text("{not json")
    acc = load_accumulator(slug_root)
    for b in BLOCKS:
        assert acc[b] == {}


def test_save_and_load_accumulator_round_trip(tmp_path: Path) -> None:
    """save_accumulator persists a payload that load_accumulator reads back."""
    slug_root = tmp_path / "p"
    payload = {b: {} for b in BLOCKS}
    payload["agent_core"] = {"agent": {"primary_model": "claude-sonnet-4-5"}}
    save_accumulator(slug_root, payload)

    reloaded = load_accumulator(slug_root)
    assert reloaded["agent_core"]["agent"]["primary_model"] == "claude-sonnet-4-5"


def test_load_current_phase_unknown_falls_back_to_default(tmp_path: Path) -> None:
    """An unknown phase value falls back to 'tier'."""
    slug_root = tmp_path / "p"
    meta = slug_root / "_meta"
    meta.mkdir(parents=True)
    (meta / "current_phase.txt").write_text("not-a-phase")
    assert load_current_phase(slug_root) == "tier"


def test_load_phase_prompt_raises_when_build_missing(monkeypatch) -> None:
    """A phase-prompt module without a `build` attribute raises AttributeError."""
    import types

    from dev_kit.agent import phase_driver

    fake_module = types.ModuleType("fake_phase_prompt")  # no `build` attr
    monkeypatch.setattr(
        phase_driver.importlib,
        "import_module",
        lambda _name: fake_module,
    )

    with pytest.raises(AttributeError, match="no 'build' function"):
        phase_driver._load_phase_prompt("tier")


# ---------------------------------------------------------------------------
# History append wiring (Task C.2)
# ---------------------------------------------------------------------------


def test_run_turn_appends_user_and_assistant_to_history(tmp_path: Path) -> None:
    """run_turn appends a user + assistant entry to _meta/history.jsonl."""
    projects_root = _setup_project(tmp_path)
    fake, _ = _fake_llm(text="ack")

    run_turn(
        "hi",
        "demo",
        projects_root=projects_root,
        llm_call=lambda sp, um: LLMResponse(text="ack", tool_calls=[], model="x"),
    )

    h_path = projects_root / "demo" / "_meta" / "history.jsonl"
    assert h_path.exists(), "history.jsonl should be created by run_turn"
    lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
    assert [(e["role"], e["content"]) for e in lines] == [
        ("user", "hi"),
        ("assistant", "ack"),
    ]


def test_run_turn_history_phase_label_matches_active_phase(tmp_path: Path) -> None:
    """History entries are tagged with the phase that was active when the turn ran."""
    projects_root = _setup_project(tmp_path, current_phase="trust")
    fake, _ = _fake_llm(text="noted")

    run_turn(
        "configure trust",
        "demo",
        projects_root=projects_root,
        llm_call=lambda sp, um: LLMResponse(text="noted", tool_calls=[], model="x"),
    )

    h_path = projects_root / "demo" / "_meta" / "history.jsonl"
    lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
    assert all(e["phase"] == "trust" for e in lines), (
        "Both user and assistant entries should carry the active phase 'trust'"
    )


def test_run_turn_build_skeleton_called_when_tier_completes(tmp_path: Path) -> None:
    """When all 7 binary flags are captured in one turn, build_skeleton populates field_status.

    Simulates the tier-completion scenario: field_status starts empty and the
    LLM emits 7 update_intake tool calls (one per binary flag). After run_turn,
    field_status must be populated (skeleton ran) and the next phase must be
    'language'.
    """
    from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS

    intake = _make_intake()  # completed=False, all flags False
    projects_root = _setup_project(
        tmp_path,
        intake=intake,
        field_status={},  # no skeleton yet
        current_phase="tier",
    )

    # LLM fires all 7 binary-flag tool calls in a single turn.
    tool_calls = [
        ToolCall("update_intake", {"field": flag, "value": True})
        for flag in sorted(BINARY_INTAKE_FIELDS)
    ]
    fake, _ = _fake_llm(text="Got it, moving on!", tool_calls=tool_calls)

    run_turn(
        user_message="yes to everything",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    # After the turn: intake_state.completed must be True.
    import json
    intake_data = json.loads(
        (projects_root / "demo" / "_meta" / "intake_state.json").read_text()
    )
    assert intake_data["completed"] is True

    # field_status must be populated (build_skeleton ran).
    field_status_data = json.loads(
        (projects_root / "demo" / "_meta" / "field_status.json").read_text()
    )
    assert len(field_status_data) > 0, "build_skeleton should have populated field_status"

    # Current phase must have advanced to 'language'.
    new_phase = load_current_phase(projects_root / "demo")
    assert new_phase == "language"


def test_run_turn_user_entry_written_before_llm_call(tmp_path: Path) -> None:
    """The user history entry is written before the LLM call, so it is persisted
    even if the LLM raises."""
    projects_root = _setup_project(tmp_path)

    h_path = projects_root / "demo" / "_meta" / "history.jsonl"

    def _boom(system_prompt: str, user_message: str) -> LLMResponse:  # type: ignore[return]
        # Verify that the user entry is already present in history.jsonl when the
        # LLM call executes (i.e., it was written before this function was called).
        assert h_path.exists(), "history.jsonl must exist before LLM call"
        lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
        assert lines and lines[0]["role"] == "user"
        raise RuntimeError("simulated LLM failure")

    with pytest.raises(RuntimeError, match="simulated LLM failure"):
        run_turn(
            "test message",
            "demo",
            projects_root=projects_root,
            llm_call=_boom,
        )

    # After the exception, only the user entry should exist (no assistant entry).
    lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["role"] == "user"
    assert lines[0]["content"] == "test message"
