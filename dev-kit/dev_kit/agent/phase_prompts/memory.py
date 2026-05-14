"""Phase prompt builder: memory.

Configures Memory Layer session state, persistent graph, user data
persistence mode, and re-engagement triggers. Part of the dev-kit
deterministic wizard's phase-prompt system.

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
    """Build the memory phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the memory phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine persistence
            requirements based on needs_persistent_user_data.

    Returns:
        A non-empty string to append to the base system prompt for the memory
        phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    needs_persistent = getattr(intake_state, "needs_persistent_user_data", False)
    is_multi_turn = getattr(intake_state, "is_multi_turn", False)

    persistence_note = ""
    if needs_persistent:
        persistence_note = """
**Persistent user data (required for this project):**

This agent needs to remember users across sessions (`needs_persistent_user_data=true`).
Set `default_mode: saved` in `user_data_persistence`:
`update_config(block=memory_layer, section=user_data_persistence,
values={{default_mode: saved}})`

Also configure the persistent graph node types (`state.persistent`) so the
Context Graph in Memgraph can store cross-session user attributes.
"""
    else:
        persistence_note = """
**User data persistence:** This project does not require cross-session memory.
Set `default_mode: anonymous` in `user_data_persistence`.
"""

    memory_states_note = ""
    if is_multi_turn:
        memory_states_note = """
**Multi-turn agents — contact-memory states:**

In the workflow phase you will structure subagents around 5 contact-memory
states (new, sparse, rich, mid-journey, post-application). Define the session
schema fields here that will populate those states. For example:
- `location` → populates `sparse` state
- `trade` or `occupation` → populates `rich` state
- `selected_option` → populates `mid-journey` state
- `last_action` → populates `post-application` state

Do NOT declare fields that the DPG manages internally (current intent,
last intent, current/previous subagent, turn count, language, consent state,
conversation phase) — they are auto-injected and must NEVER appear in
`state.session.schema`.
"""

    return f"""# Phase: Memory

You are now configuring the Memory Layer — what the agent remembers across
turns (session scope), across sessions (persistent graph), and what user
profile fields are available at call start.

**Configuration paths:**
- Session schema and TTL: `update_config(block=memory_layer,
  section=state.session, values={{ttl_minutes: ..., schema: {{...}}}})`
- Persistent graph: `section=state.persistent, values={{...}}`
- Storage mode: `section=user_data_persistence,
  values={{default_mode: saved|anonymous}}`
- Re-engagement triggers (if needed): `section=reengagement,
  values={{triggers: [...]}}`
- Observability domain: `update_config(block=memory_layer,
  section=observability, values={{domain: '<project_slug>'}})`.
  Use `section=observability` NOT `section=observability.domain` — the
  latter double-nests and crashes memory_layer at startup.
{persistence_note}{memory_states_note}
**IMPORTANT — fields to avoid in session schema:**
Do NOT propose: `current_intent`, `last_intent`, `current_subagent`,
`previous_subagent`, `turn_count`, `language`, `consent_state`,
`conversation_phase`. These are managed by Agent Core / Memory Layer
infrastructure and are auto-injected. Only declare user-visible domain
state fields (e.g. `location`, `trade`, `selected_scheme`).

**Conversation style:** Present the full memory configuration as ONE block
with suggested defaults based on the use case. Include session schema fields,
TTL, persistent graph node types, and user_data_persistence mode. Ask:
"Here is the suggested memory configuration — do these look good, or would
you like to change any?" Only ask about re-engagement triggers separately if
the agent type requires outbound follow-up.

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When session schema, persistent graph, user_data_persistence, and
reengagement (if needed) are set, the router advances to the user_state
phase automatically. Do NOT call set_phase.
"""
