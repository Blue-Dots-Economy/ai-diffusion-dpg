"""Phase prompt builder: language.

Configures LLM provider/models, language normalisation, NLU intents/entities,
conversation messages, and (for voice agents) TTS rules and terminal word.
Part of the dev-kit deterministic wizard's phase-prompt system.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import (
    _phase_focus_header,
    _closing_block,
    _common_rules,
    _path_of,
    _render_fields,
    _rule_of,
)
from dev_kit.schemas.enums import (
    ANTHROPIC_MODELS,
    OPENAI_MODELS,
    PROVIDERS,
)


def _project_slug(project_name: str) -> str:
    """Compute the hyphen-separated project slug used by the wizard.

    Mirrors ``app.py:_slugify`` — the same function that names the project
    directory at creation time. We re-implement it here (rather than
    importing from app.py) to keep the prompt module free of FastAPI
    dependencies; the regex is identical. The output matches what ends up
    on disk at ``dev-kit/configs/<slug>/`` so the LLM can use this value
    verbatim for ``observability.domain`` writes.

    Args:
        project_name: Raw project name from intake_state (e.g. "Go guide").

    Returns:
        Hyphen-separated slug (e.g. "go-guide"). Empty string if the input
        contains no slug-safe characters.
    """
    slug = (project_name or "").lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the language phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the language phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine whether voice-
            specific TTS fields apply and to surface the default language.

    Returns:
        A non-empty string to append to the base system prompt for the
        language phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    selected = getattr(intake_state, "selected_channels", ["web"])
    has_voice = "voice" in selected
    default_lang = getattr(intake_state, "default_language", "en")
    supported_langs = getattr(intake_state, "supported_languages", ["en"])
    is_multilingual = len(supported_langs) > 1
    # The project slug (hyphen-separated) is the same string the wizard
    # used to name the project directory on disk. It is also the only
    # acceptable value for `agent_core.observability.domain` — the mirror
    # schema's pattern rejects spaces, capitals, and underscores
    # (`^[a-z][a-z0-9_-]*$`). Inject it literally below so the LLM does
    # not have to guess or compute it.
    project_slug = _project_slug(getattr(intake_state, "project_name", ""))

    # Voice TTS / terminal_word / filler_phrase live in the REACH phase per
    # FIELD_RULES (`phase="reach"` on every entry under
    # `channels.voice.tts_rules.*`, `terminal_word`, `filler_phrase`,
    # `filler_threshold_ms`). The language phase MUST NOT propose or write
    # them — the runtime cascade would still accept the writes, but the
    # router won't mark them answered for the *reach* phase, so the wizard
    # would re-ask the same fields later and the LLM has to redo the work
    # (the GoGuide regression). Tell the LLM explicitly to skip these
    # here.
    voice_groups = """
**Voice TTS rules, terminal word, and filler phrase:**

These fields belong to the REACH phase, not this one. Do NOT propose,
ask about, or call `update_config` for any of the following in the
language phase — they are scheduled for the reach phase and will be
re-asked there, wasting turns if you write them now:

- `agent_core.channels.voice.tts_rules.*`
- `reach_layer.channels.voice.terminal_word`
- `reach_layer.channels.voice.filler_phrase`
- `reach_layer.channels.voice.filler_threshold_ms`

If the user proactively brings up voice TTS during the language phase,
acknowledge briefly ("voice TTS settings come up in a later step") and
move on — do not draft a proposal.
"""

    multilingual_note = ""
    if is_multilingual:
        multilingual_note = f"""
**Multilingual agent (`{default_lang}` default + {len(supported_langs)} supported):**

