"""Phase prompt builder: user_state.

Defines the user's mental journey — cognitive/emotional states and per-state
behavioural guidance for the Agent Core's user-state model. Part of the
dev-kit deterministic wizard's phase-prompt system.

Required for multi-turn companion-style agents; skipped for others.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import (
    _phase_focus_header,
    _closing_block,
    _common_rules,
    _path_of,
    _render_fields,
    _rule_of,
)

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


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

    return f"""{_phase_focus_header("user_state", pending_fields)}# Phase: User State

You are defining the user's mental journey — the cognitive and emotional
states users pass through and how the agent should behave in each.

{applicability}

{_common_rules()}

**What to configure:**

- List 2–5 states with short snake_case ids (e.g. `fog`, `orientation`,
  `evaluation`, `commitment`, `follow_through` for a job-market advisor).
- For each state:
  - `signals` — natural-language phrases or cues that indicate the user is
    in this state.
  - `guidance` — 2–3 sentences describing how the agent should behave when
    it detects this state.
- `default_state` — which state a fresh caller starts in.

**Write EVERYTHING the user confirms — proposing is not enough.** The
skeleton seeds `user_state_model` with `enabled: false` and no states,
so your proposed state model will be silently lost unless you write
it. Make these writes in the SAME turn the user confirms:

```
update_config(path="agent_core.conversation.user_state_model.enabled",
              value=true)
update_config(path="agent_core.conversation.user_state_model.default_state",
              value="<state_id>")
update_config(path="agent_core.conversation.user_state_model.states",
              value=[
                {{"id": "<state_a>", "signals": [...], "guidance": "..."}},
                {{"id": "<state_b>", "signals": [...], "guidance": "..."}},
                ...
              ])
update_config(path="agent_core.preprocessing.nlu_processor.user_state_confidence_threshold",
              value=0.4)
```

If the user gives edits ("add state X", "drop state Y", "rename A to B"),
render the updated proposal back to them, ask for confirmation, THEN
write the edited values. NEVER silently drop or rename states the user
did not mention.

The `user_state_confidence_threshold` default of 0.4 is usually correct —
only change if the user has a specific reason to tune sensitivity.

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

{_closing_block()}
"""
