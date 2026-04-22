"""Tests for Memory Layer MergedConfig strict schema validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schema.config import (
    MergedConfig,
    PersistentBackend,
    ReengagementChannel,
    ServerConfig,
    SessionFieldType,
    StorageMode,
)


def _minimal_valid_config() -> dict:
    """A minimal merged config that validates cleanly."""
    return {
        "server": {"host": "0.0.0.0", "port": 8002},
        "redis": {"host": "redis", "port": 6379, "db": 0},
        "memgraph": {"uri": "bolt://memgraph:7687", "user": "memgraph"},
        "state": {
            "session": {
                "ttl_minutes": 60,
                "schema": {
                    "mental_state": {
                        "type": "enum",
                        "values": ["fog", "commitment"],
                        "default": "fog",
                    }
                },
            },
            "persistent": {
                "backend": "memgraph",
                "graph": {
                    "user_node": {"label": "User", "key": "user_id"},
                    "subnodes": {
                        "UserProfile": {
                            "rel": "HAS_PROFILE",
                            "declared_fields": ["name", "age"],
                        }
                    },
                },
                "merge_on_session_end": [
                    {"session_field": "mental_state", "target": "Journey.mental_state_at_end"},
                ],
            },
        },
        "user_data_persistence": {"default_mode": "saved"},
        "reengagement": {
            "triggers": [
                {
                    "event": "DOP_MT",
                    "delay_hours": 72,
                    "channel": "outbound_call",
                    "message_template": "kkb_reengagement_mt",
                }
            ]
        },
        "observability": {"domain": "kkb"},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.server.port == 8002
    assert cfg.state.session.ttl_minutes == 60
    assert "mental_state" in cfg.state.session.fields_schema
    assert cfg.state.persistent.backend == PersistentBackend.memgraph
    assert "UserProfile" in cfg.state.persistent.graph.subnodes
    assert cfg.reengagement.triggers[0].event == "DOP_MT"
    assert cfg.user_data_persistence.default_mode == StorageMode.saved


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({})
    assert cfg.server.port == 8002
    assert cfg.redis.port == 6379
    assert cfg.memgraph.uri == "bolt://memgraph:7687"
    assert cfg.state.persistent is None
    assert cfg.reengagement.triggers == []


def test_rejects_unknown_top_level_key():
    config = _minimal_valid_config()
    config["typo_section"] = {"x": 1}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "typo_section" in str(exc.value)


def test_rejects_unknown_key_on_redis():
    config = _minimal_valid_config()
    config["redis"]["poll_interval_ms"] = 1000  # extra key
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "poll_interval_ms" in str(exc.value)


def test_rejects_unknown_key_on_memgraph():
    config = _minimal_valid_config()
    config["memgraph"]["pool_size"] = 10  # extra key
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "pool_size" in str(exc.value)


def test_rejects_unknown_key_on_session():
    config = _minimal_valid_config()
    config["state"]["session"]["ttl_days"] = 2  # wrong field name
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "ttl_days" in str(exc.value)


def test_rejects_unknown_key_on_session_field_definition():
    config = _minimal_valid_config()
    config["state"]["session"]["schema"]["mental_state"]["required"] = True  # not a field
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "required" in str(exc.value)


def test_rejects_unknown_key_on_subnode():
    config = _minimal_valid_config()
    config["state"]["persistent"]["graph"]["subnodes"]["UserProfile"]["index"] = "btree"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "index" in str(exc.value)


def test_rejects_unknown_key_on_merge_rule():
    config = _minimal_valid_config()
    config["state"]["persistent"]["merge_on_session_end"][0]["always"] = True
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "always" in str(exc.value)


def test_rejects_unknown_key_on_reengagement_trigger():
    config = _minimal_valid_config()
    config["reengagement"]["triggers"][0]["priority"] = "high"  # not in schema
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "priority" in str(exc.value)


def test_rejects_invalid_session_field_type_enum():
    config = _minimal_valid_config()
    config["state"]["session"]["schema"]["mental_state"]["type"] = "varchar"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_backend_enum():
    config = _minimal_valid_config()
    config["state"]["persistent"]["backend"] = "sqlite"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_storage_mode_enum():
    config = _minimal_valid_config()
    config["user_data_persistence"]["default_mode"] = "deleted"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_channel_enum():
    config = _minimal_valid_config()
    config["reengagement"]["triggers"][0]["channel"] = "email"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_ttl():
    config = _minimal_valid_config()
    config["state"]["session"]["ttl_minutes"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_server_port():
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)
    with pytest.raises(ValidationError):
        ServerConfig(port=0)


def test_rejects_out_of_range_sample_rate():
    config = _minimal_valid_config()
    config["observability"] = {"otel": {"sample_rate": -0.1}}
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_loop_threshold_trigger_valid_without_channel():
    config = _minimal_valid_config()
    config["reengagement"]["triggers"] = [
        {"event": "DOP_RL", "loop_threshold": 3, "action": "hitl_counsellor"}
    ]
    cfg = MergedConfig.validate_full(config)
    assert cfg.reengagement.triggers[0].loop_threshold == 3
    assert cfg.reengagement.triggers[0].action == "hitl_counsellor"


def test_enum_exports_are_usable():
    assert PersistentBackend.memgraph.value == "memgraph"
    assert StorageMode.saved.value == "saved"
    assert ReengagementChannel.outbound_call.value == "outbound_call"
    assert SessionFieldType.enum.value == "enum"


def test_session_schema_alias_accepts_schema_key():
    """YAML/dict uses 'schema', python attribute is 'fields_schema'."""
    cfg = MergedConfig.validate_full({
        "state": {"session": {"schema": {"x": {"type": "string", "default": ""}}}}
    })
    assert "x" in cfg.state.session.fields_schema
