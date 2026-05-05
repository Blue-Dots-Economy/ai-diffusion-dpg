"""Tests for shared enums (closed code Enums + config-driven open enums)."""
import pytest
from pydantic import BaseModel, ValidationError

from dev_kit.schemas.enums import (
    # Closed code enums
    AgentType, AuthType, DignityFailAction, HttpMethod, InstrumentType,
    InternalRoute, McpTransport, ParamSource, ParamType, PersistentBackend,
    ReengagementChannel, RoutingOperator, SessionFieldType, SpecialHandler,
    StorageMode, ToolCategory, ToolType, TrustQueueBackend,
    # Config-driven values
    ANTHROPIC_MODELS, OPENAI_MODELS, ALL_CHAT_MODELS, LANGUAGES,
    PROVIDERS, RAYA_VOICES, RAYA_VOICE_IDS, RAYA_LANGUAGES,
    RAYA_VOICE_LANGUAGE, EMBEDDING_PROVIDERS,
    # Annotated field types
    ChatModelField, EmbeddingProviderField, LanguageField,
    ProviderField, RayaLanguageField, RayaVoiceIdField,
)


# -- Closed code enums (verified against runtime code) -----------------------

def test_agent_type_values():
    assert {a.value for a in AgentType} == {
        "transactional", "informational", "agentic", "conversational"
    }

def test_trust_queue_backend_excludes_memory():
    assert {b.value for b in TrustQueueBackend} == {"log", "redis", "webhook"}

def test_dignity_fail_action_values():
    assert {a.value for a in DignityFailAction} == {"rewrite", "flag", "skip"}

def test_tool_type_values():
    assert {t.value for t in ToolType} == {"rest_api", "mcp"}

def test_tool_category_values():
    assert {c.value for c in ToolCategory} == {"read", "write", "identity"}

def test_storage_mode_values():
    assert {m.value for m in StorageMode} == {"saved", "anonymous"}

def test_persistent_backend_values():
    assert {b.value for b in PersistentBackend} == {"memgraph", "neo4j"}

def test_session_field_type_values():
    assert {t.value for t in SessionFieldType} == {"enum", "string", "int", "list"}

def test_instrument_type_values():
    assert {i.value for i in InstrumentType} == {"counter", "gauge", "histogram"}

def test_special_handler_values():
    assert {h.value for h in SpecialHandler} == {"hitl", "whatsapp_handoff"}

def test_auth_type_excludes_oauth2():
    """oauth2 deliberately excluded — adapter has no oauth2 branch."""
    assert "oauth2" not in {a.value for a in AuthType}
    assert {a.value for a in AuthType} == {"none", "api_key", "bearer"}

def test_http_method_values():
    assert {m.value for m in HttpMethod} == {"GET", "POST", "PUT", "DELETE", "PATCH"}

def test_param_source_values():
    assert {s.value for s in ParamSource} == {"agent", "static"}

def test_param_type_values():
    assert {p.value for p in ParamType} == {
        "string", "integer", "number", "boolean", "array", "object"
    }

def test_mcp_transport_excludes_stdio():
    """stdio not in _SUPPORTED_TRANSPORTS in mcp.py — must be excluded."""
    assert "stdio" not in {t.value for t in McpTransport}
    assert {t.value for t in McpTransport} == {"sse", "streamable_http"}

def test_reengagement_channel_values():
    assert {c.value for c in ReengagementChannel} == {"outbound_call", "whatsapp", "sms"}

def test_routing_operator_values():
    assert {o.value for o in RoutingOperator} == {"eq", "not_eq", "gt", "lt", "in"}

def test_internal_route_values():
    assert {r.value for r in InternalRoute} == {"knowledge_engine"}


# -- Config-driven values (loaded from enums_config.yaml) --------------------

def test_providers_loaded_from_config():
    assert "anthropic" in PROVIDERS
    assert "openai" in PROVIDERS

def test_anthropic_models_present():
    """Default config ships with at least Haiku, Sonnet, Opus."""
    assert "claude-haiku-4-5-20251001" in ANTHROPIC_MODELS
    assert "claude-sonnet-4-6" in ANTHROPIC_MODELS
    assert "claude-opus-4-7" in ANTHROPIC_MODELS

def test_openai_models_present():
    assert any(m.startswith("gpt-") for m in OPENAI_MODELS)

def test_all_chat_models_is_union():
    assert set(ALL_CHAT_MODELS) == set(ANTHROPIC_MODELS) | set(OPENAI_MODELS)

def test_raya_voices_have_required_fields():
    for v in RAYA_VOICES:
        assert "voice_id" in v and "language" in v and "name" in v

def test_raya_voice_language_map_consistent():
    """Every voice_id maps to its declared language."""
    for v in RAYA_VOICES:
        assert RAYA_VOICE_LANGUAGE[v["voice_id"]] == v["language"]

def test_raya_languages_derived_from_voices():
    """RAYA_LANGUAGES = unique languages across all voices, sorted."""
    assert RAYA_LANGUAGES == sorted({v["language"] for v in RAYA_VOICES})


# -- Annotated field types reject invalid values -----------------------------

def _wrap(t):
    """Helper: build a model with one field of the given annotated type."""
    class M(BaseModel):
        x: t
    return M

def test_provider_field_rejects_unknown():
    M = _wrap(ProviderField)
    M(x="anthropic")
    with pytest.raises(ValidationError):
        M(x="cohere")

def test_chat_model_field_rejects_unknown():
    M = _wrap(ChatModelField)
    M(x=ANTHROPIC_MODELS[0])
    with pytest.raises(ValidationError):
        M(x="not-a-real-model")

def test_language_field_rejects_unknown():
    M = _wrap(LanguageField)
    M(x="english")
    with pytest.raises(ValidationError):
        M(x="klingon")

def test_raya_voice_id_field_rejects_unknown():
    M = _wrap(RayaVoiceIdField)
    M(x=RAYA_VOICE_IDS[0])
    with pytest.raises(ValidationError):
        M(x="not-a-uuid")

def test_raya_language_field_rejects_unknown():
    M = _wrap(RayaLanguageField)
    M(x=RAYA_LANGUAGES[0])
    with pytest.raises(ValidationError):
        M(x="es")  # Spanish not in raya_voices

def test_embedding_provider_field_rejects_unknown():
    M = _wrap(EmbeddingProviderField)
    M(x="chroma_default")
    with pytest.raises(ValidationError):
        M(x="not-an-embedding")
