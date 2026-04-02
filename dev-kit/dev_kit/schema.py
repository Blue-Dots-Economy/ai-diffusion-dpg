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
  LearningLayerConfig
  ActionGatewayConfig
  ReachLayerConfig
"""

from __future__ import annotations

from typing import Any

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


class ConnectorsConfig(BaseModel):
    read: list[ConnectorDef] = []
    write: list[ConnectorDef] = []
    identity: list[ConnectorDef] = []


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
        description="Shown to user when input is blocked by Trust Layer. Translate to user language.",
    )
    escalation_message: str = Field(
        default="I'm connecting you to a human agent who can better assist you.",
        description="Shown when turn is escalated to a human agent.",
    )
    output_blocked_message: str = Field(
        default="I wasn't able to produce a safe response. Please try rephrasing your question.",
        description="Shown when LLM output is blocked by Trust Layer.",
    )


class BhashiniConfig(BaseModel):
    api_key_env: str
    user_id_env: str
    endpoint: str


class LanguageNormalisationConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for language normalisation")
    provider: str = Field(default="llm_native", description="Normalisation provider: llm_native or bhashini")
    supported_languages: list[str] = Field(..., description="Languages the agent supports, e.g. [hindi, english, kannada, hinglish]")
    transliteration: bool = Field(default=True, description="Normalise transliterated input to canonical script")
    code_switching: bool = Field(default=True, description="Handle mixed-language input within a single message")
    bhashini: BhashiniConfig | None = Field(default=None, description="Required only if provider is bhashini")


class NLUProcessorConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for NLU classification")
    confidence_threshold: float = Field(default=0.5, description="Float 0-1. Intents below this are treated as unknown")
    history_turns: int = Field(default=2)
    intents: list[str] = Field(..., description="List of intent identifiers for this domain, e.g. greeting, profile_answer, apply_now")
    entities: list[str] = Field(..., description="List of entity identifiers to extract, e.g. name, location, trade_or_stream")
    sentiment_classes: list[str] = Field(..., description="Sentiment classes to classify, e.g. [neutral, positive, distressed]")


class PreprocessingConfig(BaseModel):
    language_normalisation: LanguageNormalisationConfig
    nlu_processor: NLUProcessorConfig


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

class MemoryConfig(BaseModel):
    session_ttl_seconds: int = 3600
    max_sessions: int = 1000


class MemoryLayerConfig(BaseModel):
    server: ServerConfig
    memory: MemoryConfig


# ---------------------------------------------------------------------------
# Learning Layer
# ---------------------------------------------------------------------------

class LearningLayerSettings(BaseModel):
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")


class LearningLayerConfig(BaseModel):
    server: ServerConfig
    learning_layer: LearningLayerSettings


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


# ---------------------------------------------------------------------------
# Partial validation helper
# ---------------------------------------------------------------------------

_BLOCK_MODEL_MAP: dict[str, type] = {
    "agent_core": AgentCoreConfig,
    "knowledge_engine": KnowledgeEngineConfig,
    "trust_layer": TrustLayerConfig,
    "memory_layer": MemoryLayerConfig,
    "learning_layer": LearningLayerConfig,
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
