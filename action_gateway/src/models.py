"""Data models for the Action Gateway adapter framework.

This module defines the Pydantic v2 data contracts used by all Action Gateway
components — adapters, registry, and server — within the DPG framework. These
models are the single source of truth for request/response shapes exchanged
between Agent Core and external connectors.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class ToolDefinition(BaseModel):
    """Describes a tool that the Action Gateway can execute.

    Attributes:
        name: Unique, non-empty identifier for the tool.
        description: Human-readable, non-empty description of what the tool does.
        input_schema: JSON Schema dict describing the tool's input parameters.
        category: Access category — "read", "write", or "identity".
    """

    name: str
    description: str
    input_schema: dict
    category: Literal["read", "write", "identity"]

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        """Validate that name is non-empty after stripping whitespace.

        Args:
            v: The raw name value.

        Returns:
            The validated name string.

        Raises:
            ValueError: If name is empty or whitespace-only.
        """
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v

    @field_validator("description")
    @classmethod
    def description_must_not_be_empty(cls, v: str) -> str:
        """Validate that description is non-empty after stripping whitespace.

        Args:
            v: The raw description value.

        Returns:
            The validated description string.

        Raises:
            ValueError: If description is empty or whitespace-only.
        """
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v


class ToolResult(BaseModel):
    """Result returned after executing a tool call.

    Attributes:
        tool_use_id: Identifier matching the originating tool_use block from the LLM.
        tool_name: Name of the tool that was executed.
        result: Structured result payload from the adapter.
        success: True if the tool executed without error.
        result_text: Human-readable summary of the result; defaults to empty string.
        error: Error message if success is False; None otherwise.
    """

    tool_use_id: str
    tool_name: str
    result: dict
    success: bool
    result_text: str = ""
    error: Optional[str] = None


class ExecuteRequest(BaseModel):
    """Request payload for executing a single tool call.

    Attributes:
        tool_name: Name of the tool to execute.
        tool_use_id: Identifier from the LLM's tool_use block, returned in the result.
        input_params: Parameters to pass to the tool adapter.
        session_id: Session identifier for contextual or consent checks; defaults to empty string.
    """

    tool_name: str
    tool_use_id: str
    input_params: dict
    session_id: str = ""


class ExecuteResponse(BaseModel):
    """Response payload from a tool execution request.

    Attributes:
        tool_use_id: Identifier matching the originating ExecuteRequest.
        tool_name: Name of the tool that was executed.
        success: True if execution succeeded.
        result: Structured result payload.
        result_text: Human-readable summary; defaults to empty string.
        error: Error message on failure; None on success.
    """

    tool_use_id: str
    tool_name: str
    success: bool
    result: dict
    result_text: str = ""
    error: Optional[str] = None


class ToolsResponse(BaseModel):
    """Response listing all tools available in the Action Gateway.

    Attributes:
        tools: List of ToolDefinition objects describing available tools.
    """

    tools: list[ToolDefinition]


class HealthResponse(BaseModel):
    """Health check response for the Action Gateway service.

    Attributes:
        status: Overall health status string (e.g. "healthy", "degraded").
        adapters: Map of adapter name to its availability boolean.
    """

    status: str
    adapters: dict[str, bool]
