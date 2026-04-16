"""AdapterRegistry for the Action Gateway block.

This module provides AdapterRegistry, which maps tool names to ToolAdapter
instances. The registry is built once at startup by AdapterFactory and is
immutable after that point. It is the central lookup table for tool dispatch
and tool-catalogue assembly within the Action Gateway.
"""
from __future__ import annotations

import logging

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Maps tool names to ToolAdapter instances for the Action Gateway.

    Built once at startup via AdapterFactory.build_registry() and treated as
    immutable thereafter. Supports multiple tool names mapping to the same
    adapter instance (e.g. an MCP adapter that exposes several tools).
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._adapters: dict[str, ToolAdapter] = {}
        self._definitions_cache: list[ToolDefinition] | None = None

    # ------------------------------------------------------------------
    # Mutation (startup only)
    # ------------------------------------------------------------------

    def register(self, tool_name: str, adapter: ToolAdapter) -> None:
        """Register a tool name to an adapter instance.

        Multiple tool names may map to the same adapter instance. Registering
        an existing name overwrites the previous mapping. Invalidates the
        cached definitions list.

        Args:
            tool_name: Non-empty tool name string.
            adapter: ToolAdapter instance that handles this tool.

        Raises:
            ValueError: If tool_name is None or empty, or if adapter is None.
        """
        if not tool_name or not tool_name.strip():
            raise ValueError("tool_name must not be empty")
        if adapter is None:
            raise ValueError("adapter must not be None")

        self._adapters[tool_name] = adapter
        self._definitions_cache = None  # Invalidate cache

        logger.debug(
            "adapter_registry_register",
            extra={
                "operation": "AdapterRegistry.register",
                "status": "success",
                "tool_name": tool_name,
                "adapter_type": type(adapter).__name__,
            },
        )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(self, tool_name: str) -> ToolAdapter:
        """Return the adapter registered for the given tool name.

        Args:
            tool_name: Tool name to look up.

        Returns:
            The ToolAdapter instance registered for this name.

        Raises:
            KeyError: If tool_name is not registered.
        """
        if tool_name not in self._adapters:
            raise KeyError(f"Unknown tool: '{tool_name}'")
        return self._adapters[tool_name]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def get_all_tool_definitions(self) -> list[ToolDefinition]:
        """Return a deduplicated list of all ToolDefinitions from registered adapters.

        Each adapter instance contributes its definitions exactly once, even if
        that instance is registered under multiple tool names (e.g. an MCP
        adapter). Results are cached after the first call; cache is invalidated
        on any subsequent register() call.

        Returns:
            List of ToolDefinition objects from all unique registered adapters.
        """
        if self._definitions_cache is not None:
            return list(self._definitions_cache)

        seen_adapter_ids: set[int] = set()
        definitions: list[ToolDefinition] = []

        for adapter in self._adapters.values():
            adapter_id = id(adapter)
            if adapter_id in seen_adapter_ids:
                continue
            seen_adapter_ids.add(adapter_id)
            definitions.extend(adapter.get_tool_definitions())

        self._definitions_cache = definitions
        return list(definitions)

    def get_tool_names(self) -> set[str]:
        """Return the set of all registered tool names.

        Returns:
            Set of tool name strings currently registered.
        """
        return set(self._adapters.keys())
