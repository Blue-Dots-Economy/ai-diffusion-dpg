"""
dev-kit/tests/test_tools_openapi.py

Tests for fetch_openapi_spec_from_url and set_response_transformation tool handlers.
"""
from __future__ import annotations

import json
import os
import pytest
import respx
import httpx as _httpx
from unittest.mock import MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler


@pytest.fixture
def handler():
    acc = ConfigAccumulator()
    state = {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    return ToolHandler(acc, state)


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/search": {
            "post": {
                "summary": "Search for jobs",
                "parameters": [],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            }
                        }
                    }
                },
            }
        }
    },
}


# ---------------------------------------------------------------------------
# fetch_openapi_spec_from_url — normal
# ---------------------------------------------------------------------------

class TestFetchOpenApiSpecFromUrl:
    @respx.mock
    def test_fetches_json_spec_and_returns_candidates(self, handler):
        respx.get("https://api.example.com/openapi.json").mock(
            return_value=_httpx.Response(200, json=MINIMAL_SPEC)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/openapi.json"})
        candidates = json.loads(result)
        assert isinstance(candidates, list)
        assert len(candidates) == 1
        assert candidates[0]["path"] == "/search"
        assert candidates[0]["method"] == "POST"
        assert candidates[0]["base_url"] == "https://api.example.com"

    @respx.mock
    def test_fetches_yaml_spec(self, handler):
        import yaml as _yaml
        spec_yaml = _yaml.dump(MINIMAL_SPEC)
        respx.get("https://api.example.com/openapi.yaml").mock(
            return_value=_httpx.Response(200, content=spec_yaml.encode(), headers={"content-type": "text/yaml"})
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/openapi.yaml"})
        candidates = json.loads(result)
        assert candidates[0]["path"] == "/search"

    @respx.mock
    def test_returns_error_on_http_failure(self, handler):
        respx.get("https://bad.example.com/spec.json").mock(
            return_value=_httpx.Response(404)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://bad.example.com/spec.json"})
        assert result.startswith("ERROR")

    @respx.mock
    def test_returns_error_when_missing_paths_key(self, handler):
        bad_spec = {"openapi": "3.0.0", "info": {"title": "Bad"}}
        respx.get("https://api.example.com/bad.json").mock(
            return_value=_httpx.Response(200, json=bad_spec)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/bad.json"})
        assert result.startswith("ERROR")

    @respx.mock
    def test_returns_error_on_connect_failure(self, handler):
        respx.get("https://unreachable.example.com/spec.json").mock(
            side_effect=_httpx.ConnectError("refused")
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://unreachable.example.com/spec.json"})
        assert result.startswith("ERROR")

    def test_returns_error_on_empty_url(self, handler):
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": ""})
        assert result.startswith("ERROR")
        assert "url is required" in result

    @respx.mock
    def test_returns_error_when_response_is_not_object(self, handler):
        respx.get("https://api.example.com/list.json").mock(
            return_value=_httpx.Response(200, json=["not", "an", "object"])
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/list.json"})
        assert result.startswith("ERROR")
        assert "not a JSON/YAML object" in result
