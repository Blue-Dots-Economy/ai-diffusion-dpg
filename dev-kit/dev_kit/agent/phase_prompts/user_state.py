"""Phase prompt builder: user_state.

Defines the user's mental journey — cognitive/emotional states and per-state
behavioural guidance for the Agent Core's user-state model.

Required for multi-turn companion-style agents; skipped for others.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def _path_of(item) -> str:
    """Extract the dotted field path from a pending_fields item.

    Args:
        item: Either a FieldRule with a ``path`` attribute, a ``(path, rule)``
            tuple, or any other object (falls back to ``str(item)``).

    Returns:
        The dotted field path string.
    """
    if hasattr(item, "path"):
        return item.path
    if isinstance(item, tuple) and len(item) == 2:
        return item[0]
    return str(item)


def _rule_of(item):
    """Extract the FieldRule from a pending_fields item.

    Args:
        item: Either a bare FieldRule, a ``(path, rule)`` tuple, or any object.

    Returns:
        The FieldRule object, or the item itself as a fallback.
    """
    if hasattr(item, "category"):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        return item[1]
    return item


def _render_fields(pending_fields: list) -> str:
    """Render pending fields as a markdown bullet list.

    Args:
        pending_fields: Items where each is either a FieldRule with a ``path``
            attribute, or a ``(path, FieldRule)`` tuple.

    Returns:
        Markdown bullet list with one line per field, or a note if empty.
    """
    if not pending_fields:
        return "_No outstanding fields for this phase._"
    lines = []
    for item in pending_fields:
        path = _path_of(item)
        rule = _rule_of(item)
        desc = getattr(rule, "description", None) or ""
        default = getattr(rule, "default", None)
        applies_if = getattr(rule, "applies_if", None)
        line = f"- `{path}`"
        if desc:
            line += f": {desc}"
        if default is not None:
            line += f" _(default: {default!r})_"
        if applies_if:
            line += f" _(applies if: {applies_if})_"
        lines.append(line)
    return "\n".join(lines)


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the user_state phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the user_state phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine whether this
            phase is active (companion-style and multi-turn agents).

    Returns:
        A non-empty string to append to the base system prompt for the
        user_state phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    is_companion = getattr(intake_state, "is_companion_style", False)
    is_multi_turn = getattr(intake_state, "is_multi_turn", False)

    if not is_companion and not is_multi_turn:
        applicability = (
            "**Note:** User state modelling is most impactful for multi-turn and "
            "companion-style agents. This project does not have either flag set, "
            "so this phase may be minimal. Confirm with the user whether a simple "
            "default state (e.g. `active`) is sufficient, or whether they want a "
            "richer model."
        )
    elif is_companion:
        applicability = (
            "**This phase is REQUIRED** for this companion-style agent. "
            "The user mental journey shapes every response the agent delivers — "
            "it is not just metadata. Configure it carefully."
        )
    else:
        applicability = (
            "**This phase is recommended** for this multi-turn agent. "
            "The user mental journey helps the LLM adapt its tone and guidance "
            "as the user's situation evolves across sessions."
        )

    return f"""# Phase: User State

You are now defining the user's mental journey — the cognitive and emotional
states users pass through and how the agent should behave in each.

{applicability}

**What to configure:**

- List 2–5 states with short snake_case ids (e.g. `fog`, `orientation`,
  `evaluation`, `commitment`, `follow_through` for a job-market advisor).
- For each state:
  - `signals` — natural-language phrases or cues that indicate the user is
    in this state.
  - `guidance` — 2–3 sentences describing how the agent should behave when
    it detects this state.
- `default_state` — which state a fresh caller starts in.

**Configuration path:**
`update_config(block=agent_core, section=conversation,
values={{user_state_model: {{enabled: true, default_state: '...', states: [...]}}}})`

Also set the confidence threshold (GH-139):
`update_config(block=agent_core, section=preprocessing.nlu_processor,
values={{user_state_confidence_threshold: 0.4}})`
(Default 0.4 is usually correct — only change if the user has a specific
reason to tune sensitivity.)

**Note on the DPG sticky fallback:** When the NLU classifier's confidence
for the predicted user state falls below `user_state_confidence_threshold`,
the Agent Core retains the previous state rather than switching. This prevents
jittery state transitions on ambiguous turns.

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When the user state model is declared (or confirmed as not needed), the router
advances to the trust phase automatically. Do NOT call set_phase.
"""
