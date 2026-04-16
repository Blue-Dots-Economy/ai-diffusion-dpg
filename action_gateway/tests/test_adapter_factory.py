"""Tests for AdapterFactory.

Covers normal execution, edge cases, and failure scenarios for
AdapterFactory in the Action Gateway block.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ToolDefinition
from src.registry.adapter_factory import ADAPTER_TYPES, AdapterFactory
from src.registry.adapter_registry import AdapterRegistry


def _rest_def(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="A REST tool",
        input_schema={"type": "object", "properties": {}},
        category="read",
    )


class TestAdapterFactoryBuildRegistry:
    @pytest.mark.asyncio
    async def test_builds_rest_adapter(self, monkeypatch, rest_no_auth_config):
        """A valid rest_api config produces a registered adapter."""
        config = {"tools": [rest_no_auth_config]}
        registry = await AdapterFactory.build_registry(config)
        # rest_no_auth_config exposes "test_public" tool
        assert "test_public" in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_builds_multiple_rest_adapters(self, monkeypatch, rest_no_auth_config, rest_write_tool_config):
        config = {"tools": [rest_no_auth_config, rest_write_tool_config]}
        # rest_write_tool_config has a bearer token requirement — skip auth
        # by patching env
        monkeypatch.setenv("TEST_JOBS_TOKEN", "fake-token")
        registry = await AdapterFactory.build_registry(config)
        names = registry.get_tool_names()
        assert "test_public" in names
        assert "test_apply" in names

    @pytest.mark.asyncio
    async def test_missing_env_var_skips_adapter(self, rest_tool_config):
        """An adapter that requires a missing env var is skipped, not fatal."""
        config = {"tools": [rest_tool_config]}
        # TEST_WEATHER_KEY is NOT set — adapter should be skipped
        registry = await AdapterFactory.build_registry(config)
        assert "test_weather" not in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_unknown_type_skips_adapter(self):
        """An adapter with an unknown type is skipped gracefully."""
        config = {"tools": [{"id": "bad", "type": "ftp_adapter", "category": "read"}]}
        registry = await AdapterFactory.build_registry(config)
        assert len(registry.get_tool_names()) == 0

    @pytest.mark.asyncio
    async def test_empty_tools_list(self):
        registry = await AdapterFactory.build_registry({"tools": []})
        assert registry.get_tool_names() == set()

    @pytest.mark.asyncio
    async def test_missing_tools_key(self):
        registry = await AdapterFactory.build_registry({})
        assert registry.get_tool_names() == set()

    @pytest.mark.asyncio
    async def test_mcp_adapter_initialize_called(self, mcp_tool_config):
        """MCP adapters must have initialize() awaited during build."""
        mock_mcp_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.initialize = AsyncMock()
        mock_instance.get_tool_definitions.return_value = [
            ToolDefinition(
                name="test_mcp.search",
                description="Search tool",
                input_schema={"type": "object", "properties": {}},
                category="read",
            )
        ]
        mock_mcp_class.return_value = mock_instance

        with patch.dict("src.registry.adapter_factory.ADAPTER_TYPES", {"mcp": mock_mcp_class}):
            config = {"tools": [mcp_tool_config]}
            registry = await AdapterFactory.build_registry(config)

        mock_instance.initialize.assert_awaited_once()
        assert "test_mcp.search" in registry.get_tool_names()

    @pytest.mark.asyncio
    async def test_mcp_initialize_failure_skips_adapter(self, mcp_tool_config):
        """If MCP initialize() raises, that adapter is skipped."""
        mock_mcp_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.initialize = AsyncMock(side_effect=ConnectionError("cannot connect"))
        mock_mcp_class.return_value = mock_instance

        with patch.dict("src.registry.adapter_factory.ADAPTER_TYPES", {"mcp": mock_mcp_class}):
            config = {"tools": [mcp_tool_config]}
            registry = await AdapterFactory.build_registry(config)

        assert len(registry.get_tool_names()) == 0

    @pytest.mark.asyncio
    async def test_returns_adapter_registry_instance(self):
        registry = await AdapterFactory.build_registry({})
        assert isinstance(registry, AdapterRegistry)


class TestAdapterTypes:
    def test_adapter_types_contains_rest_api(self):
        from src.adapters.rest_api import RestApiAdapter
        assert ADAPTER_TYPES["rest_api"] is RestApiAdapter

    def test_adapter_types_contains_mcp(self):
        from src.adapters.mcp import McpAdapter
        assert ADAPTER_TYPES["mcp"] is McpAdapter


class TestAdapterFactoryOtel:
    """Tests for action.startup.adapter_init span in AdapterFactory."""

    @pytest.mark.asyncio
    async def test_successful_adapter_emits_startup_span(self, otel_setup, rest_no_auth_config):
        """A successfully built adapter must produce an action.startup.adapter_init span."""
        exporter, _ = otel_setup
        config = {"tools": [rest_no_auth_config]}
        await AdapterFactory.build_registry(config)

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        assert "action.startup.adapter_init" in span_names

        init_span = next(s for s in spans if s.name == "action.startup.adapter_init")
        assert init_span.attributes.get("adapter_type") == "rest_api"
        assert init_span.attributes.get("tool_id") == "test_public"
        assert init_span.attributes.get("success") is True

    @pytest.mark.asyncio
    async def test_failed_adapter_emits_startup_span_with_success_false(self, otel_setup, rest_tool_config):
        """A failing adapter (missing env var) must produce a span with success=False."""
        exporter, _ = otel_setup
        # TEST_WEATHER_KEY not set — init will raise ValueError
        config = {"tools": [rest_tool_config]}
        await AdapterFactory.build_registry(config)

        spans = exporter.get_finished_spans()
        init_spans = [s for s in spans if s.name == "action.startup.adapter_init"]
        assert len(init_spans) == 1
        assert init_spans[0].attributes.get("success") is False

    @pytest.mark.asyncio
    async def test_unknown_type_emits_startup_span_with_success_false(self, otel_setup):
        """An unknown adapter type must produce a span with success=False."""
        exporter, _ = otel_setup
        config = {"tools": [{"id": "bad", "type": "ftp_adapter", "category": "read"}]}
        await AdapterFactory.build_registry(config)

        spans = exporter.get_finished_spans()
        init_spans = [s for s in spans if s.name == "action.startup.adapter_init"]
        assert len(init_spans) == 1
        assert init_spans[0].attributes.get("success") is False

    @pytest.mark.asyncio
    async def test_one_span_per_adapter(self, otel_setup, rest_no_auth_config, rest_write_tool_config, monkeypatch):
        """build_registry emits exactly one action.startup.adapter_init span per tool config."""
        exporter, _ = otel_setup
        monkeypatch.setenv("TEST_JOBS_TOKEN", "tok")
        config = {"tools": [rest_no_auth_config, rest_write_tool_config]}
        await AdapterFactory.build_registry(config)

        spans = [s for s in exporter.get_finished_spans() if s.name == "action.startup.adapter_init"]
        assert len(spans) == 2
