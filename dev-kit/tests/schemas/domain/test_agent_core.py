"""Tests for agent_core domain schemas (the largest, most cross-field rules)."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.agent_core import (
    AgentSection,
    AgentWorkflowSection,
    ChannelEntry,
    ChannelsSection,
    ConnectorDef,
    ConnectorsSection,
    ConversationSection,
    EntityToProfileFieldSection,
    FeaturesSection,
    HitlSection,
    InternalConnectorDef,
    InvocationRules,
    InvocationSafety,
    LanguageNormalisationSection,
    NLUProcessorSection,
    ObservabilitySection,
    PreprocessingSection,
    RoutingCondition,
    RoutingRule,
    SubAgent,
    TtsRulesConfig,
    TurnAssemblerConfig,
    UserStateDefinition,
    UserStateModel,
)
from dev_kit.schemas.enums import ANTHROPIC_MODELS, OPENAI_MODELS, GOOGLE_MODELS


# -- FeaturesSection ---------------------------------------------------------

def test_features_section_all_none_default():
    f = FeaturesSection()
    assert f.prompt_cache is None
    assert f.streaming is None
    assert f.image_input is None


def test_features_section_extra_forbidden():
    with pytest.raises(ValidationError):
        FeaturesSection(unknown_field=True)


# -- AgentSection ------------------------------------------------------------

_ANTHROPIC_PRIMARY = "claude-sonnet-4-6"
_ANTHROPIC_FALLBACK = "claude-haiku-4-5-20251001"
_OPENAI_PRIMARY = "gpt-4o-2024-08-06"
_OPENAI_FALLBACK = "gpt-4.1-2025-04-14"
_OLLAMA_PRIMARY = "llama3.1"
_OLLAMA_FALLBACK = "llama3"
_GOOGLE_PRIMARY = "gemini-3.5-flash"
_GOOGLE_FALLBACK = "gemini-3.5-flash-lite"


def test_agent_section_minimal():
    a = AgentSection(primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK)
    assert a.provider == "anthropic"
    assert a.max_tool_rounds == 3


def test_agent_section_primary_fallback_must_differ():
    """Primary and fallback models must be different.

    The fallback exists to handle primary failures; using the same model defeats
    the purpose. This validator enforces design intent.
    """
    with pytest.raises(ValidationError) as exc_info:
        AgentSection(primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_PRIMARY)
    assert "must be different" in str(exc_info.value)


def test_agent_section_max_tool_rounds_min_1():
    """Critical: runtime crashes on max_tool_rounds=0."""
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
            max_tool_rounds=0,
        )


def test_agent_section_max_tool_rounds_max_20():
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
            max_tool_rounds=21,
        )


def test_agent_section_invalid_model():
    """ChatModelField rejects non-config models."""
    with pytest.raises(ValidationError):
        AgentSection(primary_model="claude-3-5-sonnet", fallback_model=_ANTHROPIC_FALLBACK)


def test_agent_section_models_must_match_provider_anthropic():
    """provider=anthropic + openai model → reject."""
    with pytest.raises(ValidationError, match="not valid for provider"):
        AgentSection(provider="anthropic", primary_model=_OPENAI_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK)


def test_agent_section_models_must_match_provider_openai():
    """provider=openai + anthropic model → reject."""
    with pytest.raises(ValidationError, match="not valid for provider"):
        AgentSection(provider="openai", primary_model=_OPENAI_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK)


def test_agent_section_openai_pair_valid():
    """provider=openai + 2 distinct openai models → valid."""
    a = AgentSection(provider="openai", primary_model=_OPENAI_PRIMARY, fallback_model=_OPENAI_FALLBACK)
    assert a.provider == "openai"


def test_agent_section_models_must_match_provider_ollama():
    """provider=ollama + anthropic model → reject."""
    with pytest.raises(ValidationError, match="not valid for provider"):
        AgentSection(provider="ollama", primary_model=_ANTHROPIC_PRIMARY, fallback_model=_OLLAMA_FALLBACK)


def test_agent_section_ollama_pair_valid():
    """provider=ollama + 2 distinct ollama models → valid."""
    a = AgentSection(provider="ollama", primary_model=_OLLAMA_PRIMARY, fallback_model=_OLLAMA_FALLBACK)
    assert a.provider == "ollama"


def test_agent_section_models_must_match_provider_google():
    """provider=google + anthropic model → reject."""
    with pytest.raises(ValidationError, match="not valid for provider"):
        AgentSection(provider="google", primary_model=_ANTHROPIC_PRIMARY, fallback_model=_GOOGLE_FALLBACK)


def test_agent_section_google_pair_valid():
    """provider=google + distinct google models → valid."""
    a = AgentSection(provider="google", primary_model=_GOOGLE_PRIMARY, fallback_model=_GOOGLE_FALLBACK)
    assert a.provider == "google"


def test_agent_section_features_default():
    a = AgentSection(primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK)
    assert isinstance(a.features, FeaturesSection)


def test_agent_section_features_null_coercion():
    """YAML's empty `features:` parses as None — must coerce to default FeaturesSection."""
    a = AgentSection(
        primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
        features=None,
    )
    assert isinstance(a.features, FeaturesSection)
    assert a.features.prompt_cache is None


