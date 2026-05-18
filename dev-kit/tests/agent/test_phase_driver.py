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
    _strip_banned_sentences,
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
    """Return a callable that records its args and returns a canned LLMResponse.

    Mirrors the production ``llm_call`` signature: ``(system_prompt, messages)``.
    ``messages`` is the Anthropic-format list the driver assembles per turn
    (sliding-window prior history + the current user message; later loop
    iterations also include assistant ``tool_use`` and user ``tool_result``
    rounds). For backwards compatibility with existing assertions, the helper
    also exposes ``captured["user_message"]`` set to the latest user-role
    text content seen — that is, the verbatim message the wizard appended
    just before calling the LLM.
    """
    captured: dict[str, Any] = {
        "system_prompt": None,
        "messages": None,
        "user_message": None,
        "calls": 0,
    }

    def _call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        # Extract the latest user-role text turn for the user_message shortcut.
        for entry in reversed(messages):
            if entry.get("role") == "user" and isinstance(entry.get("content"), str):
                captured["user_message"] = entry["content"]
                break
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

    user_state phase is gated by is_companion_style=False → no chat fields
    apply, so the phase is vacuously complete. With inline phase
    continuation (`_MAX_PHASE_TRANSITIONS_PER_TURN`), the same turn falls
    through into the next relevant phase (trust) and produces its first
    reply too. The combined reply is what the user sees in one message.
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

    # Both phases produced the canned "ok"; the inline continuation
    # drops the just-completed phase's text and surfaces only the new
    # phase's opening, so the user gets a clean single message.
    assert result == "ok"
    assert captured["system_prompt"]  # non-empty prompt returned
    assert captured["calls"] == 2, (
        "expected two LLM calls: one for the empty user_state phase and "
        "one for the inline-continuation trust phase"
    )


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
    """No pending fields → empty string."""
    assert render_pydantic_classes([]) == ""


def test_render_pydantic_classes_returns_real_class_source() -> None:
    """A pending field's `pydantic_class` must resolve to the actual class
    in `dev_kit.schemas.domain.<block>` and be rendered as real source.

    GoGuide regression: this function used to be a stub that returned a
    comment listing pending paths. The LLM never saw the schema and
    hallucinated field names like `consent_declined_message` (real name
    is `consent_decline_ack`) and `blocked_output_message` (real name is
    `output_blocked_message`). Every `update_config` write to those
    paths was rejected by the dev-kit mirror with `extra_forbidden`.
    """
    rule = AGGREGATED_FIELD_RULES["agent_core.conversation.profile_complete_message"]
    assert rule.pydantic_class == "ConversationSection"

    out = render_pydantic_classes(
        [("agent_core.conversation.profile_complete_message", rule)]
    )

    # Real class header — proves we resolved + dumped actual source.
    assert "class ConversationSection" in out
    # Real field names — proves the LLM can see them and stop inventing
    # `consent_declined_message` / `blocked_output_message` style names.
    assert "consent_message" in out
    assert "consent_decline_ack" in out
    assert "output_blocked_message" in out
    assert "blocked_message" in out
    assert "escalation_message" in out
    # The legacy stub marker must be gone — guards against future
    # accidental re-stubbing.
    assert "injected by Phase 9 work" not in out


def test_render_pydantic_classes_includes_referenced_submodels() -> None:
    """Referenced submodels (Optional[X], list[X]) must be rendered too.

    Without the closure walk, the LLM would see `user_state_model:
    Optional[UserStateModel] = None` but no `UserStateModel` definition,
    leading to invented sub-field names again.
    """
    rule = AGGREGATED_FIELD_RULES["agent_core.conversation.profile_complete_message"]
    out = render_pydantic_classes(
        [("agent_core.conversation.profile_complete_message", rule)]
    )

    # The closure must include both the parent class AND its referenced
    # submodels so every valid field name is in the rendered output.
    assert "class ConversationSection" in out
    assert "class UserStateModel" in out
    assert "class UserStateDefinition" in out
    # Real sub-field names from UserStateModel.
    assert "enabled" in out
    assert "default_state" in out
    assert "states" in out


