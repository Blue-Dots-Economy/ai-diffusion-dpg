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
        
        # 1. Build consent set from config
        self._consent_tools: set[str] = self._build_consent_set(config)
        
        # 2. Extract tools from Gateway (Client already parsed domain.yaml)
        # Note: action_gateway_http_client already returns read/write/identity
        self._tool_definitions = gateway.list_available_tools()
        
        # 3. Add internal tools from config (not handled by AG client)
        internal_tools = self._load_internal_tools(config)
        self._tool_definitions.extend(internal_tools)

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

    def _build_consent_set(self, config: dict) -> set[str]:
        consent_tools: set[str] = set()
        connectors = config.get("connectors", {})
        for c_type, c_list in connectors.items():
            if c_type in _CONSENT_REQUIRED_TYPES:
                for c in c_list or []:
                    if c.get("name"):
                        consent_tools.add(c["name"])
        return consent_tools

    def _load_internal_tools(self, config: dict) -> list[dict]:
        internal_tools = []
        for c in config.get("connectors", {}).get("internal", []) or []:
            if c.get("name") and c.get("input_schema"):
                internal_tools.append({
                    "name": c["name"],
                    "description": c.get("description", ""),
                    "input_schema": c["input_schema"]
                })
        return internal_tools
