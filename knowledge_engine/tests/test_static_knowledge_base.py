"""
tests/test_static_knowledge_base.py

Unit tests for StaticKnowledgeBaseBlock (Block 4).
ChromaDB and embeddings are mocked — no real vector store or embedding calls.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.blocks.static_knowledge_base import StaticKnowledgeBaseBlock
from src.base import KEContext
from src.models import SessionState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "knowledge": {
        "blocks": {
            "static_knowledge_base": {
                "enabled": True,
                "vector_store": "chromadb",
                "collection_name": "dpg_knowledge",
                "chroma_persist_dir": "/tmp/test_chroma",
                "embedding_provider": "sentence_transformers",
                "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
                "top_k": 3,
                "similarity_threshold": 0.65,
                "sources": [],
                "metadata_filters": {
                    "use_location_filter": True,
                    "use_intent_filter": True,
                },
                "intent_filters": {
                    "market_truth_query": ["scheme", "trade", "bridge_income"],
                    "scheme_query": ["scheme"],
                    "training_query": ["trade", "institute"],
                    "pay_range_query": ["trade"],
                    "apply_now": ["scheme", "institute"],
                    "counsellor_request": [],
                    "unknown": [],
                },
            }
        }
    }
}


def make_context(
    normalised_input: str = "electrician kaam chahiye Hubli mein",
    intent: str = "market_truth_query",
    entities: dict = None,
) -> KEContext:
    return KEContext(
        session_id="test-session",
        raw_input=normalised_input,
        normalised_input=normalised_input,
        detected_language="hinglish",
        intent=intent,
        entities=entities or {},
        sentiment="neutral",
        confidence=0.85,
        retrieval_chunks=[],
        always_include_chunks=[],
        session_state=SessionState.empty("test-session"),
    )


def make_mock_collection(
    query_docs=None,
    query_metas=None,
    query_distances=None,
    always_include_docs=None,
    always_include_metas=None,
):
    """Build a mock ChromaDB collection with configurable query responses."""
    collection = MagicMock()

    # Default: one relevant chunk, close distance (high similarity)
    collection.query.return_value = {
        "documents": [query_docs or ["ITI electrician training in Hubli district"]],
        "metadatas": [query_metas or [{"doc_type": "trade", "source": "trade_descriptions.pdf"}]],
        "distances": [query_distances or [0.3]],  # similarity ≈ 0.85
    }

    collection.get.return_value = {
        "documents": always_include_docs or ["Always include: market truth framing"],
        "metadatas": always_include_metas or [{"doc_type": "always_include"}],
    }

    return collection


@pytest.fixture
def block():
    return StaticKnowledgeBaseBlock()


@pytest.fixture
def mock_llm():
    return MagicMock()


# ---------------------------------------------------------------------------
# Helper: inject mock collection into block
# ---------------------------------------------------------------------------


def with_mock_collection(block, collection):
    """Inject a mock collection directly into the block instance."""
    block._collection = collection
    return block


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


def test_relevant_chunk_returned_above_threshold(block, mock_llm):
    collection = make_mock_collection(
        query_docs=["Electrician training available in Hubli ITI"],
        query_distances=[0.2],  # similarity = 0.9
    )
    with_mock_collection(block, collection)

    ctx = make_context(intent="market_truth_query")
    result = block.process(ctx, mock_llm, CONFIG)

    assert len(result.retrieval_chunks) == 1
    assert "Electrician" in result.retrieval_chunks[0]["text"]


def test_always_include_chunk_always_present(block, mock_llm):
    collection = make_mock_collection(
        always_include_docs=["Market truth framing for ONEST data"],
        always_include_metas=[{"doc_type": "always_include"}],
    )
    with_mock_collection(block, collection)

    ctx = make_context()
    result = block.process(ctx, mock_llm, CONFIG)

    assert len(result.always_include_chunks) == 1
    assert "Market truth" in result.always_include_chunks[0]["text"]


def test_intent_filter_applied_for_scheme_query(block, mock_llm):
    collection = make_mock_collection()
    with_mock_collection(block, collection)

    ctx = make_context(intent="scheme_query")
    block.process(ctx, mock_llm, CONFIG)

    call_kwargs = collection.query.call_args[1]
    where = call_kwargs.get("where", {})
    # scheme_query should filter by doc_type scheme
    assert "scheme" in str(where)


def test_intent_filter_applied_for_training_query(block, mock_llm):
    collection = make_mock_collection()
    with_mock_collection(block, collection)

    ctx = make_context(intent="training_query")
    block.process(ctx, mock_llm, CONFIG)

    call_kwargs = collection.query.call_args[1]
    where = call_kwargs.get("where", {})
    assert where is not None


def test_location_filter_applied_when_entity_present(block, mock_llm):
    collection = make_mock_collection()
    with_mock_collection(block, collection)

    ctx = make_context(entities={"location": "Hubli"})
    block.process(ctx, mock_llm, CONFIG)

    call_kwargs = collection.query.call_args[1]
    where = call_kwargs.get("where", {})
    assert "Hubli" in str(where)


def test_chunk_below_threshold_excluded(block, mock_llm):
    """Distance 1.4 → similarity 0.3 — below threshold of 0.65."""
    collection = make_mock_collection(
        query_docs=["Irrelevant content"],
        query_distances=[1.4],
    )
    with_mock_collection(block, collection)

    ctx = make_context()
    result = block.process(ctx, mock_llm, CONFIG)

    assert result.retrieval_chunks == []


def test_multiple_chunks_threshold_filtering(block, mock_llm):
    """Two chunks: one above threshold, one below."""
    collection = make_mock_collection(
        query_docs=["Relevant chunk", "Irrelevant chunk"],
        query_metas=[{"doc_type": "trade"}, {"doc_type": "institute"}],
        query_distances=[0.2, 1.5],   # similarities: 0.9, 0.25
    )
    with_mock_collection(block, collection)

    ctx = make_context()
    result = block.process(ctx, mock_llm, CONFIG)

    assert len(result.retrieval_chunks) == 1
    assert result.retrieval_chunks[0]["text"] == "Relevant chunk"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_results_returns_empty_chunks_no_error(block, mock_llm):
    collection = make_mock_collection(
        query_docs=[],
        query_metas=[],
        query_distances=[],
        always_include_docs=[],
        always_include_metas=[],
    )
    collection.query.return_value = {
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }
    collection.get.return_value = {"documents": [], "metadatas": []}
    with_mock_collection(block, collection)

    ctx = make_context(normalised_input="")
    result = block.process(ctx, mock_llm, CONFIG)

    assert result.retrieval_chunks == []
    assert result.always_include_chunks == []


def test_disabled_block_passes_context_unchanged(block, mock_llm):
    disabled_config = {
        "knowledge": {
            "blocks": {
                "static_knowledge_base": {"enabled": False}
            }
        }
    }
    ctx = make_context()
    result = block.process(ctx, mock_llm, disabled_config)
    assert result.retrieval_chunks == []
    assert result.always_include_chunks == []


def test_raw_input_never_modified(block, mock_llm):
    collection = make_mock_collection()
    with_mock_collection(block, collection)
    original = "electrician kaam chahiye"
    ctx = make_context(normalised_input=original)
    block.process(ctx, mock_llm, CONFIG)
    assert ctx.raw_input == original


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------


def test_chromadb_query_exception_returns_empty_chunks(block, mock_llm):
    """ChromaDB query failure → empty chunks, no crash."""
    collection = MagicMock()
    collection.query.side_effect = RuntimeError("ChromaDB unavailable")
    collection.get.return_value = {"documents": [], "metadatas": []}
    with_mock_collection(block, collection)

    ctx = make_context()
    result = block.process(ctx, mock_llm, CONFIG)

    assert result.retrieval_chunks == []
    # Must not raise


def test_collection_init_failure_returns_empty_chunks(block, mock_llm):
    """If collection cannot be initialised, return empty chunks."""
    with patch.object(block, "_get_collection", side_effect=RuntimeError("no chroma")):
        ctx = make_context()
        result = block.process(ctx, mock_llm, CONFIG)
        assert result.retrieval_chunks == []
        # Must not raise


def test_always_include_fetch_exception_returns_empty(block, mock_llm):
    collection = MagicMock()
    collection.query.return_value = {
        "documents": [["Relevant content"]],
        "metadatas": [[{"doc_type": "trade"}]],
        "distances": [[0.2]],
    }
    collection.get.side_effect = RuntimeError("get failed")
    with_mock_collection(block, collection)

    ctx = make_context()
    result = block.process(ctx, mock_llm, CONFIG)

    assert len(result.retrieval_chunks) == 1
    assert result.always_include_chunks == []


# ---------------------------------------------------------------------------
# Embedding provider validation
# ---------------------------------------------------------------------------


def test_invalid_embedding_provider_raises_value_error(block):
    bad_config = {
        "knowledge": {
            "blocks": {
                "static_knowledge_base": {
                    "enabled": True,
                    "embedding_provider": "totally_invalid",
                    "chroma_persist_dir": "/tmp/test",
                    "collection_name": "test",
                }
            }
        }
    }
    with pytest.raises(ValueError, match="Unknown embedding_provider"):
        block._get_embedding_function(
            bad_config["knowledge"]["blocks"]["static_knowledge_base"]
        )


def test_openai_provider_missing_api_key_raises(block):
    cfg = {
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
    }
    with patch.dict("os.environ", {}, clear=True):
        if "OPENAI_API_KEY" in __import__("os").environ:
            pytest.skip("OPENAI_API_KEY is set in environment")
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            block._get_embedding_function(cfg)
