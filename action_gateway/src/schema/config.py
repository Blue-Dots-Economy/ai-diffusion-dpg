"""
MergedConfig — strict schema for the Action Gateway merged runtime config.

Merged config = dev-kit/dpg/action_gateway.yaml (framework defaults)
                deep-merged with a domain YAML (e.g. dev-kit/configs/kkb/
                action_gateway.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first tool call.

Belongs to the Action Gateway DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ToolType(str, Enum):
    """Supported adapter types."""

    rest_api = "rest_api"
    mcp = "mcp"


class ToolCategory(str, Enum):
    """Connector category — gates Trust Layer consent requirements."""

    read = "read"
    write = "write"
    identity = "identity"


class AuthType(str, Enum):
    """Supported REST auth schemes."""

    none = "none"
    api_key = "api_key"
    bearer = "bearer"
    oauth2 = "oauth2"


class HttpMethod(str, Enum):
    """Supported HTTP methods for REST endpoints."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class ParamSource(str, Enum):
    """Where a parameter value comes from."""

    agent = "agent"
    static = "static"


class ParamType(str, Enum):
    """JSON-schema type hint shown to the LLM."""

    string = "string"
    integer = "integer"
    number = "number"
    boolean = "boolean"
    array = "array"
    object = "object"


class AuthConfig(BaseModel):
    """Authentication block attached to a REST tool.

    Attributes:
        type: Auth scheme. ``none`` means no auth header is added.
        header: Header name for ``api_key`` auth (e.g. ``X-API-KEY``).
        secret_env: Environment variable holding the key/token for
            ``api_key`` or ``bearer`` auth. Must be set at startup if
            referenced; missing env vars cause tool registration to fail.
        token_url: OAuth2 token endpoint. Only read when ``type=oauth2``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: AuthType = AuthType.none
    header: str = ""
    secret_env: str = ""
    token_url: str = ""


class HealthCheckConfig(BaseModel):
    """Controls the startup reachability probe for a tool.

    Attributes:
        enabled: Set ``false`` to skip the probe. Used by self-referential
            mocks where the probe would deadlock the synchronous /health
            route.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True


class ParamDefinition(BaseModel):
    """One parameter on a REST endpoint.

    Attributes:
        name: Parameter name as sent to the API and as declared in the
            tool schema shown to the LLM.
        source: ``agent`` means the LLM provides the value at call time;
            ``static`` means the value is baked into the config.
        type: JSON-schema type hint shown to the LLM.
        required: Whether the LLM must supply a value. Only meaningful
            when ``source=agent``.
        description: Free-form description shown to the LLM for routing.
        value: The static value used when ``source=static``; ignored
            otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    source: ParamSource = ParamSource.agent
    type: ParamType = ParamType.string
    required: bool = False
    description: str = ""
    value: Optional[object] = None


class EndpointDefinition(BaseModel):
    """One REST endpoint exposed by a REST tool.

    Attributes:
        name: Endpoint identifier (e.g. ``search``).
        method: HTTP verb.
        path: Path appended to the tool's ``base_url``. May contain
            ``{placeholder}`` segments that the adapter fills from caller
            context.
        params: Ordered parameter list.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    method: HttpMethod = HttpMethod.POST
    path: str = ""
    params: list[ParamDefinition] = Field(default_factory=list)


class FieldMapping(BaseModel):
    """One entry in a response field_mapping (reserved — not yet wired).

    Implementation tracked in GH-93. When wired, only the mapped fields
    are forwarded to the LLM; the rest of the response is dropped.

    Attributes:
        source: JSONPath from the response root.
        target: Field name the LLM sees.
        type: JSON-schema type hint.
        description: Optional human-readable description.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    type: ParamType = ParamType.string
    description: str = ""


class ResponseConfig(BaseModel):
    """How a tool's response is shaped before it reaches the LLM.

    Attributes:
        max_size_chars: Truncation threshold; responses larger than this
            are cut with a ``...[truncated]`` suffix.
        field_mapping: Reserved — see GH-93.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_size_chars: int = Field(default=4000, gt=0)
    field_mapping: Optional[list[FieldMapping]] = None


class ToolDefinition(BaseModel):
    """One tool connector exposed to the LLM via Action Gateway.

    A single model covers both ``rest_api`` and ``mcp`` tool shapes —
    the adapter factory reads only the subset relevant to the declared
    ``type``. Fields not applicable to a given adapter type are ignored
    by that adapter.

    Attributes:
        id: Unique tool identifier. Must match the agent_core connector
            key.
        type: Adapter type: ``rest_api`` or ``mcp``.
        category: Connector category — gates Trust Layer consent.
        description: Shown to the LLM for routing decisions.
        timeout_ms: Request timeout in milliseconds.
        base_url: REST — base URL of the API.
        auth: REST — auth block.
        health_check: REST — startup reachability probe control.
        endpoints: REST — endpoint list.
        response: REST — response shaping.
        server_url: MCP — base URL of the MCP server.
        transport: MCP — ``sse`` or ``stdio``.
        namespace: MCP — tool-name prefix; defaults to ``id``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: ToolType = ToolType.rest_api
    category: ToolCategory = ToolCategory.read
    description: str = ""
    timeout_ms: int = Field(default=5000, gt=0)

    # REST-only fields
    base_url: Optional[str] = None
    auth: Optional[AuthConfig] = None
    health_check: Optional[HealthCheckConfig] = None
    endpoints: Optional[list[EndpointDefinition]] = None
    response: Optional[ResponseConfig] = None

    # MCP-only fields
    server_url: Optional[str] = None
    transport: Optional[str] = None
    namespace: Optional[str] = None


class OtelConfig(BaseModel):
    """OTel SDK exporter and sampling configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ObservabilityConfig(BaseModel):
    """Observability settings — OTel plus domain identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = "unknown"
    otel: OtelConfig = Field(default_factory=OtelConfig)


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Action Gateway entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=9999, gt=0, lt=65536)


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged action_gateway config.

    Validates the deep-merged result of ``dev-kit/dpg/action_gateway.yaml``
    and the domain-specific YAML. Unknown keys at any nesting level fail
    at startup rather than silently passing.

    Attributes:
        server: Uvicorn bind settings (main.py).
        tools: Ordered list of tool connectors.
        observability: OTel + domain identifier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    tools: list[ToolDefinition] = Field(default_factory=list)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def validate_full(cls, config: dict) -> "MergedConfig":
        """Validate the full merged config dict against the strict schema.

        Args:
            config: Merged dict (dpg defaults + domain overrides).

        Returns:
            Validated MergedConfig instance.

        Raises:
            pydantic.ValidationError: If the config contains unknown keys,
                wrong value types, or values outside the allowed ranges at
                any nesting level.
            TypeError: If config is None.
        """
        if config is None:
            raise TypeError("config must be a dict, got None")
        return cls.model_validate(config)
