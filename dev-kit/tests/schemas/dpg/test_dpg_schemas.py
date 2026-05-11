"""Tests for DPG framework schemas — used by deploy wizard's DPG Values endpoint."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.dpg.agent_core import (
    AgentCoreDpgConfig, AgentDpgDefaults, FeaturesDpg,
)
from dev_kit.schemas.dpg.knowledge_engine import KnowledgeEngineDpgConfig
from dev_kit.schemas.dpg.memory_layer import MemoryLayerDpgConfig
from dev_kit.schemas.dpg.trust_layer import TrustLayerDpgConfig
from dev_kit.schemas.dpg.action_gateway import ActionGatewayDpgConfig
from dev_kit.schemas.dpg.reach_layer import ReachLayerDpgConfig
from dev_kit.schemas.dpg.observability_layer import ObservabilityLayerDpgConfig


# -- agent_core DPG ----------------------------------------------------------

def _agent_core_minimal_dict():
    """Minimal valid agent_core DPG config."""
    return {
        "server": {"host": "0.0.0.0", "port": 8000},
        "agent": {},
        "ke_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "memory_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "trust_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "learning_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "action_gateway_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "reach_layer": {},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }


def test_agent_core_dpg_minimal():
    AgentCoreDpgConfig.model_validate(_agent_core_minimal_dict())


def test_agent_core_dpg_port_range():
    base = _agent_core_minimal_dict()
    base["server"]["port"] = 0
    with pytest.raises(ValidationError):
        AgentCoreDpgConfig.model_validate(base)
    base["server"]["port"] = 65535
    AgentCoreDpgConfig.model_validate(base)


def test_agent_core_dpg_endpoint_must_be_url():
    base = _agent_core_minimal_dict()
    base["ke_client"]["endpoint"] = "not-a-url"
    with pytest.raises(ValidationError):
        AgentCoreDpgConfig.model_validate(base)


def test_agent_core_dpg_provider_default_anthropic():
    base = _agent_core_minimal_dict()
    cfg = AgentCoreDpgConfig.model_validate(base)
    assert cfg.agent.provider == "anthropic"


def test_agent_core_dpg_provider_invalid():
    base = _agent_core_minimal_dict()
    base["agent"]["provider"] = "cohere"
    with pytest.raises(ValidationError):
        AgentCoreDpgConfig.model_validate(base)


def test_agent_core_dpg_features_default_all_none():
    f = FeaturesDpg()
    assert f.prompt_cache is None
    assert f.streaming is None
    assert f.image_input is None


def test_agent_core_dpg_features_explicit():
    base = _agent_core_minimal_dict()
    base["agent"]["features"] = {"prompt_cache": True, "streaming": True}
    cfg = AgentCoreDpgConfig.model_validate(base)
    assert cfg.agent.features.prompt_cache is True


# -- knowledge_engine DPG ----------------------------------------------------

def test_knowledge_engine_dpg_minimal():
    KnowledgeEngineDpgConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 8001},
        "knowledge": {"blocks": {}},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    })


def test_knowledge_engine_dpg_invalid_otel_endpoint():
    with pytest.raises(ValidationError):
        KnowledgeEngineDpgConfig.model_validate({
            "server": {"host": "0.0.0.0", "port": 8001},
            "knowledge": {"blocks": {}},
            "observability": {"otel": {"collector_endpoint": "not-a-url"}},
        })


# -- memory_layer DPG --------------------------------------------------------

def _memory_minimal_dict():
    return {
        "server": {"host": "0.0.0.0", "port": 8002},
        "redis": {"host": "redis", "port": 6379},
        "memgraph": {"uri": "bolt://memgraph:7687", "user": "memgraph"},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }


def test_memory_layer_dpg_minimal():
    MemoryLayerDpgConfig.model_validate(_memory_minimal_dict())


def test_memory_layer_dpg_redis_port_range():
    base = _memory_minimal_dict()
    base["redis"]["port"] = 99999
    with pytest.raises(ValidationError):
        MemoryLayerDpgConfig.model_validate(base)


def test_memory_layer_dpg_memgraph_uri_must_be_bolt():
    base = _memory_minimal_dict()
    base["memgraph"]["uri"] = "http://wrong-protocol"
    with pytest.raises(ValidationError):
        MemoryLayerDpgConfig.model_validate(base)


def test_memory_layer_dpg_redis_db_range():
    base = _memory_minimal_dict()
    base["redis"]["db"] = 16
    with pytest.raises(ValidationError):
        MemoryLayerDpgConfig.model_validate(base)


# -- trust_layer DPG ---------------------------------------------------------

def test_trust_layer_dpg_minimal():
    base = {
        "server": {"host": "0.0.0.0", "port": 8003},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    cfg = TrustLayerDpgConfig.model_validate(base)
    assert cfg.dignity_check.enabled is False


def test_trust_layer_dpg_dignity_check_explicit():
    base = {
        "server": {"host": "0.0.0.0", "port": 8003},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
        "dignity_check": {"enabled": True, "questions": ["Q1", "Q2"], "fail_action": "rewrite"},
    }
    cfg = TrustLayerDpgConfig.model_validate(base)
    assert cfg.dignity_check.enabled is True


# -- action_gateway DPG ------------------------------------------------------

def test_action_gateway_dpg_minimal():
    ActionGatewayDpgConfig.model_validate({
        "server": {"host": "0.0.0.0", "port": 9999},
        "tools": [],
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    })


def test_action_gateway_dpg_port_range():
    """Port range validation must fire on port=0 specifically (not missing fields)."""
    base = {
        "server": {"host": "0.0.0.0", "port": 9999},
        "tools": [],
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    # Baseline passes
    ActionGatewayDpgConfig.model_validate(base)
    # Port 0 fails on the constraint
    base["server"]["port"] = 0
    with pytest.raises(ValidationError):
        ActionGatewayDpgConfig.model_validate(base)
    # Port 65535 passes
    base["server"]["port"] = 65535
    ActionGatewayDpgConfig.model_validate(base)


# -- reach_layer DPG ---------------------------------------------------------

def _reach_minimal_dict():
    return {"reach_layer": {
        "common": {
            "agent_core_client": {"endpoint": "http://x:1", "timeout_s": 30.0},
            "memory_layer_client": {"endpoint": "http://x:1", "timeout_s": 10.0},
            "observability": {"otel": {"collector_endpoint": "http://x:1"}},
        },
        "channels": {
            "cli": {},
            "web": {},
            "voice": {
                "vobiz": {"auth_id": "x", "auth_token": "y"},
                "raya": {"api_key": "k", "stt_wss_url": "https://x", "tts_base_url": "https://x"},
                "agent_core": {"base_url": "http://agent_core:8000"},
            },
        },
    }}


def test_reach_layer_dpg_top_level_wrapper_required():
    """reach_layer DPG yaml has a top-level reach_layer key."""
    ReachLayerDpgConfig.model_validate(_reach_minimal_dict())


def test_reach_layer_dpg_voice_agent_core_base_url_required():
    base = _reach_minimal_dict()
    base["reach_layer"]["channels"]["voice"]["agent_core"]["base_url"] = "not-a-url"
    with pytest.raises(ValidationError):
        ReachLayerDpgConfig.model_validate(base)


def test_reach_layer_dpg_vad_bounds():
    base = _reach_minimal_dict()
    base["reach_layer"]["channels"]["voice"]["vad"] = {"confidence": 1.5}
    with pytest.raises(ValidationError):
        ReachLayerDpgConfig.model_validate(base)


# -- observability_layer DPG -------------------------------------------------

def _obs_minimal_dict():
    return {
        "server": {"host": "0.0.0.0", "port": 8004},
        "observability": {
            "otel": {"collector_endpoint": "http://x:1"},
        },
    }


def test_observability_layer_dpg_minimal():
    cfg = ObservabilityLayerDpgConfig.model_validate(_obs_minimal_dict())
    assert cfg.observability.audit.retention_days == 90  # default


def test_observability_layer_dpg_audit_retention_positive():
    base = _obs_minimal_dict()
    base["observability"]["audit"] = {"retention_days": 0}
    with pytest.raises(ValidationError):
        ObservabilityLayerDpgConfig.model_validate(base)


def test_observability_layer_dpg_sli_block_rate_max_le_1():
    base = _obs_minimal_dict()
    base["observability"]["sli"] = {"trust_block_rate_max": 1.5}
    with pytest.raises(ValidationError):
        ObservabilityLayerDpgConfig.model_validate(base)


def test_observability_layer_dpg_sli_latency_positive():
    base = _obs_minimal_dict()
    base["observability"]["sli"] = {"turn_latency_p99_ms": 0}
    with pytest.raises(ValidationError):
        ObservabilityLayerDpgConfig.model_validate(base)


# -- RecordingDpg / VoiceDpg recording field ---------------------------------

from dev_kit.schemas.dpg.reach_layer import RecordingDpg, VoiceDpg  # noqa: E402


def test_recording_dpg_defaults_to_disabled():
    rec = RecordingDpg()
    assert rec.source == "disabled"
    assert rec.consent_purpose == "recording"
    assert rec.store.backend == "local"
    assert rec.store.local.base_path == "/var/recordings"


def test_recording_dpg_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RecordingDpg(source="disabled", surprise="x")


def test_recording_source_literal_enforced():
    with pytest.raises(ValidationError):
        RecordingDpg(source="ftp")


def test_recording_store_backend_literal_enforced():
    with pytest.raises(ValidationError):
        RecordingDpg(store={"backend": "azure"})


def test_voice_dpg_default_factory_includes_recording():
    rec_default = VoiceDpg.model_fields["recording"].default_factory()  # type: ignore[misc]
    assert rec_default.source == "disabled"