def test_render_pydantic_classes_deduplicates_shared_classes() -> None:
    """When multiple pending fields share a pydantic_class, render once."""
    rule_a = AGGREGATED_FIELD_RULES["agent_core.conversation.profile_complete_message"]
    rule_b = AGGREGATED_FIELD_RULES["agent_core.conversation.blocked_message"]

    out = render_pydantic_classes([
        ("agent_core.conversation.profile_complete_message", rule_a),
        ("agent_core.conversation.blocked_message", rule_b),
    ])

    # Both fields point at ConversationSection; we must NOT render the
    # class twice.
    assert out.count("class ConversationSection") == 1


def test_render_pydantic_classes_handles_unknown_block_gracefully(caplog) -> None:
    """An unknown block name on a path must not crash the prompt build."""
    rule = AGGREGATED_FIELD_RULES["agent_core.conversation.profile_complete_message"]

    with caplog.at_level(logging.WARNING, logger="dev_kit.agent.phase_driver"):
        out = render_pydantic_classes(
            [("not_a_block.conversation.profile_complete_message", rule)]
        )

    # Skipped silently in the prompt; surfaced in the log.
    assert out == ""
    assert any(
        getattr(rec, "operation", None)
        == "phase_driver.render_pydantic_classes"
        and getattr(rec, "block", None) == "not_a_block"
        for rec in caplog.records
    )


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
        llm_call=lambda sp, msgs: LLMResponse(text="ack", tool_calls=[], model="x"),
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
        llm_call=lambda sp, msgs: LLMResponse(text="noted", tool_calls=[], model="x"),
    )

    h_path = projects_root / "demo" / "_meta" / "history.jsonl"
    lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
    assert all(e["phase"] == "trust" for e in lines), (
        "Both user and assistant entries should carry the active phase 'trust'"
    )


def test_run_turn_emits_per_turn_summary_log(tmp_path: Path, caplog) -> None:
    """The end-of-turn success log must include a per-turn telemetry summary:
    tool_call_total, tool_reject_total, llm_calls, and per-tool counts.

    Without this, every chat turn produces 40+ individual log entries with
    no roll-up — making "what did this turn actually do?" hard to answer
    in production triage.
    """
    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        current_phase="trust",
    )

    fake, _ = _fake_llm(
        text="ok",
        tool_calls=[
            ToolCall(
                "update_config",
                {
                    "path": "agent_core.conversation.blocked_message",
                    "value": "Sorry.",
                },
            ),
            # An unknown tool — gets rejected; tool_reject_total should reflect it.
            ToolCall("not_a_real_tool", {}),
        ],
    )

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.phase_driver"):
        run_turn(
            user_message="set blocked message",
            project_slug="demo",
            projects_root=projects_root,
            llm_call=fake,
        )

    summary_records = [
        rec for rec in caplog.records
        if getattr(rec, "operation", None) == "phase_driver.run_turn"
        and getattr(rec, "status", None) == "success"
    ]
    assert summary_records, "no end-of-turn summary log emitted"
    rec = summary_records[-1]
    # Per-turn aggregates.
    assert isinstance(getattr(rec, "llm_calls", None), int) and rec.llm_calls >= 1
    assert getattr(rec, "tool_call_total", None) == 2
    # The unknown tool is dispatched and rejected, so the reject total is ≥ 1.
    assert getattr(rec, "tool_reject_total", 0) >= 1
    # Per-tool counts surface the tools the LLM actually tried.
    tool_calls = getattr(rec, "tool_calls", {})
    assert isinstance(tool_calls, dict)
    assert tool_calls.get("update_config") == 1
    assert tool_calls.get("not_a_real_tool") == 1


