"""Tests for dev_kit.agent.phase_prompts.tools."""
from __future__ import annotations


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False, is_multi_turn=False,
        needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="en", supported_languages=["en"],
        domain_description="test", project_name="test_project",
    )
    base.update(overrides)
    from dev_kit.agent.intake_state import IntakeState
    return IntakeState(**base)


def _fake_field(path: str, description: str = "A field"):
    from dev_kit.agent.field_rules import FieldRule
    rule = FieldRule(category="chat", phase="tools", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.tools import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Tools" in result


def test_build_contains_field_section():
    result = build([], "", "", _intake())
    assert "## Fields to capture this phase" in result


def test_build_contains_pydantic_schema_section():
    result = build([], "", "", _intake())
    assert "## Pydantic schemas" in result


def test_build_injects_pydantic_schemas_param():
    result = build([], "class FooSection(BaseModel): pass", "", _intake())
    assert "class FooSection(BaseModel): pass" in result


def test_build_injects_cross_phase_refs_param():
    result = build([], "", "preset_value=xyz", _intake())
    assert "preset_value=xyz" in result


def test_build_renders_pending_fields():
    fields = [
        _fake_field("action_gateway.tools", "Tool definitions"),
        _fake_field("agent_core.connectors.read", "Read connectors"),
    ]
    result = build(fields, "", "", _intake())
    assert "action_gateway.tools" in result
    assert "Tool definitions" in result
    assert "agent_core.connectors.read" in result
    assert "Read connectors" in result


def test_tools_expectation_when_has_external_tools():
    result = build([], "", "", _intake(has_external_tools=True))
    assert "needs external tools" in result or "has_external_tools" in result


def test_tools_expectation_when_no_external_tools():
    result = build([], "", "", _intake(has_external_tools=False))
    assert "does **NOT** need external tools" in result or "NOT" in result


def test_tools_phase_documents_spec_backed_paths_only() -> None:
    """Tools-phase prompt must describe both spec-backed entry paths to add
    external tools: OpenAPI spec (URL or paste) and MCP server discovery.

    The "Path C — Manual REST API" path was REMOVED deliberately: it
    let the LLM build a `rest_api` tool from imagination when the user
    described an API in plain English without a spec. Those tools
    crashed at runtime because the LLM cannot know real contracts.
    Every tool MUST originate from a real spec (Path A) or MCP
    discovery (Path B); the strict policy is enforced by the
    first-question block at the top of the prompt.

    Each remaining path must name the concrete tool the LLM should
    call so the LLM does not invent legacy names.
    """
    result = build([], "", "", _intake(has_external_tools=True))

    # Both spec-backed path headers present.
    assert "Path A — OpenAPI spec" in result
    assert "Path B — MCP server URL" in result
    # Manual-REST path explicitly REMOVED.
    assert "Path C" not in result
    # The "no manual tools" guard must be in place.
    assert "No manual / imagined tool definitions" in result

    # Each path names the correct tool call.
    # Path A: both fetch_openapi_spec_from_url AND parse_openapi_spec
    assert "fetch_openapi_spec_from_url" in result
    assert "parse_openapi_spec" in result
    # Path B: discover_mcp_tools (now a real implementation)
    assert "discover_mcp_tools" in result
    # Both paths converge on add_tool
    assert "add_tool" in result


def test_tools_phase_does_not_tell_llm_to_paste_when_url_given() -> None:
    """The old workaround copy ("ask them to paste the spec contents (the
    wizard does not fetch URLs)") must be gone now that the URL fetcher
    is implemented. Otherwise the LLM still asks users to paste specs by
    hand even when they offered a URL.
    """
    result = build([], "", "", _intake(has_external_tools=True))
    assert "the wizard does not fetch URLs" not in result
    assert "ask them to paste the spec contents" not in result
