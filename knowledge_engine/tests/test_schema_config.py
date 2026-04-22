"""Tests for Knowledge Engine MergedConfig strict schema validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schema.config import (
    EmbeddingProvider,
    GlossaryApplyTo,
    MergedConfig,
    MultimodalInputType,
    RefreshSchedule,
    ServerConfig,
    SourceType,
)


def _minimal_valid_config() -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 8001},
        "knowledge": {
            "blocks": {
                "glossary": {
                    "enabled": True,
                    "mappings": [
                        {"colloquial": ["job chahiye"], "canonical": "market_truth_query"},
                    ],
                    "apply_to": ["normalised_input", "entities"],
                },
                "static_knowledge_base": {
                    "enabled": True,
                    "collection_name": "kkb_knowledge",
                    "chroma_persist_dir": "/app/chroma_db",
                    "top_k": 3,
                    "similarity_threshold": 0.65,
                    "embedding_provider": "chroma_default",
                    "metadata_filters": {
                        "use_location_filter": True,
                        "use_intent_filter": True,
                    },
                    "sources": [
                        {
                            "path": "./data/schemes.pdf",
                            "type": "static",
                            "doc_type": "scheme",
                            "refresh": "manual",
                        },
                    ],
                    "intent_filters": {
                        "scheme_query": ["scheme"],
                        "unknown": [],
                    },
                },
                "multimodal_input_handler": {
                    "enabled": False,
                    "supported_types": ["pdf", "image"],
                    "audio_enabled": False,
                    "max_file_size_mb": 10,
                    "image_model": "claude-haiku-4-5-20251001",
                },
            }
        },
        "observability": {"domain": "kkb"},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.server.port == 8001
    assert cfg.knowledge.blocks.glossary.enabled is True
    assert cfg.knowledge.blocks.static_knowledge_base.top_k == 3
    assert cfg.knowledge.blocks.static_knowledge_base.similarity_threshold == 0.65
    assert (
        cfg.knowledge.blocks.static_knowledge_base.embedding_provider
        == EmbeddingProvider.chroma_default
    )
    assert "scheme_query" in cfg.knowledge.blocks.static_knowledge_base.intent_filters


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({})
    assert cfg.server.port == 8001
    assert cfg.knowledge.blocks.glossary.enabled is True
    assert cfg.knowledge.blocks.static_knowledge_base.top_k == 3
    assert cfg.knowledge.blocks.multimodal_input_handler.enabled is False


def test_rejects_unknown_top_level_key():
    config = _minimal_valid_config()
    config["conversation"] = {"persona": {"text": "..."}}  # removed in this PR
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "conversation" in str(exc.value)


def test_rejects_unknown_key_on_knowledge():
    config = _minimal_valid_config()
    config["knowledge"]["conversation"] = {"max_history_turns": 5}  # removed
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "conversation" in str(exc.value)


def test_rejects_unknown_key_on_static_kb():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["vector_store"] = "chromadb"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "vector_store" in str(exc.value)


def test_rejects_unknown_key_on_glossary():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["glossary"]["case_insensitive"] = True
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "case_insensitive" in str(exc.value)


def test_rejects_unknown_key_on_glossary_mapping():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["glossary"]["mappings"][0]["priority"] = 1
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "priority" in str(exc.value)


def test_rejects_unknown_key_on_source():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["sources"][0]["checksum"] = "abc"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "checksum" in str(exc.value)


def test_rejects_unknown_key_on_multimodal():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["multimodal_input_handler"]["ocr_backend"] = "tesseract"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "ocr_backend" in str(exc.value)


def test_rejects_invalid_source_type_enum():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["sources"][0]["type"] = "dynamic"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_refresh_enum():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["sources"][0]["refresh"] = "daily"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_embedding_provider_enum():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["embedding_provider"] = "cohere"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_apply_to_enum():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["glossary"]["apply_to"] = ["raw_input"]  # not valid
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_multimodal_type_enum():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["multimodal_input_handler"]["supported_types"] = ["video"]
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_out_of_range_similarity_threshold():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["similarity_threshold"] = 1.5
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_top_k():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["static_knowledge_base"]["top_k"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_max_file_size():
    config = _minimal_valid_config()
    config["knowledge"]["blocks"]["multimodal_input_handler"]["max_file_size_mb"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_server_port():
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)
    with pytest.raises(ValidationError):
        ServerConfig(port=0)


def test_rejects_out_of_range_sample_rate():
    config = _minimal_valid_config()
    config["observability"] = {"otel": {"sample_rate": 1.1}}
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_intent_filters_accepts_domain_defined_keys():
    """intent_filters is an open map keyed by operator-defined intents."""
    cfg = MergedConfig.validate_full({
        "knowledge": {
            "blocks": {
                "static_knowledge_base": {
                    "intent_filters": {
                        "crop_advisory": ["crop"],
                        "weather_query": ["weather"],
                    }
                }
            }
        }
    })
    assert set(cfg.knowledge.blocks.static_knowledge_base.intent_filters.keys()) == {
        "crop_advisory",
        "weather_query",
    }


def test_enum_exports_are_usable():
    assert SourceType.static.value == "static"
    assert RefreshSchedule.manual.value == "manual"
    assert EmbeddingProvider.chroma_default.value == "chroma_default"
    assert MultimodalInputType.pdf.value == "pdf"
    assert GlossaryApplyTo.entities.value == "entities"