def test_failed_update_config_leaves_accumulator_and_yaml_unchanged(
    tmp_path: Path,
) -> None:
    """Validation-before-write contract: when `update_config` fails the
    mirror schema, the live accumulator AND the on-disk YAML must be
    identical to their pre-write state.

    GoGuide regression: the user asked for the OLD code's candidate-copy
    pattern so that an invalid value never reaches the rendered YAML.
    This test runs two consecutive `update_config` calls — the first
    valid, the second deliberately invalid — and asserts:

      1. The valid first write IS reflected in the on-disk YAML.
      2. The invalid second write is rejected (ok=False).
      3. The valid first write is NOT corrupted by the rejected attempt
         (a sloppy mutate-then-revert pattern could leave artefacts
         behind if validation raised mid-way).
    """
    # Start with a populated agent_core.blocked_message; first call updates
    # it, second call sends an empty string which violates min_length=1.
    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        accumulator={
            "agent_core": {},
            "trust_layer": {},
            "knowledge_engine": {},
            "memory_layer": {},
            "action_gateway": {},
            "reach_layer": {},
            "observability_layer": {},
        },
        field_status={"agent_core.conversation.blocked_message": "pending"},
        current_phase="language",
    )

    call_count = {"i": 0}

    def _two_call_llm(system_prompt: str, messages: list[dict]) -> LLMResponse:
        call_count["i"] += 1
        if call_count["i"] == 1:
            # First turn: write a valid value.
            return LLMResponse(
                text="ok",
                tool_calls=[
                    ToolCall(
                        "update_config",
                        {
                            "path": "agent_core.conversation.blocked_message",
                            "value": "Valid blocked message.",
                        },
                    )
                ],
            )
        # Second turn: try an invalid empty string. Mirror schema's
        # `min_length=1` constraint must reject it without disturbing
        # the previously-stored value.
        return LLMResponse(
            text="trying again",
            tool_calls=[
                ToolCall(
                    "update_config",
                    {
                        "path": "agent_core.conversation.blocked_message",
                        "value": "",
                    },
                )
            ],
        )

    # First turn — the valid write lands on disk.
    run_turn(
        user_message="set the blocked message",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=_two_call_llm,
    )
    yaml_path = projects_root / "demo" / "agent_core.yaml"
    yaml_after_valid_write = yaml_path.read_text()
    assert "Valid blocked message." in yaml_after_valid_write

    # Snapshot the accumulator-on-disk before the invalid attempt.
    acc_path = projects_root / "demo" / "_meta" / "accumulator.json"
    accumulator_before_failed_write = json.loads(acc_path.read_text())

    # Second turn — invalid empty string. The mirror validator should
    # reject it; on-disk YAML and accumulator must NOT change.
    run_turn(
        user_message="actually empty please",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=_two_call_llm,
    )

    # The previously-rendered value must still be on disk — no corruption,
    # no clobber, no half-state.
    yaml_after_failed_write = yaml_path.read_text()
    assert "Valid blocked message." in yaml_after_failed_write, (
        "The previously-valid blocked_message disappeared from the rendered "
        "YAML after a failed update_config — validation-before-write contract "
        "is broken."
    )
    # And the accumulator on disk must be exactly the same as before the
    # failed attempt for the blocked_message slot.
    accumulator_after_failed_write = json.loads(acc_path.read_text())
    assert (
        accumulator_after_failed_write["agent_core"]["conversation"]["blocked_message"]
        == accumulator_before_failed_write["agent_core"]["conversation"]["blocked_message"]
    ), "Failed update_config leaked a write into the accumulator"


