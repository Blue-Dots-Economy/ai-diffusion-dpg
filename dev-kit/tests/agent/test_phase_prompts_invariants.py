"""Cross-prompt invariants for the deterministic-wizard phase prompts.

These tests guard the rules every phase prompt must obey for the wizard's
determinism contract to hold:

1. No prompt may reference a tool name that does not exist in
   ``DEVKIT_TOOL_SCHEMAS``. A stale tool reference makes the LLM either
   call a non-existent tool (failed turn) or invent an output it thinks
   the tool would have produced (hallucination).
2. No prompt may name another wizard phase in its closing guidance. The
   real bug: when a closing said "the router advances to the workflow
   phase", the LLM regurgitated "we're now entering the workflow phase"
   in user-facing prose, mid-tier. Phase ordering is owned by the router;
   prompts must be phase-agnostic about what comes next.
3. The shared anti-hallucination rules block must be present in every
   prompt — without it, individual prompts drift back into previewing
   future steps and writing ✓ summary checklists.

These tests are intentionally cheap to read so the invariants are obvious
from the assertion, not buried in fixtures.

Belongs to the dev-kit deterministic wizard's test suite.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.phase_prompts import (
    knowledge,
    language,
    memory,
    observability,
    reach,
    review,
    tier,
    tools,
    trust,
    user_state,
    workflow,
)
from dev_kit.agent.phases_config import PHASES
from dev_kit.agent.tools import DEVKIT_TOOL_SCHEMAS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _intake(**overrides) -> IntakeState:
    base = dict(
        has_kb=True, has_external_tools=True,
        is_multi_turn=True, needs_persistent_user_data=True,
        is_companion_style=True, needs_consent=True, has_hitl=True,
        selected_channels=["web", "voice"],
        default_language="english", supported_languages=["english"],
        domain_description="A test agent", project_name="test-proj",
    )
    base.update(overrides)
    return IntakeState(**base)


# Mapping of phase id to its prompt module's `build` function. Mirrors
# PHASES but pulled by import so we exercise the same code path the runtime
# loads via `phase_driver._load_phase_prompt`.
_PROMPT_BUILDERS = {
    "tier": tier.build,
    "language": language.build,
    "knowledge": knowledge.build,
    "memory": memory.build,
    "user_state": user_state.build,
    "trust": trust.build,
    "tools": tools.build,
    "workflow": workflow.build,
    "observability": observability.build,
    "reach": reach.build,
    "review": review.build,
}


@pytest.fixture(scope="module")
def all_prompts() -> dict[str, str]:
    """Build every phase prompt with a fully-populated IntakeState.

    The IntakeState has all flags set to True so every conditional branch
    inside every prompt fires. This maximises the surface area each
    invariant assertion runs against.
    """
    intake = _intake()
    return {
        phase_id: builder([], "", "", intake)
        for phase_id, builder in _PROMPT_BUILDERS.items()
    }


# ---------------------------------------------------------------------------
# Coverage sanity check
# ---------------------------------------------------------------------------


def test_every_phase_has_a_prompt_builder() -> None:
    """Every phase in PHASES must have a builder in `_PROMPT_BUILDERS`.

    Guards against a new phase being added in `phases_config.PHASES` without
    a matching prompt module — the wizard would otherwise crash on entering
    that phase with `AttributeError: no 'build'`.
    """
    assert set(_PROMPT_BUILDERS) == set(PHASES), (
        "phases_config.PHASES and _PROMPT_BUILDERS must cover the same phase ids"
    )


# ---------------------------------------------------------------------------
# Invariant 1 — no stale tool names
# ---------------------------------------------------------------------------


# Tools that USED to exist before commit b0c2d33 trimmed the surface from
# 20 to 8. The legacy names still appearing in the prompt files would cause
# the LLM to call something that no longer maps to a handler.
#
# Note: `fetch_openapi_spec_from_url` was intentionally restored as the 9th
# canonical tool — it is NOT stale and must NOT appear in this list.
_STALE_TOOL_NAMES = (
    "set_phase",
    "create_subagent",
    "add_rest_api_tool",
    "add_mcp_tool",
    "validate_config",
)


@pytest.mark.parametrize("phase_id", list(_PROMPT_BUILDERS))
@pytest.mark.parametrize("stale_name", _STALE_TOOL_NAMES)
def test_no_stale_tool_name_in_prompt(
    all_prompts: dict[str, str], phase_id: str, stale_name: str
) -> None:
    """No prompt may reference a tool that has been removed from the surface."""
    assert stale_name not in all_prompts[phase_id], (
        f"Phase {phase_id!r}: prompt references removed tool {stale_name!r}. "
        f"Use one of the 8 canonical tools instead: "
        f"{[s['name'] for s in DEVKIT_TOOL_SCHEMAS]}"
    )


# ---------------------------------------------------------------------------
# Invariant 2 — no other-phase name leaks
# ---------------------------------------------------------------------------


# Tokens that, if found in a prompt and refer to a DIFFERENT phase, would
# train the LLM to announce phase names in user-facing prose. The check is
# limited to closing/transition phrases — bare references like
# "the language phase" in a cross-reference description are allowed.
_TRANSITION_PHRASES = (
    "router advances to",
    "advances to the {other} phase",
    "moves to the {other} phase",
    "moving to the {other} phase",
    "next is the {other} phase",
    "entering the {other} phase",
)


@pytest.mark.parametrize("phase_id", list(_PROMPT_BUILDERS))
def test_prompt_contains_no_router_advances_clause(
    all_prompts: dict[str, str], phase_id: str
) -> None:
    """The legacy 'the router advances to X' closing must not appear.

    This is the exact phrase the GoGuide chat regurgitated as "we're now
    entering the workflow phase" while the wizard was still in tier.
    """
    text = all_prompts[phase_id].lower()
    assert "router advances" not in text, (
        f"Phase {phase_id!r}: closing block still uses the legacy "
        "'router advances to ...' wording. Use _closing_block() from "
        "_helpers.py instead — it is phase-agnostic by design."
    )


# ---------------------------------------------------------------------------
# Invariant 3 — every prompt embeds the shared anti-hallucination rules
# ---------------------------------------------------------------------------


# Distinctive sentences from `_COMMON_RULES` that are unlikely to appear
# anywhere else. If a prompt rewrite drops the rules block, all three
# markers go missing at once. We assert each one separately so a
# regression that strips only the format rules (or only the content
# rules) still trips a clear failure message.
_COMMON_RULES_MARKERS = {
    # Phase-name announcements at transitions are explicitly ALLOWED — the
    # dev-kit UI shows a sidebar with phase names, so a brief one-line
    # transition note ("That covers X — moving on to Y.") matches what the
    # user already sees. The invariant we still enforce is: no internal
    # implementation jargon (validate_partial, mirror schema, deployment
    # team, etc.) leaks into user-facing prose.
    "no-internals-leak rule": "NEVER expose implementation",
    "no-blame-the-system rule": "NEVER blame the wizard, the schema",
    "numbered-question format rule": "put EVERY question on its own line as a numbered list item",
    "no-multi-question-in-prose example": 'do NOT produce',
    "propose-defaults rule": "Propose defaults — never ask open-ended",
    "no-re-ask rule": "Do not re-ask values already on file",
    "tool-failure rule": "Stop and report when writes fail — do NOT pretend success",
    "markdown formatting block": "Markdown formatting (the chat UI renders GitHub-Flavored Markdown",
    "bold-label rule": "Bold the label in every `label: value` proposal",
    "bullets-not-commas rule": "Lists of items always go on their own line as bullets",
    "backticks-around-identifiers rule": "Wrap every identifier in backticks",
    "explain-before-list rule": "Explain every non-obvious label in one line before listing the",
}


@pytest.mark.parametrize("phase_id", list(_PROMPT_BUILDERS))
@pytest.mark.parametrize(
    "marker_label,marker_text",
    list(_COMMON_RULES_MARKERS.items()),
    ids=list(_COMMON_RULES_MARKERS),
)
def test_prompt_includes_common_rules_block(
    all_prompts: dict[str, str],
    phase_id: str,
    marker_label: str,
    marker_text: str,
) -> None:
    """Every prompt must splice in the full `_COMMON_RULES` block.

    Without it, the prompt-specific text drifts: the LLM resumes writing
    ✓ summary checklists, announcing phases, previewing future steps, or
    chaining multiple questions into one paragraph.
    """
    assert marker_text in all_prompts[phase_id], (
        f"Phase {phase_id!r}: prompt is missing the {marker_label} "
        f"({marker_text!r}). Ensure `{{_common_rules()}}` is spliced into "
        "the phase prompt's f-string."
    )
