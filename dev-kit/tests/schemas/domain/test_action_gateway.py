"""Tests for action_gateway domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.action_gateway import (
    AuthConfig,
    EndpointDefinition,
    ObservabilitySection,
    ParamDefinition,
    ResponseConfig,
    ToolDefinition,
    ToolsSection,
)


# -- AuthConfig --------------------------------------------------------------

def test_auth_config_default_none():
    a = AuthConfig()
    assert a.type.value == "none"


def test_auth_config_valid_types():
    AuthConfig(type="api_key", header="X-API-KEY", secret_env="MY_KEY")
    AuthConfig(type="bearer", secret_env="MY_TOKEN")


def test_auth_config_rejects_oauth2():
    """oauth2 not supported by REST adapter."""
    with pytest.raises(ValidationError):
        AuthConfig(type="oauth2")


def test_auth_config_extra_forbidden():
    with pytest.raises(ValidationError):
        AuthConfig(type="none", unknown="x")


# -- ParamDefinition ---------------------------------------------------------

def test_param_definition_minimal():
    p = ParamDefinition(name="query")
    assert p.source.value == "agent"
    assert p.type.value == "string"
    assert p.required is False
    assert p.value is None
    assert p.default is None
    assert p.items is None


def test_param_name_required_non_empty():
    with pytest.raises(ValidationError):
        ParamDefinition(name="")


def test_param_source_enum():
    ParamDefinition(name="q", source="agent")
    ParamDefinition(name="q", source="static")
    with pytest.raises(ValidationError):
        ParamDefinition(name="q", source="env")


def test_param_type_enum():
    for t in ("string", "integer", "number", "boolean", "array", "object"):
        ParamDefinition(name="q", type=t)
    with pytest.raises(ValidationError):
        ParamDefinition(name="q", type="float")


def test_param_items_for_array():
    p = ParamDefinition(name="tags", type="array", items={"type": "string"})
    assert p.items == {"type": "string"}


# -- EndpointDefinition ------------------------------------------------------

def test_endpoint_minimal():
    e = EndpointDefinition(name="search")
    assert e.method.value == "POST"
    assert e.path == ""
    assert e.params == []


def test_endpoint_method_enum():
    for m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        EndpointDefinition(name="x", method=m)
    with pytest.raises(ValidationError):
        EndpointDefinition(name="x", method="OPTIONS")


def test_endpoint_name_required_non_empty():
    with pytest.raises(ValidationError):
        EndpointDefinition(name="")


# -- ResponseConfig ----------------------------------------------------------

def test_response_default():
    r = ResponseConfig()
    assert r.max_size_chars == 4000
    assert r.projection is None


def test_response_max_size_bounds():
    ResponseConfig(max_size_chars=1)
    ResponseConfig(max_size_chars=50000)
    with pytest.raises(ValidationError):
        ResponseConfig(max_size_chars=0)
    with pytest.raises(ValidationError):
        ResponseConfig(max_size_chars=50001)


# -- ToolDefinition ----------------------------------------------------------

def _rest_tool(**overrides):
    """Build a minimal valid REST tool."""
    base = dict(
        id="search_jobs",
        description="Search jobs by location",
        type="rest_api",
        base_url="https://api.example.com",
        endpoints=[EndpointDefinition(name="search")],
    )
    base.update(overrides)
    return ToolDefinition(**base)


def test_tool_id_pattern():
    _rest_tool()
    with pytest.raises(ValidationError):
        _rest_tool(id="Has Spaces")
    with pytest.raises(ValidationError):
        _rest_tool(id="123start")
    with pytest.raises(ValidationError):
        _rest_tool(id="")


def test_tool_description_required_non_empty():
    with pytest.raises(ValidationError):
        _rest_tool(description="")


def test_tool_timeout_bounds():
    _rest_tool(timeout_ms=1)
    _rest_tool(timeout_ms=120000)
    with pytest.raises(ValidationError):
        _rest_tool(timeout_ms=0)
    with pytest.raises(ValidationError):
        _rest_tool(timeout_ms=120001)


def test_rest_tool_requires_base_url():
    with pytest.raises(ValidationError, match="REST"):
        ToolDefinition(
            id="t", description="d", type="rest_api",
            endpoints=[EndpointDefinition(name="x")],
        )


def test_rest_tool_requires_endpoints():
    with pytest.raises(ValidationError, match="REST"):
        ToolDefinition(
            id="t", description="d", type="rest_api",
            base_url="https://example.com",
        )


def test_mcp_tool_requires_server_url_and_transport():
    with pytest.raises(ValidationError, match="MCP"):
        ToolDefinition(id="t", description="d", type="mcp")
    with pytest.raises(ValidationError, match="MCP"):
        ToolDefinition(id="t", description="d", type="mcp", server_url="https://x")


def test_mcp_tool_valid():
    t = ToolDefinition(
        id="obsrv_docs", description="d",
        type="mcp", server_url="https://x", transport="streamable_http",
    )
    assert t.type.value == "mcp"


def test_mcp_transport_rejects_stdio():
    """stdio not in _SUPPORTED_TRANSPORTS in mcp.py."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="t", description="d",
            type="mcp", server_url="https://x", transport="stdio",
        )


def test_mcp_namespace_rejects_empty_string():
    """Empty namespace is semantically wrong (use None instead)."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="t", description="d",
            type="mcp", server_url="https://x", transport="sse",
            namespace="",
        )


def test_tool_extra_forbidden():
    with pytest.raises(ValidationError):
        _rest_tool(unknown_field="x")


# -- ToolsSection ------------------------------------------------------------

def test_tools_section_can_be_empty():
    """tools list CAN be empty (no external tools)."""
    s = ToolsSection.model_validate([])
    assert s.root == []


def test_tools_section_default_empty():
    s = ToolsSection()
    assert s.root == []


def test_tools_section_with_tools():
    s = ToolsSection.model_validate([_rest_tool().model_dump()])
    assert len(s.root) == 1


def test_tools_section_max_50():
    """Domain configs cap at 50 tools."""
    too_many = [_rest_tool(id=f"tool_{i}").model_dump() for i in range(51)]
    with pytest.raises(ValidationError):
        ToolsSection.model_validate(too_many)


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", typo="x")
