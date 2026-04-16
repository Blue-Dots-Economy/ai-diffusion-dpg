# Action Gateway: Production Connector Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PoC mock Action Gateway with a generic, config-driven adapter framework supporting REST API and MCP tool execution.

**Architecture:** Action Gateway becomes an adapter host. At startup it reads tool YAML, instantiates the right `ToolAdapter` subclass per tool (`RestApiAdapter` for REST, `McpAdapter` for MCP), and serves tool definitions via `GET /tools`. Agent Core fetches definitions at startup and routes tool calls via `POST /execute` — same contract as today.

**Tech Stack:** Python 3.11+, FastAPI, httpx, pydantic v2, pyyaml, `mcp` SDK, `dpg_telemetry` (OTel), pytest

**Spec:** `docs/superpowers/specs/2026-04-15-action-gateway-production-connector-design.md`
**Issue:** [#17](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/17)

**Note on OTel (#96):** This plan includes structured logging in all adapters and server endpoints per project logging rules. The full OTel instrumentation (tracer spans, meter histograms/counters from the spec's Observability section) is tracked as sub-issue [#96](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/96) and should be layered in on the same branch after the adapter framework is functional. The `dpg_telemetry` package is already a dependency.

---

## File Map

### Action Gateway — New Files

| File | Responsibility |
|---|---|
| `action_gateway/src/models.py` | Pydantic models: `ToolDefinition`, `ToolResult`, `ExecuteRequest`, `ExecuteResponse`, `ToolsResponse`, `HealthResponse` |
| `action_gateway/src/adapters/base.py` | `ToolAdapter` ABC |
| `action_gateway/src/adapters/rest_api.py` | `RestApiAdapter` — one instance per REST tool |
| `action_gateway/src/adapters/mcp.py` | `McpAdapter` — one instance per MCP server |
| `action_gateway/src/adapters/__init__.py` | Re-exports adapter classes |
| `action_gateway/src/registry/adapter_registry.py` | `AdapterRegistry` — tool_name → adapter lookup |
| `action_gateway/src/registry/adapter_factory.py` | `AdapterFactory` — type string → adapter class, builds registry from config |
| `action_gateway/src/registry/__init__.py` | Re-exports registry classes |
| `action_gateway/src/config/loader.py` | Config loading with deep-merge (extracted from main.py) |
| `action_gateway/src/config/__init__.py` | Empty |
| `action_gateway/src/server.py` | FastAPI app: `GET /tools`, `POST /execute`, `GET /health` |

### Action Gateway — Deleted Files

| File | Reason |
|---|---|
| `action_gateway/src/mock_gateway.py` | Replaced by adapter framework |
| `action_gateway/src/mock_server.py` | Replaced by adapter framework |

### Action Gateway — Modified Files

| File | Change |
|---|---|
| `action_gateway/main.py` | Import `server.py` instead of `mock_server.py`, pass config to app factory |
| `action_gateway/pyproject.toml` | Add `mcp` and `python-dotenv` dependencies |
| `action_gateway/Dockerfile` | Update entry point if needed |
| `action_gateway/README.md` | Rewrite for production architecture |

### Action Gateway — New Test Files

| File | What it tests |
|---|---|
| `action_gateway/tests/conftest.py` | Shared fixtures: sample configs, mock HTTP responses |
| `action_gateway/tests/test_models.py` | Model validation and serialization |
| `action_gateway/tests/test_rest_api_adapter.py` | RestApiAdapter: init, tool def generation, execution, auth, errors |
| `action_gateway/tests/test_mcp_adapter.py` | McpAdapter: init, tool discovery, execution, reconnect |
| `action_gateway/tests/test_adapter_registry.py` | AdapterRegistry: register, resolve, get_all |
| `action_gateway/tests/test_adapter_factory.py` | AdapterFactory: builds registry from config |
| `action_gateway/tests/test_server.py` | FastAPI endpoints: GET /tools, POST /execute, GET /health |

### Action Gateway — Deleted Test Files

| File | Reason |
|---|---|
| `action_gateway/tests/test_mock_gateway.py` | Mock gateway deleted |
| `action_gateway/tests/test_mock_server.py` | Mock server deleted |
| `action_gateway/tests/test_main.py` | Rewritten (main.py changes) |

### Agent Core — Modified Files

| File | Change |
|---|---|
| `agent_core/src/http_clients/action_gateway.py` | Fetch tool defs from `GET /tools` at startup instead of building from config |
| `agent_core/src/tool_registry.py` | Build consent set from `category` field instead of config section |

### Config — Modified Files

| File | Change |
|---|---|
| `dev-kit/configs/kkb/action_gateway.yaml` | Rewrite to new `tools:` schema |
| `dev-kit/configs/kkb/agent_core.yaml` | Remove `connectors.read` and `connectors.write`, keep `connectors.internal` |
| `dev-kit/dpg/agent_core.yaml` | Change `action_gateway_client.endpoint` from `/execute` URL to base URL |
| `dev-kit/dpg/action_gateway.yaml` | Add `tools: []` default and `response.default_max_size_chars` |

### Documentation — Modified Files

| File | Change |
|---|---|
| `ARCHITECTURE.md` | Rewrite Action Gateway section, update config architecture, update status |
| `action_gateway/README.md` | Full rewrite for production architecture |

---

## Task Dependency Graph

```
Task 1 (models) ──┐
                   ├── Task 3 (RestApiAdapter) ──┐
Task 2 (ABC)   ───┤                              ├── Task 5 (AdapterFactory) ── Task 6 (server) ── Task 7 (main.py) ──┐
                   ├── Task 4 (McpAdapter)   ────┘                                                                      │
                                                                                                                         ├── Task 10 (docs)
Task 8 (AG config) ─────────────────────────────────────────────────────── Task 9 (AC integration) ─────────────────────┘
```

Tasks 1-7: Action Gateway internals (bottom-up build).
Task 8: Config files.
Task 9: Agent Core integration.
Task 10: Documentation + cleanup.

---

### Task 1: Data Models (`action_gateway/src/models.py`)

**Files:**
- Create: `action_gateway/src/models.py`
- Test: `action_gateway/tests/test_models.py`

- [ ] **Step 1: Write tests for ToolDefinition and ToolResult**

```python
# action_gateway/tests/test_models.py
"""Tests for Action Gateway data models."""

import pytest
from pydantic import ValidationError

from src.models import (
    ExecuteRequest,
    ExecuteResponse,
    HealthResponse,
    ToolDefinition,
    ToolResult,
    ToolsResponse,
)


class TestToolDefinition:
    """ToolDefinition validation and serialization."""

    def test_valid_read_tool(self):
        td = ToolDefinition(
            name="weather",
            description="Get weather data",
            input_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
            category="read",
        )
        assert td.name == "weather"
        assert td.category == "read"

    def test_valid_write_tool(self):
        td = ToolDefinition(
            name="apply", description="Submit application", input_schema={}, category="write"
        )
        assert td.category == "write"

    def test_valid_identity_tool(self):
        td = ToolDefinition(
            name="verify", description="Verify identity", input_schema={}, category="identity"
        )
        assert td.category == "identity"

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="bad", description="Bad tool", input_schema={}, category="execute"
            )

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ToolDefinition(name="", description="No name", input_schema={}, category="read")

    def test_empty_description_rejected(self):
        with pytest.raises(ValidationError):
            ToolDefinition(name="tool", description="", input_schema={}, category="read")

    def test_serialization_round_trip(self):
        td = ToolDefinition(
            name="weather",
            description="Get weather",
            input_schema={"type": "object", "properties": {}},
            category="read",
        )
        data = td.model_dump()
        assert data["name"] == "weather"
        td2 = ToolDefinition.model_validate(data)
        assert td2 == td


class TestToolResult:
    """ToolResult construction."""

    def test_success_result(self):
        tr = ToolResult(
            tool_use_id="toolu_01",
            tool_name="weather",
            result={"temp": 30},
            success=True,
        )
        assert tr.success is True
        assert tr.error is None
        assert tr.result_text == ""

    def test_failure_result(self):
        tr = ToolResult(
            tool_use_id="toolu_02",
            tool_name="weather",
            result={},
            success=False,
            error="adapter_timeout: weather",
        )
        assert tr.success is False
        assert "timeout" in tr.error


class TestExecuteRequest:
    """ExecuteRequest validation."""

    def test_valid_request(self):
        req = ExecuteRequest(
            tool_name="weather",
            tool_use_id="toolu_01",
            input_params={"location": "Delhi"},
            session_id="sess-1",
        )
        assert req.tool_name == "weather"

    def test_optional_session_id(self):
        req = ExecuteRequest(
            tool_name="weather", tool_use_id="toolu_01", input_params={}
        )
        assert req.session_id == ""


class TestExecuteResponse:
    """ExecuteResponse serialization."""

    def test_from_tool_result(self):
        tr = ToolResult(
            tool_use_id="toolu_01",
            tool_name="weather",
            result={"temp": 30},
            success=True,
        )
        resp = ExecuteResponse(
            tool_use_id=tr.tool_use_id,
            tool_name=tr.tool_name,
            success=tr.success,
            result=tr.result,
            result_text=tr.result_text,
            error=tr.error,
        )
        assert resp.success is True


class TestToolsResponse:
    """ToolsResponse wraps tool definitions."""

    def test_empty_tools(self):
        resp = ToolsResponse(tools=[])
        assert resp.tools == []

    def test_with_tools(self):
        td = ToolDefinition(
            name="w", description="Weather", input_schema={}, category="read"
        )
        resp = ToolsResponse(tools=[td])
        assert len(resp.tools) == 1


class TestHealthResponse:
    """HealthResponse includes per-adapter status."""

    def test_healthy(self):
        resp = HealthResponse(status="healthy", adapters={"weather": True})
        assert resp.status == "healthy"

    def test_partial_unhealthy(self):
        resp = HealthResponse(
            status="healthy", adapters={"weather": True, "mcp_tool": False}
        )
        assert resp.adapters["mcp_tool"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd action_gateway && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models'` (file doesn't exist yet)

- [ ] **Step 3: Write the models**

```python
# action_gateway/src/models.py
"""Data models for the Action Gateway adapter framework.

Defines the contracts for tool definitions (served to Agent Core),
execution requests/responses, and health status.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator


class ToolDefinition(BaseModel):
    """Tool schema in Anthropic tool format, served to Agent Core via GET /tools."""

    name: str
    description: str
    input_schema: dict[str, Any]
    category: Literal["read", "write", "identity"]

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Tool name must not be empty")
        return v

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Tool description must not be empty")
        return v


class ToolResult(BaseModel):
    """Normalized result returned from any adapter execution."""

    tool_use_id: str
    tool_name: str
    result: dict[str, Any]
    success: bool
    result_text: str = ""
    error: Optional[str] = None


class ExecuteRequest(BaseModel):
    """Request body for POST /execute."""

    tool_name: str
    tool_use_id: str
    input_params: dict[str, Any]
    session_id: str = ""


class ExecuteResponse(BaseModel):
    """Response body for POST /execute."""

    tool_use_id: str
    tool_name: str
    success: bool
    result: dict[str, Any]
    result_text: str = ""
    error: Optional[str] = None


class ToolsResponse(BaseModel):
    """Response body for GET /tools."""

    tools: list[ToolDefinition]


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str
    adapters: dict[str, bool]
```

- [ ] **Step 4: Create `action_gateway/src/adapters/__init__.py` and `action_gateway/src/registry/__init__.py` and `action_gateway/src/config/__init__.py`**

```python
# action_gateway/src/adapters/__init__.py
# action_gateway/src/registry/__init__.py
# action_gateway/src/config/__init__.py
# (all empty — package markers)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd action_gateway && uv run pytest tests/test_models.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add action_gateway/src/models.py action_gateway/src/adapters/__init__.py action_gateway/src/registry/__init__.py action_gateway/src/config/__init__.py action_gateway/tests/test_models.py
git commit -m "feat(action-gateway): add data models for adapter framework

ToolDefinition, ToolResult, ExecuteRequest/Response, ToolsResponse, HealthResponse.
Pydantic v2 models with validation for category and non-empty name/description."
```

---

### Task 2: ToolAdapter ABC (`action_gateway/src/adapters/base.py`)

**Files:**
- Create: `action_gateway/src/adapters/base.py`

No separate test file — the ABC is tested via its concrete implementations.

- [ ] **Step 1: Write the ABC**

```python
# action_gateway/src/adapters/base.py
"""Abstract base class for all Action Gateway tool adapters.

Every external data source (REST API, MCP server, database, etc.) is accessed
through a ToolAdapter subclass. The AdapterRegistry instantiates adapters at
startup and routes tool calls to the correct adapter at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import ToolDefinition, ToolResult


class ToolAdapter(ABC):
    """Base class for all Action Gateway tool adapters.

    Subclass contract:
        - __init__ receives the tool's YAML config dict. Resolve all secrets
          and validate config at init time — fail loudly on missing values.
        - get_tool_definitions() returns tool schemas the LLM sees.
          RestApiAdapter returns a single-element list (one tool per instance).
          McpAdapter returns many (all tools discovered from that server).
        - execute() runs the tool call and returns a ToolResult. Never raises —
          all errors are caught and returned as ToolResult(success=False).
        - health_check() verifies the adapter's backing service is reachable.
    """

    def __init__(self, config: dict) -> None:
        """Initialize the adapter with its YAML config block.

        Args:
            config: The tool config dict from action_gateway.yaml tools[] entry.
        """
        self.config = config

    @abstractmethod
    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return tool schemas the LLM sees.

        Returns:
            List of ToolDefinition. One element for REST adapters,
            multiple for MCP adapters.
        """

    @abstractmethod
    async def execute(self, tool_name: str, params: dict, session_id: str) -> ToolResult:
        """Execute a tool call and return normalized result.

        Args:
            tool_name: The tool name as the LLM specified it.
            params: The input parameters from the LLM (source:agent params only).
            session_id: The session identifier for tracing.

        Returns:
            ToolResult with success=True on success, success=False on any error.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Verify the adapter's backing service is reachable.

        Returns:
            True if the backing service responds, False otherwise.
        """
```

- [ ] **Step 2: Update `action_gateway/src/adapters/__init__.py`**

```python
# action_gateway/src/adapters/__init__.py
from src.adapters.base import ToolAdapter

__all__ = ["ToolAdapter"]
```

- [ ] **Step 3: Commit**

```bash
git add action_gateway/src/adapters/base.py action_gateway/src/adapters/__init__.py
git commit -m "feat(action-gateway): add ToolAdapter ABC

Abstract base class with get_tool_definitions(), execute(), health_check().
Concrete adapters (RestApiAdapter, McpAdapter) implement this contract."
```

---

### Task 3: RestApiAdapter (`action_gateway/src/adapters/rest_api.py`)

**Files:**
- Create: `action_gateway/src/adapters/rest_api.py`
- Test: `action_gateway/tests/test_rest_api_adapter.py`
- Create: `action_gateway/tests/conftest.py`

- [ ] **Step 1: Write shared test fixtures**

```python
# action_gateway/tests/conftest.py
"""Shared test fixtures for Action Gateway tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def rest_tool_config():
    """Minimal REST API tool config for testing."""
    return {
        "id": "test_weather",
        "type": "rest_api",
        "category": "read",
        "description": "Get weather for a location",
        "base_url": "https://api.weather.test/v1",
        "auth": {"type": "api_key", "header": "X-API-Key", "secret_env": "TEST_WEATHER_KEY"},
        "endpoints": [
            {
                "name": "get_forecast",
                "method": "GET",
                "path": "/forecast",
                "params": [
                    {
                        "name": "location",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "City name",
                    },
                    {
                        "name": "units",
                        "source": "static",
                        "type": "string",
                        "value": "metric",
                    },
                ],
            }
        ],
        "response": {"max_size_chars": 4000},
    }


@pytest.fixture
def rest_write_tool_config():
    """REST API write tool config for consent gating tests."""
    return {
        "id": "test_apply",
        "type": "rest_api",
        "category": "write",
        "description": "Submit an application",
        "base_url": "https://api.jobs.test/v1",
        "auth": {"type": "bearer", "secret_env": "TEST_JOBS_TOKEN"},
        "endpoints": [
            {
                "name": "submit",
                "method": "POST",
                "path": "/applications",
                "params": [
                    {
                        "name": "job_id",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "Job listing ID",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def rest_no_auth_config():
    """REST API tool with no auth."""
    return {
        "id": "test_public",
        "type": "rest_api",
        "category": "read",
        "description": "Public data endpoint",
        "base_url": "https://api.public.test",
        "auth": {"type": "none"},
        "endpoints": [
            {
                "name": "get_data",
                "method": "GET",
                "path": "/data",
                "params": [
                    {
                        "name": "query",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "Search query",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def mcp_tool_config():
    """Minimal MCP tool config for testing."""
    return {
        "id": "test_mcp",
        "type": "mcp",
        "category": "read",
        "description": "Test MCP server",
        "server_url": "https://mcp.test.example/sse",
        "transport": "sse",
        "namespace": "test_mcp",
    }
```

- [ ] **Step 2: Write tests for RestApiAdapter**

```python
# action_gateway/tests/test_rest_api_adapter.py
"""Tests for RestApiAdapter."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.adapters.rest_api import RestApiAdapter
from src.models import ToolDefinition, ToolResult


class TestRestApiAdapterInit:
    """Construction and config validation."""

    def test_init_resolves_api_key_from_env(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test-123")
        adapter = RestApiAdapter(rest_tool_config)
        assert adapter._resolved_secret == "sk-test-123"

    def test_init_resolves_bearer_from_env(self, rest_write_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_JOBS_TOKEN", "bearer-token-xyz")
        adapter = RestApiAdapter(rest_write_tool_config)
        assert adapter._resolved_secret == "bearer-token-xyz"

    def test_init_no_auth_no_secret(self, rest_no_auth_config):
        adapter = RestApiAdapter(rest_no_auth_config)
        assert adapter._resolved_secret is None

    def test_init_missing_env_var_raises(self, rest_tool_config):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="TEST_WEATHER_KEY"):
                RestApiAdapter(rest_tool_config)


class TestRestApiAdapterToolDefinition:
    """Tool definition generation from config."""

    def test_returns_single_element_list(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        defs = adapter.get_tool_definitions()
        assert len(defs) == 1
        assert isinstance(defs[0], ToolDefinition)

    def test_tool_name_from_config_id(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        td = adapter.get_tool_definitions()[0]
        assert td.name == "test_weather"

    def test_description_from_config(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        td = adapter.get_tool_definitions()[0]
        assert td.description == "Get weather for a location"

    def test_category_from_config(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        td = adapter.get_tool_definitions()[0]
        assert td.category == "read"

    def test_only_agent_params_in_schema(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        td = adapter.get_tool_definitions()[0]
        props = td.input_schema["properties"]
        assert "location" in props
        assert "units" not in props  # static param excluded

    def test_required_params_in_schema(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        td = adapter.get_tool_definitions()[0]
        assert "location" in td.input_schema["required"]


class TestRestApiAdapterExecute:
    """Execution: HTTP calls, param merging, error handling."""

    @pytest.mark.asyncio
    async def test_get_request_with_merged_params(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test-123")
        adapter = RestApiAdapter(rest_tool_config)

        mock_response = httpx.Response(
            200,
            json={"temp": 30, "humidity": 60},
            request=httpx.Request("GET", "https://api.weather.test/v1/forecast"),
        )

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is True
        assert result.result["temp"] == 30
        # Verify static param 'units=metric' was injected
        call_kwargs = mock_client.request.call_args
        assert "units" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_post_request(self, rest_write_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_JOBS_TOKEN", "bearer-xyz")
        adapter = RestApiAdapter(rest_write_tool_config)

        mock_response = httpx.Response(
            200,
            json={"status": "submitted", "ref": "APP-123"},
            request=httpx.Request("POST", "https://api.jobs.test/v1/applications"),
        )

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            result = await adapter.execute("test_apply", {"job_id": "j-42"}, "sess-1")

        assert result.success is True
        assert result.result["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_api_key_injected_in_header(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test-123")
        adapter = RestApiAdapter(rest_tool_config)

        mock_response = httpx.Response(
            200,
            json={"temp": 30},
            request=httpx.Request("GET", "https://api.weather.test/v1/forecast"),
        )

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("X-API-Key") == "sk-test-123"

    @pytest.mark.asyncio
    async def test_bearer_auth_injected(self, rest_write_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_JOBS_TOKEN", "bearer-xyz")
        adapter = RestApiAdapter(rest_write_tool_config)

        mock_response = httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request("POST", "https://api.jobs.test/v1/applications"),
        )

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            await adapter.execute("test_apply", {"job_id": "j-1"}, "sess-1")

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer bearer-xyz"

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)

        mock_response = httpx.Response(
            500,
            json={"error": "internal"},
            request=httpx.Request("GET", "https://api.weather.test/v1/forecast"),
        )

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is False
        assert "http_error: 500" in result.error

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is False
        assert "adapter_timeout" in result.error

    @pytest.mark.asyncio
    async def test_response_truncation(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        config = {**rest_tool_config, "response": {"max_size_chars": 50}}
        adapter = RestApiAdapter(config)

        large_body = json.dumps({"data": "x" * 200})
        mock_response = httpx.Response(
            200,
            content=large_body.encode(),
            request=httpx.Request("GET", "https://api.weather.test/v1/forecast"),
        )
        mock_response.headers["content-type"] = "application/json"

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_response)
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is True
        result_str = json.dumps(result.result)
        assert len(result_str) <= 100  # some overhead from wrapping


class TestRestApiAdapterHealthCheck:
    """Health check verifies the backing service is reachable."""

    def test_health_check_success(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        # Health check does a HEAD to base_url; mock it
        with patch("httpx.head") as mock_head:
            mock_head.return_value = httpx.Response(200)
            assert adapter.health_check() is True

    def test_health_check_failure(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        adapter = RestApiAdapter(rest_tool_config)
        with patch("httpx.head") as mock_head:
            mock_head.side_effect = httpx.ConnectError("unreachable")
            assert adapter.health_check() is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.adapters.rest_api'`

- [ ] **Step 4: Implement RestApiAdapter**

```python
# action_gateway/src/adapters/rest_api.py
"""REST API adapter for the Action Gateway.

One instance per REST tool config block. Resolves auth secrets from
environment variables at startup. Builds tool definitions from YAML
config, exposing only source:agent params to the LLM.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE_CHARS = 4000
_DEFAULT_TIMEOUT_MS = 5000


class RestApiAdapter(ToolAdapter):
    """Adapter for REST/HTTP API tool execution.

    Args:
        config: Tool config dict from the tools[] array in action_gateway.yaml.

    Raises:
        ValueError: If a required auth secret_env is missing from the environment.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._tool_id: str = config["id"]
        self._description: str = config["description"]
        self._category: str = config["category"]
        self._base_url: str = config["base_url"].rstrip("/")
        self._endpoint: dict = config["endpoints"][0]  # MVP: one endpoint per tool
        self._max_size_chars: int = config.get("response", {}).get(
            "max_size_chars", _DEFAULT_MAX_SIZE_CHARS
        )
        self._timeout_s: float = config.get("timeout_ms", _DEFAULT_TIMEOUT_MS) / 1000.0

        # Resolve auth
        auth_config = config.get("auth", {"type": "none"})
        self._auth_type: str = auth_config.get("type", "none")
        self._auth_header: str = auth_config.get("header", "")
        self._resolved_secret: str | None = None

        if self._auth_type != "none":
            secret_env = auth_config.get("secret_env", "")
            if not secret_env:
                raise ValueError(
                    f"Tool '{self._tool_id}': auth.type='{self._auth_type}' "
                    f"requires auth.secret_env to be set."
                )
            self._resolved_secret = os.environ.get(secret_env)
            if self._resolved_secret is None:
                raise ValueError(
                    f"Tool '{self._tool_id}': environment variable '{secret_env}' "
                    f"is not set. Set it before starting the gateway."
                )

        # Build tool definition once
        self._tool_definition = self._build_tool_definition()

        # HTTP client (created lazily on first execute for async compat)
        self._http_client: httpx.AsyncClient | None = None

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return a single-element list with this tool's definition."""
        return [self._tool_definition]

    async def execute(self, tool_name: str, params: dict, session_id: str) -> ToolResult:
        """Execute the REST API call with merged params.

        Args:
            tool_name: Tool name (should match self._tool_id).
            params: LLM-provided params (source:agent only).
            session_id: Session ID for tracing.

        Returns:
            ToolResult with raw JSON response or structured error.
        """
        start = time.time()
        tool_use_id = f"exec_{tool_name}_{session_id}"

        try:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=self._timeout_s)

            method = self._endpoint["method"].upper()
            url = f"{self._base_url}{self._endpoint['path']}"
            headers = self._build_headers()
            merged_params = self._merge_params(params)

            if method == "GET":
                response = await self._http_client.request(
                    method, url, headers=headers, params=merged_params
                )
            else:
                response = await self._http_client.request(
                    method, url, headers=headers, json=merged_params
                )

            latency_ms = int((time.time() - start) * 1000)

            if response.status_code >= 400:
                logger.error(
                    "rest_api.execute",
                    extra={
                        "operation": f"rest_api.execute.{tool_name}",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": latency_ms,
                    },
                )
                return ToolResult(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    result={},
                    success=False,
                    error=f"http_error: {response.status_code}",
                )

            result_data = self._parse_and_truncate(response)

            logger.info(
                "rest_api.execute",
                extra={
                    "operation": f"rest_api.execute.{tool_name}",
                    "status": "success",
                    "latency_ms": latency_ms,
                },
            )

            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result=result_data,
                success=True,
            )

        except httpx.TimeoutException:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "rest_api.execute",
                extra={
                    "operation": f"rest_api.execute.{tool_name}",
                    "status": "failure",
                    "error": "timeout",
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"adapter_timeout: {tool_name}",
            )

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "rest_api.execute",
                extra={
                    "operation": f"rest_api.execute.{tool_name}",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"adapter_error: {type(e).__name__}: {e}",
            )

    def health_check(self) -> bool:
        """Check if the base URL is reachable with a HEAD request."""
        try:
            resp = httpx.head(self._base_url, timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False

    # --- Private helpers ---

    def _build_tool_definition(self) -> ToolDefinition:
        """Build the ToolDefinition from endpoint config."""
        agent_params = [
            p for p in self._endpoint.get("params", []) if p.get("source") == "agent"
        ]

        properties = {}
        required = []
        for p in agent_params:
            prop: dict[str, Any] = {"type": p.get("type", "string")}
            if p.get("description"):
                prop["description"] = p["description"]
            if p.get("default") is not None:
                prop["default"] = p["default"]
            properties[p["name"]] = prop
            if p.get("required", False):
                required.append(p["name"])

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required

        return ToolDefinition(
            name=self._tool_id,
            description=self._description,
            input_schema=input_schema,
            category=self._category,
        )

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers including auth."""
        headers: dict[str, str] = {}
        if self._auth_type == "api_key" and self._resolved_secret:
            headers[self._auth_header] = self._resolved_secret
        elif self._auth_type == "bearer" and self._resolved_secret:
            headers["Authorization"] = f"Bearer {self._resolved_secret}"
        return headers

    def _merge_params(self, agent_params: dict) -> dict:
        """Merge LLM-provided (source:agent) params with static params."""
        merged = dict(agent_params)
        for p in self._endpoint.get("params", []):
            if p.get("source") == "static" and "value" in p:
                merged[p["name"]] = p["value"]
        return merged

    def _parse_and_truncate(self, response: httpx.Response) -> dict:
        """Parse response JSON and truncate if needed."""
        try:
            data = response.json()
        except Exception:
            text = response.text[: self._max_size_chars]
            return {"raw_text": text}

        serialized = json.dumps(data)
        if len(serialized) <= self._max_size_chars:
            return data if isinstance(data, dict) else {"data": data}

        # Truncate: serialize, cut, wrap
        truncated = serialized[: self._max_size_chars]
        return {"truncated_json": truncated, "_truncated": True}
```

- [ ] **Step 5: Add `pytest-asyncio` to dev dependencies**

Edit `action_gateway/pyproject.toml` to add `pytest-asyncio>=0.23` to `[project.optional-dependencies] dev`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add action_gateway/src/adapters/rest_api.py action_gateway/tests/test_rest_api_adapter.py action_gateway/tests/conftest.py action_gateway/pyproject.toml
git commit -m "feat(action-gateway): add RestApiAdapter

Config-driven REST API execution with auth resolution from env vars,
static/agent param merging, response truncation, and structured error handling."
```

---

### Task 4: McpAdapter (`action_gateway/src/adapters/mcp.py`)

**Files:**
- Create: `action_gateway/src/adapters/mcp.py`
- Test: `action_gateway/tests/test_mcp_adapter.py`

- [ ] **Step 1: Write tests for McpAdapter**

```python
# action_gateway/tests/test_mcp_adapter.py
"""Tests for McpAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.mcp import McpAdapter
from src.models import ToolDefinition, ToolResult


def _mock_mcp_tool(name: str, description: str, schema: dict) -> MagicMock:
    """Create a mock MCP tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = schema
    return tool


class TestMcpAdapterInit:
    """Construction and tool discovery."""

    @pytest.mark.asyncio
    async def test_discovers_tools_from_server(self, mcp_tool_config):
        mock_tools = [
            _mock_mcp_tool("search", "Search hotels", {"type": "object", "properties": {}}),
            _mock_mcp_tool("book", "Book room", {"type": "object", "properties": {}}),
        ]

        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_tools
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        defs = adapter.get_tool_definitions()
        assert len(defs) == 2
        assert defs[0].name == "test_mcp.search"
        assert defs[1].name == "test_mcp.book"

    @pytest.mark.asyncio
    async def test_namespace_prefixed(self, mcp_tool_config):
        mock_tools = [
            _mock_mcp_tool("query", "Query data", {"type": "object", "properties": {}}),
        ]

        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_tools
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        td = adapter.get_tool_definitions()[0]
        assert td.name == "test_mcp.query"

    @pytest.mark.asyncio
    async def test_category_from_config(self, mcp_tool_config):
        mock_tools = [
            _mock_mcp_tool("tool1", "A tool", {"type": "object", "properties": {}}),
        ]

        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_tools
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        assert adapter.get_tool_definitions()[0].category == "read"

    @pytest.mark.asyncio
    async def test_no_tools_discovered(self, mcp_tool_config):
        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        assert adapter.get_tool_definitions() == []


class TestMcpAdapterExecute:
    """Tool execution via MCP protocol."""

    @pytest.mark.asyncio
    async def test_strips_namespace_for_mcp_call(self, mcp_tool_config):
        mock_tools = [
            _mock_mcp_tool("search", "Search", {"type": "object", "properties": {}}),
        ]

        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_tools
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        mock_call_result = MagicMock()
        mock_call_result.content = [MagicMock(text='{"results": []}')]
        mock_call_result.isError = False

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_call_result
            result = await adapter.execute("test_mcp.search", {"query": "hotels"}, "sess-1")

        assert result.success is True
        mock_call.assert_called_once_with("search", {"query": "hotels"})

    @pytest.mark.asyncio
    async def test_mcp_error_returns_failure(self, mcp_tool_config):
        mock_tools = [
            _mock_mcp_tool("search", "Search", {"type": "object", "properties": {}}),
        ]

        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_tools
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("MCP connection lost")
            result = await adapter.execute("test_mcp.search", {"query": "hotels"}, "sess-1")

        assert result.success is False
        assert "mcp_error" in result.error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_failure(self, mcp_tool_config):
        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()

        result = await adapter.execute("test_mcp.nonexistent", {}, "sess-1")
        assert result.success is False
        assert "unknown_tool" in result.error


class TestMcpAdapterHealthCheck:
    """Health check for MCP connection."""

    @pytest.mark.asyncio
    async def test_health_check_when_connected(self, mcp_tool_config):
        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()
            adapter._connected = True

        assert adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_when_disconnected(self, mcp_tool_config):
        with patch.object(McpAdapter, "_connect_and_discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            adapter = McpAdapter(mcp_tool_config)
            await adapter.initialize()
            adapter._connected = False

        assert adapter.health_check() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd action_gateway && uv run pytest tests/test_mcp_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.adapters.mcp'`

- [ ] **Step 3: Add `mcp` SDK dependency**

Edit `action_gateway/pyproject.toml`: add `"mcp>=1.0"` to `dependencies`.

- [ ] **Step 4: Implement McpAdapter**

```python
# action_gateway/src/adapters/mcp.py
"""MCP adapter for the Action Gateway.

One instance per MCP server. Connects at startup via tools/list to discover
available tools. Maintains connection for runtime tool execution.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE_CHARS = 4000


class McpAdapter(ToolAdapter):
    """Adapter for MCP server tool execution.

    Args:
        config: Tool config dict from the tools[] array in action_gateway.yaml.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._server_url: str = config["server_url"]
        self._transport: str = config.get("transport", "sse")
        self._namespace: str = config.get("namespace", config["id"])
        self._category: str = config["category"]
        self._max_size_chars: int = config.get("response", {}).get(
            "max_size_chars", _DEFAULT_MAX_SIZE_CHARS
        )
        self._tool_definitions: list[ToolDefinition] = []
        self._known_tools: set[str] = set()  # MCP tool names (without namespace)
        self._connected: bool = False
        self._session: Any = None
        self._client: Any = None

    async def initialize(self) -> None:
        """Connect to MCP server and discover tools.

        Must be called after __init__ and before get_tool_definitions() or execute().

        Raises:
            Exception: If MCP server is unreachable or tools/list fails.
        """
        try:
            tools = await self._connect_and_discover()
            self._tool_definitions = []
            self._known_tools = set()

            for tool in tools:
                namespaced_name = f"{self._namespace}.{tool.name}"
                self._known_tools.add(tool.name)
                self._tool_definitions.append(
                    ToolDefinition(
                        name=namespaced_name,
                        description=tool.description or f"MCP tool: {tool.name}",
                        input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        category=self._category,
                    )
                )
            self._connected = True
            logger.info(
                "mcp.initialize",
                extra={
                    "operation": f"mcp.init.{self._namespace}",
                    "status": "success",
                    "tools_discovered": len(self._tool_definitions),
                    "server_url": self._server_url,
                },
            )
        except Exception as e:
            self._connected = False
            logger.error(
                "mcp.initialize",
                extra={
                    "operation": f"mcp.init.{self._namespace}",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "server_url": self._server_url,
                },
            )
            raise

    async def _connect_and_discover(self) -> list:
        """Connect to MCP server and call tools/list.

        Returns:
            List of MCP tool objects from the server.
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self._server_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return result.tools

    async def _call_tool(self, tool_name: str, params: dict) -> Any:
        """Call a tool on the MCP server.

        Args:
            tool_name: The MCP tool name (without namespace prefix).
            params: The tool input parameters.

        Returns:
            MCP CallToolResult.
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self._server_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, params)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return all discovered tool definitions with namespace prefix."""
        return list(self._tool_definitions)

    async def execute(self, tool_name: str, params: dict, session_id: str) -> ToolResult:
        """Execute a tool call via MCP.

        Args:
            tool_name: Namespaced tool name (e.g., "travel_mcp.search_hotels").
            params: LLM-provided parameters.
            session_id: Session ID for tracing.

        Returns:
            ToolResult with MCP response or structured error.
        """
        start = time.time()
        tool_use_id = f"exec_{tool_name}_{session_id}"

        # Strip namespace to get MCP tool name
        prefix = f"{self._namespace}."
        if tool_name.startswith(prefix):
            mcp_tool_name = tool_name[len(prefix):]
        else:
            mcp_tool_name = tool_name

        if mcp_tool_name not in self._known_tools:
            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"unknown_tool: {tool_name}",
            )

        try:
            call_result = await self._call_tool(mcp_tool_name, params)
            latency_ms = int((time.time() - start) * 1000)

            if hasattr(call_result, "isError") and call_result.isError:
                logger.error(
                    "mcp.execute",
                    extra={
                        "operation": f"mcp.execute.{tool_name}",
                        "status": "failure",
                        "error": "mcp_tool_error",
                        "latency_ms": latency_ms,
                    },
                )
                return ToolResult(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    result={},
                    success=False,
                    error=f"mcp_error: tool returned error",
                )

            # Extract text content from MCP response
            result_data = self._extract_result(call_result)

            logger.info(
                "mcp.execute",
                extra={
                    "operation": f"mcp.execute.{tool_name}",
                    "status": "success",
                    "latency_ms": latency_ms,
                },
            )

            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result=result_data,
                success=True,
            )

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            self._connected = False
            logger.error(
                "mcp.execute",
                extra={
                    "operation": f"mcp.execute.{tool_name}",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"mcp_error: {type(e).__name__}: {e}",
            )

    def health_check(self) -> bool:
        """Check if the MCP connection is active."""
        return self._connected

    def _extract_result(self, call_result: Any) -> dict:
        """Extract result dict from MCP CallToolResult."""
        if not hasattr(call_result, "content") or not call_result.content:
            return {}

        texts = []
        for block in call_result.content:
            if hasattr(block, "text"):
                texts.append(block.text)

        combined = "\n".join(texts)

        # Try parsing as JSON
        try:
            data = json.loads(combined)
            if isinstance(data, dict):
                return data
            return {"data": data}
        except (json.JSONDecodeError, ValueError):
            return {"text": combined[: self._max_size_chars]}
```

- [ ] **Step 5: Update `action_gateway/src/adapters/__init__.py`**

```python
# action_gateway/src/adapters/__init__.py
from src.adapters.base import ToolAdapter
from src.adapters.mcp import McpAdapter
from src.adapters.rest_api import RestApiAdapter

__all__ = ["ToolAdapter", "RestApiAdapter", "McpAdapter"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd action_gateway && uv run pytest tests/test_mcp_adapter.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add action_gateway/src/adapters/mcp.py action_gateway/src/adapters/__init__.py action_gateway/tests/test_mcp_adapter.py action_gateway/pyproject.toml
git commit -m "feat(action-gateway): add McpAdapter

MCP server tool discovery via tools/list, namespaced tool definitions,
and async tool execution with structured error handling."
```

---

### Task 5: AdapterRegistry + AdapterFactory

**Files:**
- Create: `action_gateway/src/registry/adapter_registry.py`
- Create: `action_gateway/src/registry/adapter_factory.py`
- Test: `action_gateway/tests/test_adapter_registry.py`
- Test: `action_gateway/tests/test_adapter_factory.py`

- [ ] **Step 1: Write tests for AdapterRegistry**

```python
# action_gateway/tests/test_adapter_registry.py
"""Tests for AdapterRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models import ToolDefinition
from src.registry.adapter_registry import AdapterRegistry


def _mock_adapter(tool_defs: list[ToolDefinition]) -> MagicMock:
    adapter = MagicMock()
    adapter.get_tool_definitions.return_value = tool_defs
    return adapter


class TestAdapterRegistry:
    """Registration, resolution, and tool definition caching."""

    def test_register_and_resolve(self):
        registry = AdapterRegistry()
        adapter = _mock_adapter([])
        registry.register("tool_a", adapter)
        assert registry.resolve("tool_a") is adapter

    def test_resolve_unknown_raises(self):
        registry = AdapterRegistry()
        with pytest.raises(KeyError, match="tool_x"):
            registry.resolve("tool_x")

    def test_multiple_names_same_adapter(self):
        registry = AdapterRegistry()
        adapter = _mock_adapter([])
        registry.register("mcp.tool1", adapter)
        registry.register("mcp.tool2", adapter)
        assert registry.resolve("mcp.tool1") is registry.resolve("mcp.tool2")

    def test_get_all_tool_definitions(self):
        registry = AdapterRegistry()
        td1 = ToolDefinition(name="a", description="Tool A", input_schema={}, category="read")
        td2 = ToolDefinition(name="b", description="Tool B", input_schema={}, category="write")
        registry.register("a", _mock_adapter([td1]))
        registry.register("b", _mock_adapter([td2]))
        all_defs = registry.get_all_tool_definitions()
        names = {d.name for d in all_defs}
        assert names == {"a", "b"}

    def test_get_all_definitions_deduplicates_mcp(self):
        """MCP adapter returns multiple tools; they should appear once each."""
        registry = AdapterRegistry()
        td1 = ToolDefinition(name="mcp.a", description="A", input_schema={}, category="read")
        td2 = ToolDefinition(name="mcp.b", description="B", input_schema={}, category="read")
        adapter = _mock_adapter([td1, td2])
        registry.register("mcp.a", adapter)
        registry.register("mcp.b", adapter)
        all_defs = registry.get_all_tool_definitions()
        assert len(all_defs) == 2

    def test_duplicate_name_overwrites(self):
        registry = AdapterRegistry()
        adapter1 = _mock_adapter([])
        adapter2 = _mock_adapter([])
        registry.register("tool", adapter1)
        registry.register("tool", adapter2)
        assert registry.resolve("tool") is adapter2

    def test_get_tool_names(self):
        registry = AdapterRegistry()
        registry.register("a", _mock_adapter([]))
        registry.register("b", _mock_adapter([]))
        assert registry.get_tool_names() == {"a", "b"}

    def test_empty_registry(self):
        registry = AdapterRegistry()
        assert registry.get_all_tool_definitions() == []
        assert registry.get_tool_names() == set()
```

- [ ] **Step 2: Write tests for AdapterFactory**

```python
# action_gateway/tests/test_adapter_factory.py
"""Tests for AdapterFactory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.registry.adapter_factory import AdapterFactory, ADAPTER_TYPES


class TestAdapterFactory:
    """Factory builds registry from config."""

    @pytest.mark.asyncio
    async def test_builds_rest_adapter(self, rest_tool_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        config = {"tools": [rest_tool_config]}
        registry = await AdapterFactory.build_registry(config)
        assert "test_weather" in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_builds_multiple_rest_adapters(self, rest_tool_config, rest_no_auth_config, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "sk-test")
        config = {"tools": [rest_tool_config, rest_no_auth_config]}
        registry = await AdapterFactory.build_registry(config)
        assert "test_weather" in registry.get_tool_names()
        assert "test_public" in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_missing_env_var_skips_adapter(self, rest_tool_config):
        """Adapter with missing secret is skipped, not fatal."""
        with patch.dict("os.environ", {}, clear=True):
            config = {"tools": [rest_tool_config]}
            registry = await AdapterFactory.build_registry(config)
        assert "test_weather" not in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_unknown_type_skips_adapter(self):
        config = {"tools": [{"id": "bad", "type": "unknown_adapter", "category": "read", "description": "Bad"}]}
        registry = await AdapterFactory.build_registry(config)
        assert registry.get_tool_names() == set()

    @pytest.mark.asyncio
    async def test_empty_tools_list(self):
        config = {"tools": []}
        registry = await AdapterFactory.build_registry(config)
        assert registry.get_tool_names() == set()

    @pytest.mark.asyncio
    async def test_missing_tools_key(self):
        config = {}
        registry = await AdapterFactory.build_registry(config)
        assert registry.get_tool_names() == set()

    def test_adapter_types_contains_rest_api(self):
        assert "rest_api" in ADAPTER_TYPES

    def test_adapter_types_contains_mcp(self):
        assert "mcp" in ADAPTER_TYPES
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd action_gateway && uv run pytest tests/test_adapter_registry.py tests/test_adapter_factory.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement AdapterRegistry**

```python
# action_gateway/src/registry/adapter_registry.py
"""Adapter registry — maps tool names to adapter instances.

Built once at startup by AdapterFactory. Immutable after construction.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Maps tool names to ToolAdapter instances for routing.

    Multiple tool names can map to the same adapter instance (MCP case).
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ToolAdapter] = {}
        self._definitions_cache: list[ToolDefinition] | None = None

    def register(self, tool_name: str, adapter: ToolAdapter) -> None:
        """Register a tool name to an adapter instance.

        Args:
            tool_name: The tool name (as the LLM will see it).
            adapter: The adapter instance that handles this tool.
        """
        self._adapters[tool_name] = adapter
        self._definitions_cache = None  # invalidate cache

    def resolve(self, tool_name: str) -> ToolAdapter:
        """Look up the adapter for a tool name.

        Args:
            tool_name: The tool name to resolve.

        Returns:
            The ToolAdapter instance registered for this name.

        Raises:
            KeyError: If tool_name is not registered.
        """
        if tool_name not in self._adapters:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._adapters[tool_name]

    def get_all_tool_definitions(self) -> list[ToolDefinition]:
        """Return all tool definitions, deduplicated.

        Returns:
            List of ToolDefinition for all registered tools.
        """
        if self._definitions_cache is not None:
            return self._definitions_cache

        seen_adapters: set[int] = set()
        definitions: list[ToolDefinition] = []

        for adapter in self._adapters.values():
            adapter_id = id(adapter)
            if adapter_id in seen_adapters:
                continue
            seen_adapters.add(adapter_id)
            definitions.extend(adapter.get_tool_definitions())

        self._definitions_cache = definitions
        return definitions

    def get_tool_names(self) -> set[str]:
        """Return all registered tool names.

        Returns:
            Set of tool name strings.
        """
        return set(self._adapters.keys())
```

- [ ] **Step 5: Implement AdapterFactory**

```python
# action_gateway/src/registry/adapter_factory.py
"""Adapter factory — builds AdapterRegistry from YAML config.

Maps the 'type' field in each tool config to the correct ToolAdapter
subclass and instantiates it.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.mcp import McpAdapter
from src.adapters.rest_api import RestApiAdapter
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)

ADAPTER_TYPES: dict[str, type] = {
    "rest_api": RestApiAdapter,
    "mcp": McpAdapter,
}


class AdapterFactory:
    """Builds an AdapterRegistry from tool configuration.

    Iterates through tools[], instantiates the correct adapter per type,
    and registers all tool names. Adapters that fail init are skipped
    (logged, not fatal).
    """

    @staticmethod
    async def build_registry(config: dict) -> AdapterRegistry:
        """Build an AdapterRegistry from the tools config.

        Args:
            config: The full action_gateway config dict with a 'tools' key.

        Returns:
            Populated AdapterRegistry ready for runtime use.
        """
        registry = AdapterRegistry()
        tools = config.get("tools", [])

        for tool_config in tools:
            tool_id = tool_config.get("id", "<unknown>")
            tool_type = tool_config.get("type", "")

            if tool_type not in ADAPTER_TYPES:
                logger.error(
                    "adapter_factory.unknown_type",
                    extra={
                        "operation": f"adapter_factory.build.{tool_id}",
                        "status": "skipped",
                        "error": f"Unknown adapter type: {tool_type}",
                    },
                )
                continue

            try:
                adapter_class = ADAPTER_TYPES[tool_type]
                adapter = adapter_class(tool_config)

                # MCP adapters need async initialization
                if isinstance(adapter, McpAdapter):
                    await adapter.initialize()

                # Register all tool names from this adapter
                for td in adapter.get_tool_definitions():
                    registry.register(td.name, adapter)

                logger.info(
                    "adapter_factory.registered",
                    extra={
                        "operation": f"adapter_factory.build.{tool_id}",
                        "status": "success",
                        "adapter_type": tool_type,
                        "tools_registered": len(adapter.get_tool_definitions()),
                    },
                )

            except Exception as e:
                logger.error(
                    "adapter_factory.init_failed",
                    extra={
                        "operation": f"adapter_factory.build.{tool_id}",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                continue

        return registry
```

- [ ] **Step 6: Update `action_gateway/src/registry/__init__.py`**

```python
# action_gateway/src/registry/__init__.py
from src.registry.adapter_factory import ADAPTER_TYPES, AdapterFactory
from src.registry.adapter_registry import AdapterRegistry

__all__ = ["AdapterRegistry", "AdapterFactory", "ADAPTER_TYPES"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd action_gateway && uv run pytest tests/test_adapter_registry.py tests/test_adapter_factory.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add action_gateway/src/registry/ action_gateway/tests/test_adapter_registry.py action_gateway/tests/test_adapter_factory.py
git commit -m "feat(action-gateway): add AdapterRegistry and AdapterFactory

Registry maps tool names to adapter instances with O(1) lookup.
Factory builds registry from YAML config, skipping failed adapters gracefully."
```

---

### Task 6: FastAPI Server (`action_gateway/src/server.py`)

**Files:**
- Create: `action_gateway/src/server.py`
- Test: `action_gateway/tests/test_server.py`

- [ ] **Step 1: Write tests for the server endpoints**

```python
# action_gateway/tests/test_server.py
"""Tests for Action Gateway FastAPI server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.models import ToolDefinition, ToolResult
from src.registry.adapter_registry import AdapterRegistry
from src.server import create_app


def _build_test_app() -> tuple[TestClient, AdapterRegistry]:
    """Create a test app with a mocked registry."""
    registry = AdapterRegistry()

    td = ToolDefinition(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        category="read",
    )
    adapter = MagicMock()
    adapter.get_tool_definitions.return_value = [td]
    adapter.health_check.return_value = True
    adapter.execute = AsyncMock(
        return_value=ToolResult(
            tool_use_id="toolu_01",
            tool_name="test_tool",
            result={"answer": 42},
            success=True,
        )
    )
    registry.register("test_tool", adapter)

    app = create_app(registry)
    return TestClient(app), registry


class TestGetTools:
    """GET /tools endpoint."""

    def test_returns_all_tool_definitions(self):
        client, _ = _build_test_app()
        resp = client.get("/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "test_tool"
        assert data["tools"][0]["category"] == "read"

    def test_empty_registry(self):
        registry = AdapterRegistry()
        app = create_app(registry)
        client = TestClient(app)
        resp = client.get("/tools")
        assert resp.status_code == 200
        assert resp.json()["tools"] == []


class TestPostExecute:
    """POST /execute endpoint."""

    def test_successful_execution(self):
        client, _ = _build_test_app()
        resp = client.post(
            "/execute",
            json={
                "tool_name": "test_tool",
                "tool_use_id": "toolu_01",
                "input_params": {"q": "test"},
                "session_id": "sess-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["answer"] == 42

    def test_unknown_tool(self):
        client, _ = _build_test_app()
        resp = client.post(
            "/execute",
            json={
                "tool_name": "nonexistent",
                "tool_use_id": "toolu_02",
                "input_params": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "unknown_tool" in data["error"]

    def test_adapter_failure(self):
        registry = AdapterRegistry()
        adapter = MagicMock()
        adapter.get_tool_definitions.return_value = [
            ToolDefinition(name="fail_tool", description="Fails", input_schema={}, category="read")
        ]
        adapter.execute = AsyncMock(
            return_value=ToolResult(
                tool_use_id="toolu_03",
                tool_name="fail_tool",
                result={},
                success=False,
                error="adapter_timeout: fail_tool",
            )
        )
        registry.register("fail_tool", adapter)

        app = create_app(registry)
        client = TestClient(app)
        resp = client.post(
            "/execute",
            json={"tool_name": "fail_tool", "tool_use_id": "toolu_03", "input_params": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "timeout" in resp.json()["error"]

    def test_optional_session_id(self):
        client, _ = _build_test_app()
        resp = client.post(
            "/execute",
            json={"tool_name": "test_tool", "tool_use_id": "toolu_04", "input_params": {}},
        )
        assert resp.status_code == 200


class TestGetHealth:
    """GET /health endpoint."""

    def test_healthy_adapters(self):
        client, _ = _build_test_app()
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["adapters"]["test_tool"] is True

    def test_unhealthy_adapter(self):
        registry = AdapterRegistry()
        adapter = MagicMock()
        adapter.get_tool_definitions.return_value = [
            ToolDefinition(name="sick", description="Sick tool", input_schema={}, category="read")
        ]
        adapter.health_check.return_value = False
        registry.register("sick", adapter)

        app = create_app(registry)
        client = TestClient(app)
        resp = client.get("/health")
        data = resp.json()
        assert data["adapters"]["sick"] is False

    def test_empty_registry_healthy(self):
        registry = AdapterRegistry()
        app = create_app(registry)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd action_gateway && uv run pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.server'`

- [ ] **Step 3: Implement the server**

```python
# action_gateway/src/server.py
"""FastAPI server for the Action Gateway.

Exposes GET /tools, POST /execute, and GET /health.
The server is created via create_app() which receives a populated
AdapterRegistry — this allows main.py to build config and registry
before the server starts.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI

from src.models import (
    ExecuteRequest,
    ExecuteResponse,
    HealthResponse,
    ToolResult,
    ToolsResponse,
)
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)


def create_app(registry: AdapterRegistry) -> FastAPI:
    """Create the FastAPI application with a populated registry.

    Args:
        registry: The AdapterRegistry built at startup by AdapterFactory.

    Returns:
        Configured FastAPI app instance.
    """
    app = FastAPI(
        title="Action Gateway",
        description="Generic adapter framework for external tool execution.",
        version="1.0.0",
    )

    @app.get("/tools", response_model=ToolsResponse)
    async def get_tools() -> ToolsResponse:
        """Return all registered tool definitions."""
        return ToolsResponse(tools=registry.get_all_tool_definitions())

    @app.post("/execute", response_model=ExecuteResponse)
    async def execute_tool(request: ExecuteRequest) -> ExecuteResponse:
        """Execute a tool call via the appropriate adapter."""
        start = time.time()

        try:
            adapter = registry.resolve(request.tool_name)
        except KeyError:
            logger.warning(
                "server.execute.unknown_tool",
                extra={
                    "operation": "server.execute",
                    "status": "failure",
                    "error": f"unknown_tool: {request.tool_name}",
                },
            )
            return ExecuteResponse(
                tool_use_id=request.tool_use_id,
                tool_name=request.tool_name,
                success=False,
                result={},
                error=f"unknown_tool: {request.tool_name}",
            )

        result: ToolResult = await adapter.execute(
            request.tool_name, request.input_params, request.session_id
        )

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "server.execute",
            extra={
                "operation": "server.execute",
                "status": "success" if result.success else "failure",
                "tool_name": request.tool_name,
                "latency_ms": latency_ms,
            },
        )

        return ExecuteResponse(
            tool_use_id=result.tool_use_id,
            tool_name=result.tool_name,
            success=result.success,
            result=result.result,
            result_text=result.result_text,
            error=result.error,
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check with per-adapter status."""
        adapter_status: dict[str, bool] = {}
        seen: set[int] = set()
        for name, adapter in registry._adapters.items():
            aid = id(adapter)
            if aid not in seen:
                seen.add(aid)
                status = adapter.health_check()
            else:
                # Same adapter instance (MCP case) — reuse status
                for prev_name, prev_adapter in registry._adapters.items():
                    if id(prev_adapter) == aid and prev_name in adapter_status:
                        status = adapter_status[prev_name]
                        break
            adapter_status[name] = status

        return HealthResponse(status="healthy", adapters=adapter_status)

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd action_gateway && uv run pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add action_gateway/src/server.py action_gateway/tests/test_server.py
git commit -m "feat(action-gateway): add FastAPI server with GET /tools, POST /execute, GET /health

Server receives a pre-built AdapterRegistry and routes tool calls to
the correct adapter. Unknown tools return structured errors."
```

---

### Task 7: Update main.py + Delete Mock Code

**Files:**
- Modify: `action_gateway/main.py`
- Delete: `action_gateway/src/mock_gateway.py`
- Delete: `action_gateway/src/mock_server.py`
- Delete: `action_gateway/tests/test_mock_gateway.py`
- Delete: `action_gateway/tests/test_mock_server.py`
- Rewrite: `action_gateway/tests/test_main.py`

- [ ] **Step 1: Write tests for updated main.py**

```python
# action_gateway/tests/test_main.py
"""Tests for Action Gateway entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from main import _build_config, _deep_merge, _domain_config_path, _load_config


class TestLoadConfig:
    """Config file loading."""

    def test_load_valid_yaml(self, tmp_path):
        cfg = tmp_path / "test.yaml"
        cfg.write_text("server:\n  port: 9999\n")
        result = _load_config(str(cfg))
        assert result["server"]["port"] == 9999

    def test_load_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            _load_config("/nonexistent/config.yaml")

    def test_load_empty_yaml(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        result = _load_config(str(cfg))
        assert result == {}


class TestDeepMerge:
    """Config deep merge."""

    def test_override_scalar(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_merge_nested_dicts(self):
        base = {"server": {"host": "0.0.0.0", "port": 8000}}
        override = {"server": {"port": 9999}}
        result = _deep_merge(base, override)
        assert result["server"]["host"] == "0.0.0.0"
        assert result["server"]["port"] == 9999

    def test_add_new_key(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


class TestDomainConfigPath:
    """Domain config path resolution."""

    def test_default_path_no_env(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        path = _domain_config_path("action_gateway")
        assert path == Path("config/domain.yaml")

    def test_config_folder_env(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "action_gateway.yaml"
        cfg_file.write_text("tools: []\n")
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        path = _domain_config_path("action_gateway")
        assert path == cfg_file
```

- [ ] **Step 2: Rewrite main.py**

Replace the `from src.mock_server import app` import and the `__main__` block. The config loading helpers (`_load_config`, `_deep_merge`, `_domain_config_path`, `_build_config`) stay as-is. Changes:

In `action_gateway/main.py`:
- Remove: `from src.mock_server import app  # noqa: F401`
- Update `__main__` block to:
  1. Build config
  2. Init OTel
  3. Build AdapterRegistry via AdapterFactory
  4. Create app via `create_app(registry)`
  5. Run uvicorn with the app object

```python
# Updated action_gateway/main.py — replace lines 33 and 120-136

# Line 33: Remove mock_server import, add new imports
# (keep all existing imports above line 33)

import asyncio

from src.registry.adapter_factory import AdapterFactory
from src.server import create_app

# ... (keep all existing helper functions _load_config, _deep_merge, etc.)

if __name__ == "__main__":
    config, host, port = _build_config()

    init_otel(service_name="action_gateway", config=config)

    # Build adapter registry from config (async — MCP adapters need it)
    registry = asyncio.run(AdapterFactory.build_registry(config))

    app = create_app(registry)

    logger.info(
        "action_gateway.startup",
        extra={
            "operation": "main",
            "status": "success",
            "host": host,
            "port": port,
            "tools_registered": len(registry.get_tool_names()),
        },
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
```

- [ ] **Step 3: Delete mock files**

```bash
rm action_gateway/src/mock_gateway.py
rm action_gateway/src/mock_server.py
rm action_gateway/tests/test_mock_gateway.py
rm action_gateway/tests/test_mock_server.py
```

- [ ] **Step 4: Run all tests**

Run: `cd action_gateway && uv run pytest tests/ -v`
Expected: All PASS (test_main.py, test_models.py, test_rest_api_adapter.py, test_mcp_adapter.py, test_adapter_registry.py, test_adapter_factory.py, test_server.py)

- [ ] **Step 5: Commit**

```bash
git add -A action_gateway/
git commit -m "feat(action-gateway): wire up main.py, delete PoC mock code

main.py now builds AdapterRegistry from YAML config at startup and
creates FastAPI app via create_app(registry). Removes mock_gateway.py,
mock_server.py and their tests."
```

---

### Task 8: Config Files (KKB Domain + DPG Defaults)

**Files:**
- Modify: `dev-kit/configs/kkb/action_gateway.yaml`
- Modify: `dev-kit/dpg/action_gateway.yaml`
- Modify: `dev-kit/configs/kkb/agent_core.yaml`
- Modify: `dev-kit/dpg/agent_core.yaml`
- Modify: `action_gateway/config/dpg.yaml`
- Modify: `action_gateway/config/domain.yaml`

- [ ] **Step 1: Rewrite KKB action_gateway.yaml to new tools schema**

```yaml
# dev-kit/configs/kkb/action_gateway.yaml
# KKB domain values for Action Gateway.
# Merged with dev-kit/dpg/action_gateway.yaml by loader.py before deployment.

tools:
  - id: onest_market_lookup
    type: rest_api
    category: read
    description: "Search ONEST live job market data by trade and location. Returns salary range, market signal, and top employers currently hiring for the given trade in the specified area."
    base_url: "http://onest-api:8080/v1"
    auth:
      type: api_key
      header: X-API-Key
      secret_env: ONEST_API_KEY
    timeout_ms: 5000
    endpoints:
      - name: search_jobs
        method: GET
        path: /jobs/search
        params:
          - name: trade
            source: agent
            type: string
            required: true
            description: "Trade or skill to search for"
          - name: location
            source: agent
            type: string
            description: "City or district name"
          - name: distance_km
            source: agent
            type: integer
            description: "Search radius in km from the specified location. Default 50."
            default: 50
    response:
      max_size_chars: 4000

  - id: onest_apply
    type: rest_api
    category: write
    description: "Submit a job application via ONEST on behalf of the user. Call only after user explicitly confirms they want to apply. Returns reference number and confirmation."
    base_url: "http://onest-api:8080/v1"
    auth:
      type: api_key
      header: X-API-Key
      secret_env: ONEST_API_KEY
    timeout_ms: 5000
    endpoints:
      - name: submit_application
        method: POST
        path: /applications
        params:
          - name: trade
            source: agent
            type: string
            required: true
            description: "The trade or role the user is applying for"
          - name: employer
            source: agent
            type: string
            required: true
            description: "Name of the employer"
          - name: location
            source: agent
            type: string
            description: "City or district of the job"
          - name: applicant_name
            source: agent
            type: string
            description: "Name of the applicant from user profile"

  - id: counsellor_schedule
    type: rest_api
    category: write
    description: "Schedule a counsellor callback for the user. Returns a reference number and expected callback time."
    base_url: "http://counsellor-api:8080/v1"
    auth:
      type: bearer
      secret_env: COUNSELLOR_API_TOKEN
    timeout_ms: 3000
    endpoints:
      - name: schedule_callback
        method: POST
        path: /callbacks
        params:
          - name: session_id
            source: agent
            type: string
            required: true
            description: "Session identifier"
          - name: user_name
            source: agent
            type: string
            description: "User's name if known"
          - name: callback_within_hours
            source: agent
            type: integer
            description: "Requested callback window in hours. Default 24."
            default: 24

observability:
  domain: "kkb"
```

- [ ] **Step 2: Update DPG default action_gateway.yaml**

```yaml
# dev-kit/dpg/action_gateway.yaml
# Framework defaults for Action Gateway.
# Domain config overrides these values.

server:
  host: "0.0.0.0"
  port: 9999

tools: []

action_gateway:
  timeout_ms: 5000

response:
  default_max_size_chars: 4000

observability:
  otel:
    collector_endpoint: "http://otelcol:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
```

- [ ] **Step 3: Update action_gateway/config/dpg.yaml and domain.yaml**

`action_gateway/config/dpg.yaml`:
```yaml
server:
  host: 0.0.0.0
  port: 9999

tools: []

action_gateway:
  timeout_ms: 5000

response:
  default_max_size_chars: 4000

observability:
  otel:
    collector_endpoint: "http://localhost:4317"
    sample_rate: 1.0
    export_interval_ms: 5000
```

`action_gateway/config/domain.yaml`:
```yaml
tools: []

observability:
  domain: ""
```

- [ ] **Step 4: Remove external connectors from KKB agent_core.yaml**

In `dev-kit/configs/kkb/agent_core.yaml`, remove the `connectors.read`, `connectors.write`, and `connectors.identity` sections. Keep only `connectors.internal`.

Before (lines 31-104):
```yaml
connectors:
  read:
    - name: onest_market_lookup
      ...
  write:
    - name: onest_apply
      ...
    - name: counsellor_schedule
      ...
  identity: []
  internal:
    - name: knowledge_retrieval
      ...
```

After:
```yaml
connectors:
  internal:
    - name: knowledge_retrieval
      route: knowledge_engine
      description: >
        Search the verified Kaam Ki Baat knowledge base for information on job trades, 
        government schemes, and market trends. Use this when the user asks a specific 
        question that requires looking up facts or scheme details.
      input_schema:
        type: object
        properties:
          query:
            type: string
            description: "The search query in plain language"
          trade:
            type: string
            description: "Optional trade/skill to narrow the search"
        required:
          - query
```

- [ ] **Step 5: Update DPG default agent_core.yaml — change endpoint to base URL**

In `dev-kit/dpg/agent_core.yaml`, change line 40:
```yaml
# Before
action_gateway_client:
  endpoint: "http://action_gateway:9999/execute"
  timeout_ms: 5000

# After
action_gateway_client:
  endpoint: "http://action_gateway:9999"
  timeout_ms: 5000
```

- [ ] **Step 6: Commit**

```bash
git add dev-kit/configs/kkb/action_gateway.yaml dev-kit/dpg/action_gateway.yaml dev-kit/configs/kkb/agent_core.yaml dev-kit/dpg/agent_core.yaml action_gateway/config/dpg.yaml action_gateway/config/domain.yaml
git commit -m "feat(config): migrate to new Action Gateway tools schema

KKB connectors moved from agent_core.yaml to action_gateway.yaml using
new tools[] format with type, category, auth, endpoints, params.
Agent Core config retains only connectors.internal."
```

---

### Task 9: Agent Core Integration

**Files:**
- Modify: `agent_core/src/http_clients/action_gateway.py`
- Modify: `agent_core/src/tool_registry.py`

- [ ] **Step 1: Read current Agent Core files to confirm exact line numbers**

Run: Read `agent_core/src/http_clients/action_gateway.py` and `agent_core/src/tool_registry.py` to verify line numbers before editing.

- [ ] **Step 2: Update ActionGatewayHttpClient to fetch tool definitions from gateway**

Replace the `_build_tool_definitions()` helper and the `__init__` logic that builds tool defs from config. New behavior: call `GET /tools` on Action Gateway at startup.

In `agent_core/src/http_clients/action_gateway.py`:

Replace the module-level `_build_tool_definitions()` function (lines 21-53) and the `__init__` method (lines 62-82) with:

```python
class ActionGatewayHttpClient(ActionGatewayBase):
    """HTTP client for the Action Gateway service.

    Fetches tool definitions from GET /tools at startup.
    Routes tool calls via POST /execute.
    """

    def __init__(self, config: dict) -> None:
        """Initialize the client and fetch tool definitions from gateway.

        Args:
            config: Agent Core config dict with action_gateway_client section.

        Raises:
            ConnectionError: If the gateway is unreachable at startup.
        """
        gw_config = config.get("action_gateway_client", {})
        self._base_url = gw_config.get("endpoint", "http://action_gateway:9999").rstrip("/")
        self._timeout_ms = gw_config.get("timeout_ms", 5000)
        self._timeout_s = self._timeout_ms / 1000.0

        # Fetch tool definitions from gateway
        self._tool_definitions = self._fetch_tool_definitions()

    def _fetch_tool_definitions(self) -> list[dict]:
        """Fetch tool definitions from GET /tools on Action Gateway.

        Returns:
            List of tool definition dicts (name, description, input_schema, category).
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/tools",
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            tools = data.get("tools", [])
            logger.info(
                "action_gateway_client.fetch_tools",
                extra={
                    "operation": "fetch_tool_definitions",
                    "status": "success",
                    "tools_count": len(tools),
                },
            )
            return tools
        except Exception as e:
            logger.error(
                "action_gateway_client.fetch_tools",
                extra={
                    "operation": "fetch_tool_definitions",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return []

    def list_available_tools(self) -> list[dict]:
        """Return cached tool definitions fetched from gateway at startup."""
        return self._tool_definitions

    def execute(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """Execute a tool call via POST /execute on Action Gateway.

        Args:
            tool_call: The tool call from the LLM.
            session_id: Session identifier for tracing.

        Returns:
            ToolResult with the execution outcome.
        """
        try:
            resp = httpx.post(
                f"{self._base_url}/execute",
                json={
                    "tool_name": tool_call.tool_name,
                    "tool_use_id": tool_call.tool_use_id,
                    "input_params": tool_call.input_params,
                    "session_id": session_id,
                },
                timeout=self._timeout_s,
            )
            data = resp.json()
            return ToolResult(
                tool_use_id=data.get("tool_use_id", tool_call.tool_use_id),
                tool_name=data.get("tool_name", tool_call.tool_name),
                result=data.get("result", {}),
                success=data.get("success", False),
                result_text=data.get("result_text", ""),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            logger.error(
                "action_gateway_client.execute",
                extra={
                    "operation": f"execute.{tool_call.tool_name}",
                    "status": "failure",
                    "error": "timeout",
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"gateway_timeout: {tool_call.tool_name}",
            )
        except Exception as e:
            logger.error(
                "action_gateway_client.execute",
                extra={
                    "operation": f"execute.{tool_call.tool_name}",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"gateway_error: {type(e).__name__}: {e}",
            )
```

- [ ] **Step 3: Update ToolRegistry to build consent set from category field**

In `agent_core/src/tool_registry.py`, update `_build_consent_set()` (lines 80-88). Currently it reads tool names from `connectors.write` and `connectors.identity` config lists. New behavior: iterate through gateway tool definitions and check the `category` field.

Replace `_build_consent_set`:
```python
    def _build_consent_set(self, config: dict, gateway_tools: list[dict]) -> set[str]:
        """Build the set of tool names that require consent.

        Args:
            config: Agent Core config dict (for legacy fallback).
            gateway_tools: Tool definitions from gateway, each with a 'category' field.

        Returns:
            Set of tool names where category is 'write' or 'identity'.
        """
        consent_tools: set[str] = set()
        for tool in gateway_tools:
            category = tool.get("category", "read")
            if category in _CONSENT_REQUIRED_TYPES:
                consent_tools.add(tool["name"])
        return consent_tools
```

Update `__init__` to pass gateway tools to `_build_consent_set`:
```python
    def __init__(self, config: dict, gateway: ActionGatewayBase) -> None:
        gateway_tools = gateway.list_available_tools()
        self._consent_required = self._build_consent_set(config, gateway_tools)

        # External tool definitions from gateway
        self._external_tools = gateway_tools

        # Internal tool definitions from config
        internal_tools, routes = self._load_internal_tools(config)
        self._internal_tools = internal_tools
        self._routes = routes

        # Combined definitions
        self._all_tools = self._external_tools + self._internal_tools
        self._tool_names = {t["name"] for t in self._all_tools}
```

- [ ] **Step 4: Run Agent Core tests to verify nothing breaks**

Run: `cd agent_core && uv run pytest tests/ -v --tb=short`
Expected: All existing tests pass (may need minor fixture updates for the new `_build_consent_set` signature).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/http_clients/action_gateway.py agent_core/src/tool_registry.py
git commit -m "feat(agent-core): fetch tool definitions from Action Gateway at startup

ActionGatewayHttpClient calls GET /tools instead of building defs from config.
ToolRegistry builds consent set from category field on each tool definition.
No changes to orchestrator or manager agent."
```

---

### Task 10: Documentation + Final Cleanup

**Files:**
- Modify: `ARCHITECTURE.md`
- Rewrite: `action_gateway/README.md`

- [ ] **Step 1: Update ARCHITECTURE.md — Action Gateway section**

Replace the Action Gateway section (lines 228-245 in ARCHITECTURE.md) with production description. Key updates:
- Remove "PoC stub" language
- Document ToolAdapter ABC, RestApiAdapter, McpAdapter
- Document `GET /tools`, `POST /execute`, `GET /health` endpoints
- Update "Available tools" table to reference config-driven tools
- Update key files list
- Note planned #18 exception for caching in Module Interaction Rules
- Update Configuration Architecture to note tool defs live in `action_gateway.yaml`
- Update Implementation Status: Action Gateway moves from 🟡 to ✅

- [ ] **Step 2: Rewrite action_gateway/README.md**

Replace the entire README with production documentation covering:
- What the service does (adapter framework, not mock)
- Folder structure (new layout)
- HTTP API (GET /tools, POST /execute, GET /health with examples)
- YAML config schema (tools[], type, category, auth, endpoints, params)
- Adding new tools (add a YAML block, restart)
- Adding new adapter types (implement ToolAdapter ABC, add to ADAPTER_TYPES)
- Running the service
- Running tests
- Dependencies

- [ ] **Step 3: Run full test suite**

Run: `cd action_gateway && uv run pytest tests/ -v --cov=src --cov-report=term-missing`
Expected: All PASS, ≥70% coverage

- [ ] **Step 4: Commit**

```bash
git add ARCHITECTURE.md action_gateway/README.md
git commit -m "docs: update ARCHITECTURE.md and Action Gateway README for production connector model

Action Gateway section rewritten from PoC stub to production adapter framework.
README documents new YAML schema, adapter types, and HTTP API."
```

---

## Summary

| Task | What | New Files | Tests |
|---|---|---|---|
| 1 | Data models | `models.py` | `test_models.py` |
| 2 | ToolAdapter ABC | `adapters/base.py` | (tested via concrete adapters) |
| 3 | RestApiAdapter | `adapters/rest_api.py` | `test_rest_api_adapter.py` |
| 4 | McpAdapter | `adapters/mcp.py` | `test_mcp_adapter.py` |
| 5 | Registry + Factory | `registry/*.py` | `test_adapter_registry.py`, `test_adapter_factory.py` |
| 6 | FastAPI server | `server.py` | `test_server.py` |
| 7 | main.py + delete mocks | (modify) | `test_main.py` |
| 8 | Config files | (modify 6 files) | — |
| 9 | Agent Core integration | (modify 2 files) | existing tests |
| 10 | Documentation | (modify 2 files) | — |
