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
            phases for the LLM to reference (connectors, and any
            hand-authored ``act_intents`` rows).
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
    has_external = getattr(intake_state, "has_external_tools", False)

    # When tools phase is skipped (has_external_tools=False) the workflow
    # prompt must not mention "tools phase" — the LLM, having been told
    # tools comes next, then hallucinates tools work in the actual next
    # phase (observability). Use neutral connector-name language in that
    # case. The Akashvani Concierge E2E hit this exact regression.
    if has_external:
        tools_warning = (
            "Do NOT invent tool names from the OpenAPI spec you parsed earlier "
            "(e.g. `get_v1_forecast`, `bookTour`); those exist as connectors ONLY "
            "if `add_tool` ran successfully and registered them. If the connectors "
            "section is empty or missing some tool you expected, the tools phase "
            "did not register it — list it in NO subagent's `tools` field. Agent "
            "Core crashes at startup with a KeyError on any mismatch."
        )
        empty_connectors_note = (
            "- If the connectors list is empty (e.g. tools phase stalled), every "
            "subagent's `tools` MUST be `[]` and `global_tools` MUST be `[]`. Do "
            "NOT claim the agent can call APIs it has no registered connectors for."
        )
    else:
        # No tools phase runs for this project. Connectors will contain
        # only the framework's built-in entries (e.g. knowledge_retrieval
        # when has_kb=true). Do NOT mention "tools phase" anywhere —
        # the LLM should not anchor on a phase it will never see.
        tools_warning = (
            "This project has no external tools (`has_external_tools=false`). "
            "Every subagent's `tools` field MUST be `[]` and `global_tools` MUST "
            "be `[]` — with one exception: when `has_kb=true`, include "
            "`knowledge_retrieval` in `global_tools` only. Do NOT invent tool "
            "names. Do NOT add external API connectors. Agent Core crashes at "
            "startup with a KeyError on any unregistered tool name."
        )
        empty_connectors_note = (
            "- The only valid `global_tools` entry for this project is "
            "`knowledge_retrieval` (only if `has_kb=true`). Every subagent's "
            "`tools` field MUST be `[]`."
        )

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
sub-flows and how they route between each other based on the routing intent.

{_common_rules()}

**Execution rule:** When the user confirms the subagent design (any variant
of "yes", "looks good", "that's correct", "proceed"), immediately call
`add_subagent` for every subagent in the design, then `add_routing_rule`
for every transition. Do NOT say "Perfect! Let me set that up…" and then
ask another question. Present design → user confirms → execute tools
immediately.

**Step 0 — Set top-level workflow fields FIRST:**

Only ONE field is required from you at chat time:

- `agent_system_prompt` — the top-level persona + overarching instruction
  visible on EVERY turn.

(`workflow_id` is computed automatically from the project slug, and
`version` defaults to `"1.0.0"`. Do NOT ask the user about either —
they're handled by the wizard.)

Set the persona via:
`update_config(path="agent_core.agent_workflow.agent_system_prompt",
value="...full persona prompt...")`

Also set `default_fallback_subagent_id` once you have declared your
subagents. It MUST exactly match a declared subagent `id`.

**Routing.** NLU slots, act→intent rows and pending questions are
authored by hand in agent_core.yaml for now (see spec §16). Do NOT ask the
user for them and do NOT write `preprocessing.nlu_processor.act_intents`.
Wire routing with these intent names:

- `off_track` — the caller drifted off the subagent's job. Agent Core
  rejects a workflow that has subagents but no routing rule on `off_track`,
  so add one (usually a self-loop, or to the fallback subagent).
- `any_input` / `*` — the catch-all for everything else.
- Any intent already listed under `act_intents` in the cross-phase refs
  below. Use those exact strings, and route every one of them.
{kb_note}{memory_state_note}
**Hard rules:**

- **The ONLY valid tool names are the connector names visible in the
  "Already-set values you can reference" section below.** Look at
  `agent_core.connectors.read`, `connectors.write`, `connectors.identity`,
  `connectors.internal` — those `name` fields are the universe of tool
  names you can use. They are always snake_case (matching pattern
  `^[a-z][a-z0-9_]*$`). Use them VERBATIM in every `subagent.tools`
  list AND in every `subagent.system_prompt` that mentions a tool.
  Do NOT use any name from the prior tools-phase chat — if you
  remember a tool being called e.g. `getWeatherForecast` or
  `bookTour`, those are spec `operationId` values that were
  presented for context only; the registered connector name is the
  snake_case form (`get_weather_forecast`, `book_tour`). The
  cross-block validator rejects any camelCase tool reference at
  deploy with "X is not declared in any connectors.* list", and
  Agent Core would crash with a KeyError at runtime even if the
  validator missed it. {tools_warning}
{empty_connectors_note}
- Every `next_subagent_id` in every routing rule MUST match a declared
  subagent `id`.
- `off_track` is used by at least one routing rule (subagent `routing`
  or `global_routing`) — Agent Core crashes at startup otherwise.
- `opening_phrase` MUST be non-empty for every non-terminal subagent.
- Exactly ONE subagent has `is_start: true`.

**Self-check before advancing:**
1. `agent_system_prompt` is non-empty.
2. Every non-terminal subagent has a non-empty `opening_phrase`.
3. `default_fallback_subagent_id` matches a declared subagent id.
4. Every `next_subagent_id` in every routing rule matches a declared id.
5. `off_track`, and every `act_intents` intent in the refs, is used by
   some routing rule.
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
