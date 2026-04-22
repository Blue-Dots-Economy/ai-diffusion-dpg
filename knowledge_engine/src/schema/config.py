"""
MergedConfig — strict schema for the Knowledge Engine merged runtime config.

Merged config = dev-kit/dpg/knowledge_engine.yaml (framework defaults)
                deep-merged with a domain YAML
                (e.g. dev-kit/configs/kkb/knowledge_engine.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Open-map sub-section — ``static_knowledge_base.intent_filters`` — is
modelled as ``dict[str, list[str]]`` keyed by operator-defined intent
names.

Knowledge Engine is a retrieval-only service: prompt assembly lives in
Agent Core. Domain YAML for KE therefore covers glossary, RAG tuning,
and multimodal input — no persona / language / guardrail fields.

Belongs to the Knowledge Engine DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    """How a knowledge source is retrieved relative to the query."""

    static = "static"
    always_include = "always_include"


class RefreshSchedule(str, Enum):
    """How often an ingestion source is re-read."""

    manual = "manual"
    annual = "annual"
    monthly = "monthly"


class EmbeddingProvider(str, Enum):
    """Supported embedding providers for the static KB."""

    chroma_default = "chroma_default"
    openai = "openai"
    sentence_transformers = "sentence_transformers"


class MultimodalInputType(str, Enum):
    """Supported multimodal input types."""

    pdf = "pdf"
    image = "image"


class GlossaryApplyTo(str, Enum):
    """Fields the glossary rewrites on each turn."""

    normalised_input = "normalised_input"
    entities = "entities"


# ---------------------------------------------------------------------------
# Framework / infrastructure sections
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Knowledge Engine entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8001, gt=0, lt=65536)


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
# Knowledge blocks
# ---------------------------------------------------------------------------


class GlossaryMapping(BaseModel):
    """One colloquial-to-canonical term mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    colloquial: list[str]
    canonical: str


class GlossaryBlockConfig(BaseModel):
    """Glossary normalisation block configuration.

    Attributes:
        enabled: Toggle the block on/off.
        mappings: Colloquial-to-canonical term rewrites.
        apply_to: Request fields the rewrites are applied to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    mappings: list[GlossaryMapping] = Field(default_factory=list)
    apply_to: list[GlossaryApplyTo] = Field(
        default_factory=lambda: [
            GlossaryApplyTo.normalised_input,
            GlossaryApplyTo.entities,
        ]
    )


class KnowledgeSource(BaseModel):
    """One file-backed knowledge source ingested into the vector store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    type: SourceType = SourceType.static
    doc_type: str
    refresh: RefreshSchedule = RefreshSchedule.manual


class MetadataFiltersConfig(BaseModel):
    """Runtime filters applied to a retrieval query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    use_location_filter: bool = True
    use_intent_filter: bool = True


class StaticKnowledgeBaseConfig(BaseModel):
    """Static RAG block configuration.

    Attributes:
        enabled: Toggle the block on/off.
        collection_name: ChromaDB collection name for the domain.
        chroma_persist_dir: On-disk directory for the ChromaDB volume.
        top_k: Chunks returned per query.
        similarity_threshold: Minimum cosine similarity for a returned chunk.
        embedding_provider: Which embedding backend to use.
        embedding_model: Model id for the embedding provider (when applicable).
        default_doc_type: doc_type applied when a source does not declare one.
        metadata_filters: Runtime retrieval filters.
        sources: List of ingestion sources.
        intent_filters: Intent-to-doc_type map narrowing retrieval by intent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    collection_name: str = "dpg_knowledge"
    chroma_persist_dir: str = "./data/chroma_db"
    top_k: int = Field(default=3, gt=0)
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    embedding_provider: EmbeddingProvider = EmbeddingProvider.chroma_default
    embedding_model: Optional[str] = None
    default_doc_type: str = "general"
    metadata_filters: MetadataFiltersConfig = Field(default_factory=MetadataFiltersConfig)
    sources: list[KnowledgeSource] = Field(default_factory=list)
    intent_filters: dict[str, list[str]] = Field(default_factory=dict)


class MultimodalInputHandlerConfig(BaseModel):
    """Multimodal input extraction block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    supported_types: list[MultimodalInputType] = Field(
        default_factory=lambda: [
            MultimodalInputType.pdf,
            MultimodalInputType.image,
        ]
    )
    audio_enabled: bool = False
    max_file_size_mb: int = Field(default=10, gt=0)
    image_model: str = "claude-haiku-4-5-20251001"


class KnowledgeBlocksConfig(BaseModel):
    """Container for all knowledge blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    glossary: GlossaryBlockConfig = Field(default_factory=GlossaryBlockConfig)
    static_knowledge_base: StaticKnowledgeBaseConfig = Field(
        default_factory=StaticKnowledgeBaseConfig
    )
    multimodal_input_handler: MultimodalInputHandlerConfig = Field(
        default_factory=MultimodalInputHandlerConfig
    )


class KnowledgeConfig(BaseModel):
    """Top-level ``knowledge`` section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: KnowledgeBlocksConfig = Field(default_factory=KnowledgeBlocksConfig)


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged knowledge_engine config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
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
