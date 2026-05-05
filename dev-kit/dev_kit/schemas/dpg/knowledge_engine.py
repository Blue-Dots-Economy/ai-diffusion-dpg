"""DPG framework defaults schema for the Knowledge Engine block.

Validates the operator-edited ``dev-kit/dpg/knowledge_engine.yaml``.
"""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.dpg.agent_core import ServerConfig, OtelConfig


class MetadataFiltersDpg(BaseModel):
    """Toggles for metadata-based filtering at retrieval time."""

    model_config = ConfigDict(extra="forbid")
    use_location_filter: bool = True
    use_intent_filter: bool = True


class StaticKbDpg(BaseModel):
    """Defaults for the static knowledge-base retriever (semantic RAG)."""

    model_config = ConfigDict(extra="forbid")
    embedding_provider: str = "chroma_default"
    embedding_model: Optional[str] = None
    top_k: int = Field(default=3, gt=0, le=50)
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    metadata_filters: MetadataFiltersDpg = Field(default_factory=MetadataFiltersDpg)


class MultimodalDpg(BaseModel):
    """Defaults for the multimodal input handler block."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    supported_types: list[str] = Field(default_factory=lambda: ["pdf", "image"])
    audio_enabled: bool = False
    max_file_size_mb: int = Field(default=10, gt=0, le=500)


class KnowledgeBlocksDpg(BaseModel):
    """Container for the framework-default sub-blocks of the Knowledge Engine."""

    model_config = ConfigDict(extra="forbid")
    static_knowledge_base: StaticKbDpg = Field(default_factory=StaticKbDpg)
    multimodal_input_handler: MultimodalDpg = Field(default_factory=MultimodalDpg)


class KnowledgeDpg(BaseModel):
    """Top-level ``knowledge`` section in the framework defaults YAML."""

    model_config = ConfigDict(extra="forbid")
    blocks: KnowledgeBlocksDpg


class ObservabilityDpg(BaseModel):
    """Knowledge Engine observability section (OTel only at the framework layer)."""

    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig


class KnowledgeEngineDpgConfig(BaseModel):
    """Validated against the operator-edited dev-kit/dpg/knowledge_engine.yaml."""

    model_config = ConfigDict(extra="forbid")
    server: ServerConfig
    knowledge: KnowledgeDpg
    observability: ObservabilityDpg
