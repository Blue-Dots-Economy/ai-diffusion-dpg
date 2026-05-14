"""Phase prompt builder: review.

Final phase of the deterministic wizard. Runs a full schema-coverage check
across all 7 DPG blocks and repairs any empty required fields before the
wizard is declared complete.

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

    return f"""# Phase: Review

You are now in the final review phase. Your goal is to run a full
schema-coverage check across all 7 DPG blocks, surface any empty required
fields, and repair them before declaring the configuration complete.

The `validate_config` tool reads every block's YAML template, compares it
against the accumulated config, and returns a list of empty required fields
with exact dotted paths (e.g. `reach_layer.channels.voice.terminal_word`,
`trust_layer.dignity_check.questions[2]`). For each missing field: ask the
user for the value, call the appropriate `update_config` tool, and re-run
`validate_config` until the report is clean.

## Fields to capture this phase

{fields_section}

## Cross-block invariants to verify manually

After `validate_config` is clean, verify these rules by inspecting the
accumulated config state:

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

11. **workflow top-level fields set** — `agent_workflow.workflow_id`,
    `agent_workflow.version`, and `agent_workflow.agent_system_prompt` must
    all be non-empty.

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

Fix any violations found by `validate_config` or the manual cross-block
checks above. Once everything is clean, tell the user the configuration is
complete and they can proceed to the **Deploy** step in the wizard to push
it to their DPG infrastructure. Do NOT name a tool to call — deploy is a
wizard step the user clicks through, not a tool you invoke.
"""