def test_run_turn_renders_yaml_files_after_each_turn(tmp_path: Path) -> None:
    """Every chat turn must re-render the per-block YAML files from the
    updated accumulator. Otherwise the user opens `<slug>/agent_core.yaml`
    and sees the project-creation placeholder forever, even though the
    accumulator.json reflects all their answers.

    GoGuide regression: 13 successful update_config writes mutated
    accumulator.json but the per-block YAMLs stayed as `# trust_layer —
    no config generated yet` placeholders because render_all was only
    called at project creation.
    """
    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        current_phase="trust",
    )

    fake, _ = _fake_llm(
        text="ok",
        tool_calls=[
            ToolCall(
                "update_config",
                {
                    "path": "agent_core.conversation.blocked_message",
                    "value": "Sorry, I cannot help with that.",
                },
            )
        ],
    )

    run_turn(
        user_message="set the blocked message",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    # YAML must exist and contain the user's answer — not the placeholder
    # stub that render_all writes for empty blocks at project creation.
    yaml_path = projects_root / "demo" / "agent_core.yaml"
    assert yaml_path.exists()
    text = yaml_path.read_text()
    assert "Sorry, I cannot help with that." in text, (
        f"Expected the just-written blocked_message in the rendered YAML; "
        f"got:\n{text}"
    )
    assert "no config generated yet" not in text, (
        "agent_core.yaml is still the empty placeholder — render_all did "
        "not re-run at end of turn"
    )


def test_run_turn_deep_merges_skeleton_defaults_into_cascade_populated_accumulator(
    tmp_path: Path,
) -> None:
    """Skeleton defaults must deep-merge into cascade-populated sections.

    GoGuide regression: the router cascade writes
    `agent_core.agent.ask_for_consent=true` during the tier turn (from
    the `needs_consent=true` predetermined rule). The skeleton then
    renders `agent_core.agent.provider="anthropic"`. The old shallow
    setdefault saw `agent_core['agent']` already existed (because of
    the cascade's `ask_for_consent` write) and skipped — `provider`
    was silently lost, leaving the language phase with no provider
    default for the LLM to act on. The fix walks both dicts recursively
    so cascade keys win at every depth and skeleton fills the gaps.
    """
    from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS

    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        current_phase="tier",
    )

    tool_calls = [
        ToolCall("update_intake", {"field": flag, "value": True})
        for flag in sorted(BINARY_INTAKE_FIELDS)
    ]
    fake, _ = _fake_llm(text="ok", tool_calls=tool_calls)

    run_turn(
        user_message="yes",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    acc = json.loads(
        (projects_root / "demo" / "_meta" / "accumulator.json").read_text()
    )
    agent = acc["agent_core"].get("agent", {})

    # Cascade key survives (predetermined `ask_for_consent` from
    # `needs_consent=true`).
    assert agent.get("ask_for_consent") is True
    # AND skeleton default also lands in the same `agent` dict.
    assert agent.get("provider") == "anthropic"


def test_run_turn_runs_skeleton_even_when_cascade_populated_field_status(
    tmp_path: Path,
) -> None:
    """The skeleton must run at tier completion EVEN IF the router cascade
    already populated some `field_status` entries during tier (it does —
    every binary-flag flip cascades to `needs_re_asking` for chat fields
    listing that flag in `invalidated_by`).

    GoGuide regression: phase_driver gated skeleton on
    `not field_status`. By the time tier completed, the cascade had
    written 30+ entries, so skeleton skipped. Defaulted chat fields
    (`language_normalisation.enabled=True`, the `auto_answer` providers,
    etc.) stayed `pending` and the language phase deadlocked because the
    router could not see them as answered.

    The fix uses the presence of `language_normalisation.enabled`
    (skeleton-only, no cascade triggers) as a "skeleton has run" marker,
    and merges skeleton entries with `setdefault` so cascade-set
    `needs_re_asking` values win over the skeleton baseline.
    """
    # Seed field_status with one cascade-style entry so the OLD gate
    # would suppress the skeleton run.
    pre_populated_field_status = {
        "agent_core.conversation.profile_complete_message": "needs_re_asking",
    }

    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        field_status=pre_populated_field_status,
        current_phase="tier",
    )

    # Single LLM turn that completes tier by writing all 7 binary flags.
    from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS
    tool_calls = [
        ToolCall("update_intake", {"field": flag, "value": True})
        for flag in sorted(BINARY_INTAKE_FIELDS)
    ]
    fake, _ = _fake_llm(text="ok", tool_calls=tool_calls)

    run_turn(
        user_message="yes",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    # Skeleton MUST have run despite the pre-existing field_status entry.
    field_status_after = json.loads(
        (projects_root / "demo" / "_meta" / "field_status.json").read_text()
    )
    assert (
        "agent_core.preprocessing.language_normalisation.enabled"
        in field_status_after
    ), "skeleton did not run — language_normalisation.enabled missing from field_status"
    # And the defaulted chat field is marked answered, not pending.
    assert (
        field_status_after[
            "agent_core.preprocessing.language_normalisation.enabled"
        ]
        == "answered"
    )
    # The cascade-set `needs_re_asking` entry must survive (setdefault
    # semantics — skeleton does not clobber it).
    assert (
        field_status_after["agent_core.conversation.profile_complete_message"]
        == "needs_re_asking"
    )


def test_run_turn_inline_continues_after_tier_completes(tmp_path: Path) -> None:
    """The GoGuide regression: when tier intake completes, the same turn must
    automatically chain into language and produce its first question(s),
    rather than ending with "intake complete" and forcing the user to send
    a no-op message to trigger the next phase.

    Setup: project starts in tier with no flags seen. The LLM returns 7
    update_intake calls plus a closing tier-phase text in one shot. Under
    the inline-continuation contract, run_turn should:

      1. Dispatch the 7 update_intakes → state.completed flips True.
      2. Run build_skeleton because field_status was empty.
      3. Advance the phase from tier to language.
      4. Issue a SECOND LLM call with the language system prompt.
      5. Return the concatenation of both phases' assistant texts.
    """
    from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS

    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        field_status={},
        current_phase="tier",
    )

    # Per-call canned response so the two phases produce distinguishable text.
    # Call 1 (tier): tool_use response with 7 update_intakes + tier closing.
    # Call 2 (language): plain text with the first language question.
    tier_tool_calls = [
        ToolCall("update_intake", {"field": flag, "value": True})
        for flag in sorted(BINARY_INTAKE_FIELDS)
    ]
    call_count = {"i": 0}

    def _two_phase_llm(system_prompt: str, messages: list[dict]) -> LLMResponse:
        call_count["i"] += 1
        if call_count["i"] == 1:
            # Tier phase — emit all 7 update_intakes and a tier closing message.
            return LLMResponse(
                text="Intake captured.",
                tool_calls=tier_tool_calls,
            )
        # Language continuation — produce the new phase's first question.
        return LLMResponse(
            text="1. Which provider would you like to use (anthropic or openai)?",
            tool_calls=[],
        )

    result = run_turn(
        user_message="yes to everything",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=_two_phase_llm,
    )

    # Two LLM calls — one per phase.
    assert call_count["i"] == 2

    # Phase advanced and persisted.
    assert load_current_phase(projects_root / "demo") == "language"

    # The user-facing text concatenates BOTH phases' replies.
    # With inline continuation, the just-completed phase's closing text
    # is dropped to avoid a wall-of-text reply. Only the new phase's
    # opening question survives — the user sees just the language-phase
    # ask, not the tier-closing acknowledgment.
    assert "Intake captured." not in result
    assert "Which provider would you like to use" in result
    assert result == "1. Which provider would you like to use (anthropic or openai)?"

    # History stores ONE combined assistant entry (so user/assistant
    # alternation is preserved for the next turn).
    h_path = projects_root / "demo" / "_meta" / "history.jsonl"
    lines = [json.loads(l) for l in h_path.read_text().splitlines() if l.strip()]
    assert [(e["role"], e["phase"]) for e in lines] == [
        ("user", "tier"),
        ("assistant", "language"),  # combined entry tagged with the FINAL phase
    ]


