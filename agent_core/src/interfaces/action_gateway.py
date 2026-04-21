"""
agent_core/interfaces/action_gateway.py

Contract that Agent Core requires from the Action Gateway DPG.
Agent Core never calls external systems directly — all external access goes through here.
"""

from abc import ABC, abstractmethod

from src.models import ToolCall, ToolResult


class ActionGatewayBase(ABC):

    @abstractmethod
    def execute(
        self,
        tool_call: ToolCall,
        session_id: str,
        user_id: str = "",
    ) -> ToolResult:
        """
        Execute a single tool call against the appropriate external connector.

        session_id is passed so the gateway can enforce per-session constraints.
        user_id (E.164 for voice callers) is passed so path-templated tools
        like ``get_profile`` can substitute ``{user_id}`` in the endpoint URL
        without the LLM having to echo it back.

        Always returns a ToolResult — never raises.
        Failures are expressed via ToolResult(success=False, error=...).
        """

    @abstractmethod
    def list_available_tools(self) -> list[dict]:
        """
        Return all available tool definitions in Anthropic tool-use format.
        Called once at startup by ToolRegistry to build the cached tool list.
        """
