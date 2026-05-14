"""Phase prompt builder: language.

Configures LLM provider/models, language normalisation, NLU intents/entities,
conversation messages, and (for voice agents) TTS rules and terminal word.
Part of the dev-kit deterministic wizard's phase-prompt system.

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

    voice_groups = ""
    if has_voice:
        voice_groups = """
**Group 3 — Voice TTS rules, terminal word, and filler (voice only):**

Since voice is in selected_channels, configure:
- TTS rules per data type (numbers, money, dates, time, phone, abbreviations,
  output script, English loanwords) under
  `agent_core.channels.voice.tts_rules`. Draft rules from canonical language
  defaults and offer to generate them automatically.
- `reach_layer.channels.voice.terminal_word` — the literal word that signals
  call end (e.g. "Goodbye", "धन्यवाद"). Required for voice.
- `reach_layer.channels.voice.filler_phrase` — short utterance played if the
  LLM takes >1.5 s to produce the first sentence (e.g. "एक सेकंड", "one
  moment"). Empty string disables.
- `reach_layer.channels.voice.filler_threshold_ms` — default 1500.
"""
    else:
        voice_groups = """
**Voice channel:** Not in selected_channels — skip ALL voice-specific
configuration (TTS rules, terminal_word, filler_phrase). Do NOT ask about
them.
"""

    multilingual_note = ""
    if is_multilingual:
        multilingual_note = f"""
**Multilingual agent detected** (supported languages: {supported_langs}).
When drafting conversation messages, produce translations for ALL messages
in ALL supported languages and present them together — do NOT ask for each
translation one by one.
"""

    return f"""# Phase: Language

You are now setting the agent's LLM provider and models, language
normalisation, NLU classifier, conversation messages, and — for voice agents
— TTS normalisation rules and terminal word.

**Why it matters:** Every downstream phase assumes language and NLU are
wired. The default language captured during intake is `{default_lang}`.
Voice agents are especially sensitive — TTS engines do not reliably speak
raw numbers, dates, or Roman-script text; you must specify rules before
responses reach TTS.
{multilingual_note}
**Group 1A — Provider choice (ASK FIRST, before model IDs):**

Ask: "Which LLM provider do you want — `anthropic` (Claude) or `openai`
(GPT)? Both are supported; the available models and pricing differ."
Wait for the user's answer before proposing any model IDs.

Primary and fallback models MUST be:
- Different from each other.
- From the SAME provider (`models_must_match_provider` validator will
  reject cross-provider configs).
- From the schema's allowed list for that provider (any other ID is
  rejected by `ChatModelField`).

**Group 1B — Models and language setup (after provider is chosen):**

Present `primary_model`, `fallback_model`, consent setting, `default_language`,
and `supported_languages` together. Use the chosen provider's model table
(injected in Pydantic schemas below) to suggest defaults.

Language values MUST come from the schema's `LanguageField` allowlist. Any
other value — language codes (`en`, `hi-IN`) or display names (`English`,
`Hindi`) — will be rejected. If a language the user requests is not in the
list, tell them explicitly and ask how to proceed. NEVER silently drop a
supported language and NEVER silently substitute a different one.

Configure via:
- `update_config(block=agent_core, section=agent, values={{provider: ...,
  primary_model: ..., fallback_model: ..., ask_for_consent: ...,
  consent_prompt: ...}})`
- `section=preprocessing.language_normalisation`
- `section=preprocessing.nlu_processor`
- `section=conversation` (all message keys)
- `section=entity_to_profile_field`
- `section=hitl, values={{response_message: ...}}`
- `section=observability, values={{domain: '<project_slug>'}}`

**IMPORTANT — configure agent_core.channels for EVERY selected channel:**
Agent Core crashes at startup with `ValueError: Unsupported channel` if
`channels.<name>` is absent. This is NOT optional.
- **web** (ALWAYS configure, even if not in selected_channels):
  `update_config(block=agent_core, section=channels.web, values={{...}})`
- **voice** (if in selected_channels):
  `update_config(block=agent_core, section=channels.voice, values={{...}})`

**Group 2 — Conversation messages (all at once):**

Present ALL messages together with domain-appropriate defaults:
`consent_message`, `consent_declined_message`, `termination_message`,
`unknown_intent_message`, `blocked_message`, `escalation_message`,
`blocked_output_message`. Ask: "Do these look good, or would you like to
change any?"
{voice_groups}
**Group 4 — NLU intents and entities:**

Derive intents and entities ENTIRELY from the described use case. Rules:
- Start with ONLY `unknown` as the baseline intent. Do NOT auto-include
  `greeting`, `clarification`, `consent_granted`, or `consent_declined`
  unless the user explicitly asks for them.
- Generate the rest from the agent's described scope and present the full
  proposed list for user sign-off.
- After sign-off, the intent list is FROZEN. Do not add, rename, remove, or
  merge intents in later phases without explicit user approval.
- DPG-specific: `signal_intents` — ask "Are there intents that should write
  a longitudinal signal to the context graph?"

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When models, language normalisation, NLU, conversation messages,
`entity_to_profile_field`, `hitl.response_message`, and (if voice is in
selected_channels) `agent_core.channels.voice.tts_rules` and
`reach_layer.channels.voice.{{terminal_word, filler_phrase,
filler_threshold_ms}}` are all set, the router advances to the knowledge
phase automatically. Do NOT call set_phase.
"""
