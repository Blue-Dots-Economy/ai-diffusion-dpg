"""Phase prompt builder: memory.

Configures Memory Layer session state, persistent graph, user data
persistence mode, and re-engagement triggers. Part of the dev-kit
deterministic wizard's phase-prompt system.

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
**Persistent user data (required for this project) — propose a concrete
profile schema, do NOT ask "what fields do you want to remember?":**

The user said this agent should remember users across sessions. The
storage mode (`memory_layer.user_data_persistence.default_mode = saved`)
is set automatically by the router cascade — do NOT call `update_config`
for it.

1. Propose the persistent profile fields based on the project description.
   Adapt the list to the actual domain — pick fields the bot will
   plausibly *use* on return visits, not every entity the NLU can
   extract. Match each field name to an `entity_to_profile_field` value
   from the previous step so writes line up.

   Apply the markdown formatting rules above. Lead with a bold heading
   AND a one-line plain-English explanation (em-dash separator) so the
   user knows what the table is for. Use a Markdown table with columns
   for Field, Type, and Source/notes, so the user can scan the schema
   in one glance. For a tour-planning bot a typical default is:

       **Proposed persistent profile schema** — the user-profile fields
       the bot will store across sessions and use when the same person
       returns. Each row picks one field, its type, and how it gets
       populated. Adapt the list to your domain and remove anything you
       do not want stored:

       | Field                    | Type           | Source / notes |
       |--------------------------|----------------|----------------|
       | `name`                   | string         | from `traveller_name` entity |
       | `email`                  | string         | from `contact_email` entity |
       | `phone`                  | string         | from `contact_phone` entity |
       | `preferred_destinations` | list[string]   | written when `destination_query` fires the `profile_update` signal |
       | `past_bookings`          | list[string]   | written when `booking_request` completes |

2. Configure the Context Graph node types under `state.persistent`. Lead
   with a one-line explanation of what node types are (the persistent
   graph stores cross-session relationships — `user_node` is the root,
   `child_node` is for one-to-many relations). For most domains a
   single `user_node` is enough; add a `child_node` only if the domain
   has a one-to-many relationship the bot needs to recall (e.g. multiple
   `trip` records per user).

After the table, ONE numbered question asking the user to confirm or
adjust the schema. Do NOT comma-list the fields in prose, and never
present a table without first explaining (in one short sentence) what
the table IS — the user did not write the schema.
"""
    else:
        persistence_note = """
**User data persistence:** The user did not flag a need to remember
people across sessions. The router cascade has already set
`memory_layer.user_data_persistence.default_mode = anonymous` from the
intake flag — do NOT call `update_config` for it. Skip persistent-graph
configuration too.
"""

    memory_states_note = ""
    if is_multi_turn:
        memory_states_note = """
**Multi-turn agents — contact-memory states:**

In a later step you will structure subagents around 5 contact-memory states
(new, sparse, rich, mid-journey, post-application). Define the session
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

    return f"""{_phase_focus_header("memory", pending_fields)}# Phase: Memory

You are configuring the Memory Layer — what the agent remembers across
turns (session scope), across sessions (persistent graph), and what user
profile fields are available at call start.

{_common_rules()}

**Configuration paths:**
- Session schema and TTL: `update_config(block=memory_layer,
  section=state.session, values={{ttl_minutes: ..., schema: {{...}}}})`
- Persistent graph: `section=state.persistent, values={{...}}`
- Re-engagement triggers (if needed): `section=reengagement,
  values={{triggers: [...]}}`

Do NOT write `memory_layer.user_data_persistence.default_mode` — it's a
predetermined field that the router cascade sets from
`needs_persistent_user_data` (`saved` if true, `anonymous` if false).
Do NOT write `memory_layer.observability.domain` — derived, auto-computed.
Both are rejected as non-chat fields.
{persistence_note}{memory_states_note}
**IMPORTANT — fields to avoid in session schema:**
Do NOT propose: `current_intent`, `last_intent`, `current_subagent`,
`previous_subagent`, `turn_count`, `language`, `consent_state`,
`conversation_phase`. These are managed by Agent Core / Memory Layer
infrastructure and are auto-injected. Only declare user-visible domain
state fields (e.g. `location`, `trade`, `selected_scheme`).

**IMPORTANT — session-schema field `type` values are a 4-value enum:**

Allowed type strings (this is the COMPLETE list — anything else is
rejected by the mirror's `SessionFieldType` enum and the write fails):

| Type     | Meaning                                                  |
|----------|----------------------------------------------------------|
| `string` | Free-text values like `selected_destination`, `name`     |
| `int`    | Numbers like `party_size`, `guests` (NOT `integer`)      |
| `enum`   | Fixed allowlist; requires a `values: [...]` field too    |
| `list`   | List of strings, e.g. `preferred_destinations`           |

Do NOT write `type: "integer"`, `type: "number"`, `type: "boolean"`,
`type: "float"`, or any other variant. The mirror enum rejects them
and the field write silently fails — the user sees their proposed
schema land partly (or not at all) in the YAML. Use `int` for any
numeric field. Use `string` for booleans (e.g. `"yes"`/`"no"`) or
free text. Use `list` for collections. Use `enum` only when there's
a closed value set.

**Conversation style:** Present the full memory configuration as ONE block
with suggested defaults based on the use case. Include session schema fields,
TTL, persistent graph node types, and user_data_persistence mode. Ask:
"Here is the suggested memory configuration — do these look good, or would
you like to change any?" Only ask about re-engagement triggers separately if
the agent type requires outbound follow-up.

**Write EVERYTHING the user confirms — proposing is not enough.** The
skeleton pre-fills these fields with empty defaults (`{{}}` for
`schema`, `[]` for `merge_on_session_end`, empty `subnodes`). When you
propose a domain-specific schema and the user says "looks good", the
field is still at the skeleton default and your proposed values will
be silently lost. You MUST call `update_config` for every value you
proposed. Make ALL of these writes in the SAME turn as the user's
confirmation:

```
update_config(path="memory_layer.state.session.schema",
              value={{<proposed session fields>}})
update_config(path="memory_layer.state.session.ttl_minutes",
              value=<int minutes>)
update_config(path="memory_layer.state.persistent.merge_on_session_end",
              value=[<list of session fields that should persist>])
update_config(path="memory_layer.state.persistent.graph.user_node.label",
              value="<typically 'User'>")
update_config(path="memory_layer.state.persistent.graph.user_node.key",
              value="<typically 'user_id'>")
update_config(path="memory_layer.state.persistent.graph.subnodes",
              value={{<map of subnode types if any>}})
```

If the user said "looks good" you write the values you proposed. If
they edited ("drop selected_date, rename group_size to party_size"),
apply the edits in your write — never silently drop or rename what
they didn't ask to change.

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