The conversation-message schema stores ONE string per field (e.g.
`consent_message: str`), NOT a per-language dict. Write each message in
the project's default language only — `{default_lang}`. At runtime, the
agent's primary LLM translates these strings into the user's detected
language on the fly. Do NOT generate or paste translations for every
supported language during this chat: schema-wise they would be discarded
on write (only the `str` value survives), and the output wastes turns
the user has to read through.
"""

    # Render the model lists once so the prompt shows the real, current
    # allowlist instead of leaning on the LLM to recall model names from
    # its training data. The "suggested defaults" below pick the most
    # widely-used model from each provider's allowlist; they are heuristic
    # suggestions, NOT enforced.
    _anthropic_list = "\n".join(f"  - {m}" for m in ANTHROPIC_MODELS)
    _openai_list = "\n".join(f"  - {m}" for m in OPENAI_MODELS)
    _suggested_anthropic_primary = ANTHROPIC_MODELS[1] if len(ANTHROPIC_MODELS) > 1 else ANTHROPIC_MODELS[0]
    _suggested_anthropic_fallback = ANTHROPIC_MODELS[0]
    _suggested_openai_primary = OPENAI_MODELS[-1] if len(OPENAI_MODELS) > 0 else ""
    _suggested_openai_fallback = OPENAI_MODELS[0] if len(OPENAI_MODELS) > 0 else ""

    return f"""{_phase_focus_header("language", pending_fields)}# Phase: Language

You are configuring the agent's LLM provider and models, language
normalisation, NLU classifier, conversation messages, and — for voice
agents — TTS normalisation rules and terminal word.

**Already set on the project-creation form — do NOT ask the user about
these. Just record them via the appropriate `update_config` calls below.**
- `default_language` = `{default_lang}`
- `supported_languages` = `{supported_langs}`
- `selected_channels` = `{selected}`
- `project_slug` = `{project_slug}` (use this verbatim for any
  `observability.domain` write — do not transform it)

The user already picked their channels on the project-creation form.
NEVER ask "will it run on web?", "will it run on voice?", "which
channels?" or any variation — even when configuring `agent_core.channels.*`
below. Use the `selected_channels` value above to decide which
`channels.<name>` sub-blocks to write.

Voice agents are especially sensitive — TTS engines do not reliably speak
raw numbers, dates, or Roman-script text without explicit rules.

{_common_rules()}
{multilingual_note}
**Group 1A — First, ASK which provider (do not propose a default):**

Provider choice is a user preference (cost, in-house standardisation,
pre-existing API keys), so it must be asked rather than proposed. In
your first reply for this phase, ask exactly this — and ONLY this — as
the numbered question:

  1. Which LLM provider would you like — `anthropic` (Claude models) or
     `openai` (GPT models)?

Do NOT also suggest primary/fallback models in the same reply. The
allowlist of models depends on the provider, so models are proposed in
the NEXT turn once the user has chosen.

The full provider list (the ONLY valid values for `agent.provider`):
`{", ".join(PROVIDERS)}`

**Group 1B — Once the user picks a provider, propose models + consent:**

In the turn AFTER the user names a provider:

1. First call `update_config(path="agent_core.agent.provider",
   value=<chosen provider>)` to record their pick.
2. Then propose `primary_model` and `fallback_model` from THAT provider's
   allowlist. Do NOT ask "what primary model?" / "what fallback?" as
   open-ended questions — present a specific pair as the suggested
   defaults and let the user confirm or change either.

Anthropic models (the only valid Anthropic IDs — pick two different ones):
{_anthropic_list}

OpenAI models (the only valid OpenAI IDs — pick two different ones):
{_openai_list}

**Suggested defaults** (use these as your first proposal; the user can
override):

- Anthropic → primary=`{_suggested_anthropic_primary}`, fallback=`{_suggested_anthropic_fallback}`
- OpenAI → primary=`{_suggested_openai_primary}`, fallback=`{_suggested_openai_fallback}`

Reply pattern for Group 1B: bullet the proposed primary + fallback (and
the proposed `consent_prompt` if `needs_consent=true` — a 1–2 sentence
domain-appropriate consent line you draft yourself), then ONE numbered
question asking the user to accept the defaults or change something
specific.

Hard rules on the final `update_config` call:
- `primary_model` and `fallback_model` MUST differ.
- Both MUST come from the SAME provider's list (cross-provider configs
  are rejected by the `models_must_match_provider` validator).
- Any model ID not in the lists above is rejected by `ChatModelField`,
  wasting the turn. Never invent IDs.

Configure via:
- `update_config(block=agent_core, section=agent, values={{provider: ...,
  primary_model: ..., fallback_model: ..., consent_prompt: ...}})`
  (Skip `consent_prompt` if `needs_consent=false`. `ask_for_consent` is
  set automatically by the router; do not write it yourself.)
