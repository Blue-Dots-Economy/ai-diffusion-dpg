"""
knowledge_engine/tests/test_server.py

Unit tests for the Knowledge Engine FastAPI server (main.py: create_app).
Uses FastAPI TestClient — no real HTTP calls made.

Covers:
- Normal: /retrieve returns chunks for valid request
- Edge: missing session_id returns 422
- OTel: /retrieve emits ke.prompt_assemble span with session_id + intent attributes
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import create_app
from src.engine import KnowledgeEngine
from src.models import RetrievalChunk


# ---------------------------------------------------------------------------
# Config and fixtures
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "server": {"host": "0.0.0.0", "port": 8001},
    "knowledge": {
        "conversation": {"max_history_turns": 5},
        "blocks": {
            "glossary": {
                "enabled": False,
                "mappings": [],
                "apply_to": [],
            },
            "static_knowledge_base": {
                "enabled": False,
                "chroma_persist_dir": "/tmp/test_chroma_server",
                "collection_name": "test_col",
                "embedding_provider": "sentence_transformers",
                "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
                "top_k": 3,
                "similarity_threshold": 0.65,
                "sources": [],
                "metadata_filters": {"use_intent_filter": False, "use_location_filter": False},
            },
            "multimodal_input_handler": {
                "enabled": False,
                "supported_types": [],
                "audio_enabled": False,
                "image_model": "",
                "max_file_size_mb": 10,
            },
        },
    },
}

VALID_REQUEST = {
    "session_id": "test-session-1",
    "user_message": "kaam chahiye",
    "intent": "job_search",
    "entities": {},
    "sentiment": "neutral",
    "confidence": 0.9,
    "normalised_input": "kaam chahiye",
    "detected_language": "hi",
}


@pytest.fixture
def ke():
    """KnowledgeEngine with all blocks disabled — returns empty chunks."""
    return KnowledgeEngine(config=MINIMAL_CONFIG)


@pytest.fixture
def client(ke):
    """TestClient bound to the KE FastAPI app."""
    app = create_app(ke, MINIMAL_CONFIG)
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_app validation
# ---------------------------------------------------------------------------

def test_create_app_none_raises():
    """create_app(None, ...) must raise ValueError."""
    with pytest.raises(ValueError):
        create_app(None, MINIMAL_CONFIG)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    """GET /health returns {status: ok}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /retrieve — normal
# ---------------------------------------------------------------------------

def test_retrieve_valid_request_returns_200(client):
    """POST /retrieve with valid body returns 200."""
    response = client.post("/retrieve", json=VALID_REQUEST)
    assert response.status_code == 200


def test_retrieve_response_contains_session_id(client):
    """POST /retrieve response echoes the session_id."""
    response = client.post("/retrieve", json=VALID_REQUEST)
    assert response.json()["session_id"] == VALID_REQUEST["session_id"]


def test_retrieve_response_contains_chunks_key(client):
    """POST /retrieve response includes a chunks list."""
    response = client.post("/retrieve", json=VALID_REQUEST)
    assert "chunks" in response.json()
    assert isinstance(response.json()["chunks"], list)


# ---------------------------------------------------------------------------
# POST /retrieve — edge cases
# ---------------------------------------------------------------------------

def test_retrieve_missing_session_id_returns_422(client):
    """POST /retrieve with empty session_id returns 422."""
    payload = dict(VALID_REQUEST)
    payload["session_id"] = ""
    response = client.post("/retrieve", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# OTel span instrumentation
# ---------------------------------------------------------------------------

def test_retrieve_emits_ke_span():
    """POST /retrieve must produce a ke.prompt_assemble span."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace
    from dpg_telemetry import _reset_for_testing

    _reset_for_testing()

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    ke_instance = KnowledgeEngine(config=MINIMAL_CONFIG)
    app = create_app(ke_instance, MINIMAL_CONFIG)
    tc = TestClient(app)

    response = tc.post("/retrieve", json=VALID_REQUEST)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "ke.prompt_assemble" in span_names

    ke_span = next(s for s in spans if s.name == "ke.prompt_assemble")
    assert ke_span.attributes.get("session_id") == VALID_REQUEST["session_id"]
    assert ke_span.attributes.get("intent") == VALID_REQUEST["intent"]
