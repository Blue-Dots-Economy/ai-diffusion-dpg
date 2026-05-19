"""
Domain schemas for agent_core block.

Each class corresponds to a top-level section the LLM writes via update_config.
Phase prompts inject the relevant subset (see phase→section mapping in design doc).
"""
from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dev_kit.schemas.enums import (
    ANTHROPIC_MODELS, OPENAI_MODELS,
    ChatModelField, LanguageField, ProviderField,
    SpecialHandler, RoutingOperator, InternalRoute,
)


# -- agent_core.agent (language phase) ---------------------------------------

class FeaturesSection(BaseModel):
    """Per-deployment chat-provider feature toggles.

    None means "use the provider's intrinsic capability." A bool tightens the
    effective feature for this deployment. Cannot widen — chat_provider factory
    rejects True against a False capability.

    Mirrors agent_core/src/schema/config.py FeaturesConfig including the
    null-coercion validator: when YAML's `agent.features:` block has all
    sub-keys commented out, it parses as None. Without this coercion, the
    field would fail validation on a config the runtime accepts.
    """
    model_config = ConfigDict(extra="forbid")
    prompt_cache: Optional[bool] = None
    streaming: Optional[bool] = None
    image_input: Optional[bool] = None


def _coerce_null_features(value):
    """Treat features=None (YAML empty mapping) as the default FeaturesSection."""
    if value is None:
        return FeaturesSection()
    return value


class TerminationShortCircuitConfig(BaseModel):
    """Agent.termination_short_circuit — NLU short-circuits when confident.

    Mirrors agent_core/src/schema/config.py TerminationShortCircuitConfig.
    Cuts the goodbye turn down to NLU + translation when confidence
    crosses the threshold.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class CurrentQuestionConfig(BaseModel):
    """Agent.current_question — feature toggle for current-question tracking."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class RecentToolExchangesConfig(BaseModel):
    """Agent.recent_tool_exchanges — bounds last-N tool exchanges in context."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_exchanges: int = Field(default=3, ge=0, le=20)


class AgentSection(BaseModel):
    """LLM model selection + retry/timeout policy. Required: provider, primary_model, fallback_model."""
    model_config = ConfigDict(extra="forbid")

    provider: ProviderField = "anthropic"
    primary_model: ChatModelField = Field(..., description="Primary LLM (must match provider)")
    fallback_model: ChatModelField = Field(..., description="Fallback LLM (must match provider, must differ from primary)")
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    timeout_ms: int = Field(default=10000, gt=0, le=60000)
    retry_attempts: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=3, ge=1, le=20)
    ask_for_consent: bool = False
    consent_prompt: str = ""
    # Optional sub-blocks mirrored from runtime AgentConfig. KKB declares
    # termination_short_circuit; current_question and recent_tool_exchanges
    # are framework-defaulted but accepted here for round-trip parity.
    termination_short_circuit: Optional[TerminationShortCircuitConfig] = None
    current_question: Optional[CurrentQuestionConfig] = None
    recent_tool_exchanges: Optional[RecentToolExchangesConfig] = None

    # Pydantic 2 field validator: coerce YAML's empty mapping (None) to default FeaturesSection.
    # Null-coercion: YAML's empty mapping `features:` parses as None.
    # Mirror runtime behaviour so domain configs with commented-out features
    # don't fail validation.
    _coerce_features = field_validator("features", mode="before")(
        classmethod(lambda cls, v: _coerce_null_features(v))
    )

    @model_validator(mode="after")
    def primary_fallback_must_differ(self) -> "AgentSection":
        """Reject identical primary/fallback model IDs — fallback exists to handle primary failures."""
        if self.primary_model == self.fallback_model:
            raise ValueError(
                "primary_model and fallback_model must be different — fallback exists "
                "to handle primary failures, using the same model defeats the purpose"
            )
        return self

    @model_validator(mode="after")
    def models_must_match_provider(self) -> "AgentSection":
        """Reject configs where primary or fallback model isn't in the chosen provider's model list."""
        valid = ANTHROPIC_MODELS if self.provider == "anthropic" else OPENAI_MODELS
        if self.primary_model not in valid:
            raise ValueError(
                f"primary_model {self.primary_model!r} is not valid for provider "
                f"{self.provider!r}. Valid options: {valid}"
            )
        if self.fallback_model not in valid:
            raise ValueError(
                f"fallback_model {self.fallback_model!r} is not valid for provider "
                f"{self.provider!r}. Valid options: {valid}"
            )
        return self


# -- agent_core.preprocessing (language phase) -------------------------------

def _validate_helper_provider_model(provider: Optional[str], model: str) -> None:
    """If a helper provider is set, its model must be in that provider's list.

    When provider is None the helper inherits agent.provider — no per-helper
    validation here (cross-section validation is out of scope; spec section 6.2).
    """
    if provider is None:
        # Still verify model is in the union of known models — caught by ChatModelField AfterValidator.
        return
    valid = ANTHROPIC_MODELS if provider == "anthropic" else OPENAI_MODELS
    if model not in valid:
        raise ValueError(
            f"model {model!r} is not valid for provider {provider!r}. Valid options: {valid}"
        )


class LanguageNormalisationSection(BaseModel):
    """Language detection / normalisation helper config. provider=None inherits agent.provider.

    `model` defaults to "" — the runtime LanguageNormalisationConfig accepts
    an empty model (the helper inherits agent.primary_model in that case),
    so existing domain configs that omit the field round-trip cleanly.

    Set `enabled: false` to skip the leading LLM call; the main LLM will mirror
    the user's language via a system-prompt directive instead (#313).
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    provider: Optional[ProviderField] = None   # None → inherit agent.provider at runtime
    model: str = ""   # empty allowed — helper inherits agent.primary_model at runtime
    default_language: LanguageField
    supported_languages: list[LanguageField] = Field(..., min_length=1)
    min_detection_tokens: int = Field(default=3, gt=0)
    transliteration: bool = True
    code_switching: bool = True

    @model_validator(mode="after")
    def model_must_match_helper_provider(self) -> "LanguageNormalisationSection":
        """When provider is set and model is non-empty, model must be in that provider's list."""
        if self.model:
            _validate_helper_provider_model(self.provider, self.model)
        return self


