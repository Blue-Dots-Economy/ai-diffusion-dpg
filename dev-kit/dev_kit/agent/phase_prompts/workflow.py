"""Phase prompt builder: workflow.

Designs the subagent state machine — individual conversational sub-flows
and their NLU-intent-based routing rules for the Agent Core. Part of the
dev-kit deterministic wizard's phase-prompt system.

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
    """Build the workflow phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the workflow phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference. Crucially includes the signed-off
            NLU intents from the language phase.
        intake_state: Current IntakeState. Used for context in the intro.

    Returns:
        A non-empty string to append to the base system prompt for the
        workflow phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    has_kb = getattr(intake_state, "has_kb", False)
    is_multi_turn = getattr(intake_state, "is_multi_turn", False)

    kb_note = ""
    if has_kb:
        kb_note = """
**Knowledge base agents — knowledge_retrieval already configured:**

By the time you reach the workflow phase, the `knowledge_retrieval`
connector at `agent_core.connectors.internal[name=knowledge_retrieval]`
has been fully configured during the earlier knowledge phase (skeleton
created the connector entry; the knowledge phase wrote `description`
and the six `invocation_rules.*` chat fields). Do NOT re-create it
here — `update_config` writes to `connectors.internal` will fail
because every path-form chat field for it is already `answered`.

When wiring it into the workflow, set `global_tools:
['knowledge_retrieval']` — and NEVER list `knowledge_retrieval` in any
individual subagent's `tools` field.
"""

    memory_state_note = ""
    if is_multi_turn:
        memory_state_note = """
**Multi-turn agents — contact-memory states:**

Structure your subagent graph so the 5 contact-memory states (new, sparse,
rich, mid-journey, post-application) each land in a subagent with an
appropriate `opening_phrase`. The wizard does not enforce exactly 5 branches
— author judgement applies. The guide's 5-branch rule is pedagogy, not
validation.
"""

    return f"""{_phase_focus_header("workflow", pending_fields)}# Phase: Workflow

You are designing the subagent state machine — individual conversational
sub-flows and how they route between each other based on NLU intent.

{_common_rules()}

**Execution rule:** When the user confirms the subagent design (any variant
of "yes", "looks good", "that's correct", "proceed"), immediately call
`add_subagent` for every subagent in the design, then `add_routing_rule`
for every transition. Do NOT say "Perfect! Let me set that up…" and then
ask another question. Present design → user confirms → execute tools
immediately.

**Step 0 — Set top-level workflow fields FIRST:**

These three fields are REQUIRED. Agent Core will fail Pydantic validation at
startup if any is missing:

- `workflow_id` — unique snake_case identifier (e.g. `my_domain_agent`)
- `version` — always `"1.0.0"` for a new workflow
- `agent_system_prompt` — the top-level persona + overarching instruction
  visible on EVERY turn

Set all three in one call:
`update_config(block=agent_core, section=agent_workflow,
values={{workflow_id: 'my_domain_agent', version: '1.0.0',
agent_system_prompt: '...full persona prompt...'}})`

Also set `default_fallback_subagent_id` once you have declared your
subagents. It MUST exactly match a declared subagent `id`.

**Read existing NLU intents BEFORE designing any subagent.** The intent list
is in `agent_core.preprocessing.nlu_processor.intents` (visible in
cross-phase refs below). Use those exact strings. Do NOT ask the user to
re-confirm them — they were signed off in the language phase. Do NOT invent
new intent names without explicit user approval.
{kb_note}{memory_state_note}
**Hard rules:**

- Every tool name in `tools` or `global_tools` MUST match a connector `name`
  in `connectors.read / write / identity / internal`. Agent Core crashes at
  startup with a KeyError on any mismatch.
- Every `next_subagent_id` in every routing rule MUST match a declared
  subagent `id`.
- No intent may appear in both `global_intents` and any subagent's
  `valid_intents` — Agent Core crashes on any overlap.
- `opening_phrase` MUST be non-empty for every non-terminal subagent.
- Exactly ONE subagent has `is_start: true`.

**Self-check before advancing:**
1. `workflow_id`, `version`, `agent_system_prompt` all non-empty.
2. Every non-terminal subagent has a non-empty `opening_phrase`.
3. `default_fallback_subagent_id` matches a declared subagent id.
4. Every `next_subagent_id` in every routing rule matches a declared id.
5. No intent appears in both `global_intents` and any subagent's
   `valid_intents`.
6. Every tool name in `global_tools` and per-subagent `tools` exists in
   connectors.
7. `knowledge_retrieval` (if agent has KB) is in `global_tools` only — NOT
   in any individual subagent's `tools`.

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
