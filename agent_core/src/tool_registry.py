"""
agent_core/tool_registry.py

Builds and caches the tool definitions injected into LLM calls.
Centralizes the merged list of tools (Internal + External Action Gateway).
"""

from __future__ import annotations

import logging
from typing import Any

from src.interfaces.action_gateway import ActionGatewayBase

logger = logging.getLogger(__name__)

# Connector types that require explicit user consent before execution
_CONSENT_REQUIRED_TYPES = {"write", "identity"}


class ToolRegistry:
    """
    Registry for all tools available to the Agent.
    Initialized once at startup.
    """

    def __init__(self, config: dict, gateway: ActionGatewayBase) -> None:
        if config is None:
            raise ValueError("config must not be None")
        
        # 2. Extract tools from Gateway (fetched from /tools at startup)
        self._tool_definitions = gateway.list_available_tools()

        # 1. Build consent set from gateway tool category field (before stripping it)
        self._consent_tools: set[str] = self._build_consent_set(self._tool_definitions)

        # Strip non-Anthropic fields (e.g. "category") from gateway tool definitions.
        # The Anthropic API only accepts name, description, and input_schema.
        _ANTHROPIC_TOOL_KEYS = {"name", "description", "input_schema"}
        self._tool_definitions = [
            {k: v for k, v in t.items() if k in _ANTHROPIC_TOOL_KEYS}
            for t in self._tool_definitions
        ]

        # 3. Add internal tools from config (not handled by AG client)
        internal_tools, tool_routes = self._load_internal_tools(config)
        self._tool_definitions.extend(internal_tools)
        # Maps tool name → route target (e.g. "knowledge_engine") for routing
        # decisions in manager_agent. Only internal tools declare a route.
        self._tool_routes: dict[str, str] = tool_routes

        logger.info(
            "tool_registry.initialized",
            extra={
                "total_tools": len(self._tool_definitions),
                "consent_tools": list(self._consent_tools),
                "internal_tools": [t["name"] for t in internal_tools]
            },
        )

    def get_tool_definitions(self) -> list[dict]:
        """Return all enabled tool definitions."""
        return list(self._tool_definitions)

    def get_tool_names(self) -> set[str]:
        """Return valid tool names."""
        return {t["name"] for t in self._tool_definitions}

    def get_definitions_for(self, names: list[str]) -> list[dict]:
        """Filter definitions for a specific list of names."""
        requested = set(names or [])
        return [t for t in self._tool_definitions if t["name"] in requested]

    def requires_consent(self, tool_name: str) -> bool:
        """Check if a tool requires user consent."""
        return tool_name in self._consent_tools

    def get_route(self, tool_name: str) -> str | None:
        """Return the route target for a tool, or None if not declared.

        Internal connectors may declare a ``route`` field in config to signal
        which backend handles execution. The only currently-supported value is
        ``"knowledge_engine"``. External (Action Gateway) tools return None.
        """
        return self._tool_routes.get(tool_name)

    def _build_consent_set(self, gateway_tools: list[dict]) -> set[str]:
        """Build the set of tool names that require user consent before execution.

        Consent is required for tools with category ``write`` or ``identity``,
        as declared by the Action Gateway in the tool definition.

        Args:
            gateway_tools: Tool definitions returned by the Action Gateway /tools endpoint.

        Returns:
            Set of tool names that require consent.
        """
        consent_tools: set[str] = set()
        for tool in gateway_tools or []:
            if tool.get("category") in _CONSENT_REQUIRED_TYPES and tool.get("name"):
                consent_tools.add(tool["name"])
        return consent_tools

    def _load_internal_tools(self, config: dict) -> tuple[list[dict], dict[str, str]]:
        """Parse internal connector entries into tool definitions and a route map.

        Returns:
            Tuple of (tool_definitions, tool_routes) where tool_definitions are
            Anthropic-compatible tool dicts and tool_routes maps tool name to its
            declared route target (e.g. "knowledge_engine"), omitting tools that
            have no route declared.
        """
        internal_tools: list[dict] = []
        tool_routes: dict[str, str] = {}
        for c in config.get("connectors", {}).get("internal", []) or []:
            if c.get("name") and c.get("input_schema"):
                internal_tools.append({
                    "name": c["name"],
                    "description": c.get("description", ""),
                    "input_schema": c["input_schema"]
                })
                if c.get("route"):
                    tool_routes[c["name"]] = c["route"]
        return internal_tools, tool_routes
