"""
agent_core/interfaces/async_action_gateway.py

Async contract that Agent Core's stream_turn() requires from the Action Gateway DPG.
Mirror of ActionGatewayBase with all methods as async def.
"""

from abc import ABC, abstractmethod

from src.models import ToolCall, ToolResult


class AsyncActionGatewayBase(ABC):

    @abstractmethod
    async def execute(
        self,
        tool_call: ToolCall,
        session_id: str,
        user_id: str = "",
    ) -> ToolResult:
        """Async version of ActionGatewayBase.execute(). See sync interface for full docs."""

    @abstractmethod
    async def list_available_tools(self) -> list[dict]:
        """Async version of ActionGatewayBase.list_available_tools(). See sync interface for full docs."""