def test_agent_section_features_explicit():
    a = AgentSection(
        primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
        features={"prompt_cache": True, "streaming": False},
    )
    assert a.features.prompt_cache is True
    assert a.features.streaming is False


def test_agent_section_extra_forbidden():
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
            unknown_field="x",
        )


# -- LanguageNormalisationSection --------------------------------------------

def _lang_norm_kwargs(**overrides):
    base = dict(
        model=_ANTHROPIC_PRIMARY,
        default_language="english",
        supported_languages=["english"],
    )
    base.update(overrides)
    return base


def test_language_normalisation_minimal():
    n = LanguageNormalisationSection(**_lang_norm_kwargs())
    assert n.provider is None  # inherit at runtime


def test_language_normalisation_supported_languages_min_1():
    with pytest.raises(ValidationError):
        LanguageNormalisationSection(**_lang_norm_kwargs(supported_languages=[]))


def test_language_normalisation_provider_inherits():
    """provider=None → no per-helper validation, model just needs to be in ALL_CHAT_MODELS."""
    LanguageNormalisationSection(**_lang_norm_kwargs(provider=None, model=_OPENAI_PRIMARY))


def test_language_normalisation_provider_anthropic_with_openai_model_rejected():
    with pytest.raises(ValidationError, match="not valid for provider"):
        LanguageNormalisationSection(**_lang_norm_kwargs(provider="anthropic", model=_OPENAI_PRIMARY))


def test_language_normalisation_provider_openai_with_anthropic_model_rejected():
    with pytest.raises(ValidationError, match="not valid for provider"):
        LanguageNormalisationSection(**_lang_norm_kwargs(provider="openai", model=_ANTHROPIC_PRIMARY))


# -- NLUProcessorSection -----------------------------------------------------

def test_nlu_processor_minimal():
    n = NLUProcessorSection(model=_ANTHROPIC_PRIMARY)
    assert n.user_state_confidence_threshold == 0.4
    assert n.slots == {} and n.act_intents == []


def test_nlu_processor_user_state_confidence_threshold_range():
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, user_state_confidence_threshold=0.0)
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, user_state_confidence_threshold=1.0)
    with pytest.raises(ValidationError):
        NLUProcessorSection(model=_ANTHROPIC_PRIMARY, user_state_confidence_threshold=1.1)


def test_nlu_processor_provider_validation():
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, provider="anthropic")
    with pytest.raises(ValidationError, match="not valid for provider"):
        NLUProcessorSection(model=_OPENAI_PRIMARY, provider="anthropic")


# -- PreprocessingSection ----------------------------------------------------

def test_preprocessing_section_full():
    p = PreprocessingSection(
        language_normalisation=LanguageNormalisationSection(**_lang_norm_kwargs()),
        nlu_processor=NLUProcessorSection(model=_ANTHROPIC_PRIMARY),
    )
    assert p.nlu_processor.user_state_confidence_threshold == 0.4