- `update_config(block=agent_core,
  section=preprocessing.language_normalisation, values={{provider: ...,
  model: ...}})` — only if you want to override the language-normalisation
  provider/model away from the defaults. `default_language` and
  `supported_languages` under this section are ALREADY set by the router
  from the project form — never include them in `values`.
- `update_config(block=agent_core, section=preprocessing.nlu_processor,
  values={{provider: ..., model: ..., domain_instruction: ...,
  intents: [...], entities: [...]}})` — see Group 4 below for intents.
- `update_config(block=agent_core, section=conversation, values={{...}})`
  — see Group 2 below.
- `update_config(path="agent_core.entity_to_profile_field",
  value={{...}})` — **PATH FORM IS MANDATORY HERE.** The
  `entity_to_profile_field` section is an open dict; FIELD_RULES has a
  single entry at the section root (`agent_core.entity_to_profile_field`).
  The block/section/values form would decompose each key into a separate
  path like `agent_core.entity_to_profile_field.contact_phone`, none of
  which are registered in FIELD_RULES, and every call would be rejected
  with "unknown path".
- `update_config(block=agent_core, section=hitl, values={{response_message: ...}})`
  — only if `has_hitl=true`.

Do NOT write `agent_core.observability.domain`. It is a derived field
that the wizard computes automatically from the project slug — every
`update_config` to that path is rejected as a non-chat field.

**IMPORTANT — configure agent_core.channels for EVERY selected channel:**
Agent Core crashes at startup with `ValueError: Unsupported channel` if
`channels.<name>` is absent. This is NOT optional.
- **web** (ALWAYS configure, even if not in selected_channels):
  `update_config(block=agent_core, section=channels.web, values={{...}})`
- **voice** (if in selected_channels):
  `update_config(block=agent_core, section=channels.voice, values={{...}})`

**Group 2 — Conversation messages (all at once):**

Present ALL messages together with domain-appropriate defaults and write
them in ONE `update_config` call. Every field below is required by the
mirror schema — leaving any out keeps the language phase stuck on
`pending` and the router will not advance.

