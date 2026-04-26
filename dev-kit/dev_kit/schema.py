"""
dev-kit/schema.py

Pydantic models for every DPG service config.

Each model declares the exact shape of a valid merged config (DPG defaults +
domain values).  Missing required fields or wrong types raise ValidationError
before anything runs.

One top-level model per service:
  AgentCoreConfig
  KnowledgeEngineConfig
  TrustLayerConfig
  MemoryLayerConfig
  ObservabilityLayerConfig
  ActionGatewayConfig
  ReachLayerConfig
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from dev_kit.schemas.loader import load_template

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int


class ClientConfig(BaseModel):
    endpoint: str
    timeout_ms: int = 5000


# ---------------------------------------------------------------------------
# Agent Core
# ---------------------------------------------------------------------------

class InvocationRulesConfig(BaseModel):
    """LLM invocation contract for a connector (GH-137)."""

    call_when: str = Field(default="", description="Exact trigger condition in plain language")
    required_before_calling: list[str] = Field(
        default=[],
        description="Data field names that must be known before the tool may be invoked",
    )
    must_not_substitute: str = Field(
        default="",
        description="What the LLM must never treat as a substitute for this tool",
    )
    on_empty: str = Field(
        default="",
        description="What the agent says when the tool returns empty results",
    )
    on_failure: str = Field(
        default="",
        description="What the agent says on tool failure or timeout",
    )
    bridge_line: str = Field(
        default="",
        description="Single natural line spoken right before the tool call (for voice channels)",
    )


class ConnectorDef(BaseModel):
    name: str = Field(..., description="Connector name matching a key in action_gateway.connectors")
    description: str = Field(default="", description="Description shown to LLM explaining when to call this connector")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema object for the tool's input. Passed verbatim to the Anthropic tools API.",
    )
    invocation_rules: InvocationRulesConfig = Field(
        default_factory=InvocationRulesConfig,
        description="LLM invocation contract for this connector (GH-137)",
    )


class InternalConnectorDef(BaseModel):
    name: str = Field(..., description="Internal connector name, e.g. knowledge_retrieval")
    route: str = Field(..., description="Internal routing destination, e.g. knowledge_engine")
    description: str = Field(default="", description="Description shown to LLM explaining when to call this connector")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema object for the tool's input.",
    )
    invocation_rules: InvocationRulesConfig = Field(
        default_factory=InvocationRulesConfig,
        description="LLM invocation contract for this connector (GH-137)",
    )


class ConnectorsConfig(BaseModel):
    read: list[ConnectorDef] = []
    write: list[ConnectorDef] = []
    identity: list[ConnectorDef] = []
    internal: list[InternalConnectorDef] = Field(
        default=[],
        description="Internal connectors routed by Agent Core (e.g. knowledge_retrieval). "
                    "Not sent to Action Gateway.",
    )


class AgentConfig(BaseModel):
    primary_model: str = Field(..., description="Claude model ID for primary inference, e.g. claude-haiku-4-5-20251001")
    fallback_model: str = Field(..., description="Claude model ID used if primary call fails")
    timeout_ms: int = Field(default=10000, description="LLM call timeout in milliseconds")
    retry_attempts: int = Field(default=2, description="Number of retry attempts on transient failure")
    retry_backoff_seconds: list[float] = Field(default=[0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=1, description="Maximum tool call rounds per turn")
    ask_for_consent: bool = Field(
        default=False,
        description="If True, Agent Core asks new users for DPDP consent before storing any data.",
    )
    consent_prompt: str = Field(
        default="",
        description="Message shown to the user when requesting consent. Used when ask_for_consent is True.",
    )


class ConversationAgentConfig(BaseModel):
    max_turns: int = Field(default=20)
    blocked_message: str = Field(
        default="I'm unable to help with that request.",
        description="Shown to user when input is blocked by Trust Layer.",
    )
    escalation_message: str = Field(
        default="I'm connecting you to a human agent who can better assist you.",
        description="Shown when turn is escalated to a human agent.",
    )
    output_blocked_message: str = Field(
        default="I wasn't able to produce a safe response. Please try rephrasing your question.",
        description="Shown when LLM output is blocked by Trust Layer.",
    )
    unknown_intent_message: str = Field(
        default="I'm sorry, I didn't understand that. Could you please rephrase?",
        description="Shown when the NLU classifier returns unknown intent below confidence threshold.",
    )
    termination_message: str = Field(
        default="Thank you! Goodbye.",
        description="Shown when the user ends the session via termination_intent.",
    )
    consent_message: str = Field(
        default="",
        description="Consent request shown to new users before profile collection.",
    )
    consent_decline_ack: str = Field(
        default="",
        description="Acknowledgement shown when user declines consent.",
    )
    profile_complete_message: str = Field(
        default="",
        description="Shown to user when profile collection is complete and processing begins.",
    )
    returning_user_greeting: str = Field(
        default="",
        description="Personalised greeting for returning users whose profile already exists.",
    )
    session_end_eval: "SessionEndEvalConfig" = Field(
        default_factory=lambda: SessionEndEvalConfig(),
        description="Opt-in session-end signalling config (GH-137)",
    )


class SessionEndEvalConfig(BaseModel):
    """Opt-in configuration for detecting session-end via LLM tool (GH-137)."""

    enabled: bool = Field(
        default=False,
        description="Set true for agents that should detect call-end via end_session tool",
    )
    prompt: str = Field(
        default="",
        description="Appended to main system prompt; teaches LLM when to call end_session tool",
    )
    fail_action: str = Field(
        default="none",
        description="Action to take when end_session evaluation fails; runtime ignores in current PR",
    )


class LanguageNormalisationConfig(BaseModel):
    model: str = Field(
        default="",
        description="Optional Claude model ID override for language normalisation; falls back to agent.primary_model when empty",
    )
    provider: str = Field(default="llm_native", description="Normalisation provider (llm_native)")
    default_language: str = Field(
        default="",
        description="Default language used when none is detected from user input, e.g. hindi",
    )
    supported_languages: list[str] = Field(..., description="Languages the agent supports, e.g. [hindi, english, kannada, hinglish]")
    transliteration: bool = Field(default=True, description="Normalise transliterated input to canonical script")
    code_switching: bool = Field(default=True, description="Handle mixed-language input within a single message")


class NLUProcessorConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for NLU classification")
    confidence_threshold: float = Field(default=0.5, description="Float 0-1. Intents below this are treated as unknown")
    history_turns: int = Field(default=2)
    domain_instruction: str = Field(
        default="",
        description="Domain-specific instruction prepended to the NLU classification prompt",
    )
    intents: list[str] = Field(..., description="List of intent identifiers for this domain, e.g. greeting, profile_answer, apply_now")
    entities: list[str] = Field(..., description="List of entity identifiers to extract, e.g. name, location, trade_or_stream")
    sentiment_classes: list[str] = Field(
        default=["neutral", "positive", "distressed"],
        description="Sentiment classes to classify, e.g. [neutral, positive, distressed]",
    )


class PreprocessingConfig(BaseModel):
    language_normalisation: LanguageNormalisationConfig
    nlu_processor: NLUProcessorConfig


class HitlConfig(BaseModel):
    response_message: str = Field(
        ...,
        description="Fixed message returned to the user when the HITL subagent is triggered. "
                    "No LLM call is made — this text is returned verbatim.",
    )


# ---------------------------------------------------------------------------
# Agent Workflow — full structural validation for agent_workflow block
# ---------------------------------------------------------------------------

class RoutingConditionSchema(BaseModel):
    """A single predicate evaluated against a session field at routing time."""

    field: str = Field(
        ...,
        description="Session field name to evaluate, e.g. income_urgency or subagent_entry_count.commitment",
    )
    operator: Literal["eq", "not_eq", "in", "lt", "gt"] = Field(
        ...,
        description="Comparison operator. One of: eq, not_eq, in, lt, gt",
    )
    value: Any = Field(..., description="Scalar or list value to compare the session field against")


class RoutingRuleSchema(BaseModel):
    """A single routing decision mapping an intent (or catch-all) to the next subagent."""

    intent: str = Field(..., description="Intent to match, or '*' for catch-all")
    next_subagent_id: str = Field(..., description="ID of the destination subagent")
    condition: RoutingConditionSchema | None = Field(
        default=None,
        description="Optional single condition that must be true for this rule to fire",
    )
    conditions: list[RoutingConditionSchema] = Field(
        default=[],
        description="Optional list of conditions — ALL must be true for this rule to fire",
    )
    session_writes: dict[str, Any] = Field(
        default_factory=dict,
        description="Session field/value pairs written when this rule fires. "
                    "Values must be scalars (str, int, float, bool).",
    )


class SubAgentSchema(BaseModel):
    """Configuration for a single subagent node in the workflow graph."""

    id: str = Field(..., description="Unique subagent identifier within this workflow")
    name: str = Field(default="", description="Human-readable display name")
    description: str = Field(default="", description="Short description of this subagent's role")
    is_start: bool = Field(
        default=False,
        description="True if this is the entry subagent for new sessions. "
                    "Exactly ONE subagent in the workflow must have is_start: true.",
    )
    is_terminal: bool = Field(
        default=False,
        description="True if this subagent ends the conversation. "
                    "Terminal subagents must have an empty routing list.",
    )
    opening_phrase: str = Field(
        default="",
        description="Phrase emitted on the first turn only (after consent). "
                    "Empty string means no opening phrase (GH-137).",
    )
    special_handler: Literal["hitl", "whatsapp_handoff"] | None = Field(
        default=None,
        description="Optional framework-level handler. "
                    "'hitl' bypasses the LLM and returns hitl.response_message. "
                    "'whatsapp_handoff' triggers a channel handoff.",
    )
    valid_intents: list[str] = Field(
        default=[],
        description="Intents this subagent handles. Must be declared in preprocessing.nlu_processor.intents. "
                    "Must not overlap with agent_workflow.global_intents.",
    )
    tools: list[str] = Field(
        default=[],
        description="Tool names available in this subagent. Each name must match a connector in "
                    "connectors.read, connectors.write, connectors.identity, or connectors.internal.",
    )
    system_prompt: str = Field(
        default="",
        description="System prompt injected for LLM calls in this subagent",
    )
    output_format: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON schema for structured LLM output validation. None means free-form text.",
    )
    routing: list[RoutingRuleSchema] = Field(
        default=[],
        description="Routing rules from this subagent. "
                    "Terminal subagents must have an empty list. "
                    "Non-terminal subagents must have at least one rule.",
    )


class AgentWorkflowConfig(BaseModel):
    """Full structural definition of the multi-subagent workflow for a domain."""

    workflow_id: str = Field(..., description="Unique workflow identifier, e.g. kkb_iti_graduate")
    version: str = Field(..., description="Semantic version string, e.g. '1.0.0'")
    agent_system_prompt: str = Field(
        default="",
        description="Top-level system prompt for the orchestrating LLM. Injected on every turn.",
    )
    global_intents: list[str] = Field(
        default=[],
        description="Intents handled globally before subagent routing. "
                    "Must not appear in any subagent's valid_intents.",
    )
    global_routing: list[RoutingRuleSchema] = Field(
        default=[],
        description="Routing rules applied globally when a global_intent fires",
    )
    default_fallback_subagent_id: str = Field(
        default="",
        description="Subagent to route to when no routing rule matches the current intent",
    )
    subagents: list[SubAgentSchema] = Field(
        ...,
        min_length=1,
        description="All subagent definitions. Must contain exactly one subagent with is_start: true.",
    )


# ---------------------------------------------------------------------------
# Top-level channel config models (GH-137)
# ---------------------------------------------------------------------------

class TtsRulesConfig(BaseModel):
    """TTS formatting rules for a voice channel (GH-137)."""

    numbers: str = Field(default="", description="How to read numeric values aloud")
    money: str = Field(default="", description="How to read monetary values aloud")
    dates: str = Field(default="", description="How to read date values aloud")
    time: str = Field(default="", description="How to read time values aloud")
    phone: str = Field(default="", description="How to read phone numbers aloud")
    abbreviations: str = Field(default="", description="How to expand abbreviations aloud")
    output_script: str = Field(default="", description="Script/language to use for TTS output")
    english_loanwords: str = Field(default="", description="How to handle English loanwords in TTS")


class ChannelTurnAssemblerConfig(BaseModel):
    """Turn-assembler settings for a channel (GH-137)."""

    semantic_gate: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": False, "confidence_threshold": 0.75},
        description="NLU gate configuration for this channel",
    )
    silence_trigger: dict[str, Any] = Field(
        default_factory=lambda: {"silence_ms": 0},
        description="Silence trigger configuration for this channel",
    )
    max_wait_ceiling: dict[str, Any] = Field(
        default_factory=lambda: {"max_wait_ms": 0},
        description="Max wait ceiling configuration for this channel",
    )


class ChannelConfig(BaseModel):
    """Per-channel LLM-facing configuration (GH-137).

    GH-247: ``terminal_word`` was moved to
    ``reach_layer.channels.voice.terminal_word`` because it is read by the
    reach_layer voice service, not by agent_core. Co-locating with
    ``filler_threshold_ms`` / ``filler_phrase`` (also voice-runtime concerns)
    keeps voice delivery configuration in one block.
    """

    system_prompt_suffix: str = Field(
        default="",
        description="Appended to the main system prompt for this channel",
    )
    tts_rules: TtsRulesConfig | None = Field(
        default=None,
        description="TTS formatting rules; non-null for voice channels only",
    )
    turn_assembler: ChannelTurnAssemblerConfig = Field(
        default_factory=ChannelTurnAssemblerConfig,
        description="Turn-assembler settings for this channel",
    )


class ChannelsTopLevelConfig(BaseModel):
    """Top-level per-channel configuration block (GH-137)."""

    voice: ChannelConfig = Field(
        default_factory=lambda: ChannelConfig(tts_rules=TtsRulesConfig()),
        description="Voice channel configuration",
    )
    chat: ChannelConfig = Field(
        default_factory=ChannelConfig,
        description="Chat channel configuration",
    )
    web: ChannelConfig = Field(
        default_factory=ChannelConfig,
        description="Web channel configuration",
    )
    cli: ChannelConfig = Field(
        default_factory=ChannelConfig,
        description="CLI channel configuration",
    )


class AgentCoreConfig(BaseModel):
    server: ServerConfig
    agent: AgentConfig
    conversation: ConversationAgentConfig
    channels: ChannelsTopLevelConfig = Field(
        default_factory=ChannelsTopLevelConfig,
        description="Per-channel LLM-facing configuration (GH-137)",
    )
    connectors: ConnectorsConfig = ConnectorsConfig()
    ke_client: ClientConfig
    memory_client: ClientConfig
    trust_client: ClientConfig
    learning_client: ClientConfig
    action_gateway_client: ClientConfig
    preprocessing: PreprocessingConfig
    entity_to_profile_field: dict[str, str] = Field(
        default_factory=dict,
        description="Maps NLU entity names to UserProfile declared_fields in the Memory Layer. "
                    "e.g. {trade_or_stream: trade_or_stream, location: location}",
    )
    hitl: HitlConfig | None = Field(
        default=None,
        description="HITL config. Required if any subagent uses special_handler: hitl.",
    )
    agent_workflow: AgentWorkflowConfig


# ---------------------------------------------------------------------------
# Knowledge Engine
# ---------------------------------------------------------------------------

class GlossaryMapping(BaseModel):
    colloquial: list[str]
    canonical: str


class GlossaryConfig(BaseModel):
    enabled: bool = Field(default=True)
    mappings: list[GlossaryMapping] = Field(
        default=[],
        description="Colloquial-to-canonical term mappings. Each entry: {colloquial: [...], canonical: string}",
    )
    apply_to: list[str] = Field(
        default=["normalised_input", "entities"],
        description="Config fields to apply glossary to",
    )


class KnowledgeSource(BaseModel):
    path: str
    type: str
    doc_type: str
    refresh: str


class MetadataFiltersConfig(BaseModel):
    use_location_filter: bool = True
    use_intent_filter: bool = True


class StaticKBConfig(BaseModel):
    enabled: bool = True
    vector_store: str = "chromadb"
    collection_name: str = Field(..., description="ChromaDB collection name for this domain's knowledge base")
    chroma_persist_dir: str = "./data/chroma_db"
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    top_k: int = 3
    similarity_threshold: float = 0.65
    default_doc_type: str = "general"
    sources: list[KnowledgeSource] = Field(
        default=[],
        description="Deprecated — documents are uploaded post-deploy. Kept for backward compatibility.",
    )
    metadata_filters: MetadataFiltersConfig = MetadataFiltersConfig()
    intent_filters: dict[str, list[str]] = Field(
        default={},
        description="Map of intent → list of doc_types to retrieve. e.g. {market_truth_query: [scheme, trade]}",
    )


class MultimodalConfig(BaseModel):
    enabled: bool = False
    supported_types: list[str] = ["pdf", "image"]
    audio_enabled: bool = False
    image_model: str = ""
    max_file_size_mb: int = 10


class KBBlocksConfig(BaseModel):
    glossary: GlossaryConfig = GlossaryConfig()
    static_knowledge_base: StaticKBConfig
    multimodal_input_handler: MultimodalConfig = MultimodalConfig()


class KBInnerConversationConfig(BaseModel):
    max_history_turns: int = 10


class KnowledgeConfig(BaseModel):
    conversation: KBInnerConversationConfig = KBInnerConversationConfig()
    blocks: KBBlocksConfig


class PersonaConfig(BaseModel):
    text: str = Field(default="", description="Persona text injected into the LLM system prompt")


class ConversationKEConfig(BaseModel):
    persona: PersonaConfig = Field(
        default_factory=PersonaConfig,
        description="Persona definition for the LLM. The text is injected verbatim into every prompt.",
    )
    language_instruction: str = ""
    guardrail_reminders: list[str] = []


class KnowledgeEngineConfig(BaseModel):
    server: ServerConfig
    knowledge: KnowledgeConfig
    conversation: ConversationKEConfig = Field(
        default_factory=ConversationKEConfig,
        description="LLM persona and prompt configuration for this domain. "
                    "Provide persona.text for best quality responses.",
    )


# ---------------------------------------------------------------------------
# Trust Layer
# ---------------------------------------------------------------------------

class InputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that block user input and return blocked_message")
    escalation_topics: list[str] = Field(default=[], description="Strings that trigger human agent escalation")
    blocked_input_message: str = Field(
        default="",
        description="Message returned to the user when their input is blocked by Trust Layer.",
    )


class OutputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that must not appear in LLM output")
    output_blocked_message: str = Field(
        default="",
        description="Message returned to the user when LLM output is blocked by Trust Layer.",
    )


class GuardrailConfig(BaseModel):
    """A single guardrail rule within a policy pack."""

    id: str = Field(..., description="Unique guardrail identifier, e.g. GR-001")
    severity: str = Field(..., description="Guardrail severity: blocker or warning")
    failure_mode: str = Field(..., description="Action on failure: block or constrain")
    prompt_constraints: list[str] = Field(default=[], description="MUST/MUST NOT instructions injected into the LLM prompt")
    required_disclosures: list[str] = Field(default=[], description="Disclosure strings appended to LLM output when this guardrail fires")
    refusal_template: str | None = Field(default=None, description="Fixed refusal text returned when failure_mode is block")


class PolicyPackConfig(BaseModel):
    """A named policy pack grouping related risk categories and guardrail rules."""

    risks: list[str] = Field(default=[], description="Risk identifiers active in this policy pack")
    guardrails: dict[str, GuardrailConfig] = Field(
        default_factory=dict,
        description="Map of risk_name → GuardrailConfig. Keys must match entries in risks.",
    )


class ConsentConfig(BaseModel):
    """Phrase lists used by the consent classifier."""

    consent_phrases: list[str] = Field(default=[], description="Phrases that indicate user consent, e.g. ['yes', 'haan']")
    decline_phrases: list[str] = Field(default=[], description="Phrases that indicate declined consent, e.g. ['no', 'nahi']")


class HitlTrustConfig(BaseModel):
    """Human-in-the-loop queue and notification configuration."""

    queue_backend: str = Field(default="log", description="Backend for queuing HITL requests: log, redis, or webhook")
    holding_message: str = Field(default="", description="Message shown to user while waiting for a human agent")
    notification_webhook: str | None = Field(default=None, description="Webhook URL notified when an HITL case is queued")


class TrustConfig(BaseModel):
    policy_pack: str = Field(
        default="",
        description="Name of the active policy pack from policy_packs. Must match a key in policy_packs if set.",
    )
    input_rules: InputRulesConfig = InputRulesConfig()
    output_rules: OutputRulesConfig = OutputRulesConfig()
    policy_packs: dict[str, PolicyPackConfig] = Field(
        default_factory=dict,
        description="Map of policy_pack_name → PolicyPackConfig. Each pack defines risks and guardrail rules.",
    )
    consent: ConsentConfig = Field(
        default_factory=ConsentConfig,
        description="Consent phrase lists used by the consent classifier.",
    )
    hitl: HitlTrustConfig | None = Field(
        default=None,
        description="HITL queue config. Required if agent_core uses special_handler: hitl.",
    )


class DignityCheckConfig(BaseModel):
    """Pre-response dignity check block. Mirrors trust_layer/src/schema/config.py.

    The runtime trust_layer container hard-fails at startup if any element of
    ``questions`` is not a plain string, so the dev-kit must reject anything
    else *before* writing the domain config to disk. The Configuration Agent
    has previously emitted ``[{category, severity}, …]`` dicts here from
    confused content-moderation taxonomies; explicit ``list[str]`` typing
    surfaces that as a tool error the LLM can self-correct from.
    """

    enabled: bool = False
    questions: list[str] = Field(
        default_factory=list,
        description=(
            "List of plain English (or domain-language) sentences the LLM "
            "evaluates before each response. Strings only — never dicts or "
            "category objects."
        ),
    )
    fail_action: str = Field(
        default="rewrite",
        description="One of: rewrite, flag, skip.",
    )


class TrustLayerConfig(BaseModel):
    server: ServerConfig
    trust: TrustConfig
    dignity_check: DignityCheckConfig = Field(
        default_factory=DignityCheckConfig,
        description="Pre-response dignity check (used for Conversational agents).",
    )


# ---------------------------------------------------------------------------
# Memory Layer
# ---------------------------------------------------------------------------

class RedisConfig(BaseModel):
    host: str = Field(default="redis", description="Redis hostname or IP address")
    port: int = Field(default=6379)
    db: int = Field(default=0, description="Redis database index")
    password: str | None = Field(default=None, description="Redis password. Set via env or deployment secret.")
    socket_timeout_ms: int = Field(default=2000, description="Socket read/write timeout in milliseconds")
    socket_connect_timeout_ms: int = Field(default=2000, description="Socket connection timeout in milliseconds")


class MemgraphConfig(BaseModel):
    uri: str = Field(default="bolt://memgraph:7687", description="Bolt URI for the Memgraph instance")
    user: str = Field(default="memgraph")
    password: str | None = Field(default=None, description="Memgraph password. Set via env or deployment secret.")
    connection_timeout_s: int = Field(default=5, description="Connection timeout in seconds")


class SessionStateConfig(BaseModel):
    model_config = {"populate_by_name": True}

    ttl_minutes: int = Field(
        default=60,
        description="Session TTL in minutes. Redis evicts inactive sessions after this period.",
    )
    # Field named 'schema' in YAML; aliased to avoid shadowing BaseModel.schema()
    session_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        description="Domain-specific session fields. Each key is a field name; value declares "
                    "{type, default} or {type, values, default} for enums. "
                    "Infrastructure fields (user_id, journey_id, is_returning) are auto-injected.",
    )


class UserNodeConfig(BaseModel):
    label: str = Field(..., description="Memgraph node label for the root user node, e.g. 'User'")
    key: str = Field(..., description="Property used as the unique user identifier, e.g. 'user_id'")


class GraphConfig(BaseModel):
    user_node: UserNodeConfig
    subnodes: dict[str, Any] = Field(
        default_factory=dict,
        description="Named subnode definitions attached to the user node. "
                    "Each entry declares rel, declared_fields, adhoc, child, and/or grouping. "
                    "Recognised names: UserProfile, JourneyHistory, ContextGraph.",
    )


class MergeRuleConfig(BaseModel):
    session_field: str = Field(..., description="Session field whose final value is promoted at flush_session()")
    target: str = Field(
        ...,
        description="Destination property or node label, e.g. 'Journey.mental_state_at_end' or 'Role'",
    )


class PersistentStateConfig(BaseModel):
    backend: str = Field(default="memgraph", description="Persistent storage backend identifier")
    graph: GraphConfig
    merge_on_session_end: list[MergeRuleConfig] = Field(
        default=[],
        description="Rules for promoting session fields to graph node properties when the session is flushed",
    )


class StateConfig(BaseModel):
    session: SessionStateConfig = Field(default_factory=SessionStateConfig)
    persistent: PersistentStateConfig


class UserDataPersistenceConfig(BaseModel):
    default_mode: Literal["saved", "anonymous"] = Field(
        default="saved",
        description="Default storage mode. 'saved' retains Memgraph data across sessions. "
                    "'anonymous' deletes all graph data at session end (DPDP-compliant erasure).",
    )


class AuditConfig(BaseModel):
    db_path: str = Field(default="audit.db", description="Path to the SQLite audit log database file")


class ReengagementTriggerConfig(BaseModel):
    event: str = Field(..., description="Drop-off event code that triggers this rule, e.g. DOP_MT, DOP_EG, DOP_RL")
    delay_hours: int | None = Field(default=None, description="Hours after the event before re-engagement fires")
    loop_threshold: int | None = Field(default=None, description="Loop count threshold before action fires (used for DOP_RL)")
    channel: str | None = Field(default=None, description="Re-engagement channel, e.g. outbound_call, whatsapp")
    message_template: str | None = Field(default=None, description="Message template identifier for the re-engagement message")
    action: str | None = Field(default=None, description="Framework action to perform, e.g. hitl_counsellor")


class ReengagementConfig(BaseModel):
    triggers: list[ReengagementTriggerConfig] = Field(
        default=[],
        description="List of re-engagement trigger rules executed after drop-off events",
    )


class MemoryLayerConfig(BaseModel):
    server: ServerConfig
    redis: RedisConfig = Field(
        default_factory=RedisConfig,
        description="Redis connection config for session (turn/session scope) storage",
    )
    memgraph: MemgraphConfig = Field(
        default_factory=MemgraphConfig,
        description="Memgraph connection config for persistent (cross-session) user profile storage",
    )
    state: StateConfig = Field(
        ...,
        description="Session and persistent state configuration. Must be provided in domain config.",
    )
    user_data_persistence: UserDataPersistenceConfig = Field(
        default_factory=UserDataPersistenceConfig,
        description="Controls the default user data retention policy (saved vs anonymous)",
    )
    audit: AuditConfig = Field(
        default_factory=AuditConfig,
        description="SQLite audit log configuration for DPDP-compliant consent and data access records",
    )
    reengagement: ReengagementConfig | None = Field(
        default=None,
        description="Re-engagement trigger rules. Optional — omit if not using re-engagement.",
    )


# ---------------------------------------------------------------------------
# Observability Layer
# ---------------------------------------------------------------------------

class OtelConfig(BaseModel):
    """OpenTelemetry collector connection settings."""

    collector_endpoint: str = Field(default="http://otelcol:4317", description="gRPC endpoint of the OTEL collector")
    sample_rate: float = Field(default=1.0, description="Trace sampling rate, 0.0–1.0")
    export_interval_ms: int = Field(default=5000, description="Metric export interval in milliseconds")


class TelemetryConfig(BaseModel):
    pii_fields_excluded: list[str] = Field(
        default=["user_message"],
        description="Field names stripped from telemetry spans before export",
    )


class SliConfig(BaseModel):
    turn_latency_p99_ms: int = Field(default=1200, description="P99 turn latency SLI threshold in milliseconds")
    trust_block_rate_max: float = Field(default=0.05, description="Maximum acceptable Trust Layer block rate (0.0–1.0)")


class AuditObsConfig(BaseModel):
    pii_fields_excluded: list[str] = Field(
        default=["user_message", "user_id"],
        description="Field names stripped from audit log entries",
    )
    retention_days: int = Field(default=90, description="Days to retain audit records before deletion")


class LifecycleStateConfig(BaseModel):
    state: str = Field(..., description="Outcome lifecycle state name, e.g. enquiry, applied, placed")
    trigger_tool: str | None = Field(default=None, description="Tool call that triggers this state transition. None means set on session start.")
    trigger_condition: str | None = Field(default=None, description="Python-style condition expression evaluated against tool result")


class MetricConfig(BaseModel):
    name: str = Field(..., description="Metric name, e.g. placement.applications")
    instrument: str = Field(..., description="OTEL instrument type: counter, gauge, or histogram")
    description: str = Field(default="", description="Human-readable metric description")
    unit: str = Field(default="", description="Optional unit string, e.g. '%', 'ms'")
    attributes: list[str] = Field(default=[], description="OTEL attribute keys attached to each data point")


class OutcomesConfig(BaseModel):
    lifecycle: list[LifecycleStateConfig] = Field(
        default=[],
        description="Ordered list of domain outcome states and their trigger conditions",
    )
    metrics: list[MetricConfig] = Field(
        default=[],
        description="Custom OTEL metrics emitted by the Outcome Tracker",
    )


class ObservabilitySettings(BaseModel):
    """Top-level observability settings — merged from DPG defaults and domain config."""

    domain: str = Field(default="", description="Domain identifier attached to all telemetry spans and metrics")
    otel: OtelConfig = Field(default_factory=OtelConfig, description="OpenTelemetry collector connection settings")
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig, description="Telemetry PII exclusion settings")
    audit: AuditObsConfig = Field(default_factory=AuditObsConfig, description="Audit log PII exclusion and retention settings")
    sli: SliConfig = Field(default_factory=SliConfig, description="SLI/SLO threshold definitions")
    outcomes: OutcomesConfig = Field(
        default_factory=OutcomesConfig,
        description="Domain outcome lifecycle and custom metric definitions",
    )


class ObservabilityLayerConfig(BaseModel):
    server: ServerConfig
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


# ---------------------------------------------------------------------------
# Action Gateway
# ---------------------------------------------------------------------------


class ToolParamDef(BaseModel):
    """Definition of a single parameter for a REST API tool endpoint."""

    name: str = Field(..., description="Parameter name")
    source: Literal["agent", "static"] = Field(
        ..., description="'agent' = LLM fills this at call time; 'static' = fixed value"
    )
    type: Literal["string", "integer", "boolean", "array"] = Field(default="string", description="JSON type")
    required: bool = Field(default=False, description="Whether the agent must provide this param")
    description: str = Field(default="", description="Description shown to the agent")
    value: Any = Field(default=None, description="Fixed value when source is 'static'")
    default: Any = Field(default=None, description="Default value when source is 'agent' and param is optional")


class ToolEndpointDef(BaseModel):
    """One HTTP endpoint within a REST API tool definition."""

    name: str = Field(..., description="Endpoint name, e.g. 'search', 'apply'")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(..., description="HTTP method")
    path: str = Field(..., description="Path appended to base_url, e.g. '/search'")
    params: list[ToolParamDef] = Field(default=[], description="Parameters for this endpoint")


class ToolResponseConfig(BaseModel):
    """Response handling config for a REST API tool."""

    max_size_chars: int = Field(default=4000, description="Truncate response body to this many characters before returning to agent")


class AuthConfig(BaseModel):
    """Authentication configuration for a REST API tool."""

    type: Literal["none", "api_key", "bearer", "oauth2"] = Field(
        ..., description="Auth scheme: none | api_key | bearer | oauth2"
    )
    header: str | None = Field(default=None, description="Header name for api_key auth, e.g. X-API-KEY")
    secret_env: str | None = Field(default=None, description="Environment variable holding the API key or token")
    token_url: str | None = Field(default=None, description="Token endpoint URL for oauth2")

    @model_validator(mode="after")
    def _validate_auth_fields(self) -> "AuthConfig":
        """Validate that required fields are present for each auth type."""
        if self.type == "api_key" and not self.secret_env:
            raise ValueError("api_key auth requires secret_env")
        if self.type == "bearer" and not self.secret_env:
            raise ValueError("bearer auth requires secret_env")
        if self.type == "oauth2" and not self.token_url:
            raise ValueError("oauth2 auth requires token_url")
        return self


class RestApiToolDef(BaseModel):
    """Full definition of a REST API tool executed by the Action Gateway."""

    id: str = Field(..., description="Unique tool identifier — must match name in agent_core connectors")
    type: Literal["rest_api"] = Field(default="rest_api")
    category: Literal["read", "write", "identity"] = Field(
        ..., description="Tool category: read (no consent), write/identity (Trust Layer consent required)"
    )
    description: str = Field(..., description="What this tool does — shown to LLM for routing decisions")
    base_url: str = Field(..., description="Base URL of the API, e.g. https://api.example.com/v1")
    auth: AuthConfig = Field(..., description="Authentication scheme for this API")
    timeout_ms: int = Field(default=5000, description="Request timeout in milliseconds")
    endpoints: list[ToolEndpointDef] = Field(default=[], description="One or more endpoint definitions")
    response: ToolResponseConfig = Field(default_factory=ToolResponseConfig, description="Response handling config")


class McpToolDef(BaseModel):
    """Full definition of an MCP server tool executed by the Action Gateway."""

    id: str = Field(..., description="Unique tool identifier — must match name in agent_core connectors")
    type: Literal["mcp"] = Field(default="mcp")
    category: Literal["read", "write", "identity"] = Field(..., description="Tool category")
    description: str = Field(..., description="What this tool does — shown to LLM")
    mcp_server_url: str = Field(..., description="Base URL of the MCP server")
    tool_name: str = Field(..., description="Tool name as returned by tools/list on the MCP server")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the tool input, as returned by MCP tools/list"
    )
    timeout_ms: int = Field(default=5000, description="Request timeout in milliseconds")


ToolDef = Annotated[RestApiToolDef | McpToolDef, Field(discriminator="type")]


class ActionGatewayConfig(BaseModel):
    """Top-level config for the Action Gateway domain config file."""

    tools: list[ToolDef] = Field(
        default=[],
        description="List of tool definitions. Each entry is either a rest_api or mcp tool."
    )
    observability: dict[str, Any] = Field(
        default_factory=dict,
        description="Observability settings. At minimum: {domain: 'your_domain_slug'}"
    )

    @model_validator(mode="after")
    def _validate_unique_tool_ids(self) -> "ActionGatewayConfig":
        """Validate that all tool IDs are unique within the config."""
        ids = [t.id for t in self.tools]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"Duplicate tool ids: {dupes}")
        return self


# ---------------------------------------------------------------------------
# Reach Layer
# ---------------------------------------------------------------------------


class CLIChannelConfig(BaseModel):
    """Configuration for the CLI (stdin/stdout) channel adapter."""

    prompt: str = Field(default="You: ", description="Prompt prefix shown before user input")
    agent_prefix: str = Field(default="Agent: ", description="Prefix shown before agent replies")


class WebAuthConfig(BaseModel):
    """Authentication settings for the web channel."""

    enabled: bool = Field(default=False, description="Whether Google OAuth is enabled")
    google_client_id: str = Field(default="", description="Google OAuth2 client ID")
    cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on session cookie. False for local http:// dev.",
    )


class WebChannelConfig(BaseModel):
    """Configuration for the web channel adapter (React frontend)."""

    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Web UI branding and copy. Common keys: app_name, app_tagline, app_icon, "
            "agent_avatar, user_avatar, setup_heading, setup_subtitle, user_id_placeholder, "
            "user_id_hint, start_btn_label, new_session_msg, returning_user_msg, "
            "storage_key, theme_storage_key, sign_out_confirm, switch_user_confirm, "
            "delete_conversation_confirm"
        ),
    )


class RayaSTTTTSConfig(BaseModel):
    """Raya STT/TTS configuration for the voice channel."""

    stt_language: str = Field(..., description="BCP-47 language code for speech-to-text, e.g. 'hi', 'en'")
    tts_language: str = Field(..., description="BCP-47 language code for text-to-speech")
    voice_id: str = Field(..., description="Voice ID for the TTS provider")


class VoiceAgentCoreConfig(BaseModel):
    """Agent Core connection settings for the voice channel."""

    timeout_ms: int = Field(default=15000, description="Agent Core call timeout in milliseconds")
    greeting: str = Field(default="", description="First message spoken to the user when voice session starts")
    fallback_phrase: str = Field(default="", description="Phrase spoken when STT fails or input is unintelligible")


class VoiceChannelConfig(BaseModel):
    """Configuration for the voice (VOIP/Raya) channel adapter.

    GH-247: ``terminal_word``, ``filler_threshold_ms``, and ``filler_phrase``
    moved here from ``agent_core.channels.voice`` — they are read by the
    reach_layer voice service, not agent_core, so co-locating them with the
    other voice-runtime knobs (raya, agent_core client) keeps the source of
    truth where the consumer is.
    """

    raya: RayaSTTTTSConfig = Field(..., description="Raya STT/TTS language and voice settings")
    agent_core: VoiceAgentCoreConfig = Field(
        default_factory=VoiceAgentCoreConfig,
        description="Agent Core connection settings for voice",
    )
    terminal_word: str | None = Field(
        default=None,
        description=(
            "Required when voice is deployed. Word spoken just before the "
            "WebSocket close on bot-initiated hangup (DoneEvent.session_ended=true)."
        ),
    )
    filler_threshold_ms: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Push the filler utterance if no SentenceEvent has reached TTS "
            "within this many ms of submit_input completing. Cancelled the "
            "moment a real sentence arrives. Set to null to disable."
        ),
    )
    filler_phrase: str | None = Field(
        default=None,
        description=(
            "Short phrase spoken when filler_threshold_ms elapses without a "
            "real sentence (e.g. 'एक सेकंड'). Empty string also disables."
        ),
    )


class ChannelsConfig(BaseModel):
    """Per-channel configuration. Omit channels that are not deployed."""

    cli: CLIChannelConfig | None = Field(default=None, description="CLI channel config. None = not deployed.")
    web: WebChannelConfig | None = Field(default=None, description="Web channel config. None = not deployed.")
    voice: VoiceChannelConfig | None = Field(default=None, description="Voice channel config. None = not deployed.")


class CommonReachConfig(BaseModel):
    """Common settings shared across all channels."""

    observability: dict[str, Any] = Field(
        default_factory=dict,
        description="Observability settings. At minimum: {domain: 'your_domain_slug'}",
    )


class ReachLayerSettings(BaseModel):
    """Top-level reach layer settings wrapping channel configs."""

    common: CommonReachConfig = Field(default_factory=CommonReachConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


class AgentCoreClientConfig(BaseModel):
    """Agent Core HTTP client settings for the Reach Layer."""

    endpoint: str = Field(default="http://agent_core:8000", description="Agent Core base URL")
    timeout_s: float = Field(default=30.0, description="HTTP request timeout in seconds")


class ReachLayerConfig(BaseModel):
    """Top-level config for the Reach Layer domain config file."""

    server: ServerConfig = Field(
        default_factory=lambda: ServerConfig(port=3000),
        description="HTTP server bind settings for the Reach Layer",
    )
    reach_layer: ReachLayerSettings = Field(default_factory=ReachLayerSettings)
    agent_core_client: AgentCoreClientConfig = Field(
        default_factory=AgentCoreClientConfig,
        description="Agent Core HTTP client settings",
    )


# ---------------------------------------------------------------------------
# Partial validation helper
# ---------------------------------------------------------------------------

_BLOCK_MODEL_MAP: dict[str, type] = {
    "agent_core": AgentCoreConfig,
    "knowledge_engine": KnowledgeEngineConfig,
    "trust_layer": TrustLayerConfig,
    "memory_layer": MemoryLayerConfig,
    "observability_layer": ObservabilityLayerConfig,
    "action_gateway": ActionGatewayConfig,
    "reach_layer": ReachLayerConfig,
}


# ---------------------------------------------------------------------------
# Raya TTS voices — first voice per language from https://hub.getraya.app/v1/voices
# Used by validate_partial to reject hallucinated voice IDs.
# ---------------------------------------------------------------------------
RAYA_VOICES: dict[str, dict[str, str]] = {
    "c849b31b-b0ba-488f-b97d-3fd12f2656f4": {"name": "Sneha", "language": "mr"},
    "d6a002d0-230c-49b1-a137-b8a7d564b1ae": {"name": "Priyanka", "language": "hi"},
    "25a7c7d9-57b3-488a-a880-33edf6642902": {"name": "Tanvi", "language": "te"},
    "6a897d02-83ab-43ea-b17f-a8cc2d96a279": {"name": "Meera", "language": "kn"},
    "a1b2c3d4-e5f6-4789-a012-b3c4d5e6f789": {"name": "Aishwarya", "language": "bn"},
    "d4e5f6a7-b8c9-4a01-d345-e6f7a8b9c012": {"name": "Priti", "language": "as"},
    "9a01bcde-2345-6789-abc1-123456abcdef": {"name": "Jignesh", "language": "gu"},
    "0f24fb66-e495-4781-9e84-1224aa7dacde": {"name": "Nayra", "language": "en-in"},
    "90534e23-8bcb-4b1c-a16b-b9a4be646321": {"name": "Solene", "language": "en-us"},
    "57a1e849-8e0f-43ee-adab-b4b74a9d79e1": {"name": "Devika", "language": "ml"},
    "5d6c7ee4-2563-4dab-9c8a-c3269e22cba9": {"name": "Ritu", "language": "ne"},
    "fed6231c-7e35-4fbe-bbca-254f566e5dd5": {"name": "Abirami", "language": "ta"},
}

VALID_RAYA_VOICE_IDS: frozenset[str] = frozenset(RAYA_VOICES.keys())


def _validate_raya_voice_id(data: dict) -> list[str]:
    """Check that voice_id in reach_layer config is a known Raya voice UUID.

    Args:
        data: The reach_layer config dict.

    Returns:
        List of error strings. Empty if valid or voice not configured.
    """
    voice_cfg = (
        data.get("reach_layer", {})
        .get("channels", {})
        .get("voice", {})
        .get("raya", {})
    )
    voice_id = voice_cfg.get("voice_id")
    if not voice_id:
        return []
    if voice_id not in VALID_RAYA_VOICE_IDS:
        available = ", ".join(
            f"{v['name']} ({v['language']})" for v in RAYA_VOICES.values()
        )
        return [
            f"reach_layer.channels.voice.raya.voice_id: '{voice_id}' is not a valid "
            f"Raya voice ID. Available voices: {available}"
        ]
    return []


def validate_partial(block: str, data: dict) -> list[str]:
    """Validate partial config data for a block without requiring completeness.

    Runs two checks in order:
    1. Template structural check — every key in ``data`` must exist in the
       YAML template. Catches renamed keys (e.g. ``blocked_msg`` instead of
       ``blocked_message``) at every nesting level.
    2. Pydantic type check — validates value types; filters out missing-field
       errors so partial data is accepted.
    3. Domain-specific checks (e.g. Raya voice ID validation for reach_layer).

    Args:
        block: Block name, e.g. "agent_core" or "trust_layer".
        data: Partial config dict to validate.

    Returns:
        List of error strings. Empty list means valid so far.
    """
    # --- Block existence check ---
    try:
        load_template(block)
    except ValueError:
        return [f"Unknown block: {block!r}"]

    if not data:
        return []

    # --- 1. YAML template structural check: catch wrong key names at all levels ---
    try:
        template = load_template(block)
        key_errors = _check_keys_against_template(data, template, path="")
        if key_errors:
            return key_errors
    except (ValueError, FileNotFoundError) as exc:
        logger.warning(
            "validate_partial: template load failed for block %r — skipping key check",
            block,
            extra={"operation": "validate_partial", "status": "skipped", "error": str(exc)},
        )

    # --- 2. Pydantic type/value check (filters out missing-field errors) ---
    pydantic_errors: list[str] = []
    model_cls = _BLOCK_MODEL_MAP.get(block)
    if model_cls is not None:
        try:
            model_cls.model_validate(data)
        except ValidationError as exc:
            pydantic_errors = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
                if err["type"] != "missing"
            ]

    # --- 3. Domain-specific value checks ---
    domain_errors: list[str] = []
    if block == "reach_layer":
        domain_errors.extend(_validate_raya_voice_id(data))

    return pydantic_errors + domain_errors


# Open-map sentinel: template value is a dict/list whose keys are examples,
# not fixed field names. We detect these by looking for placeholder key names.
_OPEN_MAP_PLACEHOLDER_KEYS = frozenset({
    "field_name", "param_name", "connector_name", "NodeName",
    "intent_name", "doc_type_name", "value_one",
    # Trust Layer open maps
    "policy_pack_name", "guardrail_name",
})


def _check_keys_against_template(
    data: object,
    template: object,
    path: str,
) -> list[str]:
    """Recursively check that every key in ``data`` exists in ``template``.

    Detects renamed or invented keys at any nesting level. Skips open maps
    (template dicts whose only keys are placeholder names) since those accept
    arbitrary user-defined keys.

    Args:
        data: The generated config value (any type).
        template: The corresponding template value.
        path: Dot-notation path for error messages.

    Returns:
        List of error strings. Empty list means all keys are valid.
    """
    errors: list[str] = []

    if not isinstance(data, dict) or not isinstance(template, dict):
        # Not both dicts — nothing to key-check at this level.
        # If data is a list, recurse into each item against the template list item.
        if isinstance(data, list) and isinstance(template, list) and template:
            item_template = template[0]
            for i, item in enumerate(data):
                child_path = f"{path}[{i}]" if path else f"[{i}]"
                errors.extend(_check_keys_against_template(item, item_template, child_path))
        return errors

    # Check if this is an open map (template has only placeholder keys, or is
    # an empty dict {} which means "any keys accepted").
    template_keys = set(template.keys())
    if not template_keys or template_keys.issubset(_OPEN_MAP_PLACEHOLDER_KEYS):
        # Open map — any user-defined key is valid; recurse into values.
        # Empty template dict ({}) means "accept any keys, no sub-structure to validate".
        if template_keys:
            placeholder_key = next(iter(template_keys))
            item_template = template[placeholder_key]
            for key, value in data.items():
                child_path = f"{path}.{key}" if path else key
                errors.extend(_check_keys_against_template(value, item_template, child_path))
        return errors

    # Fixed-key dict — every key in data must be in the template.
    for key in data:
        child_path = f"{path}.{key}" if path else key
        if key not in template:
            errors.append(
                f"Unknown key '{child_path}' — not in the {path.split('.')[0] if path else 'root'} template. "
                f"Valid keys here: {sorted(template.keys())}"
            )
        else:
            errors.extend(_check_keys_against_template(data[key], template[key], child_path))

    return errors