def test_run_turn_caps_inline_continuation_at_one_transition(tmp_path: Path) -> None:
    """_MAX_PHASE_TRANSITIONS_PER_TURN bounds the cascade.

    If two phases would advance in one turn (e.g. tier→language and
    language→knowledge both within reach), the cap stops at one continuation.
    The cap design says: when the cap is hit, STAY at the phase that just
    had an LLM call — do not commit a transition the user has not seen any
    text from.
    """
    from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS

    projects_root = _setup_project(
        tmp_path,
        intake=_make_intake(),
        field_status={},
        current_phase="tier",
    )

    # Each LLM call returns a fully-answering tool batch so the phase
    # advances immediately. Call 1: complete tier. Call 2: complete every
    # language chat field. Call 3 should NEVER happen.
    answered_paths: list[str] = []
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category == "chat" and rule.phase == "language":
            answered_paths.append(path)
    call_count = {"i": 0}

    def _cascading_llm(system_prompt: str, messages: list[dict]) -> LLMResponse:
        call_count["i"] += 1
        if call_count["i"] == 1:
            return LLMResponse(
                text="tier_text",
                tool_calls=[
                    ToolCall("update_intake", {"field": flag, "value": True})
                    for flag in sorted(BINARY_INTAKE_FIELDS)
                ],
            )
        return LLMResponse(
            text="language_text",
            tool_calls=[
                ToolCall("update_config", {"path": path, "value": "placeholder"})
                for path in answered_paths
            ],
        )

    run_turn(
        user_message="kick off",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=_cascading_llm,
    )

    # Exactly two LLM calls — one for tier, one for language. The would-be
    # third (knowledge) is suppressed by the cap.
    assert call_count["i"] == 2, (
        f"cap should bound to 2 LLM calls per turn (1 + 1 continuation); "
        f"got {call_count['i']}"
    )
    # We stay at language even if its update_config calls "completed" the
    # phase from the router's perspective — the next user message will
    # drive the language→next transition.
    assert load_current_phase(projects_root / "demo") == "language"


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

    def _boom(system_prompt: str, messages: list[dict]) -> LLMResponse:  # type: ignore[return]
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