def test_preprocessing_section_extra_forbidden():
    with pytest.raises(ValidationError):
        PreprocessingSection(
            language_normalisation=LanguageNormalisationSection(**_lang_norm_kwargs()),
            nlu_processor=NLUProcessorSection(model=_ANTHROPIC_PRIMARY),
            unknown="x",
        )


# -- ConversationSection -----------------------------------------------------

def _conv_kwargs(**overrides):
    base = dict(
        blocked_message="blocked",
        escalation_message="escalating",
        output_blocked_message="output blocked",
    )
    base.update(overrides)
    return base


def test_conversation_minimal():
    c = ConversationSection(**_conv_kwargs())
    assert c.unknown_intent_message == ""


def test_conversation_required_messages_min_1():
    with pytest.raises(ValidationError):
        ConversationSection(**_conv_kwargs(blocked_message=""))
    with pytest.raises(ValidationError):
        ConversationSection(**_conv_kwargs(escalation_message=""))
    with pytest.raises(ValidationError):
        ConversationSection(**_conv_kwargs(output_blocked_message=""))


# -- UserStateModel ----------------------------------------------------------

def test_user_state_model_disabled_default():
    m = UserStateModel()
    assert m.enabled is False


def test_user_state_model_default_must_be_in_states():
    with pytest.raises(ValidationError, match="default_state"):
        UserStateModel(
            enabled=True,
            default_state="ghost",
            states=[UserStateDefinition(id="real")],
        )


def test_user_state_model_disabled_skips_check():
    """When disabled, default_state can be empty without error."""
    UserStateModel(enabled=False, default_state="", states=[])


def test_user_state_model_enabled_with_empty_states_accepted_as_partial_draft():
    """Chat-time partial drafts: (enabled=True, states=[]) must NOT raise.

    The dev-kit's predetermined cascade flips `enabled=True` on tier
    completion for companion-style agents, but `states` and `default_state`
    are populated only later in the user_state phase. The original
    validator fired during that gap, rejecting every `update_config` write
    to `conversation.*` (consent_message, blocked_message, etc.) in
    language/memory/trust phases — exactly the regression that bricked the
    GoGuide chat for ~10 turns. Strict deploy-time enforcement still runs
    against the runtime schema in the pre-deploy dry-run.
    """
    m = UserStateModel(enabled=True, default_state="", states=[])
    assert m.enabled is True
    assert m.states == []
    assert m.default_state == ""


def test_user_state_model_enabled_with_states_still_validates_default():
    """Fully-configured case still rejects an out-of-list default_state."""
    with pytest.raises(ValidationError, match="default_state"):
        UserStateModel(
            enabled=True,
            default_state="ghost",
            states=[UserStateDefinition(id="real")],
        )


# -- TtsRulesConfig ----------------------------------------------------------

def test_tts_rules_includes_email_and_named_entities():
    """Blue Dots has these fields."""
    t = TtsRulesConfig(email="Spell email", named_entities="Speak entities")
    assert t.email == "Spell email"
    assert t.named_entities == "Speak entities"


# -- ChannelsSection ---------------------------------------------------------

def test_channels_section_all_optional():
    c = ChannelsSection()
    assert c.web is None and c.voice is None and c.cli is None


def test_channels_section_with_voice():
    c = ChannelsSection(voice=ChannelEntry(system_prompt_suffix="voice prompt"))
    assert c.voice is not None


# -- InvocationRules ---------------------------------------------------------

def test_invocation_rules_minimal_all_empty_strings():
    """Runtime accepts empty defaults; spec relaxed from min_length=1."""
    r = InvocationRules()
    assert r.call_when == ""
    assert r.must_not_substitute == ""
    assert r.on_empty == ""
    assert r.on_failure == ""


def test_invocation_rules_gh176_fields():
    r = InvocationRules(
        exception_no_call="cannot call when context missing",
        ranking_order=["match_score", "distance"],
        presentation_limit=3,
        refinement_loop_max=2,
        safety=InvocationSafety(never_present=["raw_score"], never_speak=["price_internal"]),
    )
    assert r.presentation_limit == 3
    assert r.safety.never_present == ["raw_score"]


