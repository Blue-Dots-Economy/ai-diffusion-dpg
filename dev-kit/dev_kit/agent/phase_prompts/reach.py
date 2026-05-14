"""Phase prompt builder: reach.

Configures the Reach Layer channel adapters — voice (Raya TTS/STT), web UI
branding, and any other selected channels.

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
    """Build the reach phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the reach phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine which channels
            need configuration.

    Returns:
        A non-empty string to append to the base system prompt for the reach
        phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    selected = getattr(intake_state, "selected_channels", ["web"])
    has_voice = "voice" in selected

    voice_note = ""
    if has_voice:
        voice_note = """
**Voice channel (Raya TTS/STT):**

Voice uses **Raya** as the only TTS/STT provider — do NOT ask the user which
provider they want. There is no choice. Voice supports **one language at a
time** — the schema's `voice_id_matches_language` validator enforces that
`stt_language`, `tts_language`, and the chosen `voice_id` all belong to the
same single language.

Steps:
1. Ask: "Voice supports a single language. Which language should the bot
   speak in over voice calls?" Show the available Raya languages. Do NOT
   offer multi-language voice — it is not supported.
2. Auto-select the matching `voice_id`, `stt_language`, and `tts_language`
   from the Raya voice table.
3. Present the full voice config block with defaults:
   - `timeout_ms`: 15000
   - `fallback_phrase`: Suggest a domain-appropriate phrase in the target language
   - `barge_in_acknowledgement`: empty string (silent)
4. Ask for confirmation.

Configure via:
`update_config(block=reach_layer, section=channels.voice,
values={{raya: {{stt_language: ..., tts_language: ..., voice_id: ...}},
agent_core: {{timeout_ms: 15000, fallback_phrase: ...,
barge_in_acknowledgement: ''}}}})`

**NEVER invent voice IDs.** Schema validation will reject any ID not in the
Raya voice table.
"""
    else:
        voice_note = """
**Voice channel:** Not selected — skip all voice-specific configuration.
"""

    return f"""# Phase: Reach

You are now configuring the Reach Layer. This phase declares channel adapters
and their domain-specific settings (voice config, web UI branding).

Channel selection was already done during the tier intake chat (selected
channels: {selected}). Do NOT ask which channels to deploy — go straight to
configuring each selected channel.

**Channels to configure: {selected}** (web is always deployed even if not
explicitly listed).

**IMPORTANT — reach_layer.channels must be set for every selected channel.**
Also set the observability domain tag first:
`update_config(block=reach_layer,
section=reach_layer.common.observability,
values={{domain: '<project_slug>'}})`
Note: the section path is `reach_layer.common.observability` (not
`observability` or `observability.domain`).

**Web channel (always required):**

Present ALL web UI branding fields together — do NOT ask about app_name,
then icon, then tagline one by one:

- `app_name` — from the project name
- `app_tagline` — from the project description
- `app_icon` — domain-appropriate emoji
- `agent_avatar` and `user_avatar`
- Setup screen: heading, subtitle, placeholder, hint, button label
- Session messages: `new_session_msg`, `returning_user_msg`
- Confirmation dialogs

Web auth (Google login) is pre-configured in the DPG defaults and does NOT
need to be set per-project. Do NOT set `auth.enabled`, `google_client_id`,
or `cookie_secure`.

Ask: "Here is the suggested web UI configuration — do these look good, or
would you like to change any?"
{voice_note}

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

**Self-check before advancing:**
1. `agent_core.channels.web` is configured (always required).
2. `agent_core.channels.<X>` is configured for every channel in
   selected_channels.
3. `reach_layer.channels.<X>` is non-null and has domain-specific fields set
   for every channel in selected_channels.

Fix any missing channel config. When all selected channels are configured,
the router advances to the review phase automatically. Do NOT call set_phase.
"""