class NLUProcessorSection(BaseModel):
    """NLU classifier helper config. provider=None inherits agent.provider; intents must be non-empty."""
    model_config = ConfigDict(extra="forbid")
    provider: Optional[ProviderField] = None   # None → inherit agent.provider at runtime
    model: str = ""   # empty allowed — helper inherits agent.primary_model at runtime
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    user_state_confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    domain_instruction: str = ""
    intents: list[str] = Field(..., min_length=1)   # workflow_loader rejects empty list
    entities: list[str] = Field(default_factory=list)
    sentiment_classes: list[str] = Field(
        default_factory=lambda: ["neutral", "positive", "distressed", "frustrated"]
    )
    signal_intents: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def model_must_match_helper_provider(self) -> "NLUProcessorSection":
        """When provider is set and model is non-empty, model must be in that provider's list."""
        if self.model:
            _validate_helper_provider_model(self.provider, self.model)
        return self


class PreprocessingSection(BaseModel):
    """Container for the language_normalisation + nlu_processor helpers."""
    model_config = ConfigDict(extra="forbid")
    language_normalisation: LanguageNormalisationSection
    nlu_processor: NLUProcessorSection


# -- agent_core.conversation (language, trust, memory phases) ----------------

class UserStateDefinition(BaseModel):
    """One state in the conversation user-state model (e.g., 'fog', 'orientation')."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1)
    signals: list[str] = Field(default_factory=list)
    guidance: str = ""


class UserStateModel(BaseModel):
    """Conversational-agent user-state model.

    Invariant when fully configured: `default_state` references an `id` in
    `states`. Enforced ONLY once `states` is non-empty — see
    `default_must_be_in_states` below.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    default_state: str = ""
    states: list[UserStateDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_must_be_in_states(self) -> "UserStateModel":
        """When the model is fully populated, default_state must be a declared id.

        The dev-kit's predetermined cascade flips `enabled=True` on tier
        completion for companion-style agents, but `states` and
        `default_state` are populated only later in the user_state phase. If
        this validator fired during that gap, every `update_config` write to
        `conversation.*` (e.g. `consent_message`) in language/memory/trust
        phases would be rejected — exactly the loop the GoGuide chat hit.

        We deliberately keep the check lenient at chat time:

        - `enabled=False`                      → no constraint (vacuous OK)
        - `enabled=True`, `states=[]`          → partial draft, accept it
        - `enabled=True`, `states=[...]`       → enforce `default_state in states`

        Strict deploy-time enforcement happens against the runtime schema in
        the pre-deploy dry-run (see `renderer.runtime_validate`).
        """
        if not self.enabled:
            return self
        if not self.states:
            # Partial draft — user_state phase has not populated `states` yet.
            return self
        ids = {s.id for s in self.states}
        if not self.default_state or self.default_state not in ids:
            raise ValueError(
                f"default_state '{self.default_state}' must be one of declared states: {sorted(ids)}"
            )
        return self


class SessionEndEvalConfig(BaseModel):
    """Conversation.session_end_eval — opt-in session-end LLM signal (GH-137).

    Mirrors runtime SessionEndEvalConfig. fail_action is validated but not
    yet dispatched at runtime.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    prompt: str = ""
    fail_action: str = "none"


class ConversationSection(BaseModel):
    """All conversation-level messages + optional user_state_model."""
    model_config = ConfigDict(extra="forbid")
    blocked_message: str = Field(..., min_length=1)
    escalation_message: str = Field(..., min_length=1)
    output_blocked_message: str = Field(..., min_length=1)
    unknown_intent_message: str = ""
    termination_message: str = ""
    consent_message: str = ""
    consent_decline_ack: str = ""
    profile_complete_message: str = ""
    returning_user_greeting: str = ""
    unsupported_language_message: str = ""
    user_state_model: Optional[UserStateModel] = None
    session_end_eval: Optional[SessionEndEvalConfig] = None


# -- agent_core.channels (language, reach phases) ----------------------------

class TtsRulesConfig(BaseModel):
    """Voice-channel TTS-rendering rules per data type (numbers, dates, etc.)."""
    model_config = ConfigDict(extra="forbid")
    numbers: str = ""
    money: str = ""
    dates: str = ""
    time: str = ""
    phone: str = ""
    abbreviations: str = ""
    output_script: str = ""
    english_loanwords: str = ""
    email: str = ""               # KKB has this; LLM doesn't generate
    named_entities: str = ""      # KKB has this; LLM doesn't generate


class SemanticGateConfig(BaseModel):
    """Mirrors runtime SemanticGateConfig 1:1.

    Earlier the parent ``TurnAssemblerConfig`` typed this as a bare
    ``dict``, which let typo keys like ``threshhold`` through the
    mirror; the runtime's strict ``SemanticGateConfig(extra="forbid")``
    then crashed at boot. Same fix pattern as ConnectorDef.input_schema
    above.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class SilenceTriggerConfig(BaseModel):
    """Mirrors runtime SilenceTriggerConfig 1:1."""
    model_config = ConfigDict(extra="forbid")
    silence_ms: int = Field(default=0, ge=0)


class MaxWaitCeilingConfig(BaseModel):
    """Mirrors runtime MaxWaitCeilingConfig 1:1."""
    model_config = ConfigDict(extra="forbid")
    max_wait_ms: int = Field(default=0, ge=0)


class TurnAssemblerConfig(BaseModel):
    """TurnAssembler policy stack — semantic gate + silence trigger + max-wait ceiling.

    Sub-fields now use strict Pydantic classes that mirror the runtime
    exactly. Previously each was typed ``dict``, which silently
    accepted wrong keys and only failed at boot.
    """
    model_config = ConfigDict(extra="forbid")
    semantic_gate: SemanticGateConfig = Field(default_factory=SemanticGateConfig)
    silence_trigger: SilenceTriggerConfig = Field(default_factory=SilenceTriggerConfig)
    max_wait_ceiling: MaxWaitCeilingConfig = Field(default_factory=MaxWaitCeilingConfig)


class ChannelEntry(BaseModel):
    """One channel-specific entry under agent_core.channels (web/voice/cli)."""
    model_config = ConfigDict(extra="forbid")
    system_prompt_suffix: str = ""
    tts_rules: Optional[TtsRulesConfig] = None
    turn_assembler: Optional[TurnAssemblerConfig] = None
    terminal_word: Optional[str] = None
    max_tokens: Optional[int] = Field(default=None, gt=0)


class ChannelsSection(BaseModel):
    """agent_core.channels — at most one entry per channel type."""
    model_config = ConfigDict(extra="forbid")
    web: Optional[ChannelEntry] = None
    voice: Optional[ChannelEntry] = None
    cli: Optional[ChannelEntry] = None


# -- agent_core.connectors (knowledge, tools phases) -------------------------

class InvocationSafety(BaseModel):
    """GH-176 safety contract: data the LLM must never present or speak aloud."""
    model_config = ConfigDict(extra="forbid")
    never_present: list[str] = Field(default_factory=list)
    never_speak: list[str] = Field(default_factory=list)


class InvocationRules(BaseModel):
    """LLM invocation contract for one connector.

    The first six fields are LLM-authored (call_when, required_before_calling,
    must_not_substitute, on_empty, on_failure, bridge_line). The remaining
    GH-176 presentation-contract fields (exception_no_call, ranking_order,
    presentation_limit, refinement_loop_max, safety) are hand-authored by
    the operator in the YAML — the LLM phase prompt does not ask for them.
    Spec accepts them so existing KKB-style configs round-trip cleanly.
    Runtime accepts empty defaults on all fields.
    """
    model_config = ConfigDict(extra="forbid")
    call_when: str = ""
    required_before_calling: list[str] = Field(default_factory=list)
    must_not_substitute: str = ""
    on_empty: str = ""
    on_failure: str = ""
    bridge_line: str = ""
    # GH-176 presentation contract (hand-authored, not LLM-generated)
    exception_no_call: str = ""
    ranking_order: list[str] = Field(default_factory=list)
    presentation_limit: Optional[int] = Field(default=None, gt=0)
    refinement_loop_max: Optional[int] = Field(default=None, gt=0)
    safety: InvocationSafety = Field(default_factory=InvocationSafety)


class InputSchema(BaseModel):
    """JSON-Schema-shaped description of a connector's input.

    Mirrors the runtime ``InputSchema`` at
    ``agent_core/src/schema/config.py`` 1:1. The runtime enforces
    ``extra="forbid"`` and rejects anything that isn't a JSON Schema
    of shape ``{type, properties, required}`` — most commonly with
    ``type="object"`` and ``properties`` keyed by parameter name.
    Earlier this mirror used ``dict[str, Any]``, which silently
    accepted wrong shapes (e.g. ``{'query': {'type': 'string'}}``
    instead of ``{'type': 'object', 'properties': {'query': {...}}}``)
    and the runtime crashed on boot with
    ``connectors.internal.0.input_schema.query Extra inputs are not
    permitted``. Tightening the mirror catches that drift at chat
    time rather than at deploy.
    """
    model_config = ConfigDict(extra="forbid")
    type: str = "object"
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    additionalProperties: bool = False


class ConnectorDef(BaseModel):
    """External tool/connector exposed to the LLM (REST API, identity, write actions).

    description and invocation_rules are optional — the runtime ConnectorDef
    defaults description="" and invocation_rules=InvocationRules() when omitted.
    External read-only tools (like a public weather API) often skip both.
    """
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: InputSchema = Field(default_factory=InputSchema)
    invocation_rules: InvocationRules = Field(default_factory=InvocationRules)


class InternalConnectorDef(ConnectorDef):
    """Routes to an internal block (e.g. knowledge_retrieval → knowledge_engine)."""
    route: InternalRoute = InternalRoute.knowledge_engine


class ConnectorsSection(BaseModel):
    """agent_core.connectors — internal/read/write/identity connector lists."""
    model_config = ConfigDict(extra="forbid")
    internal: list[InternalConnectorDef] = Field(default_factory=list)
    read: list[ConnectorDef] = Field(default_factory=list)
    write: list[ConnectorDef] = Field(default_factory=list)
    identity: list[ConnectorDef] = Field(default_factory=list)


# -- agent_core.agent_workflow (workflow phase) ------------------------------

class RoutingCondition(BaseModel):
    """Typed condition on a routing rule. Mirrors agent_core's runtime RoutingCondition."""
    model_config = ConfigDict(extra="forbid")
    field: str = Field(..., min_length=1)
    operator: RoutingOperator
    value: Any = None


class RoutingRule(BaseModel):
    """One routing rule on a subagent or in global_routing — fires on a matched intent."""
    model_config = ConfigDict(extra="forbid")
    intent: str = Field(..., min_length=1)
    next_subagent_id: str = Field(..., min_length=1)
    conditions: list[RoutingCondition] = Field(default_factory=list)
    session_writes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def session_writes_must_be_scalars(self) -> "RoutingRule":
        """Runtime workflow_loader rejects non-scalar session_writes values.

        Mirrors agent_core/src/workflow_loader.py: each value must be a
        scalar (str, int, float, bool, None) — dict/list values cause a
        ConfigurationError at startup.
        """
        for key, val in self.session_writes.items():
            if isinstance(val, (dict, list)):
                raise ValueError(
                    f"session_writes[{key!r}] must be a scalar "
                    f"(str/int/float/bool/None), got {type(val).__name__}: {val!r}"
                )
        return self


class SubAgent(BaseModel):
    """One subagent in the workflow graph.

    Runtime workflow_loader.py requires opening_phrase for ALL subagents
    (terminal and non-terminal alike — adopted-state callbacks always
    need a phrase to emit on entry).
    """
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""
    is_start: bool = False
    is_terminal: bool = False
    special_handler: Optional[SpecialHandler] = None
    valid_intents: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    system_prompt: str = Field(..., min_length=1)
    opening_phrase: str = Field(..., min_length=1)   # required for all subagents
    routing: list[RoutingRule] = Field(default_factory=list)
    # opening_phrase non-empty enforced by Field(..., min_length=1) above —
    # runtime requires it for ALL subagents (adopted-state callbacks).


class AgentWorkflowSection(BaseModel):
    """Top-level workflow definition: subagents, routing, fallback. 4 cross-field validators enforce graph integrity."""
    model_config = ConfigDict(extra="forbid")
    # workflow_id allows hyphens — runtime workflow_loader does not enforce a
    # pattern beyond non-empty (e.g. youth-schemes-agent uses hyphens).
    workflow_id: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    # agent_system_prompt min_length=1 — runtime accepts any non-empty string.
    agent_system_prompt: str = Field(..., min_length=1)
    subagents: list[SubAgent] = Field(..., min_length=1)
    global_intents: list[str] = Field(default_factory=list)
    global_tools: list[str] = Field(default_factory=list)
    global_routing: list[RoutingRule] = Field(default_factory=list)
    default_fallback_subagent_id: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def fallback_must_be_declared(self) -> "AgentWorkflowSection":
        """default_fallback_subagent_id must reference a declared subagent id."""
        ids = {s.id for s in self.subagents}
        if self.default_fallback_subagent_id not in ids:
            raise ValueError(
                f"default_fallback_subagent_id '{self.default_fallback_subagent_id}' "
                f"is not a declared subagent id. Declared: {sorted(ids)}"
            )
        return self

    @model_validator(mode="after")
    def routing_targets_must_be_declared(self) -> "AgentWorkflowSection":
        """Every next_subagent_id (in subagent.routing or global_routing) must reference a declared subagent."""
        ids = {s.id for s in self.subagents}
        for sa in self.subagents:
            for rule in sa.routing:
                if rule.next_subagent_id not in ids:
                    raise ValueError(
                        f"Subagent '{sa.id}' routing intent '{rule.intent}' targets "
                        f"unknown subagent '{rule.next_subagent_id}'. Declared: {sorted(ids)}"
                    )
        for rule in self.global_routing:
            if rule.next_subagent_id not in ids:
                raise ValueError(
                    f"global_routing intent '{rule.intent}' targets unknown subagent "
                    f"'{rule.next_subagent_id}'"
                )
        return self

    @model_validator(mode="after")
    def global_intents_must_not_overlap_subagent_intents(self) -> "AgentWorkflowSection":
        """An intent cannot appear in both global_intents and any subagent's valid_intents — runtime crashes on overlap."""
        global_set = set(self.global_intents)
        for sa in self.subagents:
            overlap = global_set & set(sa.valid_intents)
            if overlap:
                raise ValueError(
                    f"Intents {sorted(overlap)} appear in both global_intents and "
                    f"subagent '{sa.id}' valid_intents — runtime crashes on overlap"
                )
        return self

    @model_validator(mode="after")
    def exactly_one_start_subagent(self) -> "AgentWorkflowSection":
        """Exactly one subagent must have is_start=True (entry point of the workflow)."""
        starts = [s for s in self.subagents if s.is_start]
        if len(starts) != 1:
            raise ValueError(
                f"Exactly one subagent must have is_start=true (got {len(starts)})"
            )
        return self


# -- agent_core.entity_to_profile_field (memory phase) -----------------------

class EntityToProfileFieldSection(BaseModel):
    """Open map: entity_name → profile_field_name."""
    model_config = ConfigDict(extra="allow")  # open map by design


# -- agent_core.hitl (language phase) ----------------------------------------

class HitlSection(BaseModel):
    """HiTL handoff section — `response_message` is what the agent says when escalating."""
    model_config = ConfigDict(extra="forbid")
    response_message: str = Field(..., min_length=1)


# -- agent_core.reach_layer (top-level default turn-assembler) ---------------

class ReachLayerDefaultsSection(BaseModel):
    """agent_core.reach_layer — fallback turn-assembler for channels without one.

    Mirrors runtime ReachLayerDefaultsConfig. Lives at the top level of
    agent_core.yaml intentionally — Agent Core reads these as defaults
    when a channel does not declare its own turn_assembler block.
    """
    model_config = ConfigDict(extra="forbid")
    turn_assembler: Optional[TurnAssemblerConfig] = None


# -- agent_core.observability (observability phase) --------------------------

class ObservabilitySection(BaseModel):
    """agent_core.observability — domain identifier (slug pattern).

    The pattern accepts both ``-`` and ``_`` separators so the mirror does
    not reject values produced by ``derived_fields.slug()`` (underscore-
    based) OR by the project-creation slug in ``app.py:_slugify`` (hyphen-
    based). The runtime ``ObservabilityConfig.domain`` field has no
    pattern constraint at all (``agent_core/src/schema/config.py``), so
    the only reason this regex exists is to keep the LLM from writing
    obvious junk values (capitalised names, spaces, etc.).
    """
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
