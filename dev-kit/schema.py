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

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


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

class ConnectorDef(BaseModel):
    name: str = Field(..., description="Connector name matching a key in action_gateway.connectors")
    description: str = Field(default="", description="Description shown to LLM explaining when to call this connector")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema object for the tool's input. Passed verbatim to the Anthropic tools API.",
    )


class InternalConnectorDef(BaseModel):
    name: str = Field(..., description="Internal connector name, e.g. knowledge_retrieval")
    route: str = Field(..., description="Internal routing destination, e.g. knowledge_engine")
    description: str = Field(default="", description="Description shown to LLM explaining when to call this connector")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema object for the tool's input.",
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
        description="Shown to user when profile collection is complete and market lookup begins.",
    )
    returning_user_greeting: str = Field(
        default="",
        description="Greeting shown to returning users whose profile already exists.",
    )


class BhashiniConfig(BaseModel):
    api_key_env: str
    user_id_env: str
    endpoint: str


class LanguageNormalisationConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for language normalisation")
    provider: str = Field(default="llm_native", description="Normalisation provider: llm_native or bhashini")
    default_language: str = Field(
        default="",
        description="Default language when none is detected from user input, e.g. hindi",
    )
    supported_languages: list[str] = Field(..., description="Languages the agent supports, e.g. [hindi, english, kannada, hinglish]")
    transliteration: bool = Field(default=True, description="Normalise transliterated input to canonical script")
    code_switching: bool = Field(default=True, description="Handle mixed-language input within a single message")
    bhashini: BhashiniConfig | None = Field(default=None, description="Required only if provider is bhashini")


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
    sentiment_classes: list[str] = Field(..., description="Sentiment classes to classify, e.g. [neutral, positive, distressed]")


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

    field: str = Field(..., description="Session field name to evaluate, e.g. income_urgency or subagent_entry_count.commitment")
    operator: Literal["eq", "not_eq", "in", "lt", "gt"] = Field(
        ..., description="Comparison operator. One of: eq, not_eq, in, lt, gt"
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
    is_start: bool = Field(default=False, description="True if this is the entry subagent for new sessions. Exactly one subagent must have is_start=true.")
    is_terminal: bool = Field(default=False, description="True if this subagent ends the conversation. Terminal subagents must have an empty routing list.")
    special_handler: Literal["hitl", "whatsapp_handoff"] | None = Field(
        default=None,
        description="Optional framework-level handler. hitl bypasses the LLM entirely. whatsapp_handoff triggers a channel handoff.",
    )
    valid_intents: list[str] = Field(
        default=[],
        description="Intents this subagent handles. Must be a subset of preprocessing.nlu_processor.intents. Must not overlap with agent_workflow.global_intents.",
    )
    tools: list[str] = Field(
        default=[],
        description="Tool names available in this subagent. Each name must match a connector in connectors.read, connectors.write, connectors.identity, or connectors.internal.",
    )
    system_prompt: str = Field(default="", description="System prompt injected for LLM calls in this subagent")
    output_format: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON schema for structured output validation. None means free-form text.",
    )
    routing: list[RoutingRuleSchema] = Field(
        default=[],
        description="Routing rules emitted from this subagent. Terminal subagents must have an empty list. Non-terminal subagents must have at least one rule.",
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
        description="Intents handled globally before subagent routing, e.g. counsellor_request, termination_intent. Must not appear in any subagent's valid_intents.",
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
        description="All subagent definitions. Must contain exactly one subagent with is_start=true.",
    )


class AgentCoreConfig(BaseModel):
    server: ServerConfig
    agent: AgentConfig
    conversation: ConversationAgentConfig
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
        description="HITL (human-in-the-loop) config. Required if any subagent uses special_handler: hitl.",
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
    sources: list[KnowledgeSource] = Field(
        default=[],
        description="Knowledge sources to ingest. Each: {path, type, doc_type, refresh}",
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
    text: str


class ConversationKEConfig(BaseModel):
    persona: PersonaConfig
    language_instruction: str = ""
    guardrail_reminders: list[str] = []


class KnowledgeEngineConfig(BaseModel):
    server: ServerConfig
    knowledge: KnowledgeConfig
    conversation: ConversationKEConfig


# ---------------------------------------------------------------------------
# Trust Layer
# ---------------------------------------------------------------------------

class InputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that block user input and return blocked_message")
    escalation_topics: list[str] = Field(default=[], description="Strings that trigger human agent escalation")


class OutputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that must not appear in LLM output")


class TrustConfig(BaseModel):
    input_rules: InputRulesConfig = InputRulesConfig()
    output_rules: OutputRulesConfig = OutputRulesConfig()


class TrustLayerConfig(BaseModel):
    server: ServerConfig
    trust: TrustConfig


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
    ttl_minutes: int = Field(
        default=60,
        description="Session TTL in minutes. Redis evicts inactive sessions after this period.",
    )
    schema: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific session fields. Each key is a field name; value is "
                    "{type, default} or {type, values, default} for enums. "
                    "Infrastructure fields (user_id, journey_id, is_returning) are injected automatically.",
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
        description="Destination property or node label. e.g. 'Journey.mental_state_at_end' or 'Role'",
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
        description="Default storage mode. 'saved' retains Neo4j/Memgraph data across sessions. "
                    "'anonymous' deletes all graph data at session end (DPDP-compliant erasure).",
    )


