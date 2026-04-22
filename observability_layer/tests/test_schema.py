"""Tests for ObservabilityConfig Pydantic v2 schema."""
import pytest
from pydantic import ValidationError


def test_from_config_full_kkb_config():
    from schema.config import ObservabilityConfig, InstrumentType
    config = {
        "observability": {
            "domain": "kkb",
            "otel": {
                "collector_endpoint": "http://otelcol:4317",
                "sample_rate": 0.5,
                "export_interval_ms": 3000,
            },
            "outcomes": {
                "lifecycle": [
                    {"state": "enquiry", "trigger_tool": None},
                    {"state": "applied", "trigger_tool": "onest_apply", "trigger_condition": "result == 'success'"},
                ],
                "metrics": [
                    {"name": "placement.applications", "instrument": "counter", "description": "Applications submitted"},
                    {"name": "placement.rate", "instrument": "gauge", "description": "Placement rate", "unit": "%"},
                ],
            },
            "sli": {"turn_latency_p99_ms": 1200, "trust_block_rate_max": 0.05},
            "audit": {"retention_days": 90},
        }
    }
    cfg = ObservabilityConfig.from_config(config)
    assert cfg.domain == "kkb"
    assert cfg.otel.collector_endpoint == "http://otelcol:4317"
    assert cfg.otel.sample_rate == 0.5
    assert len(cfg.outcomes.lifecycle) == 2
    assert cfg.outcomes.lifecycle[1].trigger_tool == "onest_apply"
    assert len(cfg.outcomes.metrics) == 2
    assert cfg.outcomes.metrics[0].instrument == InstrumentType.counter
    assert cfg.sli.turn_latency_p99_ms == 1200
    assert cfg.audit.retention_days == 90


def test_from_config_empty_uses_defaults():
    from schema.config import ObservabilityConfig
    cfg = ObservabilityConfig.from_config({})
    assert cfg.domain == "unknown"
    assert cfg.otel.collector_endpoint == "http://localhost:4317"
    assert cfg.otel.sample_rate == 1.0
    assert cfg.otel.export_interval_ms == 5000
    assert cfg.outcomes.lifecycle == []
    assert cfg.outcomes.metrics == []
    assert cfg.sli.turn_latency_p99_ms == 1200
    assert cfg.audit.retention_days == 90


def test_invalid_instrument_type_raises():
    from schema.config import MetricDefinition
    with pytest.raises(ValidationError):
        MetricDefinition(
            name="foo",
            instrument="not_valid",
            description="test",
        )


def test_lifecycle_state_optional_fields():
    from schema.config import LifecycleState
    state = LifecycleState(state="enquiry")
    assert state.trigger_tool is None
    assert state.trigger_condition is None


def test_pii_fields_excluded_defaults():
    from schema.config import ObservabilityConfig
    cfg = ObservabilityConfig.from_config({})
    assert "user_message" in cfg.audit.pii_fields_excluded
    assert "user_id" in cfg.audit.pii_fields_excluded
    assert "user_message" in cfg.telemetry.pii_fields_excluded
    assert "user_id" not in cfg.telemetry.pii_fields_excluded


def test_from_config_with_none_raises():
    from schema.config import ObservabilityConfig
    with pytest.raises(TypeError):
        ObservabilityConfig.from_config(None)


def test_metric_definition_default_unit_is_empty():
    from schema.config import MetricDefinition, InstrumentType
    m = MetricDefinition(name="foo", instrument=InstrumentType.counter, description="bar")
    assert m.unit == ""
    assert m.attributes == []


def test_otel_config_rejects_invalid_sample_rate():
    from pydantic import ValidationError
    from schema.config import OtelConfig
    with pytest.raises(ValidationError):
        OtelConfig(sample_rate=2.0)


def test_sli_config_rejects_negative_latency():
    from pydantic import ValidationError
    from schema.config import SLIConfig
    with pytest.raises(ValidationError):
        SLIConfig(turn_latency_p99_ms=-1)


def test_merged_config_accepts_valid_full_config():
    from schema.config import MergedConfig
    cfg = MergedConfig.validate_full({
        "server": {"host": "0.0.0.0", "port": 8004},
        "observability": {"domain": "kkb"},
    })
    assert cfg.server.port == 8004
    assert cfg.observability.domain == "kkb"


def test_merged_config_rejects_unknown_top_level_key():
    from schema.config import MergedConfig
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({
            "server": {"host": "0.0.0.0", "port": 8004},
            "observability": {"domain": "kkb"},
            "typo_section": {"foo": "bar"},
        })
    assert "typo_section" in str(exc.value)


def test_merged_config_rejects_unknown_nested_key():
    from schema.config import MergedConfig
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({
            "observability": {
                "domain": "kkb",
                "otel": {"collector_endpoint": "x", "sampl_rate": 0.5},  # typo
            }
        })
    assert "sampl_rate" in str(exc.value)


def test_merged_config_rejects_unknown_key_on_lifecycle_state():
    from schema.config import MergedConfig
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({
            "observability": {
                "outcomes": {
                    "lifecycle": [
                        {"state": "enquiry", "next_state": "applied"},  # extra key
                    ],
                }
            }
        })
    assert "next_state" in str(exc.value)


def test_merged_config_rejects_unknown_key_on_metric_definition():
    from schema.config import MergedConfig
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({
            "observability": {
                "outcomes": {
                    "metrics": [
                        {"name": "m", "instrument": "counter", "description": "d", "buckets": [1, 2]},
                    ],
                }
            }
        })
    assert "buckets" in str(exc.value)


def test_merged_config_rejects_none():
    from schema.config import MergedConfig
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_server_config_rejects_invalid_port():
    from schema.config import ServerConfig
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)
    with pytest.raises(ValidationError):
        ServerConfig(port=0)