class TestStripBannedSentences:
    """Verify the post-output lint strips known internal-state-leak phrases."""

    def test_empty_passthrough(self) -> None:
        assert _strip_banned_sentences("") == ""

    def test_clean_text_unchanged(self) -> None:
        text = "Here is the suggested setup. Does this look good?"
        assert _strip_banned_sentences(text) == text

    def test_strips_let_me_try_again(self) -> None:
        text = "Let me try again with the right path. The setup is ready."
        out = _strip_banned_sentences(text)
        assert "try again" not in out.lower()
        assert "setup is ready" in out.lower()

    def test_strips_path_mismatch(self) -> None:
        text = "There was a path mismatch in the previous call. Now configured."
        out = _strip_banned_sentences(text)
        assert "path mismatch" not in out.lower()
        assert "now configured" in out.lower()

    def test_strips_version_of_the_schema(self) -> None:
        text = "I see — that's a newer version of the schema. Here's the config."
        out = _strip_banned_sentences(text)
        assert "version of the schema" not in out.lower()
        assert "here's the config" in out.lower()

    def test_strips_apology(self) -> None:
        text = "Apologies for that. Here is the corrected proposal."
        out = _strip_banned_sentences(text)
        assert "apolog" not in out.lower()
        assert "corrected proposal" in out.lower()

    def test_strips_issue_found(self) -> None:
        text = "Issue found: wrong field. Here is the right one."
        out = _strip_banned_sentences(text)
        assert "issue found" not in out.lower()
        assert "right one" in out.lower()

    def test_collapses_blank_lines_left_behind(self) -> None:
        text = "Line one.\nLet me try again.\n\nLine three."
        out = _strip_banned_sentences(text)
        assert "\n\n\n" not in out
