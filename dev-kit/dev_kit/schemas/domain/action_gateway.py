"""Domain schemas for action_gateway block.

Sections written by the LLM during the tools phase. The tools list CAN BE
EMPTY when no external tools are configured (informational agents, KB-only).

Notable runtime constraints baked into enums:
- AuthType excludes 'oauth2' — REST adapter has no oauth2 branch.
- McpTransport excludes 'stdio' — _SUPPORTED_TRANSPORTS in mcp.py is {sse, streamable_http}.
"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import (
    ToolType, ToolCategory, AuthType, HttpMethod,
    ParamSource, ParamType, McpTransport,
)


class AuthConfig(BaseModel):
    """REST auth block. type=oauth2 is excluded — adapter has no oauth2 branch."""
    model_config = ConfigDict(extra="forbid")
    type: AuthType = AuthType.none
    header: str = ""
    secret_env: str = ""
    token_url: str = ""    # reserved (no oauth2 support today)


class ParamDefinition(BaseModel):
    """One REST endpoint parameter or MCP tool input field."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    source: ParamSource = ParamSource.agent
    type: ParamType = ParamType.string
    required: bool = False
    description: str = ""
    value: Optional[Any] = None
    default: Optional[Any] = None
    items: Optional[dict] = None   # JSON schema for array elements when type=array (OpenAI requires)


class EndpointDefinition(BaseModel):
    """One REST endpoint exposed as a callable function to the LLM."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    method: HttpMethod = HttpMethod.POST
    path: str = ""
    params: list[ParamDefinition] = Field(default_factory=list)


class ResponseConfig(BaseModel):
    """Tool response handling — size cap + optional projection."""
    model_config = ConfigDict(extra="forbid")
    max_size_chars: int = Field(default=4000, gt=0, le=50000)
    projection: Optional[dict] = None


class ToolDefinition(BaseModel):
    """One tool exposed to the LLM. Either REST API or MCP server-backed.

    The shape_matches_type validator enforces:
    - REST tools require base_url + endpoints
    - MCP tools require server_url + transport
    """
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    type: ToolType = ToolType.rest_api
    category: ToolCategory = ToolCategory.read
    description: str = Field(..., min_length=1)
    timeout_ms: int = Field(default=5000, gt=0, le=120000)

    # REST-only
    base_url: Optional[str] = None
    auth: Optional[AuthConfig] = None
    endpoints: Optional[list[EndpointDefinition]] = None
    response: Optional[ResponseConfig] = None

    # MCP-only — McpTransport excludes 'stdio' (not supported by adapter)
    server_url: Optional[str] = None
    transport: Optional[McpTransport] = None
    namespace: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def shape_matches_type(self) -> "ToolDefinition":
        if self.type == ToolType.rest_api:
            if not self.base_url or not self.endpoints:
                raise ValueError(
                    f"REST API tool '{self.id}' requires base_url and at least one endpoint"
                )
        elif self.type == ToolType.mcp:
            if not self.server_url or not self.transport:
                raise ValueError(
                    f"MCP tool '{self.id}' requires server_url and transport"
                )
        return self


class ToolsSection(BaseModel):
    """The tools list — CAN BE EMPTY when no external tools are configured."""
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=50)


class ObservabilitySection(BaseModel):
    """action_gateway.observability — domain identifier."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
