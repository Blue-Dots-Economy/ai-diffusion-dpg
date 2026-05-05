"""Tests for knowledge_engine domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.knowledge_engine import (
    GlossaryMapping,
    GlossarySection,
    KnowledgeBlocksSection,
    KnowledgeSection,
    MetadataFiltersConfig,
    ObservabilitySection,
    StaticKnowledgeBaseSection,
)


# -- StaticKnowledgeBaseSection ----------------------------------------------

def test_static_kb_minimal_valid_uses_defaults():
    """All fields have defaults — empty construction works (with use_intent_filter=False to skip validator)."""
    s = StaticKnowledgeBaseSection(
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
    )
    assert s.enabled is True
    assert s.collection_name == "dpg_knowledge"      # default
    assert s.default_doc_type == "general"           # default
    assert s.chroma_persist_dir == "./data/chroma_db"  # default
    assert s.top_k == 3
    assert s.similarity_threshold == 0.65


def test_static_kb_collection_name_pattern():
    """collection_name must match snake_case pattern."""
    StaticKnowledgeBaseSection(
        collection_name="kkb_docs",
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
    )
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(
            collection_name="Invalid Name",
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )


def test_static_kb_top_k_must_be_positive():
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(
            top_k=0,
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )


def test_static_kb_top_k_max_50():
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(
            top_k=51,
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )


def test_static_kb_similarity_threshold_range():
    StaticKnowledgeBaseSection(
        similarity_threshold=0.0,
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
    )
    StaticKnowledgeBaseSection(
        similarity_threshold=1.0,
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
    )
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(
            similarity_threshold=1.1,
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )


def test_static_kb_intent_filter_requires_mappings():
    """If use_intent_filter=True (default), intent_filters must be non-empty."""
    with pytest.raises(ValidationError, match="intent_filters"):
        StaticKnowledgeBaseSection(
            metadata_filters=MetadataFiltersConfig(use_intent_filter=True),
            intent_filters={},
        )


def test_static_kb_intent_filter_disabled_allows_empty():
    """If use_intent_filter=False, intent_filters can be empty."""
    s = StaticKnowledgeBaseSection(
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        intent_filters={},
    )
    assert s.intent_filters == {}


def test_static_kb_intent_filter_with_mappings_passes():
    s = StaticKnowledgeBaseSection(
        metadata_filters=MetadataFiltersConfig(use_intent_filter=True),
        intent_filters={"job_search": ["jobs", "careers"]},
    )
    assert s.intent_filters == {"job_search": ["jobs", "careers"]}


def test_static_kb_chroma_persist_dir_overridable():
    s = StaticKnowledgeBaseSection(
        chroma_persist_dir="/app/chroma_db",
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
    )
    assert s.chroma_persist_dir == "/app/chroma_db"


def test_static_kb_embedding_provider_rejects_unknown():
    """embedding_provider uses EmbeddingProviderField — must be from enums config."""
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(
            embedding_provider="not-a-valid-provider",
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )


def test_static_kb_embedding_provider_accepts_known():
    for provider in ("chroma_default", "openai", "sentence_transformers"):
        s = StaticKnowledgeBaseSection(
            embedding_provider=provider,
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        )
        assert s.embedding_provider == provider


def test_static_kb_extra_forbidden():
    with pytest.raises(ValidationError, match="Extra"):
        StaticKnowledgeBaseSection(
            metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
            vector_store="not_a_valid_field",
        )


# -- GlossarySection ---------------------------------------------------------

def test_glossary_mapping_requires_canonical():
    with pytest.raises(ValidationError):
        GlossaryMapping(colloquial=["cd"], canonical="")


def test_glossary_mapping_requires_colloquial():
    with pytest.raises(ValidationError):
        GlossaryMapping(colloquial=[], canonical="compact disc")


def test_glossary_apply_to_only_two_values():
    GlossarySection(apply_to=["normalised_input"])
    GlossarySection(apply_to=["entities"])
    GlossarySection(apply_to=["normalised_input", "entities"])
    with pytest.raises(ValidationError):
        GlossarySection(apply_to=["bogus_value"])


def test_glossary_section_default_apply_to():
    g = GlossarySection()
    assert g.apply_to == ["normalised_input", "entities"]


# -- KnowledgeSection --------------------------------------------------------

def test_knowledge_section_full_valid():
    k = KnowledgeSection(
        blocks=KnowledgeBlocksSection(
            static_knowledge_base=StaticKnowledgeBaseSection(
                metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
            )
        )
    )
    assert k.blocks.static_knowledge_base.enabled is True


def test_knowledge_blocks_section_optional_blocks():
    """Both static_knowledge_base and glossary are optional."""
    b = KnowledgeBlocksSection()
    assert b.static_knowledge_base is None
    assert b.glossary is None


# -- ObservabilitySection ----------------------------------------------------

def test_observability_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_domain_must_be_non_empty():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="")


def test_observability_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", typo_field="x")
