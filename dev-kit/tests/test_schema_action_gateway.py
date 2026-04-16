"""Tests for updated ActionGateway schema."""
import pytest
from pydantic import ValidationError
from dev_kit.schema import ActionGatewayConfig, validate_partial


def test_rest_api_tool_validates():
    """A valid REST API tool definition should parse without errors."""
    data = {
        "tools": [
            {
                "id": "onest_market_lookup",
                "type": "rest_api",
                "category": "read",
                "description": "Search ONEST job listings",
                "base_url": "https://api.example.com",
                "auth": {"type": "api_key", "header": "X-API-KEY", "secret_env": "MY_KEY"},
                "timeout_ms": 5000,
                "endpoints": [
                    {
                        "name": "search",
                        "method": "POST",
                        "path": "/search",
                        "params": [
                            {"name": "query", "source": "agent", "type": "string", "required": True, "description": "search query"},
                            {"name": "limit", "source": "static", "type": "integer", "value": 10},
                        ],
                    }
                ],
                "response": {"max_size_chars": 4000},
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].id == "onest_market_lookup"
    assert config.tools[0].endpoints[0].params[0].source == "agent"


def test_mcp_tool_validates():
    """A valid MCP tool definition should parse without errors."""
    data = {
        "tools": [
            {
                "id": "obsrv_query",
                "type": "mcp",
                "category": "read",
                "description": "Query Obsrv data",
                "mcp_server_url": "https://mcp.example.com",
                "tool_name": "query_dataset",
                "input_schema": {"type": "object", "properties": {"dataset": {"type": "string"}}},
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].type == "mcp"


def test_auth_none_validates():
    """Auth type 'none' requires no extra fields."""
    data = {
        "tools": [
            {
                "id": "webhook",
                "type": "rest_api",
                "category": "write",
                "description": "Post to webhook",
                "base_url": "https://webhook.site/abc",
                "auth": {"type": "none"},
                "endpoints": [{"name": "post", "method": "POST", "path": "/"}],
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].auth.type == "none"


def test_validate_partial_rest_api_tool():
    """validate_partial should accept valid action_gateway partial data."""
    data = {
        "tools": [
            {
                "id": "test_tool",
                "type": "rest_api",
                "category": "read",
                "description": "test",
                "base_url": "https://api.example.com",
                "auth": {"type": "none"},
                "endpoints": [{"name": "get", "method": "GET", "path": "/data"}],
            }
        ]
    }
    errors = validate_partial("action_gateway", data)
    assert errors == [], f"Unexpected errors: {errors}"


def test_invalid_category_rejected():
    """Invalid category value should fail validation."""
    data = {
        "tools": [
            {
                "id": "bad",
                "type": "rest_api",
                "category": "invalid_category",
                "description": "x",
                "base_url": "https://api.example.com",
                "auth": {"type": "none"},
                "endpoints": [],
            }
        ]
    }
    with pytest.raises(ValidationError):
        ActionGatewayConfig.model_validate(data)
