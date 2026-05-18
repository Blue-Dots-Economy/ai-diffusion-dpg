"""Phase prompt builder: reach.

Configures the Reach Layer channel adapters — voice (Raya TTS/STT), web UI
branding, and any other selected channels. Part of the dev-kit deterministic
wizard's phase-prompt system.

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
    supported_languages = list(getattr(intake_state, "supported_languages", []) or [])

    voice_note = ""
    if has_voice:
        # Pre-filter Raya voices to only the languages this project actually
        # supports. The LLM has been hallucinating Google/AWS voice IDs (e.g.
        # `en-US-Neural2-C`) when this list isn't injected — fabrication that
        # the mirror schema's RayaVoiceIdField validator then rejects, sending
        # the wizard into a stall loop. Embedding the allowlist directly in
        # the prompt — filtered to project languages so it stays short —
        # removes the model's reason to invent.
        from dev_kit.agent.skeleton import _LANG_CODE_MAP  # noqa: PLC0415
        from dev_kit.schemas.enums import RAYA_VOICES  # noqa: PLC0415

        # Map the project's supported_languages (enum names like "english")
        # to their Raya tags ("en-in"). Keep both forms so the LLM can pick
        # using whichever the user types.
        project_raya_tags = {
            _LANG_CODE_MAP.get(lang.lower())
            for lang in supported_languages
        }
        project_raya_tags.discard(None)
        # Pull the matching voice rows. Also include en-us when en-in is
        # selected, in case the user wants the US accent — both share
        # mostly the same English STT.
        candidate_voices = [
            v for v in RAYA_VOICES
            if v["language"] in project_raya_tags
        ]
        if not candidate_voices:
            # Fallback: surface en-in so the wizard never has zero options.
            candidate_voices = [v for v in RAYA_VOICES if v["language"] == "en-in"]

        voices_table_lines = ["| voice_id | language | name |", "|---|---|---|"]
        for v in candidate_voices:
            voices_table_lines.append(
                f"| `{v['voice_id']}` | `{v['language']}` | {v.get('name', '')} |"
            )
        voices_table = "\n".join(voices_table_lines)

        voice_note = f"""
**Voice channel (Raya TTS/STT):**

Voice uses **Raya** as the only TTS/STT provider — do NOT ask the user which
provider they want. There is no choice. Voice supports **one language at a
time** — the schema's `voice_id_matches_language` validator enforces that
`stt_language`, `tts_language`, and the chosen `voice_id` all belong to the
same single language.

**Allowed Raya voices for this project (filtered by its supported
languages — pick `voice_id` from this table, NEVER invent a value):**

{voices_table}

Steps:
1. Ask: "Voice supports a single language. Which language should the bot
   speak in over voice calls?" Offer ONLY the languages present in the
   table above.
2. From the user's chosen language, pick the `voice_id` + `language` row
   from the table. Set `stt_language` and `tts_language` to that row's
   `language` value (e.g. `en-in`, `hi`, `mr`). Set `voice_id` to the
   exact UUID from the table.
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

**NEVER invent voice IDs.** The mirror's `RayaVoiceIdField` validator
rejects any UUID not in the Raya allowlist above. If you write an
invented ID (e.g. a Google Cloud or AWS Polly voice name) the write
fails and the wizard stalls — the user has no way to fix it.

**Voice — TTS rendering rules (`agent_core.channels.voice.tts_rules`):**

These rules tell the TTS engine how to speak structured data types
naturally. Propose a sensible default block in the project's
`default_language` and let the user confirm:

- `numbers` — how to speak digits (e.g. "Speak as words: 123 → one
  hundred twenty-three")
- `money` — currency rendering (e.g. "Include currency symbol: ₹500 →
  five hundred rupees")
- `dates` — date format (e.g. "Expand to full date: 2025-03-15 → March
  fifteenth, twenty twenty-five")
- `time` — time format (e.g. "Speak in twelve-hour format with AM/PM")
- `phone` — phone-number rendering (e.g. "Spell out digit by digit")
- `abbreviations` — abbreviation expansion (e.g. "Expand common
  abbreviations: USD → US dollar")
- `output_script` — preferred script for non-English text (e.g.
  `Devanagari` for Hindi)
- `english_loanwords` — loanword pronunciation (e.g. "Pronounce naturally
  without transliteration")

Configure via ONE call:
`update_config(block=agent_core, section=channels.voice.tts_rules,
values={{numbers: "...", money: "...", dates: "...", time: "...",
phone: "...", abbreviations: "...", output_script: "...",
english_loanwords: "..."}})`

**Voice — terminal word + filler phrase
(`reach_layer.channels.voice.*`):**

- `terminal_word` — the literal word that signals call end (e.g.
  "Goodbye" in English, "धन्यवाद" in Hindi). REQUIRED for voice.
- `filler_phrase` — short utterance played if the LLM takes >1.5s to
  produce the first sentence (e.g. "One moment please"). Both
  `filler_phrase` and `filler_threshold_ms` are `Optional[X]` with
  `default=None`; the pair `(None, None)` means "no filler". Do NOT
  write an empty string or `0` — the mirror's `min_length=1` /
  `gt=0` validators reject those.
- `filler_threshold_ms` — milliseconds before the filler kicks in
  (default 1500). Must be paired with `filler_phrase`.

Configure via:
`update_config(block=reach_layer, section=channels.voice,
values={{terminal_word: "...", filler_phrase: "...",
filler_threshold_ms: 1500}})`

If the user says "drop the filler" / "no filler" / "remove the
filler phrase", ALWAYS write both fields explicitly to null —
even if you think they may not have been set previously. Do NOT
rely on "just omitting the keys"; an omitted key leaves any prior
value in place, and the user expects removal to actually remove.
Make these TWO calls in the same turn the user confirms:
```
update_config(path="reach_layer.channels.voice.filler_phrase", value=null)
update_config(path="reach_layer.channels.voice.filler_threshold_ms", value=null)
```
"""
    else:
        voice_note = """
**Voice channel:** Not selected — skip all voice-specific configuration
(TTS rules, Raya voice, terminal_word, filler_phrase). Do NOT ask about
them.
"""

    return f"""{_phase_focus_header("reach", pending_fields)}# Phase: Reach

You are configuring the Reach Layer — channel adapters and their domain-
specific settings (voice config, web UI branding).

**Already set on the project-creation form — do NOT ask the user about
these:**
- `selected_channels` = `{selected}`

The user picked these channels on the form before chat began. **NEVER ask
"will it run on web?", "will it run on voice?", "which channels?" or any
variation.** Go straight to configuring each selected channel.

Wrong shape (do NOT produce these — the user already answered both):
- "Will it operate on web (chat interface in a browser or app)?"
- "Will it operate on voice (phone call or voice app)?"
- "Which channels should the bot run on?"

Web is always deployed even if not explicitly listed in
`selected_channels` (every deployment has a web admin surface).

{_common_rules()}

**IMPORTANT — reach_layer.channels must be set for every selected channel.**

Do NOT write `reach_layer.common.observability.domain` — derived field,
auto-computed by the wizard.

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

Fix any missing channel config before stopping.

{_closing_block()}
"""
