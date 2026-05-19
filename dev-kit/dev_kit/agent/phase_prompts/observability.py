"""Phase prompt builder: observability.

Configures outcome lifecycle states, quality metrics, and the domain tag used
in all OTel spans for the DPG Observability Layer. Part of the dev-kit
deterministic wizard's phase-prompt system.

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

    return f"""{_phase_focus_header("observability", pending_fields)}# Phase: Observability

You are configuring the Observability Layer — outcome lifecycle states,
quality metrics, and the domain tag attached to every OTel span emitted
by the running agent.

**This is NOT the tools phase.** Do NOT propose tool integrations, REST
APIs, OpenAPI specs, MCP servers, or any external connector configuration
here. If the prior phase mentioned tools, that's an artefact of the
wizard's phase order — it does not mean tools are pending. The tools
phase has either already run, or was skipped because the project has
`has_external_tools=false`. Either way, your job here is purely
observability config — lifecycle states + metrics. Stay on that.

{_common_rules()}

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
- **Quality signals (metrics)** — what to count after each session
  (e.g. drop-off at specific subagents, low-confidence NLU turns,
  consent declines, tool failures).

**Conversation style:** Present the full observability configuration as one
block with suggested defaults based on the use case. Ask: "Here is the
suggested observability setup — do these look good, or would you like to
change any?"

**Write EVERYTHING the user confirms — proposing is not enough.** The
skeleton only seeds `lifecycle` with a minimal `[{{state: "started"}}]`
entry to satisfy the runtime's `min_length=1` validator; your proposed
multi-state lifecycle will be silently lost unless you write it. Make
both writes in the SAME turn the user confirms:

```
update_config(path="observability_layer.observability.outcomes.lifecycle",
              value=[
                {{"state": "<state_a>", "trigger_tool": null}},
                {{"state": "<state_b>", "trigger_tool": null}},
                ...
              ])
update_config(path="observability_layer.observability.outcomes.metrics",
              value=[
                {{
                  "name": "<metric_name>",
                  "instrument": "counter" | "gauge" | "histogram",
                  "description": "...",
                  "unit": "<unit, e.g. '1' or 'ms'>",
                  "attributes": ["<attr1>", "<attr2>"]
                }},
                ...
              ])
```

If the user gives edits ("add stage X", "drop metric Y", "rename
state A to B"), render the updated proposal back to them, ask for
confirmation, THEN write the edited values. NEVER silently drop or
rename items the user did not mention.

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
