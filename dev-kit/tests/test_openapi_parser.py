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


def test_suggested_id_uses_operation_id_when_present():
    """When the spec supplies a camelCase operationId, suggested_id should
    snake_case it instead of falling back to the path. This is what keeps
    chat history (which shows suggested_id) and registered connector
    names (which add_tool stores) in lockstep — preventing the
    workflow-phase regression where subagent.tools reference `bookTour`
    while connectors.write has `book_tour`, or where a UUID path forces
    the LLM to rename at add_tool time.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/v1/forecast": {
                "get": {
                    "operationId": "getWeatherForecast",
                    "summary": "Weather forecast",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/d394c4e8-4890-41d7-a619-cd6f19880232": {
                "post": {
                    "operationId": "bookTour",
                    "summary": "Book a tour",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    tools = parse_openapi_spec(spec)
    by_path = {t.path: t for t in tools}
    assert by_path["/v1/forecast"].suggested_id == "get_weather_forecast"
    # The UUID path's id now comes from operationId, NOT the path — so
    # the LLM sees a clean name and has no reason to rename at add_tool.
    assert (
        by_path["/d394c4e8-4890-41d7-a619-cd6f19880232"].suggested_id
        == "book_tour"
    )


def test_suggested_id_falls_back_to_path_when_no_operation_id():
    """Without operationId, the parser falls back to {method}_{path}. The
    id may be ugly for UUID paths but is deterministic; the tools-phase
    prompt now forbids the LLM from renaming so chat history matches
    what add_tool registers.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/v1/forecast": {"get": {"summary": "x", "responses": {"200": {"description": "OK"}}}},
            "/d394c4e8-4890-41d7-a619-cd6f19880232": {
                "post": {"summary": "x", "responses": {"200": {"description": "OK"}}}
            },
        },
    }
    tools = parse_openapi_spec(spec)
    by_path = {t.path: t for t in tools}
    assert by_path["/v1/forecast"].suggested_id == "get_v1_forecast"
    assert (
        by_path["/d394c4e8-4890-41d7-a619-cd6f19880232"].suggested_id
        == "post_d394c4e8_4890_41d7_a619_cd6f19880232"
    )
