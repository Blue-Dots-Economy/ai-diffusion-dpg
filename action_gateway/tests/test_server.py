"""Tests for the Action Gateway FastAPI server.

Covers normal execution, edge cases, and failure scenarios for
GET /tools, POST /execute, and GET /health.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.models import ToolDefinition, ToolResult
from src.registry.adapter_registry import AdapterRegistry
from src.server import create_app


def _make_tool_def(name: str, category: str = "read") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Description of {name}",
        input_schema={"type": "object", "properties": {}},
        category=category,
    )


def _make_adapter(
    tool_names: list[str],
    execute_result: ToolResult | None = None,
    healthy: bool = True,
) -> MagicMock:
    """Create a mock ToolAdapter."""
    adapter = MagicMock()
    adapter.get_tool_definitions.return_value = [_make_tool_def(n) for n in tool_names]
    adapter.health_check.return_value = healthy

    if execute_result is None:
        execute_result = ToolResult(
            tool_use_id="",
            tool_name=tool_names[0] if tool_names else "tool",
            result={"data": "ok"},
            success=True,
            result_text='{"data": "ok"}',
        )
    adapter.execute = AsyncMock(return_value=execute_result)
    return adapter


def _build_registry(*adapters_with_names: tuple[list[str], MagicMock]) -> AdapterRegistry:
    registry = AdapterRegistry()
    for names, adapter in adapters_with_names:
        for name in names:
            registry.register(name, adapter)
    return registry


class TestGetTools:
    def test_get_tools_returns_all_definitions(self):
        adapter = _make_adapter(["tool_a", "tool_b"])
        registry = _build_registry((["tool_a", "tool_b"], adapter))
        client = TestClient(create_app(registry))
        response = client.get("/tools")
        assert response.status_code == 200
        body = response.json()
        names = {t["name"] for t in body["tools"]}
        assert names == {"tool_a", "tool_b"}

    def test_get_tools_empty_registry_returns_empty_list(self):
        registry = AdapterRegistry()
        client = TestClient(create_app(registry))
        response = client.get("/tools")
        assert response.status_code == 200
        assert response.json() == {"tools": []}


class TestExecuteTool:
    def test_execute_successful(self):
        result = ToolResult(
            tool_use_id="",
            tool_name="tool_a",
            result={"answer": 42},
            success=True,
            result_text='{"answer": 42}',
        )
        adapter = _make_adapter(["tool_a"], execute_result=result)
        registry = _build_registry((["tool_a"], adapter))
        client = TestClient(create_app(registry))
        response = client.post(
            "/execute",
            json={
                "tool_name": "tool_a",
                "tool_use_id": "tu_001",
                "input_params": {"q": "hello"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["tool_use_id"] == "tu_001"
        assert body["result"] == {"answer": 42}

    def test_execute_unknown_tool_returns_error(self):
        registry = AdapterRegistry()
        client = TestClient(create_app(registry))
        response = client.post(
            "/execute",
            json={
                "tool_name": "nonexistent",
                "tool_use_id": "tu_002",
                "input_params": {},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "unknown_tool" in body["error"]
        assert body["tool_use_id"] == "tu_002"

    def test_execute_adapter_failure_returns_error(self):
        result = ToolResult(
            tool_use_id="",
            tool_name="tool_a",
            result={},
            success=False,
            error="adapter_error: something went wrong",
        )
        adapter = _make_adapter(["tool_a"], execute_result=result)
        registry = _build_registry((["tool_a"], adapter))
        client = TestClient(create_app(registry))
        response = client.post(
            "/execute",
            json={
                "tool_name": "tool_a",
                "tool_use_id": "tu_003",
                "input_params": {},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "adapter_error" in body["error"]

    def test_execute_optional_session_id_defaults_to_empty(self):
        adapter = _make_adapter(["tool_a"])
        registry = _build_registry((["tool_a"], adapter))
        client = TestClient(create_app(registry))
        response = client.post(
            "/execute",
            json={
                "tool_name": "tool_a",
                "tool_use_id": "tu_004",
                "input_params": {},
                # session_id omitted
            },
        )
        assert response.status_code == 200
        _, kwargs = adapter.execute.call_args
        # session_id should be empty string (default)
        called_session_id = adapter.execute.call_args[0][2]
        assert called_session_id == ""

    def test_execute_with_explicit_session_id(self):
        adapter = _make_adapter(["tool_a"])
        registry = _build_registry((["tool_a"], adapter))
        client = TestClient(create_app(registry))
        response = client.post(
            "/execute",
            json={
                "tool_name": "tool_a",
                "tool_use_id": "tu_005",
                "input_params": {},
                "session_id": "sess_xyz",
            },
        )
        assert response.status_code == 200
        called_session_id = adapter.execute.call_args[0][2]
        assert called_session_id == "sess_xyz"


class TestHealthEndpoint:
    def test_health_all_healthy(self):
        adapter1 = _make_adapter(["tool_a"], healthy=True)
        adapter2 = _make_adapter(["tool_b"], healthy=True)
        registry = _build_registry((["tool_a"], adapter1), (["tool_b"], adapter2))
        client = TestClient(create_app(registry))
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert all(body["adapters"].values())

    def test_health_degraded_when_adapter_unhealthy(self):
        adapter1 = _make_adapter(["tool_a"], healthy=True)
        adapter2 = _make_adapter(["tool_b"], healthy=False)
        registry = _build_registry((["tool_a"], adapter1), (["tool_b"], adapter2))
        client = TestClient(create_app(registry))
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"

    def test_health_empty_registry_is_healthy(self):
        registry = AdapterRegistry()
        client = TestClient(create_app(registry))
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["adapters"] == {}

    def test_health_deduplicates_adapters(self):
        """An MCP adapter registered under two names should be health-checked once."""
        mcp_adapter = _make_adapter(["mcp.search", "mcp.list"], healthy=True)
        registry = _build_registry((["mcp.search", "mcp.list"], mcp_adapter))
        client = TestClient(create_app(registry))
        response = client.get("/health")
        assert response.status_code == 200
        mcp_adapter.health_check.assert_called_once()
