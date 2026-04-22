"""
MergedConfig — strict schema for the Agent Core merged runtime config.

Merged config = dev-kit/dpg/agent_core.yaml (framework defaults)
                deep-merged with a domain YAML
                (e.g. dev-kit/configs/kkb/agent_core.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Open-map sub-sections are modelled as ``dict[str, <inner>]``:

- ``entity_to_profile_field`` — entity_name → profile_field_name
- ``agent_workflow.tool_result_mappings`` — tool_name → mapping
- ``agent_workflow.tool_result_mappings.*.field_map`` — graph_field → result_field
- ``preprocessing.nlu_processor.signal_intents`` — intent → signal_type
- ``connectors.*.[].input_schema.properties`` — JSON Schema property map

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RoutingOperator(str, Enum):
    """Comparison operator for a routing condition."""

    eq = "eq"
    not_eq = "not_eq"
    gt = "gt"
    lt = "lt"
    in_ = "in"


class SpecialHandler(str, Enum):
    """Framework-level subagent handler that bypasses normal LLM flow."""

    hitl = "hitl"
    whatsapp_handoff = "whatsapp_handoff"


class AssemblyMode(str, Enum):
    """Turn assembly mode (cosmetic label, channel-specific)."""

    streaming = "streaming"
    batch = "batch"


# ---------------------------------------------------------------------------
# Framework / infrastructure sections
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Agent Core entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)


class ClientConfig(BaseModel):
    """Generic HTTP client config for an inter-service call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    timeout_ms: int = Field(default=5000, gt=0)


class OtelConfig(BaseModel):
    """OTel SDK exporter and sampling configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ObservabilityConfig(BaseModel):
    """Observability settings — OTel plus domain identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = "unknown"
    otel: OtelConfig = Field(default_factory=OtelConfig)


# ---------------------------------------------------------------------------
# agent.*
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Top-level LLM wrapper settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_model: str = ""
    fallback_model: str = ""
    timeout_ms: int = Field(default=10000, gt=0)
    retry_attempts: int = Field(default=2, ge=1)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=3, ge=1)
    ask_for_consent: bool = False
    consent_prompt: str = ""


# ---------------------------------------------------------------------------
# conversation.*
# ---------------------------------------------------------------------------


class UserStateDefinition(BaseModel):
    """One user-state entry (GH-139)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    signals: list[str] = Field(default_factory=list)
    guidance: str = ""


class UserStateModelConfig(BaseModel):
    """User-state classifier (opt-in, GH-139)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    default_state: str = ""
    states: list[UserStateDefinition] = Field(default_factory=list)


class SessionEndEvalConfig(BaseModel):
    """Opt-in session-end signalling (GH-137).

    ``fail_action`` is validated but not yet dispatched at runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    prompt: str = ""
    fail_action: str = "none"


class ConversationConfig(BaseModel):
    """Static response strings plus opt-in sub-blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocked_message: str = ""
    escalation_message: str = ""
    output_blocked_message: str = ""
    unknown_intent_message: str = ""
    termination_message: str = ""
    consent_message: str = ""
    consent_decline_ack: str = ""
    unsupported_language_message: str = ""
    profile_complete_message: str = ""
    returning_user_greeting: str = ""
    user_state_model: UserStateModelConfig = Field(default_factory=UserStateModelConfig)
    session_end_eval: SessionEndEvalConfig = Field(default_factory=SessionEndEvalConfig)


# ---------------------------------------------------------------------------
# connectors.*
# ---------------------------------------------------------------------------


