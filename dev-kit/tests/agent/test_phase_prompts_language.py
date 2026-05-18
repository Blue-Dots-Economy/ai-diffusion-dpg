"""Tests for dev_kit.agent.phase_prompts.language."""
from __future__ import annotations


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False, is_multi_turn=False,
        needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="en", supported_languages=["en"],
        domain_description="test", project_name="test_project",
    )
    base.update(overrides)
    from dev_kit.agent.intake_state import IntakeState
    return IntakeState(**base)


def _fake_field(path: str, description: str = "A field"):
    from dev_kit.agent.field_rules import FieldRule
    rule = FieldRule(category="chat", phase="language", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.language import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Language" in result


def test_build_contains_field_section():
    result = build([], "", "", _intake())
    assert "## Fields to capture this phase" in result


def test_build_contains_pydantic_schema_section():
    result = build([], "", "", _intake())
    assert "## Pydantic schemas" in result


def test_build_injects_pydantic_schemas_param():
    result = build([], "class FooSection(BaseModel): pass", "", _intake())
    assert "class FooSection(BaseModel): pass" in result


def test_build_injects_cross_phase_refs_param():
    result = build([], "", "preset_value=xyz", _intake())
    assert "preset_value=xyz" in result


def test_build_renders_pending_fields():
    fields = [
        _fake_field("agent_core.agent.primary_model", "Primary LLM model ID"),
        _fake_field("agent_core.preprocessing.nlu_processor.intents", "NLU intent list"),
    ]
    result = build(fields, "", "", _intake())
    assert "agent_core.agent.primary_model" in result
    assert "Primary LLM model ID" in result
    assert "agent_core.preprocessing.nlu_processor.intents" in result
    assert "NLU intent list" in result


def test_language_group2_names_no_default_conversation_messages() -> None:
    """Group 2 must explicitly name conversation messages with no FieldRule
    default so the LLM doesn't forget to write them.

    GoGuide regression: `unsupported_language_message` and the voice-only
    `session_end_eval.prompt` sat at `pending` indefinitely because the
    LLM proposed only the obvious six messages (blocked, escalation,
    output_blocked, unknown_intent, termination, plus consent_* when
    applicable). Naming every required field by path forces the LLM to
    include them in the single batched `update_config` call.
    """
    result = build([], "", "", _intake(selected_channels=["web", "voice"]))

    # The non-obvious field names are listed verbatim so the LLM cannot
    # miss them.
    assert "unsupported_language_message" in result
    assert "session_end_eval" in result
    # And there is explicit guidance on what to write for each.
    assert "Enumerate the\n  supported languages" in result
    assert "session-end" in result.lower() or "session_end_eval.prompt" in result
    # The fix for the GoGuide validation regression: session_end_eval.prompt
    # must be written via the path form (block/section/values would try
    # to write `enabled` and `fail_action` too, and both paths are absent
    # from FIELD_RULES).
    assert 'path="agent_core.conversation.session_end_eval.prompt"' in result


def test_language_prompt_surfaces_project_slug_and_bans_observability_domain_write() -> None:
    """`observability.domain` is a derived field — the language prompt must
    NOT instruct the LLM to write it, AND must still surface the project
    slug in the already-set values block for context (other phases may
    legitimately reference it).

    GoGuide regression: the prompt used to tell the LLM `update_config(...
    observability, values={domain: '<project_slug>'})`. But every block's
    `observability.domain` FIELD_RULE is `category="derived"`, not chat —
    so the LLM's call hit "path is not a chat field" and the turn was
    wasted. The fix removes the write instruction entirely and adds an
    explicit "do not write" guard, while keeping the slug visible in
    the already-set values block.
    """
    result = build(
        [], "", "", _intake(project_name="Go guide", selected_channels=["web"])
    )
    # The hyphenated slug still appears in the already-set values block.
    assert "`project_slug` = `go-guide`" in result
    # But there is NO `update_config(... observability, values={domain: ...})`
    # instruction and NO `domain: 'go-guide'` literal — both would
    # produce a non-chat-field rejection.
    assert "domain: 'go-guide'" not in result
    assert "section=observability" not in result
    # And an explicit "do not write observability.domain" guard is present.
    assert "Do NOT write `agent_core.observability.domain`" in result


def test_language_prompt_delegates_voice_tts_to_reach_phase() -> None:
    """Voice TTS / terminal_word / filler_phrase have `phase="reach"` in
    FIELD_RULES — the language prompt MUST tell the LLM to skip them
    here, not configure them.

    GoGuide regression: the language prompt used to propose a full TTS
    config (numbers, money, dates, phone, etc.) plus terminal_word and
    filler_phrase. The LLM did the work and the user confirmed, but the
    writes failed (`validation_unknown_section` on `tts_rules` written
    at the wrong path) and the reach phase would have re-asked the same
    questions anyway.
    """
    # Voice-selected case — must say "belongs to reach phase" not "configure here".
    result_voice = build([], "", "", _intake(selected_channels=["web", "voice"]))
    assert "belong to the REACH phase" in result_voice
    assert "Do NOT propose" in result_voice
    # The four reach-owned paths are listed verbatim so the LLM can
    # cross-check what NOT to write here.
    assert "`agent_core.channels.voice.tts_rules.*`" in result_voice
    assert "`reach_layer.channels.voice.terminal_word`" in result_voice
    assert "`reach_layer.channels.voice.filler_phrase`" in result_voice

    # Web-only case — also delegates, with the same message.
    result_web = build([], "", "", _intake(selected_channels=["web"]))
    assert "belong to the REACH phase" in result_web


def test_language_multilingual_note():
    result = build([], "", "", _intake(supported_languages=["en", "hi"]))
    assert "Multilingual" in result or "multilingual" in result


def test_language_multilingual_note_writes_one_language_not_all() -> None:
    """The conversation-message schema stores ONE string per field; per-
    language translations get discarded on write. The multilingual note
    must instruct the LLM to write in the default language only and let
    the runtime LLM auto-translate.

    GoGuide regression: the bot generated 5x translations of every
    message because the prior note said "produce translations for ALL
    messages in ALL supported languages" — wasted tokens for the user
    to scroll through, none of which was persisted.
    """
    result = build([], "", "", _intake(
        default_language="english",
        supported_languages=["english", "hindi", "telugu"],
    ))

    # The new instruction must be present.
    assert "ONE string per field" in result
    assert "Write each message in" in result
    # The legacy "produce translations for ALL messages in ALL supported
    # languages" line — the source of the multi-language wall of text —
    # must be gone.
    assert "produce translations for ALL messages" not in result
    assert "in ALL supported languages and present them together" not in result


def test_language_prompt_lists_real_anthropic_models_from_enums() -> None:
    """The model allowlist in the prompt must come from `enums_config.yaml`,
    not from the LLM's training data. Reproduces the GoGuide regression
    where the bot suggested fictional Claude model names after the user
    picked OpenAI.
    """
    from dev_kit.schemas.enums import ANTHROPIC_MODELS

    result = build([], "", "", _intake())
    # At least one real Anthropic ID — and the most recently shipped one
    # specifically — must appear so the LLM has the actual allowlist to
    # propose from.
    for model in ANTHROPIC_MODELS:
        assert model in result, (
            f"Anthropic model {model!r} is in enums_config.yaml but missing "
            "from the rendered language prompt"
        )


def test_language_prompt_lists_real_openai_models_from_enums() -> None:
    """Same guarantee for OpenAI — when the user picks OpenAI, the LLM
    needs the actual `openai_models` list, not made-up names.
    """
    from dev_kit.schemas.enums import OPENAI_MODELS

    result = build([], "", "", _intake())
    for model in OPENAI_MODELS:
        assert model in result, (
            f"OpenAI model {model!r} is in enums_config.yaml but missing "
            "from the rendered language prompt"
        )


def test_language_prompt_asks_provider_before_proposing_models() -> None:
    """Provider is a user preference and must be asked.
    Models are derived from the chosen provider, so they are proposed (not asked).

    GoGuide regression: the bot was proposing both provider AND models in
    one shot ("Provider: Anthropic. Primary: ..."). The user wanted
    provider to be a clean choice with no default leaning.
    """
    result = build([], "", "", _intake())

    # Group 1A markers — explicit ASK pattern for provider.
    assert "Group 1A" in result
    assert "do not propose a default" in result.lower()
    # The provider question is part of the prompt verbatim so the LLM
    # follows the canonical wording.
    assert "Which LLM provider would you like" in result

    # Group 1B markers — propose-defaults pattern for models, gated on
    # provider being known.
    assert "Group 1B" in result
    assert "Once the user picks a provider" in result
    # Suggested defaults block names a concrete primary+fallback per
    # provider — required so the model has something specific to propose.
    assert "Anthropic → primary=" in result
    assert "OpenAI → primary=" in result


def test_language_group4_explains_each_label_before_listing_values() -> None:
    """Every bold label in the worked example must be followed by a one-line
    plain-English explanation on the same line (em-dash separator) so the
    user knows what the thing is before deciding whether to keep the list.

    GoGuide regression: the bot shipped `**signal_intents:**` immediately
    followed by `{"booking_request": "event", ...}` — no explanation of
    what signal_intents are, so the user had no basis to accept or
    override the proposal.
    """
    result = build([], "", "", _intake(needs_persistent_user_data=True))

    # Each of the four bold labels must have the em-dash explanation form
    # `**Label** — explanation:` rather than the bare colon form
    # `**Label:**` straight to values.
    assert "**Intents** — the categories of user request" in result
    assert "**Entities** — the structured values" in result
    assert "**entity_to_profile_field** — the mapping" in result
    assert "**signal_intents** — intents that fire" in result

    # The bare-colon shape that triggered the regression must be flagged
    # as wrong in the prompt's anti-pattern section.
    assert "Never present a label like `**signal_intents:**` and jump straight" in result


def test_language_group4_reply_pattern_uses_markdown_formatting() -> None:
    """The Group 4 worked example must use markdown formatting conventions
    so the LLM has a template to copy.

    GoGuide regression: the bot shipped `Intents: unknown, destination_query,
    package_inquiry, booking_request, ...` — a single comma-separated line.
    The user wanted bold labels, one-per-line bullets, a markdown table for
    the entity-to-profile mapping, and a fenced code block for the JSON
    dict. All of these must appear in the in-prompt example.
    """
    result = build([], "", "", _intake(needs_persistent_user_data=True))

    # The example block starts with a bold "Proposed NLU setup:" line.
    assert "**Proposed NLU setup:**" in result
    # Each sub-list has a bold label with em-dash explanation, then
    # one-per-line bullets. The bold labels appear as
    # `**Intents** — ...` / `**Entities** — ...` etc.
    assert "**Intents** — " in result
    assert "**Entities** — " in result
    # The intent example uses backticked identifiers on separate bullet lines.
    assert "- `unknown`" in result
    assert "- `destination_query`" in result
    # entity_to_profile_field example is a markdown table.
    assert "**entity_to_profile_field** — " in result
    assert "| Entity" in result
    assert "| Profile field |" in result
    # signal_intents example is a fenced JSON code block, not inline prose.
    assert "**signal_intents** — " in result
    assert "```json" in result
    # The anti-pattern (comma-list) is explicitly forbidden in the prompt.
    assert "Never ship comma-separated lists" in result


def test_language_group4_proposes_intents_entities_and_signal_intents() -> None:
    """Group 4 must propose all four lists (intents, entities,
    entity_to_profile_field, signal_intents) in ONE turn — not split into
    three open-ended questions.

    GoGuide regression: the bot asked three separate open-ended questions
    ("are there intents that should write a signal?", "are there entity
    types to extract?", "what profile fields should the bot remember?")
    instead of proposing concrete defaults for the user to confirm.
    """
    result = build([], "", "", _intake(needs_persistent_user_data=True))

    # All four list names appear as proposal targets.
    assert "intents" in result.lower()
    assert "entities" in result.lower()
    assert "entity_to_profile_field" in result
    assert "signal_intents" in result

    # Group 4 must instruct the LLM to propose, not ask open-ended.
    assert "propose" in result.lower()
    # Reply pattern must include the concrete worked example so the LLM
    # has a template to follow (bold heading + markdown structure).
    assert "**Proposed NLU setup:**" in result
    # Must explicitly forbid the open-ended signal_intents ask that the
    # GoGuide bot produced (the phrase is split across a soft wrap in the
    # prompt, so match the distinctive fragment only).
    assert "are there intents that should write a signal?" in result


def test_language_prompt_does_not_ask_for_default_or_supported_languages() -> None:
    """`default_language` and `supported_languages` are captured at project
    creation and stored as `predetermined` in FIELD_RULES. The language
    phase prompt MUST NOT instruct the LLM to ask the user for them
    again — and MUST surface the values from intake as "already set".
    """
    result = build([], "", "", _intake(
        default_language="english",
        supported_languages=["english", "hindi"],
    ))

    # The intake values must surface so the LLM can ground its replies.
    assert "english" in result
    assert "hindi" in result

    # The legacy "Present default_language and supported_languages" copy
    # — the line that caused the GoGuide bot to re-ask both — must be gone.
    assert "Present `primary_model`, `fallback_model`, consent setting, `default_language`" not in result
    assert "and `supported_languages` together" not in result
    # The "already set on the project-creation form" marker must explicitly
    # appear so the rule is impossible to miss when the LLM scans the prompt.
    assert "Already set on the project-creation form" in result
    # The intake values should surface as labelled values, not as questions
    # the LLM has to ask.
    assert "`default_language` = `english`" in result
    assert "`supported_languages` = `['english', 'hindi']`" in result
