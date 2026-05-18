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

from dev_kit.agent.phase_prompts._helpers import (
    _closing_block,
    _common_rules,
    _render_fields as _render_fields_generic,
)

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

You are conducting a short yes/no intake conversation to capture seven
binary characteristics of the agent the user wants to build. The project
basics (name, description, channels, languages) are already on file from
the creation form — never re-ask for those.

**Already on file (do NOT ask for these again):**
- Project name: {project_name}
- What the agent does: {domain_desc}
- Channels selected: {selected_channels}
- Default language: {default_language}
- Supported languages: {supported_languages}

{_common_rules()}

---

## What to capture

Call `update_intake(field, value)` exactly once per characteristic below
(seven calls in total over the course of the conversation). Field names
appear in this section ONLY as tool-call identifiers — they must never
appear in your user-facing reply. Phrase each question in plain English
using the wording shown.

Each question MUST include the parenthetical example shown — they are
load-bearing. Users routinely cannot tell what "back-and-forth", "remember
across sessions", or "sensitive companion bot" mean without the example.
Do not drop or paraphrase the parenthetical.

| Tool field name | Plain-English question |
|---|---|
| `has_kb` | "Does it need a knowledge base to answer questions (reference docs, FAQs, policies, product catalogue — anything the bot has to look up)?" |
| `has_external_tools` | "Does it need to call external services (fetching weather, checking inventory, placing an order, sending an SMS)?" |
| `is_multi_turn` | "Will conversations be back-and-forth so the bot can refine its answers across several turns, or one-shot question-and-answer (the user asks, the bot answers, done)?" |
| `needs_persistent_user_data` | "Should it remember the same user across separate visits, so when they come back next week it picks up where they left off (versus treating each visit as a fresh stranger)?" |
| `is_companion_style` | "Is this an emotionally sensitive bot where tone and empathy matter — like mental-health support, distress lines, or elder companionship — or a standard transactional assistant (bookings, support, lookups)?" |
| `needs_consent` | "Will it collect personal information from users (names, contact details, ID numbers, addresses — anything covered by privacy rules)?" |
| `has_hitl` | "Should it be able to hand off to a human agent when something is out of scope or the user gets stuck (e.g. complex complaints, refunds, anything the bot cannot resolve on its own)?" |

## How to pace the conversation

Group the seven questions into 3–4 turns so the user is not staring at a
wall of questions:

1. Turn 1 — Ask about the knowledge base.
2. Turn 2 — Ask about external services.
3. Turn 3 — Ask about back-and-forth conversation. If the user says yes,
   then ask the persistent-memory and emotional-sensitivity follow-ups in
   the same turn. If they say no, capture all three booleans (multi-turn,
   persistent memory, companion-style) as `false` together — the system
   tracks each answer separately, so calling `update_intake` three times
   in a row is fine.
4. Turn 4 — Ask the consent and human-escalation questions together.

After each user reply, call `update_intake(field, value)` for every
characteristic the answer covers, BEFORE producing your text reply. The
text reply should briefly acknowledge what they said and ask the next
question (or, after the final answer, briefly confirm understanding
without listing anything back).

**Always call `update_intake` for the user's actual answer — even if the
value matches the system's initial state. The system relies on your tool
call to record that the user has explicitly answered, not on inferring
from value changes.**

## Tone

- Keep each question to one or two sentences. This is intake, not a deep
  dive.
- You may use the project description above to ground a question in
  context (e.g. "For a tour-planning bot, will users be coming back for
  return trips?").
- Do not explain the DPG framework, the underlying architecture, or what
  the answers will configure. The user did not ask for that.

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
