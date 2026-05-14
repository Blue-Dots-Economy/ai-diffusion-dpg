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
**Block 2 — Dignity check (required for companion-style agents):**

You MUST call `update_config` to set all five questions and `fail_action`.
Do NOT leave `questions: []` — the dignity check will always pass (no
questions to fail) and the protection is disabled.

Default questions (adapt phrasing to the domain language):
1. "Does this blame the user?"
2. "Does it over-promise?"
3. "Does it push urgency?"
4. "Does it reduce their agency?"
5. "Does it sound like a script instead of a human call?"

Set them in one call:
`update_config(block=trust_layer, section=dignity_check, values={{enabled:
true, questions: ['Does this blame the user?', 'Does it over-promise?',
'Does it push urgency?', 'Does it reduce their agency?', 'Does it sound
like a script instead of a human call?'], fail_action: 'rewrite'}})`

`questions` MUST be a list of plain string sentences. Do NOT emit dicts like
`{{category: 'hate_speech', severity: 'high'}}` — that's a content-
moderation taxonomy, not a dignity prompt. The wrong shape causes a Pydantic
ValidationError at startup.

If the domain is non-English, translate the questions into the domain
language before setting.
"""
    else:
        dignity_section = """
**Dignity check:** Not required for this agent type (not companion-style).
"""

    return f"""# Phase: Trust

You are now configuring the Trust Layer — the mandatory safety gate that runs
twice per turn (input before LLM, output before delivery). This phase sets
blocked content rules, prohibited language, topic firewall, and escalation
rules.

The Trust Layer is never skipped. All agents need at minimum: content rules,
blocked phrases, and escalation topics.

**Configuration paths:**
- Content and output rules: `update_config(block=trust_layer, section=rules, values={{...}})`
- Consent rules (DPDP Act): `section=consent`
- HITL queue backend: `section=trust, values={{hitl: {{queue_backend: 'log'}}}}`
  Valid values: `log` | `redis` | `webhook`. Default to `log` for dev.
  NEVER use `memory` — it is not a valid backend and will crash the Trust Layer.
- Observability domain: `update_config(block=trust_layer, section=observability,
  values={{domain: '<project_slug>'}})`. Use `section=observability` NOT
  `section=observability.domain` (double-nesting crashes trust_layer).

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

When content rules, blocked phrases, and (for companion-style agents)
dignity check questions are set, the router advances to the tools phase
automatically. Do NOT call set_phase.
"""