def test_invocation_rules_presentation_limit_must_be_positive():
    with pytest.raises(ValidationError):
        InvocationRules(presentation_limit=0)


# -- ConnectorDef / InternalConnectorDef -------------------------------------

def _connector_def_kwargs(**overrides):
    base = dict(name="api_x", description="desc", invocation_rules=InvocationRules())
    base.update(overrides)
    return base


def test_connector_def_minimal():
    """A connector with no input_schema gets the default InputSchema(type='object').

    Earlier the mirror typed input_schema as a bare `dict[str, Any]` and
    the default was `{}`. The mirror was tightened to use the strict
    `InputSchema` class (mirrors runtime exactly), so the default is now
    an `InputSchema` instance with `type='object'`, empty `properties`,
    and empty `required` — the canonical JSON-Schema "no input" shape
    the runtime accepts at boot.
    """
    c = ConnectorDef(**_connector_def_kwargs())
    assert c.input_schema.type == "object"
    assert c.input_schema.properties == {}
    assert c.input_schema.required == []


def test_internal_connector_default_route():
    c = InternalConnectorDef(**_connector_def_kwargs(name="knowledge_retrieval"))
    assert c.route.value == "knowledge_engine"


def test_internal_connector_invalid_route():
    with pytest.raises(ValidationError):
        InternalConnectorDef(**_connector_def_kwargs(route="bogus"))


# -- RoutingCondition --------------------------------------------------------

def test_routing_condition_typed():
    c = RoutingCondition(field="state", operator="eq", value="ready")
    assert c.operator.value == "eq"


def test_routing_condition_invalid_operator():
    with pytest.raises(ValidationError):
        RoutingCondition(field="x", operator="startswith", value="y")


def test_routing_condition_contains_accepted():
    c = RoutingCondition(field="current_question", operator="contains", value=["a", "b"])
    assert c.operator.value == "contains"


# -- RoutingRule + session_writes scalar validator ---------------------------

def test_routing_rule_minimal():
    r = RoutingRule(intent="greet", next_subagent_id="welcome")
    assert r.session_writes == {}


def test_routing_rule_session_writes_scalars_ok():
    r = RoutingRule(
        intent="x", next_subagent_id="y",
        session_writes={"key1": "string_val", "key2": 42, "key3": True, "key4": 1.5, "key5": None},
    )
    assert r.session_writes["key1"] == "string_val"


def test_routing_rule_session_writes_rejects_dict():
    with pytest.raises(ValidationError, match="scalar"):
        RoutingRule(
            intent="x", next_subagent_id="y",
            session_writes={"nested": {"a": 1}},
        )


def test_routing_rule_session_writes_rejects_list():
    with pytest.raises(ValidationError, match="scalar"):
        RoutingRule(
            intent="x", next_subagent_id="y",
            session_writes={"list_field": ["a", "b"]},
        )


# -- SubAgent ----------------------------------------------------------------

def _subagent_kwargs(**overrides):
    base = dict(
        id="greeting", name="Greeting", system_prompt="Welcome the user.",
        opening_phrase="Hi there!", is_start=False, is_terminal=False,
    )
    base.update(overrides)
    return base


def test_subagent_minimal_valid():
    s = SubAgent(**_subagent_kwargs())
    assert s.opening_phrase == "Hi there!"


def test_subagent_opening_phrase_required_for_terminal_too():
    """Runtime workflow_loader requires opening_phrase for ALL subagents."""
    with pytest.raises(ValidationError):
        SubAgent(**_subagent_kwargs(opening_phrase="", is_terminal=True))


def test_subagent_special_handler_enum():
    SubAgent(**_subagent_kwargs(special_handler="hitl"))
    SubAgent(**_subagent_kwargs(special_handler="whatsapp_handoff"))
    with pytest.raises(ValidationError):
        SubAgent(**_subagent_kwargs(special_handler="bogus"))


# -- AgentWorkflowSection ----------------------------------------------------

