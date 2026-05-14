"""Phase prompt builder: observability.

Configures outcome lifecycle states, quality metrics, and the domain tag used
in all OTel spans for the DPG Observability Layer. Part of the dev-kit
deterministic wizard's phase-prompt system.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import _path_of, _rule_of, _render_fields

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the observability phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the observability phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used for context in the intro.

    Returns:
        A non-empty string to append to the base system prompt for the
        observability phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"
    project_name = getattr(intake_state, "project_name", "")

    return f"""# Phase: Observability

You are now configuring the Observability Layer. This phase sets outcome
lifecycle states, quality metrics, and the domain tag that will be attached
to every OTel span emitted by the running agent.

The Observability Layer is async-only — it never runs in the response path.
Its job is to let operators answer questions like "how many users reached the
'applied' state?" and "how many turns had low-confidence NLU?" after the fact.

The domain tag for this project is `{project_name}`. Set it with:
`update_config(block=observability_layer, section=observability,
values={{domain: '{project_name}'}})`.
Use `section=observability` (NOT `section=observability.domain`) — the
latter double-nests and crashes observability_layer at startup.

**What to configure:**

- **Outcome lifecycle states** — a short ordered list of named stages a user
  session can reach (e.g. `profile_gathered`, `options_shown`, `applied`,
  `callback_pending`). Derive these from the agent's described flow; present
  them to the user for sign-off.
- **Quality signals** — metrics worth tracking (e.g. drop-off at specific
  subagents, low-confidence NLU turns, consent declines, tool failures).
  Present as a block alongside the lifecycle states.
- **Exception-handling policies** — what the agent says on tool timeout,
  empty result, ASR misrecognition, or mid-call drop. These are prose
  descriptions the ops team can reference, not runtime config.

**Conversation style:** Present the full observability configuration as one
block with suggested defaults based on the use case. Ask: "Here is the
suggested observability setup — do these look good, or would you like to
change any?"

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When outcomes and quality signals are set, the router advances to the reach
phase automatically. Do NOT call set_phase.
"""
