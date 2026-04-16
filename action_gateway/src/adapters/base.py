"""Base adapter interface for the Action Gateway block.

This module defines the ToolAdapter ABC that all concrete adapters must implement.
It belongs to the Action Gateway block within the DPG framework, and provides the
contract that the ToolRegistry uses to discover tools and route executions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import ToolDefinition, ToolResult


class ToolAdapter(ABC):
    """Base class for all Action Gateway tool adapters.

    Every adapter wraps one external system (REST API, MCP server, etc.) and
    exposes a uniform interface for tool discovery and execution. Agent Core
    never calls external systems directly — all external access goes through
    a ToolAdapter subclass.

    Attributes:
        config: Raw configuration dict for this adapter instance, loaded from
            the domain YAML at startup.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the adapter with its configuration block.

        Args:
            config: Adapter-level config dict parsed from the domain YAML.
                Must not be None; an empty dict is acceptable for adapters
                with no required configuration.

        Raises:
            ValueError: If config is None.
        """
        if config is None:
            raise ValueError("config must not be None")
        self.config = config

    @abstractmethod
    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return the tool schemas that the LLM sees for this adapter.

        The returned list is merged into the global tool catalogue served
        by the Action Gateway. Each definition contains the name, description,
        input schema, and access category the LLM uses for tool selection.

        Returns:
            A list of ToolDefinition objects. May be empty if the adapter
            exposes no tools in the current configuration.
        """

    @abstractmethod
    async def execute(
        self, tool_name: str, params: dict, session_id: str
    ) -> ToolResult:
        """Execute a tool call and return a normalised result.

        Agent Core calls this method after the LLM emits a tool_use block.
        The adapter is responsible for translating params into the external
        call, handling errors, and returning a structured ToolResult.

        Args:
            tool_name: Name of the tool to execute; must match one of the
                names returned by get_tool_definitions().
            params: Parameter dict from the LLM's tool_use input block.
                May be empty; the adapter must not crash on missing keys.
            session_id: Session identifier forwarded from Agent Core for
                logging and consent tracking; may be an empty string.

        Returns:
            ToolResult with success=True and a populated result dict on
            success, or success=False and a non-empty error string on failure.
            Never raises — all exceptions must be caught and returned as
            a failed ToolResult.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Verify that the adapter's backing service is reachable.

        Used by the Action Gateway health endpoint to surface degraded
        connectors without affecting the response path.

        Returns:
            True if the backing service responds acceptably; False otherwise.
        """