def _make_subagent(id="greeting", **kw):
    defaults = dict(
        id=id, name=id.title(), system_prompt="prompt",
        is_start=False, is_terminal=False, opening_phrase="hi",
    )
    defaults.update(kw)
    return SubAgent(**defaults)


def _workflow_kwargs(**overrides):
    base = dict(
        workflow_id="blue_dots_demo",
        version="1.0.0",
        agent_system_prompt="A demo agent for testing the workflow validators.",
        subagents=[_make_subagent(is_start=True)],
        default_fallback_subagent_id="greeting",
    )
    base.update(overrides)
    return base


def test_workflow_minimal_valid():
    w = AgentWorkflowSection(**_workflow_kwargs())
    assert w.workflow_id == "blue_dots_demo"


def test_workflow_workflow_id_pattern():
    with pytest.raises(ValidationError):
        AgentWorkflowSection(**_workflow_kwargs(workflow_id="Has Spaces"))


def test_workflow_version_pattern():
    with pytest.raises(ValidationError):
        AgentWorkflowSection(**_workflow_kwargs(version="not_semver"))


def test_workflow_system_prompt_must_be_nonempty():
    """Runtime accepts any non-empty agent_system_prompt; only "" is rejected."""
    with pytest.raises(ValidationError):
        AgentWorkflowSection(**_workflow_kwargs(agent_system_prompt=""))


def test_workflow_fallback_must_be_declared():
    with pytest.raises(ValidationError, match="default_fallback_subagent_id"):
        AgentWorkflowSection(**_workflow_kwargs(default_fallback_subagent_id="ghost"))


def test_workflow_routing_target_must_be_declared():
    with pytest.raises(ValidationError, match="unknown subagent"):
        AgentWorkflowSection(**_workflow_kwargs(
            subagents=[_make_subagent(
                is_start=True,
                routing=[RoutingRule(intent="next", next_subagent_id="ghost")],
            )],
        ))


def test_workflow_global_routing_target_must_be_declared():
    with pytest.raises(ValidationError, match="unknown subagent"):
        AgentWorkflowSection(**_workflow_kwargs(
            global_routing=[RoutingRule(intent="next", next_subagent_id="ghost")],
        ))


def test_workflow_exactly_one_start_no_starts():
    with pytest.raises(ValidationError, match="is_start"):
        AgentWorkflowSection(**_workflow_kwargs(
            subagents=[_make_subagent(id="a"), _make_subagent(id="b")],
            default_fallback_subagent_id="a",
        ))


def test_workflow_exactly_one_start_two_starts():
    with pytest.raises(ValidationError, match="is_start"):
        AgentWorkflowSection(**_workflow_kwargs(
            subagents=[_make_subagent(id="a", is_start=True), _make_subagent(id="b", is_start=True)],
            default_fallback_subagent_id="a",
        ))


def test_workflow_subagents_min_1():
    with pytest.raises(ValidationError):
        AgentWorkflowSection(**_workflow_kwargs(
            subagents=[],
            default_fallback_subagent_id="greeting",
        ))


# -- HitlSection -------------------------------------------------------------

def test_hitl_section_response_message_required():
    with pytest.raises(ValidationError):
        HitlSection(response_message="")


def test_hitl_section_valid():
    h = HitlSection(response_message="Connecting to agent")
    assert h.response_message == "Connecting to agent"


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_pattern():
    """Accepts both hyphen-separated and underscore-separated slugs.

    GoGuide regression: the pattern used to reject underscores
    (`^[a-z][a-z0-9-]*$`). `derived_fields.slug()` produces underscore-
    separated values, and the LLM occasionally inferred underscored
    slugs from `project_name`, both of which the mirror then rejected.
    The runtime schema has no pattern constraint at all, so a permissive
    `^[a-z][a-z0-9_-]*$` is consistent with runtime AND with the sibling
    `workflow_id` / `collection_name` fields.
    """
    # Both separator styles must round-trip.
    ObservabilitySection(domain="blue-dots")
    ObservabilitySection(domain="employ-voice-bot")
    ObservabilitySection(domain="go-guide")
    ObservabilitySection(domain="go_guide")          # underscore — newly accepted
    ObservabilitySection(domain="employ_voice_bot")  # underscore — newly accepted

    # But genuine junk values are still rejected.
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="UPPERCASE")
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="123_starts_with_num")
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="has spaces")
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="Has-Caps")


