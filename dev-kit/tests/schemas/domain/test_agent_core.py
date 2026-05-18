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
from dev_kit.schemas.enums import ANTHROPIC_MODELS, OPENAI_MODELS


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
    n = NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["greet"])
    assert n.confidence_threshold == 0.5
    assert n.user_state_confidence_threshold == 0.4


def test_nlu_processor_intents_required_min_1():
    """workflow_loader rejects empty intents list."""
    with pytest.raises(ValidationError):
        NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=[])


def test_nlu_processor_confidence_threshold_range():
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"], confidence_threshold=0.0)
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"], confidence_threshold=1.0)
    with pytest.raises(ValidationError):
        NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"], confidence_threshold=1.1)


def test_nlu_processor_provider_validation():
    NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"], provider="anthropic")
    with pytest.raises(ValidationError, match="not valid for provider"):
        NLUProcessorSection(model=_OPENAI_PRIMARY, intents=["x"], provider="anthropic")


# -- PreprocessingSection ----------------------------------------------------

def test_preprocessing_section_full():
    p = PreprocessingSection(
        language_normalisation=LanguageNormalisationSection(**_lang_norm_kwargs()),
        nlu_processor=NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"]),
    )
    assert p.nlu_processor.confidence_threshold == 0.5


def test_preprocessing_section_extra_forbidden():
    with pytest.raises(ValidationError):
        PreprocessingSection(
            language_normalisation=LanguageNormalisationSection(**_lang_norm_kwargs()),
            nlu_processor=NLUProcessorSection(model=_ANTHROPIC_PRIMARY, intents=["x"]),
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
    """KKB has these fields."""
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
        RoutingCondition(field="x", operator="contains", value="y")


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
        workflow_id="kkb_demo",
        version="1.0.0",
        agent_system_prompt="A demo agent for testing the workflow validators.",
        subagents=[_make_subagent(is_start=True)],
        default_fallback_subagent_id="greeting",
    )
    base.update(overrides)
    return base


def test_workflow_minimal_valid():
    w = AgentWorkflowSection(**_workflow_kwargs())
    assert w.workflow_id == "kkb_demo"


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


def test_workflow_global_intents_must_not_overlap():
    with pytest.raises(ValidationError, match="both global_intents"):
        AgentWorkflowSection(**_workflow_kwargs(
            subagents=[_make_subagent(is_start=True, valid_intents=["help"])],
            global_intents=["help"],
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
    ObservabilitySection(domain="kkb")
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