class InvocationSafety(BaseModel):
    """Safety constraints exposed through a connector's invocation rules.

    Lists entries the agent must never present from a tool result (e.g.
    closed or inactive jobs) or speak aloud (e.g. GPS coordinates, match
    scores). Consumed by per-subagent prompts as grounding context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    never_present: list[str] = Field(default_factory=list)
    never_speak: list[str] = Field(default_factory=list)


class InvocationRules(BaseModel):
    """LLM invocation contract for a connector (GH-137, GH-176)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_when: str = ""
    required_before_calling: list[str] = Field(default_factory=list)
    must_not_substitute: str = ""
    on_empty: str = ""
    on_failure: str = ""
    bridge_line: str = ""
    # GH-176: optional per-connector presentation contract. Consumed by the
    # subagent prompt rather than by any adapter — these fields shape how
    # the LLM talks about the tool's results, they do not change how the
    # tool is invoked.
    exception_no_call: str = ""
    ranking_order: list[str] = Field(default_factory=list)
    presentation_limit: int | None = None
    refinement_loop_max: int | None = None
    safety: InvocationSafety = Field(default_factory=InvocationSafety)


class InputSchema(BaseModel):
    """JSON-Schema-shaped description of a connector's input.

    Passed verbatim to the Anthropic tools API. ``properties`` is an
    open map keyed by parameter name; each property dict is left
    permissive (``dict[str, Any]``) since JSON Schema is large.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = "object"
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    additionalProperties: bool = False


class ConnectorDef(BaseModel):
    """External-facing connector (read / write / identity)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    input_schema: InputSchema = Field(default_factory=InputSchema)
    invocation_rules: InvocationRules = Field(default_factory=InvocationRules)


class InternalConnectorDef(BaseModel):
    """Internal connector routed to another DPG block (e.g. knowledge_retrieval)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    route: str
    description: str = ""
    input_schema: InputSchema = Field(default_factory=InputSchema)
    invocation_rules: InvocationRules = Field(default_factory=InvocationRules)


class ConnectorsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    read: list[ConnectorDef] = Field(default_factory=list)
    write: list[ConnectorDef] = Field(default_factory=list)
    identity: list[ConnectorDef] = Field(default_factory=list)
    internal: list[InternalConnectorDef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# preprocessing.*
# ---------------------------------------------------------------------------


class LanguageNormalisationConfig(BaseModel):
    """LLM-native language normalisation block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = ""
    default_language: str = ""
    supported_languages: list[str] = Field(default_factory=list)
    min_detection_tokens: int = Field(default=3, gt=0)
    transliteration: bool = True
    code_switching: bool = True


class NLUProcessorConfig(BaseModel):
    """NLU intent + entity + sentiment classifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = ""
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    user_state_confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    domain_instruction: str = ""
    intents: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    sentiment_classes: list[str] = Field(
        default_factory=lambda: ["neutral", "positive", "distressed", "frustrated"]
    )
    signal_intents: dict[str, str] = Field(default_factory=dict)


class PreprocessingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language_normalisation: LanguageNormalisationConfig = Field(
        default_factory=LanguageNormalisationConfig
    )
    nlu_processor: NLUProcessorConfig = Field(default_factory=NLUProcessorConfig)


# ---------------------------------------------------------------------------
# hitl
# ---------------------------------------------------------------------------


class HitlResponseConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    response_message: str = ""


# ---------------------------------------------------------------------------
# agent_workflow.*
# ---------------------------------------------------------------------------


class RoutingCondition(BaseModel):
    """Single predicate evaluated against a session field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    operator: RoutingOperator
    value: Any = None


class RoutingRule(BaseModel):
    """Routing decision from a subagent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str
    next_subagent_id: str
    condition: Optional[RoutingCondition] = None
    conditions: list[RoutingCondition] = Field(default_factory=list)
    session_writes: dict[str, Any] = Field(default_factory=dict)


class SubAgent(BaseModel):
    """One subagent node in the workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    description: str = ""
    is_start: bool = False
    is_terminal: bool = False
    opening_phrase: str = ""
    special_handler: Optional[SpecialHandler] = None
    valid_intents: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    output_format: Optional[dict[str, Any]] = None
    routing: list[RoutingRule] = Field(default_factory=list)