# -- EntityToProfileFieldSection ---------------------------------------------

def test_entity_to_profile_field_open_map():
    """This is an open map — accepts arbitrary string mappings."""
    e = EntityToProfileFieldSection(user_name="name", user_location="location", anything_goes="here")
    # extra="allow" — values are just stored
    assert hasattr(e, "user_name") or e.model_extra



class TestTurnAssemblerLifecycleMirror:
    """Mirror of runtime InterruptionConfig / FoldConfig / CarryoverConfig."""

    def test_accepts_valid(self):
        ta = TurnAssemblerConfig.model_validate({
            "interruption": {"on_new_input": "replace", "on_disconnect": "continue",
                             "drain_max_ms": 100},
            "fold": {"max_segments": 2},
            "carryover": {"max_age_ms": 10, "undelivered_note": "n"},
            "session_idle_ttl_ms": 1000,
        })
        assert ta.fold.max_segments == 2

    @pytest.mark.parametrize("payload", [
        {"interruption": {"on_new_input": "explode"}},
        {"fold": {"max_segments": -1}},
        {"carryover": {"enabled": True}},
        {"semantic_gate": {"enabled": False}},
    ])
    def test_rejects_invalid(self, payload):
        with pytest.raises(ValidationError):
            TurnAssemblerConfig.model_validate(payload)


# -- tool-result persistence --------------------------------------------------

def test_connectors_section_accepts_cache_and_invalidates():
    s = ConnectorsSection.model_validate({
        "read": [{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 60, "vary_on": ["trade"]}}],
        "write": [{"name": "apply", "invalidates": ["fetch_jobs"]}],
    })
    assert s.read[0].cache.ttl_seconds == 60
    assert s.write[0].invalidates == ["fetch_jobs"]


@pytest.mark.parametrize("payload", [
    {"write": [{"name": "w", "cache": {"scope": "user", "ttl_seconds": 5}}]},
    {"read": [{"name": "r", "invalidates": ["r"]}]},
    {"write": [{"name": "w", "invalidates": ["ghost"]}]},
    {"internal": [{"name": "i", "cache": {"scope": "user", "ttl_seconds": 5}}]},
    {"internal": [{"name": "i", "invalidates": ["r"]}], "read": [{"name": "r"}]},
    {"read": [{"name": "r", "cache": {"scope": "user", "ttl_seconds": 0}}]},
    {"read": [{"name": "r", "cache": {"scope": "global", "ttl_seconds": 5}}]},
])
def test_connectors_section_rejects_bad_cache_rules(payload):
    with pytest.raises(ValidationError):
        ConnectorsSection.model_validate(payload)


def test_tool_results_and_memory_tool_sections():
    from dev_kit.schemas.domain.agent_core import MemoryToolSection, ToolResultsSection
    assert ToolResultsSection().max_user_ttl_seconds == 86400
    m = MemoryToolSection.model_validate({"fields": {"x": {"scope": "session", "grounded_in": ["a"]}}})
    assert m.name == "remember"
    with pytest.raises(ValidationError):
        MemoryToolSection.model_validate({"fields": {}})
    with pytest.raises(ValidationError):
        ToolResultsSection.model_validate({"max_user_ttl_seconds": 0})
    with pytest.raises(ValidationError):
        ToolResultsSection.model_validate({"bogus": 1})


def test_validation_registry_has_tool_result_sections():
    from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS
    assert ("agent_core", "tool_results") in DOMAIN_SECTION_SCHEMAS
    assert ("agent_core", "memory_tool") in DOMAIN_SECTION_SCHEMAS


# -- session bootstrap ---------------------------------------------------------

