"""Tests for AdapterRegistry.

Covers normal execution, edge cases, and failure scenarios for
AdapterRegistry in the Action Gateway block.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models import ToolDefinition
from src.registry.adapter_registry import AdapterRegistry


def _make_adapter(tool_names: list[str], category: str = "read") -> MagicMock:
    """Create a mock ToolAdapter that returns ToolDefinitions for given names."""
    adapter = MagicMock()
    adapter.get_tool_definitions.return_value = [
        ToolDefinition(
            name=name,
            description=f"Description of {name}",
            input_schema={"type": "object", "properties": {}},
            category=category,
        )
        for name in tool_names
    ]
    return adapter


class TestAdapterRegistryRegisterAndResolve:
    def test_register_and_resolve_returns_adapter(self):
        registry = AdapterRegistry()
        adapter = _make_adapter(["tool_a"])
        registry.register("tool_a", adapter)
        assert registry.resolve("tool_a") is adapter

    def test_resolve_unknown_raises_key_error(self):
        registry = AdapterRegistry()
        with pytest.raises(KeyError):
            registry.resolve("nonexistent")

    def test_register_duplicate_name_overwrites(self):
        registry = AdapterRegistry()
        adapter1 = _make_adapter(["tool_a"])
        adapter2 = _make_adapter(["tool_a"])
        registry.register("tool_a", adapter1)
        registry.register("tool_a", adapter2)
        assert registry.resolve("tool_a") is adapter2

    def test_multiple_names_same_adapter_instance(self):
        """Multiple tool names from one MCP adapter all resolve to the same instance."""
        registry = AdapterRegistry()
        adapter = _make_adapter(["mcp.search", "mcp.list"])
        registry.register("mcp.search", adapter)
        registry.register("mcp.list", adapter)
        assert registry.resolve("mcp.search") is adapter
        assert registry.resolve("mcp.list") is adapter


class TestAdapterRegistryToolDefinitions:
    def test_get_all_tool_definitions_returns_definitions(self):
        registry = AdapterRegistry()
        adapter = _make_adapter(["tool_a", "tool_b"])
        registry.register("tool_a", adapter)
        registry.register("tool_b", adapter)
        defs = registry.get_all_tool_definitions()
        names = {d.name for d in defs}
        assert names == {"tool_a", "tool_b"}

    def test_get_all_definitions_deduplicates_mcp_adapter(self):
        """An MCP adapter registered under two names should appear only once."""
        registry = AdapterRegistry()
        mcp_adapter = _make_adapter(["mcp.search", "mcp.list"])
        registry.register("mcp.search", mcp_adapter)
        registry.register("mcp.list", mcp_adapter)
        defs = registry.get_all_tool_definitions()
        # get_tool_definitions() should only be called once
        mcp_adapter.get_tool_definitions.assert_called_once()
        assert len(defs) == 2

    def test_get_all_definitions_multiple_adapters(self):
        registry = AdapterRegistry()
        adapter1 = _make_adapter(["rest_tool"])
        adapter2 = _make_adapter(["mcp.search", "mcp.list"])
        registry.register("rest_tool", adapter1)
        registry.register("mcp.search", adapter2)
        registry.register("mcp.list", adapter2)
        defs = registry.get_all_tool_definitions()
        names = {d.name for d in defs}
        assert names == {"rest_tool", "mcp.search", "mcp.list"}

    def test_get_all_definitions_caches_result(self):
        registry = AdapterRegistry()
        adapter = _make_adapter(["tool_a"])
        registry.register("tool_a", adapter)
        registry.get_all_tool_definitions()
        registry.get_all_tool_definitions()
        # Should only call get_tool_definitions once due to cache
        adapter.get_tool_definitions.assert_called_once()

    def test_register_invalidates_cache(self):
        registry = AdapterRegistry()
        adapter1 = _make_adapter(["tool_a"])
        registry.register("tool_a", adapter1)
        registry.get_all_tool_definitions()  # prime cache

        adapter2 = _make_adapter(["tool_b"])
        registry.register("tool_b", adapter2)
        defs = registry.get_all_tool_definitions()
        names = {d.name for d in defs}
        assert "tool_b" in names


class TestAdapterRegistryGetToolNames:
    def test_get_tool_names_returns_all_registered_names(self):
        registry = AdapterRegistry()
        adapter = _make_adapter(["tool_a", "tool_b"])
        registry.register("tool_a", adapter)
        registry.register("tool_b", adapter)
        assert registry.get_tool_names() == {"tool_a", "tool_b"}

    def test_get_tool_names_empty_registry(self):
        registry = AdapterRegistry()
        assert registry.get_tool_names() == set()


class TestAdapterRegistryEdgeCases:
    def test_empty_registry_get_all_definitions(self):
        registry = AdapterRegistry()
        assert registry.get_all_tool_definitions() == []

    def test_register_none_adapter_raises_value_error(self):
        registry = AdapterRegistry()
        with pytest.raises(ValueError):
            registry.register("tool_a", None)  # type: ignore[arg-type]

    def test_register_empty_tool_name_raises_value_error(self):
        registry = AdapterRegistry()
        adapter = _make_adapter(["tool_a"])
        with pytest.raises(ValueError):
            registry.register("", adapter)
