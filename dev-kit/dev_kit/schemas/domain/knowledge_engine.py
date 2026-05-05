"""Domain schemas for knowledge_engine block.

Sections written by the LLM during the knowledge phase. The 'sources' field
is intentionally omitted from this schema — the deploy wizard's IngestDocuments
step (post-deploy, via API) handles document ingestion. The LLM does not
generate sources.
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import EmbeddingProviderField


# -- Knowledge source typing (mirrors runtime KnowledgeSource) ----------

KnowledgeSourceType = Literal["static", "always_include"]
KnowledgeRefreshSchedule = Literal["manual", "annual", "monthly"]


class KnowledgeSourceEntry(BaseModel):
    """One ingestion source entry (mirrors runtime KnowledgeSource)."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., min_length=1)
    type: KnowledgeSourceType
    doc_type: str = Field(..., min_length=1)
    refresh: KnowledgeRefreshSchedule


class MetadataFiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_location_filter: bool = True
    use_intent_filter: bool = True


class StaticKnowledgeBaseSection(BaseModel):
    """RAG knowledge base configuration. All fields have sensible runtime defaults."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # collection_name pattern allows hyphens — runtime knowledge_engine accepts
    # any non-empty string (e.g. youth-schemes-kb).
    collection_name: str = Field(default="dpg_knowledge", min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    top_k: int = Field(default=3, gt=0, le=50)
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    embedding_provider: EmbeddingProviderField = "chroma_default"
    embedding_model: str = ""
    default_doc_type: str = Field(default="general", min_length=1)
    chroma_persist_dir: str = "./data/chroma_db"   # Domain-overridable storage path
    metadata_filters: MetadataFiltersConfig = Field(default_factory=MetadataFiltersConfig)
    intent_filters: dict[str, list[str]] = Field(default_factory=dict)
    # Existing domain configs declare a sources list inline (path/type/doc_type/refresh).
    # The deploy wizard's IngestDocuments step also writes here. Typed as KnowledgeSourceEntry
    # to mirror runtime strictness — schema is no longer looser than runtime.
    sources: list[KnowledgeSourceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def intent_filter_requires_mappings_when_enabled(self) -> "StaticKnowledgeBaseSection":
        if self.metadata_filters.use_intent_filter and not self.intent_filters:
            raise ValueError(
                "metadata_filters.use_intent_filter=True requires intent_filters to be non-empty "
                "(or set use_intent_filter=False to allow searching all doc_types)"
            )
        return self


class GlossaryMapping(BaseModel):
    """One glossary entry mapping colloquial terms to a canonical form."""
    model_config = ConfigDict(extra="forbid")
    colloquial: list[str] = Field(..., min_length=1)
    canonical: str = Field(..., min_length=1)


class GlossarySection(BaseModel):
    """Glossary block — colloquial → canonical normalisation."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    mappings: list[GlossaryMapping] = Field(default_factory=list)
    apply_to: list[Literal["normalised_input", "entities"]] = Field(
        default_factory=lambda: ["normalised_input", "entities"]
    )


class MultimodalInputHandlerSection(BaseModel):
    """Multimodal input handler block — image processing model selection.

    Mirrors knowledge_engine runtime block. Empty/missing = disabled.
    """
    model_config = ConfigDict(extra="forbid")
    image_model: str = ""


class KnowledgeBlocksSection(BaseModel):
    """Container for the knowledge blocks. All children optional."""
    model_config = ConfigDict(extra="forbid")
    static_knowledge_base: Optional[StaticKnowledgeBaseSection] = None
    glossary: Optional[GlossarySection] = None
    multimodal_input_handler: Optional[MultimodalInputHandlerSection] = None


class KnowledgeSection(BaseModel):
    """Top-level knowledge_engine.knowledge section."""
    model_config = ConfigDict(extra="forbid")
    blocks: KnowledgeBlocksSection


class ObservabilitySection(BaseModel):
    """knowledge_engine.observability — auto-set by devkit to project slug."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