class ToolResultMapping(BaseModel):
    """How to persist one tool's results to the Journey graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    journey_event_label: str
    result_list_key: str
    field_map: dict[str, str] = Field(default_factory=dict)


class AgentWorkflowConfig(BaseModel):
    """Multi-subagent workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = ""
    version: str = "1.0.0"
    agent_system_prompt: str = ""
    global_intents: list[str] = Field(default_factory=list)
    global_routing: list[RoutingRule] = Field(default_factory=list)
    default_fallback_subagent_id: str = ""
    tool_result_mappings: dict[str, ToolResultMapping] = Field(default_factory=dict)
    subagents: list[SubAgent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# channels.* (GH-137) — chat channel removed as unused
# ---------------------------------------------------------------------------


class TtsRulesConfig(BaseModel):
    """TTS formatting rules for a voice channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    numbers: str = ""
    money: str = ""
    dates: str = ""
    time: str = ""
    phone: str = ""
    abbreviations: str = ""
    output_script: str = ""
    english_loanwords: str = ""
    email: str = ""
    named_entities: str = ""


class SemanticGateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class SilenceTriggerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    silence_ms: int = Field(default=0, ge=0)


class MaxWaitCeilingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_wait_ms: int = Field(default=0, ge=0)


class TurnAssemblerConfig(BaseModel):
    """Turn-assembler policy stack."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_gate: SemanticGateConfig = Field(default_factory=SemanticGateConfig)
    silence_trigger: SilenceTriggerConfig = Field(default_factory=SilenceTriggerConfig)
    max_wait_ceiling: MaxWaitCeilingConfig = Field(default_factory=MaxWaitCeilingConfig)


class ChannelConfig(BaseModel):
    """Per-channel LLM-facing configuration (GH-137)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_prompt_suffix: str = ""
    tts_rules: Optional[TtsRulesConfig] = None
    terminal_word: Optional[str] = None
    turn_assembler: TurnAssemblerConfig = Field(default_factory=TurnAssemblerConfig)


class ChannelsConfig(BaseModel):
    """Per-channel config block. ``chat`` removed as unused in this PR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    voice: ChannelConfig = Field(default_factory=ChannelConfig)
    web: ChannelConfig = Field(default_factory=ChannelConfig)
    cli: ChannelConfig = Field(default_factory=ChannelConfig)


# ---------------------------------------------------------------------------
# reach_layer — top-level default turn-assembler (inherited by channels)
# ---------------------------------------------------------------------------


class ReachLayerDefaultsConfig(BaseModel):
    """Default turn-assembler policy stack consumed by Agent Core.

    Lives at the top level intentionally — Agent Core reads these as
    fallbacks when a channel does not declare its own turn_assembler
    block under ``channels.<name>``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_assembler: TurnAssemblerConfig = Field(default_factory=TurnAssemblerConfig)


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged agent_core config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)

    agent: AgentConfig = Field(default_factory=AgentConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    entity_to_profile_field: dict[str, str] = Field(default_factory=dict)
    hitl: HitlResponseConfig = Field(default_factory=HitlResponseConfig)
    agent_workflow: AgentWorkflowConfig = Field(default_factory=AgentWorkflowConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    reach_layer: ReachLayerDefaultsConfig = Field(default_factory=ReachLayerDefaultsConfig)

    # Inter-service HTTP clients
    ke_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(
            endpoint="http://knowledge_engine:8001/retrieve"
        )
    )
    memory_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://memory_layer:8002")
    )
    trust_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://trust_layer:8003")
    )
    learning_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://observability_layer:8004")
    )
    action_gateway_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://action_gateway:9999")
    )

    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def validate_full(cls, config: dict) -> "MergedConfig":
        """Validate the full merged config dict against the strict schema.

        Args:
            config: Merged dict (dpg defaults + domain overrides).

        Returns:
            Validated MergedConfig instance.

        Raises:
            pydantic.ValidationError: If the config contains unknown keys,
                wrong value types, or values outside the allowed ranges at
                any nesting level.
            TypeError: If config is None.
        """
        if config is None:
            raise TypeError("config must be a dict, got None")
        return cls.model_validate(config)
