"""Tests for OpenAPI spec parser."""
import pytest
from dev_kit.agent.openapi_parser import parse_openapi_spec, ParsedTool, ParsedParam


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/search": {
            "post": {
                "operationId": "searchJobs",
                "summary": "Search for jobs",
                "description": "Search job listings by query",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string", "description": "Search term"},
                                    "limit": {"type": "integer", "default": 10},
                                },
                            }
                        }
                    },
                },
            }
        },
        "/apply/{job_id}": {
            "post": {
                "summary": "Apply to a job",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Job identifier",
                    }
                ],
            }
        },
    },
}


def test_parse_base_url():
    """Parser extracts the first server URL as base_url."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    assert tools[0].base_url == "https://api.example.com/v1"


def test_parse_endpoints():
    """Parser returns one ParsedTool per path+method combination."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    paths = [(t.path, t.method) for t in tools]
    assert ("/search", "POST") in paths
    assert ("/apply/{job_id}", "POST") in paths


def test_parse_request_body_params():
    """Request body schema properties become ParsedParam entries with source='agent'."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    search_tool = next(t for t in tools if t.path == "/search")
    param_names = [p.name for p in search_tool.params]
    assert "query" in param_names
    assert "limit" in param_names
    query_param = next(p for p in search_tool.params if p.name == "query")
    assert query_param.required is True
    assert query_param.source == "agent"


def test_parse_path_params():
    """Path parameters become ParsedParam entries."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    apply_tool = next(t for t in tools if t.path == "/apply/{job_id}")
    assert any(p.name == "job_id" for p in apply_tool.params)


def test_parse_description_from_summary():
    """Tool description uses summary or path+method fallback."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    search_tool = next(t for t in tools if t.path == "/search")
    assert "Search" in search_tool.description or "search" in search_tool.description.lower()


def test_parse_api_key_auth():
    """API key auth scheme is detected and mapped correctly."""
    spec = {
        **MINIMAL_SPEC,
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-KEY",
                }
            }
        },
        "security": [{"ApiKeyAuth": []}],
    }
    tools = parse_openapi_spec(spec)
    assert tools[0].auth_type == "api_key"
    assert tools[0].auth_header == "X-API-KEY"


def test_empty_paths_returns_empty_list():
    """A spec with no paths returns an empty tool list."""
    spec = {"openapi": "3.0.0", "info": {"title": "Empty", "version": "1"}, "paths": {}}
    result = parse_openapi_spec(spec)
    assert result == []


def test_invalid_spec_raises_value_error():
    """A dict without 'paths' key raises ValueError."""
    with pytest.raises(ValueError, match="paths"):
        parse_openapi_spec({"openapi": "3.0.0"})
