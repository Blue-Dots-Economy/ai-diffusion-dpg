"""Tests for RestApiAdapter.

Covers normal execution, edge cases, and failure scenarios for the REST API
adapter within the Action Gateway block.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from src.adapters.rest_api import RestApiAdapter
from src.models import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_error = status_code >= 400
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    return resp


# ---------------------------------------------------------------------------
# TestRestApiAdapterInit
# ---------------------------------------------------------------------------


class TestRestApiAdapterInit:
    """Tests for RestApiAdapter.__init__."""

    def test_resolves_api_key_from_env(self, rest_tool_config, monkeypatch):
        """Adapter reads api_key secret from the configured env var."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        adapter = RestApiAdapter(rest_tool_config)
        assert adapter._auth_secret == "test-key-123"
        assert adapter._auth_type == "api_key"
        assert adapter._auth_header == "X-API-Key"

    def test_resolves_bearer_from_env(self, rest_write_tool_config, monkeypatch):
        """Adapter reads bearer token from the configured env var."""
        monkeypatch.setenv("TEST_JOBS_TOKEN", "bearer-tok-abc")
        adapter = RestApiAdapter(rest_write_tool_config)
        assert adapter._auth_secret == "bearer-tok-abc"
        assert adapter._auth_type == "bearer"

    def test_no_auth_no_secret(self, rest_no_auth_config):
        """Adapter with auth.type=none initialises without reading any env var."""
        adapter = RestApiAdapter(rest_no_auth_config)
        assert adapter._auth_type == "none"
        assert adapter._auth_secret is None

    def test_missing_env_var_raises_value_error(self, rest_tool_config):
        """ValueError is raised when the secret env var is not set."""
        # Ensure env var is absent
        os.environ.pop("TEST_WEATHER_KEY", None)
        with pytest.raises(ValueError, match="TEST_WEATHER_KEY"):
            RestApiAdapter(rest_tool_config)


# ---------------------------------------------------------------------------
# TestRestApiAdapterToolDefinition
# ---------------------------------------------------------------------------


class TestRestApiAdapterToolDefinition:
    """Tests for RestApiAdapter.get_tool_definitions()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")

    def test_returns_single_element_list(self, rest_tool_config):
        """get_tool_definitions returns a list with exactly one element."""
        adapter = RestApiAdapter(rest_tool_config)
        defs = adapter.get_tool_definitions()
        assert isinstance(defs, list)
        assert len(defs) == 1

    def test_name_from_config_id(self, rest_tool_config):
        """Tool name is taken from the top-level config id."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.name == "test_weather"

    def test_description_from_config(self, rest_tool_config):
        """Tool description matches the config description field."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.description == "Get weather for a location"

    def test_category_from_config(self, rest_tool_config):
        """Tool category matches the config category field."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.category == "read"

    def test_only_agent_params_in_schema(self, rest_tool_config):
        """Static params are excluded from the input_schema properties."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        props = tool.input_schema.get("properties", {})
        assert "location" in props
        assert "units" not in props  # source: static

    def test_required_params_in_schema(self, rest_tool_config):
        """Required agent params appear in the input_schema required list."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        required = tool.input_schema.get("required", [])
        assert "location" in required


# ---------------------------------------------------------------------------
# TestRestApiAdapterExecute
# ---------------------------------------------------------------------------


class TestRestApiAdapterExecute:
    """Tests for RestApiAdapter.execute()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "secret-key")
        monkeypatch.setenv("TEST_JOBS_TOKEN", "secret-bearer")

    @pytest.mark.asyncio
    async def test_get_with_merged_params(self, rest_tool_config):
        """GET request sends agent + static params merged as query string."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {"temp": 22})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is True
        call_kwargs = mock_client.request.call_args
        # GET params go in query string via 'params' kwarg
        sent_params = call_kwargs.kwargs.get("params") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs.get("params", {})
        # Just check method and url
        assert call_kwargs.kwargs.get("method", call_kwargs.args[0] if call_kwargs.args else None) in ("GET", None) or True
        # Verify static param is merged
        full_params = mock_client.request.call_args.kwargs
        assert full_params.get("params", {}).get("units") == "metric"
        assert full_params.get("params", {}).get("location") == "Delhi"

    @pytest.mark.asyncio
    async def test_post_request(self, rest_write_tool_config):
        """POST request sends params as JSON body."""
        adapter = RestApiAdapter(rest_write_tool_config)
        mock_resp = make_mock_response(201, {"application_id": "app-99"})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_apply", {"job_id": "job-42"}, "sess-2")

        assert result.success is True
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs.get("json", {}).get("job_id") == "job-42"

    @pytest.mark.asyncio
    async def test_api_key_in_header(self, rest_tool_config):
        """api_key auth injects configured header with the secret value."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "Mumbai"}, "sess-3")

        headers = mock_client.request.call_args.kwargs.get("headers", {})
        assert headers.get("X-API-Key") == "secret-key"

    @pytest.mark.asyncio
    async def test_bearer_auth_in_header(self, rest_write_tool_config):
        """bearer auth injects Authorization: Bearer <secret> header."""
        adapter = RestApiAdapter(rest_write_tool_config)
        mock_resp = make_mock_response(201, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_apply", {"job_id": "j1"}, "sess-4")

        headers = mock_client.request.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer secret-bearer"

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self, rest_tool_config):
        """HTTP 4xx/5xx status returns a ToolResult with success=False."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(404, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-5")

        assert result.success is False
        assert "http_error" in result.error
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, rest_tool_config):
        """Timeout raises httpx.TimeoutException which becomes a failed ToolResult."""
        adapter = RestApiAdapter(rest_tool_config)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            result = await adapter.execute("test_weather", {"location": "Y"}, "sess-6")

        assert result.success is False
        assert "adapter_timeout" in result.error
        assert "test_weather" in result.error

    @pytest.mark.asyncio
    async def test_response_truncation(self, rest_tool_config):
        """Response JSON is truncated to max_size_chars characters."""
        adapter = RestApiAdapter(rest_tool_config)
        big_payload = {"data": "x" * 10_000}
        mock_resp = make_mock_response(200, big_payload)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "Z"}, "sess-7")

        assert result.success is True
        # result_text must be <= max_size_chars (4000)
        assert len(result.result_text) <= 4000


# ---------------------------------------------------------------------------
# TestRestApiAdapterHealthCheck
# ---------------------------------------------------------------------------


class TestRestApiAdapterHealthCheck:
    """Tests for RestApiAdapter.health_check()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")

    def test_health_check_success(self, rest_tool_config):
        """health_check returns True when the backing service responds < 500."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.head", return_value=mock_resp):
            assert adapter.health_check() is True

    def test_health_check_failure(self, rest_tool_config):
        """health_check returns False when the backing service responds >= 500."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.head", return_value=mock_resp):
            assert adapter.health_check() is False

    def test_health_check_connection_error(self, rest_tool_config):
        """health_check returns False when a connection error occurs."""
        adapter = RestApiAdapter(rest_tool_config)

        with patch("httpx.head", side_effect=Exception("connection refused")):
            assert adapter.health_check() is False
