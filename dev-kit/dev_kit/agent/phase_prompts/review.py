"""Phase prompt builder: review.

Final phase of the dev-kit deterministic wizard. Runs a full schema-coverage
check across all 7 DPG blocks and repairs any empty required fields before the
wizard is declared complete.

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
    """Build the review phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the review phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used for context in the intro.

    Returns:
        A non-empty string to append to the base system prompt for the review
        phase.
    """
    fields_section = _render_fields(pending_fields)

    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A — no schema changes in review._"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    return f"""{_phase_focus_header("review", pending_fields)}# Phase: Review

The authoritative validator runs when the user clicks **Deploy** — it
runs the strict baked runtime schemas + the full cross-block invariant
set on the merged config. You CANNOT reproduce that gate here, and you
MUST NOT try.

**Strict output contract for this phase — there are only two valid
shapes for your reply:**

**Shape A — re-asks pending.** If the "Fields to capture this phase"
section below lists any field with status `needs_re_asking`, ask
the user for it, record their answer via `update_config`, confirm.
Repeat until all re-asks are clear. Then produce Shape B on the
turn that clears the last re-ask.

**Shape B — single-paragraph closing.** Emit ONE short paragraph (≤4
sentences, no bullets, no headers) that:
1. Lists the project name + main blocks generated (e.g. "Configured
   <project> with N subagents, M tools, voice+web channels, KB,
   N outcome states.").
2. Tells the user to click **Deploy** to run the canonical
   validation and ship the config.

That is your entire reply. No preamble. No "Let me check…", no
"I'll verify…", no narrating invariants you are "scanning". No
self-checks of any kind. No claims that anything has been
"verified" or "validated". The Deploy button is the validator.

**Banned reply patterns (these waste a turn and confuse the user):**

- "I need to review the configuration against the runtime invariants."
- "Let me check for any violations:" / "Checking…" / "Scanning…"
- "Tool-name case matching — Checking subagent tool references…"
- "I don't see X in the accumulated state — could you confirm…"
- "Have the N tools been registered?"
- "Could you confirm the routing rules and tool connectors?"
- Any per-rule bullet list describing checks.
- Any "I'll now verify…" narration.

Each of these asks the user for state the accumulator already holds,
or describes work you cannot actually do from here. Skip them.

**Silent auto-fixes before emitting Shape B.** Before closing, scan
the references section and auto-fix any of these — write via
`update_config` without narrating, then proceed to Shape B:

- **Tool-name mismatch in `subagent.tools` / `subagent.system_prompt`.**
  Every name in a `subagent.tools` list MUST appear verbatim in
  `agent_core.connectors.{{read,write,identity,internal}}[].name`.
  If a tools entry shows a different form than the connector
  (camelCase vs snake_case, or a renamed id), REPLACE it with the
  exact connector name. Same rule for tool references inside any
  `subagent.system_prompt` string — rewrite the prompt text so
  every tool name matches a real connector.

- **`global_tools` ⊆ connector names.** Same fix as above —
  replace any entry that isn't a real connector with the matching
  registered name, or remove it if no match exists.

- **knowledge_retrieval placement.** If it appears in any tool list,
  its connector must be in `connectors.internal`, not
  `connectors.read`. Fix silently if misplaced.

- **`global_intents` ∩ `subagent.valid_intents` empty.** Remove any
  overlap; prefer keeping the subagent-specific placement.

- **`intent_filters` keys ⊆ `nlu_processor.intents`.** NLU intents
  may be a strict superset — that is FINE, do not "fix" it.

- **`default_fallback_subagent_id`** must be a declared subagent id;
  set it to the first non-terminal subagent's id if missing.

- **`routing.next_subagent_id`** must reference a declared id; drop
  or remap if not.

- **`opening_phrase`** non-empty for every non-terminal subagent.
  If empty, write one short greeting line and move on.

Do NOT mention the fixes you applied beyond a short clause in the
single-paragraph closing (e.g. "(fixed N tool-name references)").
Do not enumerate them.

{_common_rules()}

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