Required (always written; the schema's `min_length=1` will reject empty):

- `blocked_message`
- `escalation_message`
- `output_blocked_message`
- `unknown_intent_message`
- `termination_message`
- `unsupported_language_message` — what the bot says when the user
  switches to a language outside `supported_languages`. Enumerate the
  supported languages in plain English (e.g. "I currently support
  English, Telugu, and Hindi. Please switch to one of these to
  continue.").

Conditional (write ONLY if the flag below is true; otherwise omit):

- `consent_message` — required if `needs_consent=true`. The intake-time
  prompt before collecting personal data.
- `consent_decline_ack` — required if `needs_consent=true`. What the bot
  says if the user declines consent.
- `profile_complete_message` — required if `needs_consent=true`. What
  the bot says once the profile is filled.
- `returning_user_greeting` — required if `needs_persistent_user_data=true`.

Voice-only sub-field (write only if `"voice"` is in `selected_channels`):

- `session_end_eval.prompt` — the only user-configurable field in the
  `session_end_eval` sub-block. `enabled` is set automatically by the
  router cascade from `selected_channels`; `fail_action` is not exposed
  to the wizard. Write the prompt via **path form**:
  `update_config(path="agent_core.conversation.session_end_eval.prompt",
  value="<one-line classifier prompt>")`. The block/section/values form
  would attempt to write `enabled` and `fail_action` too, and both
  paths are absent from FIELD_RULES so the call would be rejected.
  A safe default for any domain: "Did the user complete what they came
  for in this session? Respond with one of: completed, abandoned,
  escalated, unclear." Adapt to the project's actual outcomes.

Present the whole conversation-messages block once for confirmation,
then call `update_config(block=agent_core, section=conversation,
values={{...}})` ONCE for all the messages above, and (if voice) one
extra path-form call for `session_end_eval.prompt`. Ask: "Do these look
good, or would you like to change any?"
{voice_groups}
**Group 4 — NLU intents, entities, entity→profile map, and signal_intents
(one turn, propose everything together):**

Derive ALL four lists ENTIRELY from the described use case (`domain_description`
above) and present them as a single labelled block — do NOT ask the user
"what intents do you want?" or "what entities should we extract?" with no
suggestion. Propose concrete values; the user only types if they want to
change something specific.

Rules for the proposal:

- **intents** — 6–10 intent names in snake_case. Start with `unknown` (the
  baseline for unrecognised input — required). Do NOT auto-include
  `greeting`, `clarification`, `consent_granted`, or `consent_declined`
  unless the user explicitly asks for them. Generate the rest from the
  project's scope (e.g. for a tour-planning bot: `destination_query`,
  `package_inquiry`, `booking_request`, `weather_check`,
  `escalation_request`). After the user signs off, the intent list is
  FROZEN — do not add, rename, remove, or merge intents in later phases
  without explicit user approval.
- **entities** — 4–8 entities the bot will need to extract from user
  messages in snake_case. Derive from the domain (e.g. for a tour bot:
  `destination`, `date_range`, `budget`, `group_size`, `contact_phone`,
  `contact_email`, `traveller_name`).
- **entity_to_profile_field** — a `dict[str, str]` mapping each
  user-data entity name to the profile-field key where it should be
  stored (e.g. `{{"contact_phone": "phone", "contact_email": "email",
  "traveller_name": "name"}}`). Skip the mapping for transient entities
  like `date_range` or `budget` that do not belong in a persistent
  profile.
- **signal_intents** — a `dict[str, str]` mapping each intent that
  represents a meaningful user action to a signal type. Use `"event"`
  for one-off interactions (e.g. `booking_request: event`) and
  `"profile_update"` for intents that change persistent state (e.g.
  `destination_query: profile_update` if the bot should remember the
  user's interest). Skip transient/utility intents (`unknown`,
  `clarification`, `escalation_request`).

Reply pattern for Group 4 — ONE turn, bulleted proposals + ONE numbered
question. Apply the markdown formatting rules from the "Strict reply
rules" block above (bold labels, one-per-line bullets for lists, table
for the two-column mapping, fenced code block for the JSON dict, and
backticks around every identifier). Critically: each bold label that
names a schema concept gets a ONE-LINE plain-English explanation on
the same line (em-dash separator) so the user can decide what to keep
without having to read the schema.

```
**Proposed NLU setup:**

**Intents** — the categories of user request the NLU classifier learns
to recognise (one intent per user message, used to pick the right
subagent and to gate knowledge-base lookups):

- `unknown`
- `destination_query`
- `package_inquiry`
- `booking_request`
- `weather_check`
- `escalation_request`

**Entities** — the structured values the NLU extracts from each user
message (e.g. a destination name, a date, a phone number). Extracted
entities are written into session state and used by tools/connectors:

- `destination`
- `date_range`
- `budget`
- `group_size`
- `traveller_name`
- `contact_phone`
- `contact_email`

**entity_to_profile_field** — the mapping from each extracted entity to
the persistent user-profile field where its value should be stored on
write. Transient entities (dates, budget) are omitted because they do
not belong in a long-lived profile:

| Entity            | Profile field |
|-------------------|---------------|
| `traveller_name`  | `name`        |
| `contact_phone`   | `phone`       |
| `contact_email`   | `email`       |

**signal_intents** — intents that fire a longitudinal write to the
context graph when they occur. Use `event` for one-off actions
(e.g. a booking) and `profile_update` for intents that should
remember a preference across sessions. Skip transient/utility intents
like `unknown` or `escalation_request`:

```json
{{
  "booking_request": "event",
  "destination_query": "profile_update",
  "package_inquiry": "event"
}}
```

1. Does this look right, or would you like to add, remove, or rename anything?
```

Never ship comma-separated lists like `Intents: unknown, destination_query,
booking_request` — they are unscannable on the dark chat UI. Never ask
"are there intents that should write a signal?" as a separate
open-ended question — propose the map yourself and let the user adjust.
Never present a label like `**signal_intents:**` and jump straight to
the values without the one-liner explanation — the user does not know
what signal_intents is and cannot meaningfully accept or override an
unexplained list.

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
