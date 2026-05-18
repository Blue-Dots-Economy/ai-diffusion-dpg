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

You are in the final review step. **Before anything else, re-ask any
fields that were marked `needs_re_asking` in prior phases** — these appear
in the "Fields to capture this phase" section below if any exist. Work
through every re-ask first: ask the user for the value, call the
appropriate `update_config` tool to record it, and confirm before
continuing. If no re-asks remain, note that briefly and proceed straight
to the cross-block checks.

The system runs a strict Pydantic dry-run against the runtime block
schemas when the user clicks **Deploy** in the wizard's next step. There
is no per-turn validation tool you can call yourself — your job here is
to inspect the accumulated config in the references section below and
flag any issues against the cross-block invariants listed beneath.

{_common_rules()}

## Fields to capture this phase

{fields_section}

## Cross-block invariants to verify manually

By inspecting the accumulated config below, verify these rules. For each
violation: ask the user for the correction and call the appropriate
`update_config` tool to record it.

1. **Tool names exist in connectors** — every name in any subagent's `tools`
   list or in `global_tools` must match a connector `name` in
   `connectors.read`, `connectors.write`, `connectors.identity`, or
   `connectors.internal`.

2. **knowledge_retrieval placement** — if `knowledge_retrieval` appears in
   any tool list or `global_tools`, it must be in `connectors.internal`
   (route field), not `connectors.read` (base_url field).

3. **global_intents ∩ subagent valid_intents = empty** — Agent Core crashes
   at startup on any overlap.

4. **NLU intents cover intent_filters** — every key in
   `knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters`
   must appear in `agent_core.preprocessing.nlu_processor.intents`.

5. **Voice configured if voice selected** — if `voice` is in
   `selected_channels`, `reach_layer.channels.voice` must include
   `raya.voice_id`, `raya.stt_language`, and `raya.tts_language`.

6. **agent_core.channels set for every selected channel** — web is always
   required; voice and cli if selected. Missing entries cause
   `ValueError: Unsupported channel` at Agent Core startup.

7. **reach_layer.channels set for every selected channel** — the DPG
   defaults provide all three; verify no domain config nullifies one.

8. **default_fallback_subagent_id is a declared subagent id** — a mismatch
   causes a KeyError at runtime.

9. **routing.next_subagent_id values are declared subagent ids** — walk
   every routing rule in every subagent and in global_routing.

10. **opening_phrase non-empty for every non-terminal subagent** — an empty
    opening_phrase means the agent says nothing on first entry.

11. **agent_system_prompt is non-empty** — required for runtime startup.
    Do NOT ask the user about `agent_workflow.workflow_id` or
    `agent_workflow.version` — `workflow_id` is auto-derived from the
    project slug (e.g. `akashvani_concierge_workflow`) and `version`
    defaults to `"1.0.0"` via the skeleton. Both already appear in the
    rendered YAML; treating them as user-configurable here would just
    waste a turn asking for values the user has no input on.

12. **dignity_check questions populated** — if
    `trust_layer.dignity_check.enabled` is true, `questions` must be a
    non-empty list of plain strings and `fail_action` must be set.

13. **observability.domain is a non-empty string in every block** — a dict
    value means the config used `section=observability.domain` (double-nested
    bug) instead of `section=observability`. Fix by calling
    `update_config(block=<block>, section=observability,
    values={{domain: '<slug>'}})` for any offending block. Check every block
    including `reach_layer.common.observability.domain`.

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

Fix any violations found by the manual cross-block checks above. Once
everything is clean, tell the user the configuration is complete and they
can proceed to the **Deploy** step in the wizard to push it to their DPG
infrastructure. The Deploy step is a UI action the user clicks; it is not
a tool you invoke.

{_closing_block()}
"""
