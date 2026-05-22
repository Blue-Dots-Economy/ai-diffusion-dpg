"""Domain schemas for action_gateway block.

Sections written by the LLM during the tools phase. The tools list CAN BE
EMPTY when no external tools are configured (informational agents, KB-only).

Notable runtime constraints baked into enums:
- AuthType excludes 'oauth2' — REST adapter has no oauth2 branch.
- McpTransport excludes 'stdio' — _SUPPORTED_TRANSPORTS in mcp.py is {sse, streamable_http}.
"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

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
    """One REST endpoint exposed as a callable function to the LLM.

    Mirrors ``action_gateway/src/schema/config.py::EndpointDefinition``.
    ``body_template`` declares the nested request body shape for non-GET
    methods, with ``{placeholder}`` strings filled at call time from
    static + agent params.
    """
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    method: HttpMethod = HttpMethod.POST
    path: str = ""
    params: list[ParamDefinition] = Field(default_factory=list)
    body_template: Optional[dict | list] = None


class FieldMapping(BaseModel):
    """One entry in response.field_mapping (reserved — runtime impl in GH-93).

    Mirrors action_gateway runtime FieldMapping. Existing healthbot/edubot
    YAMLs declare these; runtime currently passes them through unprocessed.
    """
    model_config = ConfigDict(extra="forbid")
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    type: ParamType = ParamType.string
    description: str = ""


class ProjectionConfig(BaseModel):
    """Slim projection applied to the raw tool response before it reaches the LLM.

    Mirrors the runtime ``ProjectionConfig`` in
    ``action_gateway/src/schema/config.py``. The wizard writes this from
    the user-confirmed response-field list in the tools phase: each
    ``fields`` entry maps a short output name (what the LLM sees) to the
    dot-path inside the API response that it should read.
    """
    model_config = ConfigDict(extra="forbid")
    list_key: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class ResponseConfig(BaseModel):
    """Tool response handling — size cap + optional projection / field_mapping."""
    model_config = ConfigDict(extra="forbid")
    max_size_chars: int = Field(default=4000, gt=0, le=50000)
    projection: Optional[ProjectionConfig] = None
    field_mapping: Optional[list[FieldMapping]] = None


class HealthCheckConfig(BaseModel):
    """Per-tool startup health-check toggle. enabled=False skips the HEAD probe."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


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
    health_check: Optional[HealthCheckConfig] = None

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


class ToolsSection(RootModel[list[ToolDefinition]]):
    """The tools list — CAN BE EMPTY when no external tools are configured.

    Wraps a bare list because the YAML structure is ``tools: [ ... ]`` at the
    top of action_gateway.yaml — the section value IS the list. Using a
    RootModel lets validate() accept the list directly without an extra
    wrapper key.
    """
    root: list[ToolDefinition] = Field(default_factory=list, max_length=50)


class ObservabilitySection(BaseModel):
    """action_gateway.observability — domain identifier."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
