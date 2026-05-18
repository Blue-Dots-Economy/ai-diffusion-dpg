"""Phase prompt builder: trust.

Configures the Trust Layer safety gate — blocked content rules, prohibited
language, topic firewall, escalation rules, and (for companion-style agents)
the pre-response dignity check. Part of the dev-kit deterministic wizard's
phase-prompt system.

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
    """Build the trust phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the trust phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine whether the
            dignity check is needed (companion-style agents).

    Returns:
        A non-empty string to append to the base system prompt for the trust
        phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    is_companion = getattr(intake_state, "is_companion_style", False)

    dignity_section = ""
    if is_companion:
        dignity_section = """
**Block 2 — Dignity check (predetermined, not user-configurable here):**

For companion-style agents the router cascade has already populated
`trust_layer.dignity_check.enabled = true` and a canonical five-question
list — confirm to the user the dignity check is in place, and DO NOT
call `update_config` for either field. The chat field on this section
is `fail_action`, which has no FieldRule entry in this codebase and is
also not user-configurable.
"""
    else:
        dignity_section = """
**Dignity check:** Not required for this agent type (not companion-style).
"""

    return f"""{_phase_focus_header("trust", pending_fields)}# Phase: Trust

You are configuring the Trust Layer — the mandatory safety gate that runs
twice per turn (input before LLM, output before delivery). Set blocked
content rules, prohibited language, topic firewall, and escalation rules.

The Trust Layer is never skipped. All agents need at minimum: content rules,
blocked phrases, and escalation topics.

{_common_rules()}

**Configuration paths (use the path form — every chat field below has its
own FIELD_RULES entry):**

- Input blocked phrases: `update_config(path="trust_layer.trust.input_rules.blocked_phrases", value=[...])`
- Input blocked-input message: `path="trust_layer.trust.input_rules.blocked_input_message"`
- Escalation topics: `path="trust_layer.trust.input_rules.escalation_topics", value=[...]`
- Output blocked phrases: `path="trust_layer.trust.output_rules.blocked_phrases"`
- Output blocked-output message: `path="trust_layer.trust.output_rules.output_blocked_message"`
- HITL holding message: `path="trust_layer.trust.hitl.holding_message"`
- HITL queue backend: `path="trust_layer.trust.hitl.queue_backend"` —
  valid values: `log` | `redis` | `webhook`. Default to `log` for dev.
  NEVER use `memory` (not a valid backend; the Trust Layer crashes on it).
- Policy pack (optional content/safety policy stack): `path="trust_layer.trust.policy_pack"`

Do NOT write `trust_layer.observability.domain` — derived, auto-computed.
Do NOT write `trust_layer.dignity_check.enabled` or
`trust_layer.dignity_check.questions` — both are predetermined fields the
router cascade sets from `is_companion_style` (enabled + a canonical
five-question set). They are NOT user-configurable here.

**Block 1 — Content rules and blocked phrases (all agents):**

Suggest domain-appropriate blocked phrases, escalation topics, and content
rules. Present them all together and ask: "Here are the suggested safety
rules — do these look good, or would you like to change any?"
{dignity_section}
**Self-check before advancing:**
1. Content rules and blocked phrases are non-empty.
2. For companion-style agents: `dignity_check.enabled: true`, `questions`
   has all 5 strings (not empty, not dicts), and `fail_action: 'rewrite'`.

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