def test_session_bootstrap_and_prompt_session_fields_accepted():
    from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS
    a = AgentSection(primary_model=_ANTHROPIC_PRIMARY, fallback_model=_ANTHROPIC_FALLBACK,
                     prompt_session_fields=["profile_item_id"])
    assert a.prompt_session_fields == ["profile_item_id"]
    schema = DOMAIN_SECTION_SCHEMAS[("agent_core", "session_bootstrap")]
    s = schema.model_validate({"steps": [{"type": "tool", "tool": "fetch_profile", "args": {"a": 1}}]})
    assert s.timeout_ms == 1500
    assert s.steps[0].requires_consent is False


@pytest.mark.parametrize("payload, match", [
    ({"steps": [{"type": "set", "tool": "t"}]}, "Input should be 'tool'"),
    ({"steps": []}, "List should have at least 1 item"),
    ({"timeout_ms": 0, "steps": [{"type": "tool", "tool": "t"}]}, "greater than 0"),
    ({"steps": [{"type": "tool", "tool": ""}]}, "at least 1 character"),
    ({"steps": [{"type": "tool", "tool": "t", "bogus": 1}]}, "Extra inputs are not permitted"),
])
def test_session_bootstrap_rejects_bad_shapes(payload, match):
    from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS
    with pytest.raises(ValidationError, match=match):
        DOMAIN_SECTION_SCHEMAS[("agent_core", "session_bootstrap")].model_validate(payload)


def test_nlu_section_accepts_dialogue_act_blocks():
    s = NLUProcessorSection(slots={"age": {"type": "int", "min": 14, "max": 80}},
                            act_intents=[{"acts": ["affirm"], "intent": "apply_now"}])
    assert s.slots["age"].max == 80 and s.act_intents[0].intent == "apply_now"


def test_nlu_section_rejects_unknown_act():
    with pytest.raises(ValidationError, match="unknown act"):
        NLUProcessorSection(act_intents=[{"acts": ["shout"], "intent": "x"}])


def test_subagent_accepts_pending():
    sa = SubAgent(id="job_match", name="Job match", system_prompt="p", opening_phrase="o",
                  pending=[{"id": "select_job", "expects": "one of the jobs",
                            "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"},
                            "resolves_to": "selected_job_item_id"}])
    assert sa.pending[0].options_from.id_field == "item_id"


# -- Removed legacy NLU keys (NLU single-mode, spec §16) --------------------

@pytest.mark.parametrize("key, value", [
    ("mode", "dialogue_act"), ("intents", ["greet"]), ("entities", ["name"]),
    ("domain_instruction", "x"), ("confidence_threshold", 0.5), ("sentiment_classes", ["neutral"]),
])
def test_nlu_section_rejects_removed_key(key, value):
    with pytest.raises(ValidationError, match=key):
        NLUProcessorSection(**{key: value})


def test_subagent_rejects_valid_intents():
    with pytest.raises(ValidationError, match="valid_intents"):
        _make_subagent(is_start=True, valid_intents=["help"])


def test_workflow_rejects_global_intents():
    with pytest.raises(ValidationError, match="global_intents"):
        AgentWorkflowSection(**_workflow_kwargs(global_intents=["help"]))


# -- IdentitySection / HandoffSection ----------------------------------------

_IDENT = {"name": "ब्लू डॉट्स सहायक", "operator": "Blue Dots",
          "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "no_handoff_line": "अभी कोई इंसान उपलब्ध नहीं है।"}
_LINES = {"lines": {"delivered": "d", "failed": "f", "already": "a"}}


def test_identity_section_accepts_valid_and_defaults():
    from dev_kit.schemas.domain.agent_core import IdentitySection
    s = IdentitySection(**_IDENT)
    assert s.human_handoff == "none" and s.kind == "ai_assistant"


def test_identity_section_rejects_empty_disclosure():
    from dev_kit.schemas.domain.agent_core import IdentitySection
    with pytest.raises(ValidationError):
        IdentitySection(**{**_IDENT, "disclosure": ""})


def test_handoff_section_accepts_valid_and_bounds():
    from dev_kit.schemas.domain.agent_core import HandoffSection
    assert HandoffSection(**_LINES).summary_turns == 6
    with pytest.raises(ValidationError):
        HandoffSection(**{**_LINES, "summary_turns": 21})
