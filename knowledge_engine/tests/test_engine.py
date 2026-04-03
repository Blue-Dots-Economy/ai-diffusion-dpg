"""
tests/test_engine.py

Unit tests for KnowledgeEngine orchestrator (src/engine.py).

Tests cover:
- Constructor validation
- retrieve(): returns list of RetrievalChunk
- retrieve(): always_include flag is set correctly
- retrieve(): empty message returns []
- retrieve(): None session_id raises ValueError
- retrieve(): block exception returns partial results
- retrieve(): NLU params forwarded to context
"""

import pytest
from unittest.mock import MagicMock

from src.engine import KnowledgeEngine
from src.models import RetrievalChunk


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

CONFIG = {
    "server": {"host": "0.0.0.0", "port": 8001},
    "knowledge": {
        "conversation": {"max_history_turns": 5},
        "blocks": {
            "glossary": {
                "enabled": True,
                "mappings": [
                    {"colloquial": ["kaam chahiye", "naukri chahiye"], "canonical": "market_truth_query"},
                    {"colloquial": ["electrician", "bijli wala"], "canonical": "trade:electrician"},
                    {"colloquial": ["Hubli", "Dharwad"], "canonical": "location:karnataka_north"},
                ],
                "apply_to": ["normalised_input", "entities"],
            },
            "static_knowledge_base": {
                "enabled": True,
                "chroma_persist_dir": "/tmp/test_chroma_engine",
                "collection_name": "test_collection",
                "embedding_provider": "sentence_transformers",
                "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
                "top_k": 3,
                "similarity_threshold": 0.65,
                "sources": [],
                "metadata_filters": {"use_intent_filter": True, "use_location_filter": True},
            },
            "multimodal_input_handler": {
                "enabled": False,
                "supported_types": ["pdf", "image"],
                "audio_enabled": False,
                "image_model": "claude-sonnet-4-6",
                "max_file_size_mb": 10,
            },
        },
    },
    "conversation": {
        "persona": {
            "text": "You are Kaam Ki Baat, a labour advisory helpline assistant."
        },
        "guardrail_reminders": [
            "Do not discuss topics unrelated to employment or schemes."
        ],
    },
}


@pytest.fixture
def engine_for_retrieve():
    """Engine with Static KB block mocked to return raw chunk dicts for retrieve() tests."""
    engine = KnowledgeEngine(config=CONFIG)

    for i, block in enumerate(engine._blocks):
        if type(block).__name__ == "StaticKnowledgeBaseBlock":
            mock_kb = MagicMock()

            def mock_process(context, llm, cfg):
                context.retrieval_chunks = [
                    {"text": "Electrician jobs in Hubli", "doc_type": "trade", "source": "onest"},
                ]
                context.always_include_chunks = [
                    {"text": "Market truth: 50 jobs posted", "doc_type": "market_truth", "source": "onest_api"},
                ]
                return context

            mock_kb.process.side_effect = mock_process
            engine._blocks[i] = mock_kb
            break

    return engine


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_init_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        KnowledgeEngine(config=None)


def test_init_creates_three_blocks():
    """KE runs only 3 blocks: Glossary, Static KB, Multimodal."""
    engine = KnowledgeEngine(config=CONFIG)
    assert len(engine._blocks) == 3


def test_init_llm_optional():
    """llm is optional — not needed when multimodal is disabled."""
    engine = KnowledgeEngine(config=CONFIG)
    assert engine._llm is None


# ---------------------------------------------------------------------------
# retrieve() — normal paths
# ---------------------------------------------------------------------------


def test_retrieve_returns_list_of_retrieval_chunks(engine_for_retrieve):
    chunks = engine_for_retrieve.retrieve(
        session_id="sess1",
        user_message="electrician kaam chahiye",
        profile={},
        session={},
    )
    assert isinstance(chunks, list)
    assert all(isinstance(c, RetrievalChunk) for c in chunks)


def test_retrieve_always_include_chunks_have_flag_set(engine_for_retrieve):
    chunks = engine_for_retrieve.retrieve(
        session_id="sess1",
        user_message="kaam chahiye",
        profile={},
        session={},
    )
    always_include = [c for c in chunks if c.always_include]
    regular = [c for c in chunks if not c.always_include]
    assert len(always_include) >= 1
    assert always_include[0].text == "Market truth: 50 jobs posted"
    assert len(regular) >= 1
    assert regular[0].text == "Electrician jobs in Hubli"


def test_retrieve_passes_nlu_params_to_context(engine_for_retrieve):
    """NLU params (intent, entities, detected_language) flow into the context."""
    chunks = engine_for_retrieve.retrieve(
        session_id="sess1",
        user_message="electrician kaam chahiye Hubli mein",
        profile={"name": "Ravi"},
        session={"current_node": "market_truth"},
        intent="market_truth_query",
        entities={"trade": "electrician", "location": "Hubli"},
        sentiment="positive",
        confidence=0.92,
        normalised_input="electrician kaam chahiye Hubli",
        detected_language="hinglish",
    )
    assert isinstance(chunks, list)


# ---------------------------------------------------------------------------
# retrieve() — edge cases
# ---------------------------------------------------------------------------


def test_retrieve_empty_user_message_returns_empty_list():
    engine = KnowledgeEngine(config=CONFIG)
    result = engine.retrieve(
        session_id="sess1",
        user_message="",
        profile={},
        session={},
    )
    assert result == []


def test_retrieve_none_session_id_raises():
    engine = KnowledgeEngine(config=CONFIG)
    with pytest.raises(ValueError, match="session_id must not be None"):
        engine.retrieve(
            session_id=None,
            user_message="hello",
            profile={},
            session={},
        )


# ---------------------------------------------------------------------------
# retrieve() — failure resilience
# ---------------------------------------------------------------------------


def test_retrieve_block_exception_returns_partial_results():
    """If one block raises, retrieve() continues and returns whatever chunks remain."""
    engine = KnowledgeEngine(config=CONFIG)

    # Make Glossary block raise
    engine._blocks[0].process = MagicMock(side_effect=RuntimeError("Glossary crashed"))

    # Mock Static KB to return a chunk
    for i, block in enumerate(engine._blocks):
        if type(block).__name__ == "StaticKnowledgeBaseBlock":
            mock_kb = MagicMock()

            def mock_process(context, llm, cfg):
                context.retrieval_chunks = [
                    {"text": "Fallback result", "doc_type": "trade", "source": "kb"},
                ]
                return context

            mock_kb.process.side_effect = mock_process
            engine._blocks[i] = mock_kb
            break

    chunks = engine.retrieve(
        session_id="sess1",
        user_message="kaam chahiye",
        profile={},
        session={},
    )
    assert isinstance(chunks, list)
    assert any(c.text == "Fallback result" for c in chunks)
