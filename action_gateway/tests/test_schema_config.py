"""Tests for Action Gateway MergedConfig strict schema validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schema.config import (
    AuthType,
    HttpMethod,
    MergedConfig,
    ParamSource,
    ParamType,
    ServerConfig,
    ToolCategory,
    ToolDefinition,
    ToolType,
)


def _valid_rest_tool() -> dict:
    return {
        "id": "onest_market_lookup",
        "type": "rest_api",
        "category": "read",
        "description": "Search job network",
        "timeout_ms": 5000,
        "base_url": "https://example.com",
        "auth": {"type": "api_key", "header": "X-API-KEY", "secret_env": "EXAMPLE_KEY"},
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/search",
                "params": [
                    {"name": "q", "source": "agent", "type": "string", "required": True},
                    {"name": "limit", "source": "static", "type": "integer", "value": 10},
                ],
            }
        ],
        "response": {"max_size_chars": 4000},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full({
        "server": {"host": "0.0.0.0", "port": 9999},
        "tools": [_valid_rest_tool()],
        "observability": {"domain": "kkb"},
    })
    assert cfg.server.port == 9999
    assert len(cfg.tools) == 1
    assert cfg.tools[0].id == "onest_market_lookup"
    assert cfg.tools[0].type == ToolType.rest_api
    assert cfg.tools[0].category == ToolCategory.read


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({})
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9999
    assert cfg.tools == []
    assert cfg.observability.domain == "unknown"


def test_accepts_mcp_tool():
    cfg = MergedConfig.validate_full({
        "tools": [{
            "id": "docs_api",
            "type": "mcp",
            "category": "read",
            "description": "Docs search",
            "server_url": "https://mcp.example.com",
            "transport": "sse",
            "namespace": "docs_api",
        }],
    })
    assert cfg.tools[0].type == ToolType.mcp


def test_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [], "typo_section": {"x": 1}})
    assert "typo_section" in str(exc.value)


def test_rejects_unknown_key_on_tool():
    tool = _valid_rest_tool()
    tool["timout_ms"] = 3000  # typo
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "timout_ms" in str(exc.value)


def test_rejects_unknown_key_on_auth():
    tool = _valid_rest_tool()
    tool["auth"]["scheme"] = "basic"  # extra key
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "scheme" in str(exc.value)


def test_rejects_unknown_key_on_endpoint():
    tool = _valid_rest_tool()
    tool["endpoints"][0]["verb"] = "GET"  # should be 'method'
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "verb" in str(exc.value)


def test_rejects_unknown_key_on_param():
    tool = _valid_rest_tool()
    tool["endpoints"][0]["params"][0]["default"] = "foo"  # removed field
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "default" in str(exc.value)


def test_rejects_unknown_key_on_response():
    tool = _valid_rest_tool()
    tool["response"]["default_max_size_chars"] = 4000  # removed global field
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "default_max_size_chars" in str(exc.value)


def test_rejects_unknown_key_on_health_check():
    tool = _valid_rest_tool()
    tool["health_check"] = {"enabled": False, "interval_s": 30}  # interval_s not in schema
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full({"tools": [tool]})
    assert "interval_s" in str(exc.value)


def test_rejects_invalid_method_enum():
    tool = _valid_rest_tool()
    tool["endpoints"][0]["method"] = "FETCH"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({"tools": [tool]})


def test_rejects_invalid_auth_type_enum():
    tool = _valid_rest_tool()
    tool["auth"]["type"] = "jwt"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({"tools": [tool]})


def test_rejects_invalid_param_source_enum():
    tool = _valid_rest_tool()
    tool["endpoints"][0]["params"][0]["source"] = "config"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({"tools": [tool]})


def test_rejects_non_positive_timeout():
    tool = _valid_rest_tool()
    tool["timeout_ms"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({"tools": [tool]})


def test_rejects_out_of_range_sample_rate():
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({
            "observability": {"otel": {"sample_rate": 1.5}},
        })


def test_rejects_invalid_server_port():
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)
    with pytest.raises(ValidationError):
        ServerConfig(port=0)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_health_check_enabled_defaults_to_true():
    tool = _valid_rest_tool()
    tool["health_check"] = {}
    cfg = MergedConfig.validate_full({"tools": [tool]})
    assert cfg.tools[0].health_check.enabled is True


def test_field_mapping_accepted_but_optional():
    tool = _valid_rest_tool()
    tool["response"]["field_mapping"] = [
        {"source": "results[*].title", "target": "job_title", "type": "string"}
    ]
    cfg = MergedConfig.validate_full({"tools": [tool]})
    assert cfg.tools[0].response.field_mapping[0].target == "job_title"


def test_enum_exports_are_usable():
    assert ToolType.rest_api.value == "rest_api"
    assert AuthType.api_key.value == "api_key"
    assert HttpMethod.POST.value == "POST"
    assert ParamSource.agent.value == "agent"
    assert ParamType.string.value == "string"
    assert ToolCategory.read.value == "read"
