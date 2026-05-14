"""Phase prompt builder: observability.

Configures outcome lifecycle states, quality metrics, and the domain tag used
in all OTel spans for the DPG Observability Layer.

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
