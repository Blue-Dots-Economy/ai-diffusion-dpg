"""Phase prompt builder: tier.

Orchestrates the 4-turn yes/no intake chat that captures 7 binary flags
(has_kb, has_external_tools, is_multi_turn, needs_persistent_user_data,
is_companion_style, needs_consent, has_hitl). Part of the dev-kit
deterministic wizard's phase-prompt system.

The 5 form-captured fields (project_name, domain_description,
selected_channels, default_language, supported_languages) are already set
server-side via update_intake before this phase begins — do NOT ask for them.

See design §4 and §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import _render_fields as _render_fields_generic

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def _render_fields(pending_fields: list) -> str:
    """Render pending fields for the tier phase.

    Delegates to the shared helper for non-empty lists; returns a tier-specific
    sentinel string when the list is empty (tier flags live in IntakeState, not
    FIELD_RULES).

    Args:
        pending_fields: Items where each is either a FieldRule with a ``path``
            attribute, or a ``(path, FieldRule)`` tuple.

    Returns:
        Markdown bullet list with one line per field, or a tier-specific note
        if empty.
    """
    if not pending_fields:
        return "_No outstanding fields — tier intake flags live in IntakeState, not FIELD_RULES._"
    return _render_fields_generic(pending_fields)


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the tier phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the tier phase. Will typically be empty because
            the 7 binary flags live in IntakeState, not in FIELD_RULES.
        pydantic_schemas: Pre-rendered Pydantic class source code. Typically
            empty for the tier phase; injected verbatim if provided.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases. Typically empty at the start of the wizard.
        intake_state: Current IntakeState. The 5 form fields
            (project_name, domain_description, selected_channels,
            default_language, supported_languages) are already populated;
            do NOT ask for them again.

    Returns:
        A non-empty string to append to the base system prompt for the tier
        phase.
    """
    fields_section = _render_fields(pending_fields)

    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A — tier flags are captured via update_intake, not Pydantic config schemas._"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_N/A — no prior phases at wizard start._"

    project_name = getattr(intake_state, "project_name", "")
    domain_desc = getattr(intake_state, "domain_description", "")
    selected_channels = getattr(intake_state, "selected_channels", [])
    default_language = getattr(intake_state, "default_language", "")
    supported_languages = getattr(intake_state, "supported_languages", [])

    return f"""# Phase: Tier intake chat

You are conducting the tier intake chat — a short 4-turn yes/no conversation
that captures 7 binary flags before the main configuration phases begin.

**Goal:** Capture exactly these 7 flags via `update_intake(field, value)`:
1. `has_kb` — does the agent need a knowledge base?
2. `has_external_tools` — does it need to call external APIs?
3. `is_multi_turn` — is this a multi-turn back-and-forth conversation?
4. `needs_persistent_user_data` — should it remember users across sessions?
5. `is_companion_style` — is it a sensitive companion bot?
6. `needs_consent` — does it collect personal information?
7. `has_hitl` — should it escalate to a human agent?

**Already set from the project creation form — do NOT ask for these again:**
- `project_name` = `{project_name}`
- `domain_description` = `{domain_desc}`
- `selected_channels` = `{selected_channels}`
- `default_language` = `{default_language}`
- `supported_languages` = `{supported_languages}`

Do NOT mention or re-ask for `project_name`, `domain_description`,
`selected_channels`, `default_language`, or `supported_languages`. They are
already set.

---

## 4-turn conversation script

Work through the turns below in order. Each turn is a single conversational
exchange. Ask the question, wait for the user's response, call
`update_intake(field, value)` for each captured flag, then move to the next
turn.

**Turn 1 — Knowledge:**
Ask: "Does your agent need to answer questions from a knowledge base
(reference docs, FAQ, domain content)?"
- YES → `update_intake("has_kb", true)`
- NO  → `update_intake("has_kb", false)`

**Turn 2 — External tools:**
Ask: "Does your agent need to call external APIs or services? (e.g. looking
up jobs, placing orders, fetching weather, etc.)"
- YES → `update_intake("has_external_tools", true)`
- NO  → `update_intake("has_external_tools", false)`

**Turn 3 — Conversation style:**
Ask: "Is this a multi-turn back-and-forth conversation, or a single Q&A?"
- Single Q&A (no) → `update_intake("is_multi_turn", false)`,
  `update_intake("needs_persistent_user_data", false)`,
  `update_intake("is_companion_style", false)`.
  Advance to Turn 4.
- Multi-turn (yes) → `update_intake("is_multi_turn", true)`.
  Then ask two follow-ups in the SAME response:
  "Since it's multi-turn, two quick follow-ups:
  1. Should it remember users across sessions (pick up where they left off
     next time)?
  2. Is this a sensitive companion bot (mental health, distressed users,
     vulnerable populations)?"
  Capture: `update_intake("needs_persistent_user_data", <bool>)` and
  `update_intake("is_companion_style", <bool>)`.

**Turn 4 — Operational sensitivity:**
Ask: "Two more:
1. Does the agent collect personal information (name, location, ID — anything
   covered by privacy rules)?
2. Should it be able to escalate to a human agent when needed?"
- Capture: `update_intake("needs_consent", <bool>)` and
  `update_intake("has_hitl", <bool>)`.

---

## Conversation style

- Keep each turn brief and direct. This is a yes/no intake, not a deep dive.
- Do NOT explain the DPG framework or configuration phases to the user.
- Do NOT skip turns or merge turns 3 and 4 (even if the answers seem
  obvious from the project description).
- DO use the project description (`domain_description` above) to frame
  questions in context, e.g. "For a {domain_desc} agent, does it need a
  knowledge base?"

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When all 7 binary flags are captured via `update_intake`, the router advances
to the language phase automatically. Do NOT call set_phase.
"""
