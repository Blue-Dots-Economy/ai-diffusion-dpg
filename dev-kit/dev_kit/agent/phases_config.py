"""PHASES — declarative phase definitions for the deterministic wizard.

Each entry describes one wizard phase: its display label, which prompt-module
to invoke (stored as a dotted-name string to avoid circular imports at test
time), the default successor phase, and an optional ``is_relevant`` predicate
that lets the phase driver skip the phase when no fields in it apply to the
current project's IntakeState.

This module is the single source of truth for phase metadata. The phase driver
(Task 6.3) will import ``PHASES`` here and use it in place of the duplicate
``PHASE_ORDER`` / ``PHASE_RELEVANCE`` dicts in ``router.py``.

Belongs to the dev-kit deterministic wizard.
See design §6: docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from dev_kit.agent.intake_state import IntakeState


@dataclass(frozen=True)
class PhaseDefinition:
    """Immutable descriptor for a single wizard phase.

    Attributes:
        id: Canonical phase identifier; must match the key in ``PHASES``.
        label: Human-readable phase title shown in the wizard UI.
        prompt_module: Leaf module name under ``dev_kit.agent.phase_prompts``
            that the phase driver imports at runtime to obtain the phase prompt
            function. Stored as a string (not a callable) to avoid circular
            imports during test collection.
        next_default: The id of the next phase in the default chain, or
            ``None`` for the terminal phase (``review``).
        is_relevant: Optional predicate called with the current ``IntakeState``.
            When ``None``, the phase is always considered relevant.  When
            provided, returning ``False`` causes the phase driver to skip this
            phase entirely (no questions asked, no config written).
    """

    id: str
    label: str
    prompt_module: str
    next_default: Optional[str]
    is_relevant: Optional[Callable[[IntakeState], bool]] = None


def _always(_: IntakeState) -> bool:
    """Return True unconditionally; used for phases that are always relevant."""
    return True


PHASES: dict[str, PhaseDefinition] = {
    "tier": PhaseDefinition(
        "tier", "Intake", "tier", "language", _always
    ),
    "language": PhaseDefinition(
        "language", "Language & NLU", "language", "knowledge", _always
    ),
    "knowledge": PhaseDefinition(
        "knowledge", "Knowledge base", "knowledge", "memory",
        lambda s: s.has_kb,
    ),
    # "memory" is always relevant: every deployment has at least a session
    # scope. Deviates from the plan's lambda (is_multi_turn or
    # needs_persistent_user_data) to match router.PHASE_RELEVANCE["memory"];
    # individual memory chat fields remain gated by their own applies_if.
    "memory": PhaseDefinition(
        "memory", "Memory & sessions", "memory", "user_state", _always
    ),
    "user_state": PhaseDefinition(
        "user_state", "User state", "user_state", "trust",
        lambda s: s.is_companion_style,
    ),
    "trust": PhaseDefinition(
        "trust", "Trust & safety", "trust", "tools", _always
    ),
    "tools": PhaseDefinition(
        "tools", "External tools", "tools", "workflow",
        lambda s: s.has_external_tools,
    ),
    "workflow": PhaseDefinition(
        "workflow", "Workflow", "workflow", "observability", _always
    ),
    "observability": PhaseDefinition(
        "observability", "Observability", "observability", "reach", _always
    ),
    "reach": PhaseDefinition(
        "reach", "Channels", "reach", "review", _always
    ),
    "review": PhaseDefinition(
        "review", "Review", "review", None, _always
    ),
}


__all__ = ["PhaseDefinition", "PHASES"]
