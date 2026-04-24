"""
agent_core/tool_registry.py

Builds and caches the tool definitions injected into LLM calls.
Centralizes the merged list of tools (Internal + External Action Gateway).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.interfaces.action_gateway import ActionGatewayBase

logger = logging.getLogger(__name__)

# Connector types that require explicit user consent before execution
_CONSENT_REQUIRED_TYPES = {"write", "identity"}

# Anthropic tool name pattern: ^[a-zA-Z0-9_-]{1,128}$
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _sanitize_tool_name(name: str) -> str:
    """Replace characters invalid in Anthropic tool names with double underscore.

    MCP tools use dotted names (e.g. ``obsrv_docs.searchDocumentation``) which
    the Anthropic API rejects. This replaces dots (and any other invalid chars)
    with ``__`` so the name passes validation while remaining reversible.

    Args:
        name: Original tool name.

    Returns:
        Sanitized name safe for the Anthropic API.
    """
    if _VALID_TOOL_NAME.match(name):
        return name
    return re.sub(r"[^a-zA-Z0-9_-]", "__", name)[:128]


class ToolRegistry:
    """
    Registry for all tools available to the Agent.
    Initialized once at startup.
    """

    def __init__(self, config: dict, gateway: ActionGatewayBase) -> None:
        if config is None:
            raise ValueError("config must not be None")

        # Reverse mapping: sanitized LLM name → original Action Gateway name.
        # Populated during name sanitization so manager_agent can route tool
        # calls back to the correct Action Gateway tool.
        self._name_map: dict[str, str] = {}

        # 2. Extract tools from Gateway (fetched from /tools at startup)
        self._tool_definitions = gateway.list_available_tools()

        # 1. Build consent set from gateway tool category field (before stripping it)
        self._consent_tools: set[str] = self._build_consent_set(self._tool_definitions)

        # Strip non-Anthropic fields (e.g. "category") from gateway tool definitions.
        # The Anthropic API only accepts name, description, and input_schema.
        # Also strip "$schema" from input_schema — MCP tools (e.g. GitBook) include
        # JSON Schema draft references that the Anthropic API rejects with 400.
        # Sanitize tool names — replace dots and other invalid chars for Anthropic API.
        _ANTHROPIC_TOOL_KEYS = {"name", "description", "input_schema"}
        cleaned: list[dict] = []
        for t in self._tool_definitions:
            tool = {k: v for k, v in t.items() if k in _ANTHROPIC_TOOL_KEYS}
            schema = tool.get("input_schema")
            if isinstance(schema, dict):
                schema.pop("$schema", None)
            original_name = tool.get("name", "")
            sanitized_name = _sanitize_tool_name(original_name)
            if sanitized_name != original_name:
                self._name_map[sanitized_name] = original_name
                tool["name"] = sanitized_name
            cleaned.append(tool)
        self._tool_definitions = cleaned

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
                "internal_tools": [t["name"] for t in internal_tools],
                "sanitized_names": dict(self._name_map) if self._name_map else {},
            },
        )

    def register_internal(
        self,
        *,
        name: str,
        route: str,
        description: str,
        input_schema: dict,
    ) -> None:
        """Register an orchestrator-routed internal tool at runtime.

        Used by the orchestrator (GH-137) to inject built-in signals such as
        ``end_session`` without requiring a domain config entry. Idempotent:
        re-registering a tool with the same name overwrites the previous entry.

        Args:
            name:         Tool name exposed to the LLM.
            route:        Route target (e.g. ``"orchestrator"``) used by the
                          manager_agent to decide how to handle the tool call.
            description:  Tool description shown to the LLM.
            input_schema: JSON Schema for the tool's input parameters.
        """
        if not name:
            raise ValueError("name must not be empty")
        # Replace any existing entry with the same name, then append.
        self._tool_definitions = [t for t in self._tool_definitions if t.get("name") != name]
        self._tool_definitions.append({
            "name": name,
            "description": description,
            "input_schema": input_schema,
        })
        self._tool_routes[name] = route

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

    def resolve_original_name(self, sanitized_name: str) -> str:
        """Map a sanitized LLM tool name back to the original Action Gateway name.

        MCP tool names with dots (e.g. ``obsrv_docs.searchDocumentation``) are
        sanitized for the Anthropic API (``obsrv_docs__searchDocumentation``).
        This method returns the original name for routing to Action Gateway.
        Returns the input unchanged if no mapping exists.

        Args:
            sanitized_name: Tool name as returned by the LLM.

        Returns:
            Original tool name for Action Gateway routing.
        """
        return self._name_map.get(sanitized_name, sanitized_name)

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
