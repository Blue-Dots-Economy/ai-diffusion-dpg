"""Backtracking smoke test — wizard returns to an earlier phase on intake change.

Belongs to the dev-kit deterministic wizard (Task 12.4). Verifies that when
the user changes an intake field mid-conversation, the end-of-turn router
cascades through ``FIELD_RULES`` to mark dependent chat fields as
``needs_re_asking`` and rewinds the wizard to the earliest invalidated
phase.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
§7 (router) for the backtracking contract under test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
from dev_kit.agent.field_status import (
    load_field_status,
    save_field_status,
)
from dev_kit.agent.intake_state import (
    IntakeState,
    load_intake_state,
    save_intake_state,
)
from dev_kit.agent.phase_driver import (
    LLMResponse,
    ToolCall,
    collect_pending_fields,
    load_accumulator,
    load_current_phase,
    run_turn,
    save_accumulator,
    save_current_phase,
)
from dev_kit.agent.router import PHASE_ORDER
from dev_kit.agent.skeleton import build_skeleton


# Selected schema-aware test values reused across the backtracking script.
# Mirrors the registry in test_wizard_flow.py — kept in sync manually to keep
# this file self-contained. If you add a stuck-field workaround there, drop
# the equivalent entry here too.
_TEST_VALUES: dict[str, Any] = {
    "agent_core.preprocessing.nlu_processor.intents": ["greeting", "question"],
    "agent_core.preprocessing.nlu_processor.entities": ["topic"],
    "agent_core.preprocessing.nlu_processor.domain_instruction": (
        "Classify user intents."
    ),
    "agent_core.preprocessing.nlu_processor.provider": "anthropic",
    "agent_core.preprocessing.nlu_processor.model": "claude-sonnet-4-6",
    "agent_core.preprocessing.language_normalisation.provider": "anthropic",
    "agent_core.preprocessing.language_normalisation.model": "claude-sonnet-4-6",
    "agent_core.agent.primary_model": "claude-sonnet-4-6",
    "agent_core.agent.fallback_model": "claude-haiku-4-5-20251001",
    "agent_core.agent.provider": "anthropic",
    "agent_core.conversation.blocked_message": "Cannot help.",
    "agent_core.conversation.escalation_message": "Connecting to a human.",
    "agent_core.conversation.output_blocked_message": "Cannot share that.",
    "agent_core.conversation.unknown_intent_message": "Did not understand.",
    "agent_core.conversation.unsupported_language_message": "Unsupported language.",
    "agent_core.channels.web.system_prompt_suffix": "Web suffix.",
    # knowledge phase
    "agent_core.connectors.internal[name=knowledge_retrieval].description": (
        "Search the knowledge base."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.call_when": (
        "When the user asks a factual question."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling": [
        "NLU intent classified",
    ],
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute": (
        "Do not invent answers."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty": (
        "No information found."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure": (
        "Apologise for the error."
    ),
    "agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line": (
        "Let me check the knowledge base."
    ),
    "knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters": {
        "general_query": ["general"],
    },
}


def _test_value_for(path: str, default: Any) -> Any:
    """Return a schema-acceptable value for ``path``.

    Args:
        path: Full dotted accumulator path including block prefix.
        default: Fallback value from the FieldRule.

    Returns:
        A Python value the validation layer should accept.
    """
    if path in _TEST_VALUES:
        return _TEST_VALUES[path]
    if default is not None:
        return default
    return "test_value"


def _setup_project_at_workflow(tmp_path: Path) -> tuple[Path, Path, IntakeState]:
    """Create a project with has_kb=False sitting at the workflow phase.

    Simulates a user who has progressed through several phases with their
    initial intake. All previously-answerable chat fields are stamped
    ``answered`` so only the has_kb cascade should generate
    ``needs_re_asking`` entries.

    Args:
        tmp_path: Pytest tmp_path fixture root.

    Returns:
        ``(projects_root, slug_root, intake_state)`` for the test.
    """
    projects_root = tmp_path / "projects"
    slug = "demo"
    slug_root = projects_root / slug
    (slug_root / "_meta").mkdir(parents=True)

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
        domain_description="Test domain",
        project_name="TestBot",
    )
    save_intake_state(slug_root / "_meta" / "intake_state.json", intake)

    accumulator, field_status = build_skeleton(intake)
    # Stamp every pending chat field as answered to mimic completed earlier
    # phases. We do not actually write valid values into the accumulator —
    # the router only consults field_status to decide phase advancement.
    for path, status in list(field_status.items()):
        if status == "pending":
            field_status[path] = "answered"

    save_accumulator(slug_root, accumulator)
    save_field_status(slug_root / "_meta" / "field_status.json", field_status)
    save_current_phase(slug_root, "workflow")

    return projects_root, slug_root, intake


def _scripted_llm(scripted: list[list[ToolCall]]):
    """Return an ``llm_call`` closure that emits the next scripted tool batch per call.

    After the script is exhausted, subsequent calls return no tool calls.

    Args:
        scripted: Ordered list of tool-call batches, one per turn.

    Returns:
        A callable matching ``phase_driver.run_turn``'s ``llm_call`` contract.
    """
    counter = {"i": 0}

    def _call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        idx = counter["i"]
        counter["i"] += 1
        if idx >= len(scripted):
            return LLMResponse(text="done", tool_calls=[])
        return LLMResponse(text=f"turn{idx}", tool_calls=list(scripted[idx]))

    return _call


def _auto_answer_llm(slug_root: Path, intake: IntakeState):
    """Return an ``llm_call`` closure that auto-answers pending fields per phase.

    Mirrors the helper in ``test_wizard_flow`` — kept self-contained here so
    the backtracking test does not import test utilities from a sibling
    test module.

    Args:
        slug_root: Project directory.
        intake: Current IntakeState (used for applies_if evaluation).

    Returns:
        A callable matching ``phase_driver.run_turn``'s ``llm_call`` contract.
    """

    def _call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        phase = load_current_phase(slug_root)
        field_status = load_field_status(slug_root / "_meta" / "field_status.json")
        pending = collect_pending_fields(phase, intake, field_status)
        calls: list[ToolCall] = []
        for path, rule in pending:
            value = _test_value_for(path, rule.default)
            calls.append(ToolCall("update_config", {"path": path, "value": value}))
        return LLMResponse(
            text=f"phase={phase} pending={len(pending)}",
            tool_calls=calls,
        )

    return _call


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_backtracking_flips_has_kb_returns_to_language(tmp_path: Path) -> None:
    """Flipping has_kb mid-conversation rewinds the wizard to an earlier phase.

    Sequence:

    1. Project starts with ``has_kb=False`` parked at the ``workflow`` phase
       with all prior chat fields stamped ``answered``.
    2. On turn 0 the scripted LLM emits ``update_intake(has_kb=True)``.
    3. The end-of-turn router observes ``needs_re_asking`` entries in
       earlier phases (language + knowledge + workflow + reach) and selects
       the earliest one. Per the FIELD_RULES catalogue,
       ``agent_core.preprocessing.nlu_processor.intents`` (language phase)
       lists ``has_kb`` in ``invalidated_by`` — language is earlier than
       knowledge in ``PHASE_ORDER`` — so the wizard backtracks there.
    """
    projects_root, slug_root, _ = _setup_project_at_workflow(tmp_path)

    llm = _scripted_llm(
        [
            [ToolCall("update_intake", {"field": "has_kb", "value": True})],
        ]
    )

    assert load_current_phase(slug_root) == "workflow"

    run_turn(
        user_message="actually I do need a knowledge base",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=llm,
    )

    # has_kb has flipped on disk.
    saved_intake = load_intake_state(slug_root / "_meta" / "intake_state.json")
    assert saved_intake.has_kb is True

    # Router backtracked to the earliest invalidated phase.
    new_phase = load_current_phase(slug_root)
    assert new_phase == "language", (
        f"expected backtrack to 'language', got {new_phase!r}"
    )
    assert PHASE_ORDER.index(new_phase) < PHASE_ORDER.index("workflow")

    # The NLU intents field (language phase) is now marked needs_re_asking.
    field_status = load_field_status(slug_root / "_meta" / "field_status.json")
    assert (
        field_status.get("agent_core.preprocessing.nlu_processor.intents")
        == "needs_re_asking"
    )

    # Knowledge-phase chat fields that were `not_applicable` (has_kb=False)
    # have transitioned to `applicable`. Per the cascade contract they
    # now sit at `pending` (no default) or `answered` (default seeded) —
    # NOT `needs_re_asking`, which only makes sense for fields the user
    # had already answered. The router will still visit the knowledge
    # phase after the language re-ask completes, because the phase has
    # pending chat fields.
    knowledge_chat_paths = {
        p
        for p, rule in AGGREGATED_FIELD_RULES.items()
        if rule.category == "chat" and rule.phase == "knowledge"
    }
    activated_kb_paths = {
        p for p in knowledge_chat_paths
        if field_status.get(p) in ("pending", "answered")
    }
    assert activated_kb_paths, (
        "expected knowledge-phase chat fields to have transitioned out of "
        "not_applicable now that has_kb=True"
    )


def test_backtracking_then_advance_progresses_through_invalidated_phases(
    tmp_path: Path,
) -> None:
    """After backtracking, driving the wizard advances through invalidated phases.

    Sequence:

    1. Same starting state as the previous test (workflow phase, has_kb=False).
    2. Turn 0: scripted LLM flips has_kb to True (backtracks to language).
    3. Subsequent turns auto-answer every pending field for the current
       phase. The wizard should advance language -> knowledge -> ... until
       it returns to a phase at or beyond workflow (or stalls on a field the
       auto-answer registry does not cover).

    The contract under test is that the wizard does not regress to the
    invalidated state after backtracking — it must move forward again
    through every phase it rewound.
    """
    projects_root, slug_root, intake = _setup_project_at_workflow(tmp_path)

    # Turn 0: flip has_kb.
    initial_llm = _scripted_llm(
        [
            [ToolCall("update_intake", {"field": "has_kb", "value": True})],
        ]
    )
    run_turn(
        user_message="I need a knowledge base after all",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=initial_llm,
    )
    assert load_current_phase(slug_root) == "language"

    # Update the in-memory intake snapshot used by the auto-answer LLM so
    # collect_pending_fields evaluates applies_if against the new value.
    intake = load_intake_state(slug_root / "_meta" / "intake_state.json")
    auto_llm = _auto_answer_llm(slug_root, intake)

    # Drive forward — bounded turns and stall detection so a misconfigured
    # registry can't infinite-loop the test.
    last_phase = "language"
    stall = 0
    final_phase = last_phase
    for i in range(20):
        before = load_current_phase(slug_root)
        run_turn(
            user_message=f"continue{i}",
            project_slug="demo",
            projects_root=projects_root,
            llm_call=auto_llm,
        )
        after = load_current_phase(slug_root)
        final_phase = after
        if after == "review":
            break
        if after == before:
            stall += 1
            if stall >= 3:
                break
        else:
            stall = 0
        last_phase = after

    # The wizard must have advanced past language again — the whole point
    # of backtracking is that we re-ask and then move forward, not that we
    # park there forever.
    assert PHASE_ORDER.index(final_phase) > PHASE_ORDER.index("language"), (
        f"expected wizard to advance past language after backtracking; "
        f"final_phase={final_phase!r}"
    )

    # And the previously-answered earlier phases that were NOT invalidated
    # remain answered (e.g. trust_layer.trust.input_rules.blocked_input_message).
    field_status = load_field_status(
        slug_root / "_meta" / "field_status.json"
    )
    trust_msg = "trust_layer.trust.input_rules.blocked_input_message"
    if trust_msg in field_status:
        assert field_status[trust_msg] != "pending", (
            f"trust field {trust_msg} should not have been reverted to pending "
            f"by the has_kb cascade; got {field_status[trust_msg]!r}"
        )

    # Accumulator content from blocks unrelated to the has_kb cascade
    # should still be present (e.g. trust_layer was populated by skeleton
    # defaults and never invalidated).
    accumulator = load_accumulator(slug_root)
    assert accumulator.get("trust_layer"), (
        "trust_layer accumulator should retain skeleton-seeded defaults "
        "across the has_kb cascade"
    )


def test_backtracking_field_status_marks_invalidated_only(tmp_path: Path) -> None:
    """Only fields whose ``invalidated_by`` includes the flipped field are re-asked.

    Verifies the cascade in ``on_intake_update`` is surgical: it does not
    over-invalidate fields unrelated to ``has_kb``.
    """
    projects_root, slug_root, _ = _setup_project_at_workflow(tmp_path)

    llm = _scripted_llm(
        [
            [ToolCall("update_intake", {"field": "has_kb", "value": True})],
        ]
    )
    run_turn(
        user_message="enable kb",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=llm,
    )

    field_status = load_field_status(
        slug_root / "_meta" / "field_status.json"
    )

    # Build the expected set of re-askable paths: every chat field that
    # (a) applies under the new intake state and
    # (b) lists has_kb in its invalidated_by.
    expected_re_ask = {
        path
        for path, rule in AGGREGATED_FIELD_RULES.items()
        if rule.category == "chat" and "has_kb" in rule.invalidated_by
    }

    # Every needs_re_asking field must be in expected_re_ask. The reverse
    # need not hold — fields gated out by applies_if (e.g. voice channel
    # paths when selected_channels=["web"]) end up as not_applicable instead.
    actual_re_ask = {
        p for p, s in field_status.items() if s == "needs_re_asking"
    }

    unexpected = actual_re_ask - expected_re_ask
    assert not unexpected, (
        f"unexpected needs_re_asking entries (not gated by has_kb): {unexpected}"
    )
    assert actual_re_ask, "at least one field should have been marked needs_re_asking"