class AuditConfig(BaseModel):
    db_path: str = Field(default="audit.db", description="Path to the SQLite audit log database file")


class ReengagementTriggerConfig(BaseModel):
    event: str = Field(..., description="Drop-off event code that triggers this rule, e.g. DOP_MT, DOP_EG, DOP_RL")
    delay_hours: int | None = Field(default=None, description="Hours after the event before re-engagement fires")
    loop_threshold: int | None = Field(default=None, description="Loop count threshold before action fires (used for DOP_RL)")
    channel: str | None = Field(default=None, description="Re-engagement channel, e.g. outbound_call")
    message_template: str | None = Field(default=None, description="Message template identifier for the re-engagement message")
    action: str | None = Field(default=None, description="Framework action to perform, e.g. hitl_counsellor")


class ReengagementConfig(BaseModel):
    triggers: list[ReengagementTriggerConfig] = Field(
        default=[],
        description="List of re-engagement trigger rules executed by the Learning Layer",
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
        description="Session and persistent state configuration. Must be provided by domain config.",
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
        description="Re-engagement trigger rules. Optional — omit if the domain does not use re-engagement.",
    )


# ---------------------------------------------------------------------------
# Observability Layer
# ---------------------------------------------------------------------------

class ObservabilityLayerSettings(BaseModel):
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")


class ObservabilityLayerConfig(BaseModel):
    server: ServerConfig
    observability_layer: ObservabilityLayerSettings


# ---------------------------------------------------------------------------
# Action Gateway
# ---------------------------------------------------------------------------

class ConnectorEndpointConfig(BaseModel):
    endpoint: str
    timeout_ms: int = 5000


class ActionGatewaySettings(BaseModel):
    timeout_ms: int = 5000
    connectors: dict[str, ConnectorEndpointConfig] = Field(
        default={},
        description="Map of connector_name → {endpoint, timeout_ms}. Keys must match names declared in agent_core connectors",
    )


class ActionGatewayConfig(BaseModel):
    server: ServerConfig = Field(default_factory=lambda: ServerConfig(port=9999))
    action_gateway: ActionGatewaySettings


# ---------------------------------------------------------------------------
# Reach Layer
# ---------------------------------------------------------------------------

class CLIConfig(BaseModel):
    prompt: str = "You: "
    agent_prefix: str = "Agent: "


class ReachLayerSettings(BaseModel):
    cli: CLIConfig = CLIConfig()


class AgentCoreClientConfig(BaseModel):
    endpoint: str
    timeout_s: float = 30.0


class ReachLayerConfig(BaseModel):
    server: ServerConfig
    reach_layer: ReachLayerSettings
    agent_core_client: AgentCoreClientConfig
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description="Web UI configuration for the web channel adapter. "
                    "Keys vary by domain. Common keys: app_name, app_tagline, app_icon, "
                    "storage_key, setup_heading, new_session_msg, returning_user_msg.",
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


def validate_partial(block: str, data: dict) -> list[str]:
    """Validate partial config data for a block without requiring completeness.

    Runs schema validation but filters out missing-field errors so configs
    that are still being built do not fail.

    Args:
        block: Block name, e.g. "agent_core" or "trust_layer".
        data: Partial config dict to validate.

    Returns:
        List of error strings for type/value violations. Empty list means valid so far.
    """
    model_cls = _BLOCK_MODEL_MAP.get(block)
    if model_cls is None:
        return [f"Unknown block: {block!r}"]
    try:
        model_cls.model_validate(data)
        return []
    except ValidationError as exc:
        return [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()
            if err["type"] != "missing"
        ]
