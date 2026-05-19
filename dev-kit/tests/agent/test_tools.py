"""Tests for the 8-tool set in dev_kit.agent.tools (design §6: Slimmed tool surface).

Covers the canonical 4-param signature, side effects, and failure paths for
each of the 8 tools, plus integration tests verifying TOOL_HANDLERS wiring in
phase_driver.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §6.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dev_kit.agent.field_status import save_field_status
from dev_kit.agent.intake_state import IntakeState, save_intake_state
from dev_kit.agent.phase_driver import (
    TOOL_HANDLERS,
    LLMResponse,
    ToolCall,
    load_accumulator,
    run_turn,
    save_accumulator,
    save_current_phase,
)
from dev_kit.agent.skeleton import BLOCKS
from dev_kit.agent.tools import (
    add_routing_rule,
    add_subagent,
    add_tool,
    discover_mcp_tools,
    fetch_openapi_spec_from_url,
    parse_openapi_spec,
    update_config,
    update_intake,
    update_subagent,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TOOL_NAMES = {
    "update_intake",
    "update_config",
    "add_subagent",
    "update_subagent",
    "add_routing_rule",
    "add_tool",
    "parse_openapi_spec",
    "fetch_openapi_spec_from_url",
    "discover_mcp_tools",
}


def _make_intake(**overrides: Any) -> IntakeState:
    """Return a minimal valid IntakeState with optional field overrides."""
    base = dict(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="test domain",
        project_name="test-project",
    )
    base.update(overrides)
    return IntakeState(**base)


def _empty_accumulator() -> dict[str, dict]:
    """Return a fresh accumulator with all blocks as empty dicts."""
    return {block: {} for block in BLOCKS}


def _empty_field_status() -> dict[str, str]:
    """Return an empty field status registry."""
    return {}


def _setup_project(
    tmp_path: Path,
    *,
    slug: str = "demo",
    intake: IntakeState | None = None,
    accumulator: dict[str, dict] | None = None,
    field_status: dict[str, str] | None = None,
    current_phase: str | None = "tier",
) -> Path:
    """Lay out a minimal project tree for run_turn integration tests.

    Returns the projects_root path.
    """
    projects_root = tmp_path / "projects"
    slug_root = projects_root / slug
    meta = slug_root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)

    if intake is None:
        intake = _make_intake()
    save_intake_state(meta / "intake_state.json", intake)

    if accumulator is not None:
        save_accumulator(slug_root, accumulator)

    if field_status is not None:
        save_field_status(meta / "field_status.json", field_status)

    if current_phase is not None:
        save_current_phase(slug_root, current_phase)

    return projects_root


def _fake_llm(text: str = "ok", tool_calls: list[ToolCall] | None = None):
    """Return a callable that returns a canned LLMResponse."""
    def _call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        return LLMResponse(text=text, tool_calls=list(tool_calls or []))
    return _call


# ---------------------------------------------------------------------------
# Meta — TOOL_HANDLERS must export exactly the 8 expected names
# ---------------------------------------------------------------------------


def test_tools_match_TOOL_HANDLERS_keys() -> None:
    """TOOL_HANDLERS keys must equal exactly the 8-tool set."""
    assert set(TOOL_HANDLERS.keys()) == _TOOL_NAMES


def test_tool_functions_are_callable() -> None:
    """Each of the 8 tool functions must be callable."""
    for name in _TOOL_NAMES:
        fn = TOOL_HANDLERS[name]
        assert callable(fn), f"{name} is not callable"


# ---------------------------------------------------------------------------
# 1. update_intake
# ---------------------------------------------------------------------------


class TestUpdateIntake:
    """Tests for the update_intake tool."""

    def test_normal_mutates_intake_state(self) -> None:
        """Valid update_intake call mutates IntakeState in place."""
        intake = _make_intake(has_kb=False)
        acc = _empty_accumulator()
        fs = _empty_field_status()

        result = update_intake(
            {"field": "has_kb", "value": True},
            intake,
            acc,
            fs,
        )

        assert result.get("ok") is True
        assert intake.has_kb is True

    def test_missing_field_returns_error(self) -> None:
        """update_intake with no 'field' key returns ok=False."""
        result = update_intake({}, _make_intake(), _empty_accumulator(), _empty_field_status())
        assert result["ok"] is False
        assert "field" in result["error"]

    def test_missing_value_returns_error(self) -> None:
        """update_intake without 'value' key returns ok=False."""
        result = update_intake(
            {"field": "has_kb"},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "value" in result["error"]

    def test_unknown_field_returns_error(self) -> None:
        """update_intake with an unknown field name returns ok=False (no crash)."""
        result = update_intake(
            {"field": "nonexistent_field_xyz", "value": True},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# 2. update_config
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    """Tests for the update_config tool."""

    def test_path_form_writes_to_accumulator(self) -> None:
        """Path form sets the value and marks field_status answered."""
        intake = _make_intake()
        acc = _empty_accumulator()
        fs = {"trust_layer.trust.input_rules.blocked_phrases": "pending"}

        result = update_config(
            {
                "path": "trust_layer.trust.input_rules.blocked_phrases",
                "value": ["badword"],
            },
            intake,
            acc,
            fs,
        )

        assert result.get("ok") is True
        assert acc["trust_layer"]["trust"]["input_rules"]["blocked_phrases"] == ["badword"]
        assert fs["trust_layer.trust.input_rules.blocked_phrases"] == "answered"

    def test_path_form_missing_value_returns_error(self) -> None:
        """Path form without 'value' returns ok=False."""
        result = update_config(
            {"path": "trust_layer.trust.input_rules.blocked_phrases"},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_block_section_form_writes_multiple_values(self) -> None:
        """Block/section/values form writes all keys from values dict."""
        intake = _make_intake()
        acc = _empty_accumulator()
        fs = {"trust_layer.trust.input_rules.blocked_phrases": "pending"}

        result = update_config(
            {
                "block": "trust_layer",
                "section": "trust.input_rules",
                "values": {"blocked_phrases": ["spam"]},
            },
            intake,
            acc,
            fs,
        )

        assert result.get("ok") is True
        assert "results" in result

    def test_block_form_missing_block_returns_error(self) -> None:
        """Block/section/values without block returns ok=False."""
        result = update_config(
            {"section": "trust", "values": {}},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_invalid_path_returns_error(self) -> None:
        """Updating an unknown path returns ok=False (no crash)."""
        result = update_config(
            {"path": "agent_core.nonexistent.field", "value": "x"},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# 3. add_subagent
# ---------------------------------------------------------------------------


class TestAddSubagent:
    """Tests for the add_subagent tool."""

    def test_normal_appends_subagent(self) -> None:
        """Valid definition is appended to agent_workflow.subagents."""
        acc = _empty_accumulator()
        defn = {
            "id": "greeting",
            "name": "Greeting",
            "description": "Greets the user",
            "is_start": True,
        }

        result = add_subagent({"definition": defn}, _make_intake(), acc, _empty_field_status())

        assert result.get("ok") is True
        assert result["id"] == "greeting"
        subagents = acc["agent_core"]["agent_workflow"]["subagents"]
        assert any(sa["id"] == "greeting" for sa in subagents)

    def test_missing_definition_returns_error(self) -> None:
        """No definition key returns ok=False."""
        result = add_subagent({}, _make_intake(), _empty_accumulator(), _empty_field_status())
        assert result["ok"] is False

    def test_missing_id_in_definition_returns_error(self) -> None:
        """Definition without id returns ok=False."""
        result = add_subagent(
            {"definition": {"name": "no-id"}},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "id" in result["error"]

    def test_duplicate_id_returns_error(self) -> None:
        """Adding a subagent with an existing id returns ok=False."""
        acc = _empty_accumulator()
        defn = {"id": "dup", "name": "Dup"}
        add_subagent({"definition": defn}, _make_intake(), acc, _empty_field_status())

        result = add_subagent({"definition": defn}, _make_intake(), acc, _empty_field_status())
        assert result["ok"] is False
        assert "already exists" in result["error"]


# ---------------------------------------------------------------------------
# 4. update_subagent
# ---------------------------------------------------------------------------


class TestUpdateSubagent:
    """Tests for the update_subagent tool."""

    def _acc_with_subagent(self, subagent_id: str = "worker") -> dict[str, dict]:
        """Return an accumulator pre-populated with one subagent."""
        acc = _empty_accumulator()
        acc["agent_core"].setdefault("agent_workflow", {}).setdefault("subagents", []).append(
            {"id": subagent_id, "name": "Worker", "description": "does work"}
        )
        return acc

    def test_normal_updates_fields(self) -> None:
        """Valid fields are merged into the target subagent."""
        acc = self._acc_with_subagent("worker")

        result = update_subagent(
            {"id": "worker", "fields": {"description": "updated description"}},
            _make_intake(),
            acc,
            _empty_field_status(),
        )

        assert result.get("ok") is True
        subagents = acc["agent_core"]["agent_workflow"]["subagents"]
        worker = next(sa for sa in subagents if sa["id"] == "worker")
        assert worker["description"] == "updated description"

    def test_not_found_returns_error(self) -> None:
        """update_subagent on a missing id returns ok=False."""
        acc = self._acc_with_subagent("worker")

        result = update_subagent(
            {"id": "missing", "fields": {}},
            _make_intake(),
            acc,
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_missing_id_returns_error(self) -> None:
        """update_subagent without id returns ok=False."""
        result = update_subagent(
            {"fields": {"name": "x"}},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_missing_fields_returns_error(self) -> None:
        """update_subagent without fields dict returns ok=False."""
        result = update_subagent(
            {"id": "worker"},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# 5. add_routing_rule
# ---------------------------------------------------------------------------


class TestAddRoutingRule:
    """Tests for the add_routing_rule tool."""

    def _acc_with_subagent(self, *ids: str) -> dict[str, dict]:
        acc = _empty_accumulator()
        subagents = acc["agent_core"].setdefault("agent_workflow", {}).setdefault("subagents", [])
        for sid in ids:
            subagents.append({"id": sid, "routing": []})
        return acc

    def test_normal_appends_rule(self) -> None:
        """Valid routing rule is appended to the from-subagent's routing list.

        The tool writes the field names declared by the mirror schema
        (`next_subagent_id`, not `to`) so the result passes the mirror's
        `extra="forbid"` check at write time. Earlier versions wrote `to`
        and `condition` and silently slipped through because validation
        was not wired on this tool.
        """
        acc = self._acc_with_subagent("intro", "main")

        result = add_routing_rule(
            {
                "from_subagent_id": "intro",
                "intent": "greet",
                "to_subagent_id": "main",
            },
            _make_intake(),
            acc,
            _empty_field_status(),
        )

        assert result.get("ok") is True
        subagents = acc["agent_core"]["agent_workflow"]["subagents"]
        intro = next(sa for sa in subagents if sa["id"] == "intro")
        assert len(intro["routing"]) == 1
        rule = intro["routing"][0]
        assert rule["intent"] == "greet"
        assert rule["next_subagent_id"] == "main"

    def test_with_condition_appended(self) -> None:
        """Optional condition field is included in the rule when provided.

        Schema declares `conditions: list[RoutingCondition]` — the tool
        wraps a single condition dict into a one-element list so callers
        can pass either shape.
        """
        acc = self._acc_with_subagent("a", "b")
        result = add_routing_rule(
            {
                "from_subagent_id": "a",
                "intent": "confirm",
                "to_subagent_id": "b",
                "condition": {
                    "field": "slot.confirmed",
                    "operator": "eq",
                    "value": True,
                },
            },
            _make_intake(),
            acc,
            _empty_field_status(),
        )
        assert result["ok"] is True
        rule = acc["agent_core"]["agent_workflow"]["subagents"][0]["routing"][0]
        assert rule["conditions"] == [
            {"field": "slot.confirmed", "operator": "eq", "value": True}
        ]

    def test_source_not_found_returns_error(self) -> None:
        """add_routing_rule with unknown from_subagent_id returns ok=False."""
        acc = self._acc_with_subagent("worker")
        result = add_routing_rule(
            {"from_subagent_id": "nonexistent", "intent": "x", "to_subagent_id": "worker"},
            _make_intake(),
            acc,
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_missing_required_args_returns_error(self) -> None:
        """Missing from_subagent_id returns ok=False."""
        result = add_routing_rule(
            {"intent": "x", "to_subagent_id": "y"},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# 6. add_tool
# ---------------------------------------------------------------------------


class TestAddTool:
    """Tests for the add_tool tool."""

    def _rest_spec(self, tool_id: str = "my_api") -> dict:
        return {
            "id": tool_id,
            "type": "rest_api",
            "category": "read",
            "description": "Looks up items",
            "base_url": "https://api.example.com/v1",
            "auth": {"type": "none"},
            "endpoints": [
                {
                    "name": "search",
                    "method": "GET",
                    "path": "/search",
                    "params": [
                        {
                            "name": "query",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "search query",
                        }
                    ],
                }
            ],
        }

    def test_normal_adds_to_action_gateway(self) -> None:
        """Valid spec is appended to action_gateway.tools."""
        acc = _empty_accumulator()
        result = add_tool({"spec": self._rest_spec()}, _make_intake(), acc, _empty_field_status())

        assert result.get("ok") is True
        assert result["id"] == "my_api"
        assert any(t["id"] == "my_api" for t in acc["action_gateway"].get("tools", []))

    def test_normal_syncs_agent_core_connector(self) -> None:
        """REST tool also writes a connector to agent_core.connectors.read."""
        acc = _empty_accumulator()
        add_tool({"spec": self._rest_spec()}, _make_intake(), acc, _empty_field_status())

        connectors = acc["agent_core"].get("connectors", {}).get("read", [])
        assert any(c["name"] == "my_api" for c in connectors)

    def test_mcp_tool_no_connector(self) -> None:
        """MCP tool does not create an agent_core connector.

        The mirror's ``shape_matches_type`` validator requires both
        ``server_url`` and ``transport`` on MCP tools; add_tool now
        strictly validates the spec, so the test data must include
        both (transport was missing before — earlier partial
        validation silently dropped the "missing field" error).
        """
        acc = _empty_accumulator()
        mcp_spec = {
            "id": "my_mcp",
            "type": "mcp",
            "category": "read",
            "description": "MCP server",
            "server_url": "https://mcp.example.com",
            "transport": "sse",
        }
        result = add_tool({"spec": mcp_spec}, _make_intake(), acc, _empty_field_status())

        assert result.get("ok") is True
        connectors = acc["agent_core"].get("connectors", {})
        read = connectors.get("read", [])
        assert not any(c.get("name") == "my_mcp" for c in read)

    def test_missing_spec_returns_error(self) -> None:
        """No spec key returns ok=False."""
        result = add_tool({}, _make_intake(), _empty_accumulator(), _empty_field_status())
        assert result["ok"] is False

    def test_missing_id_in_spec_returns_error(self) -> None:
        """spec without id returns ok=False."""
        result = add_tool(
            {"spec": {"type": "rest_api", "category": "read"}},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "id" in result["error"]

    def test_duplicate_id_returns_error(self) -> None:
        """Adding the same tool id twice returns ok=False on second call."""
        acc = _empty_accumulator()
        spec = self._rest_spec("api_v1")
        add_tool({"spec": spec}, _make_intake(), acc, _empty_field_status())

        result = add_tool({"spec": spec}, _make_intake(), acc, _empty_field_status())
        assert result["ok"] is False
        assert "already exists" in result["error"]


# ---------------------------------------------------------------------------
# 7. parse_openapi_spec
# ---------------------------------------------------------------------------


class TestParseOpenApiSpec:
    """Tests for the parse_openapi_spec utility tool."""

    _MINIMAL_SPEC = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/search": {
                "get": {
                    "summary": "Search items",
                    "operationId": "searchItems",
                    "parameters": [],
                }
            }
        },
        "servers": [{"url": "https://api.example.com"}],
    }

    def test_normal_dict_spec_returns_operations(self) -> None:
        """Dict spec returns ok=True with at least one operation."""
        result = parse_openapi_spec(
            {"spec": self._MINIMAL_SPEC},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )

        assert result.get("ok") is True
        assert "operations" in result
        assert len(result["operations"]) >= 1
        first = result["operations"][0]
        # Discovery keys are now underscore-prefixed (see
        # parse_openapi_spec docstring) so the LLM doesn't conflate
        # them with the add_tool spec shape.
        assert "_discovery_id" in first
        assert "_path" in first
        assert "_method" in first

    def test_json_string_spec_returns_operations(self) -> None:
        """JSON string spec is parsed and returns operations."""
        import json

        result = parse_openapi_spec(
            {"spec": json.dumps(self._MINIMAL_SPEC)},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result.get("ok") is True

    def test_missing_spec_returns_error(self) -> None:
        """No spec key returns ok=False."""
        result = parse_openapi_spec(
            {},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_spec_without_paths_returns_error(self) -> None:
        """Spec dict missing 'paths' returns ok=False."""
        result = parse_openapi_spec(
            {"spec": {"openapi": "3.0.0"}},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_invalid_type_returns_error(self) -> None:
        """spec as an integer returns ok=False."""
        result = parse_openapi_spec(
            {"spec": 42},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False

    def test_does_not_mutate_accumulator(self) -> None:
        """parse_openapi_spec never mutates the accumulator."""
        acc = _empty_accumulator()
        acc_before = json.loads(json.dumps(acc))

        parse_openapi_spec(
            {"spec": self._MINIMAL_SPEC},
            _make_intake(),
            acc,
            _empty_field_status(),
        )

        assert acc == acc_before


# ---------------------------------------------------------------------------
# 8. discover_mcp_tools
# ---------------------------------------------------------------------------


class TestDiscoverMcpTools:
    """Tests for the discover_mcp_tools utility tool.

    Detailed JSON-RPC / SSE / error-path coverage lives in the module-level
    `test_discover_mcp_tools_*` functions below (they share the
    `_FakeHttpxResponse` helper). The cases here cover the argument-
    validation and accumulator-immutability invariants that hold regardless
    of network behavior.
    """

    def test_missing_server_url_returns_error(self) -> None:
        """Missing server_url returns ok=False without touching the network."""
        result = discover_mcp_tools(
            {},
            _make_intake(),
            _empty_accumulator(),
            _empty_field_status(),
        )
        assert result["ok"] is False
        assert "server_url" in result["error"]

    def test_does_not_mutate_accumulator(self, monkeypatch) -> None:
        """discover_mcp_tools never mutates the accumulator, even on success."""
        import httpx

        def _fake_post(url, *, json, headers, timeout):  # noqa: A002
            return _FakeHttpxResponse(
                status_code=200,
                json_payload={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
            )

        _patch_httpx_post(monkeypatch, _fake_post)

        acc = _empty_accumulator()
        acc_before = json.loads(json.dumps(acc))

        discover_mcp_tools(
            {"server_url": "https://mcp.example.com"},
            _make_intake(),
            acc,
            _empty_field_status(),
        )

        assert acc == acc_before


# ---------------------------------------------------------------------------
# Integration — phase_driver routes each tool through run_turn
# ---------------------------------------------------------------------------


def test_phase_driver_routes_update_intake(tmp_path: Path) -> None:
    """update_intake in a tool call from run_turn mutates persisted IntakeState."""
    projects_root = _setup_project(tmp_path)

    fake = _fake_llm(
        tool_calls=[ToolCall("update_intake", {"field": "has_kb", "value": True})]
    )
    run_turn(
        user_message="yes",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    saved = json.loads((projects_root / "demo" / "_meta" / "intake_state.json").read_text())
    assert saved["has_kb"] is True


def test_phase_driver_routes_update_config(tmp_path: Path) -> None:
    """update_config in a tool call from run_turn writes to accumulator."""
    intake = _make_intake()
    projects_root = _setup_project(
        tmp_path,
        intake=intake,
        field_status={"trust_layer.trust.input_rules.blocked_phrases": "pending"},
        current_phase="trust",
    )

    fake = _fake_llm(
        tool_calls=[
            ToolCall(
                "update_config",
                {
                    "path": "trust_layer.trust.input_rules.blocked_phrases",
                    "value": ["spam"],
                },
            )
        ]
    )
    run_turn(
        user_message="ok",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )

    acc = load_accumulator(projects_root / "demo")
    assert acc["trust_layer"]["trust"]["input_rules"]["blocked_phrases"] == ["spam"]


def test_phase_driver_skips_unknown_tool(tmp_path: Path) -> None:
    """An unknown tool name is logged and skipped — run_turn does not crash."""
    projects_root = _setup_project(tmp_path)

    fake = _fake_llm(
        tool_calls=[ToolCall("nonexistent_tool_xyz", {"foo": "bar"})]
    )
    result = run_turn(
        user_message="ok",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )
    assert result == "ok"


@pytest.mark.parametrize("tool_name", sorted(_TOOL_NAMES))
def test_phase_driver_routes_each_tool_no_crash(tmp_path: Path, tool_name: str) -> None:
    """Each tool name in TOOL_HANDLERS can be routed through run_turn without crashing.

    We pass empty/minimal args so the handler returns ok=False for most tools
    (missing required args) — but the point is no unhandled exception escapes
    run_turn, since all handlers return structured errors rather than raising.
    """
    projects_root = _setup_project(tmp_path)

    fake = _fake_llm(tool_calls=[ToolCall(tool_name, {})])
    # Should not raise.
    result = run_turn(
        user_message="test",
        project_slug="demo",
        projects_root=projects_root,
        llm_call=fake,
    )
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# discover_mcp_tools — real JSON-RPC + SSE discovery
# ---------------------------------------------------------------------------


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response used in monkeypatched tests."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_payload: dict | None = None,
        text: str = "",
        json_raises: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text
        self._json_raises = json_raises

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> dict:
        if self._json_raises or self._json_payload is None:
            import json as _json
            raise _json.JSONDecodeError("not json", "", 0)
        return self._json_payload


def test_discover_mcp_tools_missing_server_url() -> None:
    """server_url is required."""
    out = discover_mcp_tools({}, _make_intake(), _empty_accumulator(), _empty_field_status())
    assert out == {"ok": False, "error": "args.server_url is required"}


def test_discover_mcp_tools_plain_jsonrpc_response(monkeypatch) -> None:
    """JSON-RPC `result.tools` parsed into the canonical {name, description,
    input_schema} shape."""
    import httpx

    captured: dict = {}

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002 — match httpx.post signature
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeHttpxResponse(
            status_code=200,
            json_payload={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "searchDocs",
                            "description": "Search documentation.",
                            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                        },
                        {
                            "name": "listRepos",
                            "description": "List repos.",
                            "inputSchema": {"type": "object"},
                        },
                    ]
                },
            },
        )

    _patch_httpx_post(monkeypatch, _fake_post)

    out = discover_mcp_tools(
        {"server_url": "https://mcp.example.com/rpc/"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is True
    assert out["server_url"] == "https://mcp.example.com/rpc"  # trailing slash stripped
    assert out["tools"] == [
        {
            "name": "searchDocs",
            "description": "Search documentation.",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        {
            "name": "listRepos",
            "description": "List repos.",
            "input_schema": {"type": "object"},
        },
    ]
    # The wizard sent the canonical tools/list payload.
    assert captured["json"] == {"jsonrpc": "2.0", "method": "tools/list", "id": 1}


def test_discover_mcp_tools_sse_response(monkeypatch) -> None:
    """SSE `data: <json>` line is auto-detected when response.json() fails."""
    import httpx

    sse_text = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, '
        '"result": {"tools": [{"name": "ping", "description": "Ping the server."}]}}\n'
        "\n"
    )

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        return _FakeHttpxResponse(
            status_code=200,
            json_raises=True,
            text=sse_text,
        )

    _patch_httpx_post(monkeypatch, _fake_post)

    out = discover_mcp_tools(
        {"server_url": "https://mcp.example.com/sse"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is True
    assert out["tools"] == [
        {"name": "ping", "description": "Ping the server.", "input_schema": {}}
    ]


def test_discover_mcp_tools_network_error_returns_structured_error(monkeypatch) -> None:
    """httpx connection failure surfaces as a tool_result error (no crash)."""
    import httpx

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        raise httpx.ConnectError("Connection refused")

    _patch_httpx_post(monkeypatch, _fake_post)

    out = discover_mcp_tools(
        {"server_url": "https://unreachable.example.com"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is False
    assert "unreachable.example.com" in out["error"]
    assert "could not reach" in out["error"].lower()


def test_discover_mcp_tools_unparseable_response_returns_error(monkeypatch) -> None:
    """A non-JSON, non-SSE response is reported with a preview, not silently
    treated as zero tools."""
    import httpx

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        return _FakeHttpxResponse(
            status_code=200,
            json_raises=True,
            text="<html><body>not an MCP server</body></html>",
        )

    _patch_httpx_post(monkeypatch, _fake_post)

    out = discover_mcp_tools(
        {"server_url": "https://not-an-mcp.example.com"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is False
    assert "unrecognised response format" in out["error"]


# ---------------------------------------------------------------------------
# fetch_openapi_spec_from_url
# ---------------------------------------------------------------------------


class _FakeHttpxClient:
    """Stand-in for httpx.Client used in the fetch_openapi_spec_from_url tests."""

    def __init__(self, response: _FakeHttpxResponse) -> None:
        self._response = response

    def __enter__(self) -> "_FakeHttpxClient":
        return self

    def __exit__(self, *args) -> None:
        pass

    def get(self, url: str) -> _FakeHttpxResponse:
        self._response_url = url
        return self._response


def _patch_httpx_client(monkeypatch, response: _FakeHttpxResponse) -> None:
    import httpx

    def _fake_client(*args, **kwargs):
        return _FakeHttpxClient(response)

    monkeypatch.setattr(httpx, "Client", _fake_client)


def _patch_httpx_post(monkeypatch, fake_post) -> None:
    """Patch ``httpx.Client(...).post(...)`` to delegate to ``fake_post``.

    Used by ``discover_mcp_tools`` tests now that production code calls
    through a Client (so retries=1 transport applies) rather than the
    legacy module-level ``httpx.post``. The legacy
    ``monkeypatch.setattr(httpx, "post", ...)`` pattern no longer
    intercepts. ``fake_post`` keeps the same signature (url, json=...,
    headers=..., timeout=...) so individual tests don't need to change
    their fake-response construction.
    """
    import httpx

    class _ClientForPost:
        def __init__(self, *args, **kwargs):
            self._kwargs = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            # Forward the Client-level timeout if the call didn't supply one
            kwargs.setdefault("timeout", self._kwargs.get("timeout"))
            return fake_post(url, **kwargs)

    monkeypatch.setattr(httpx, "Client", _ClientForPost)


def test_fetch_openapi_spec_from_url_missing_url() -> None:
    out = fetch_openapi_spec_from_url(
        {}, _make_intake(), _empty_accumulator(), _empty_field_status()
    )
    assert out["ok"] is False
    assert "args.url" in out["error"]


def test_fetch_openapi_spec_from_url_parses_remote_json(monkeypatch) -> None:
    """A JSON OpenAPI 3.0 document fetched from a URL produces the same
    `operations` shape as parse_openapi_spec."""
    spec_text = """{
        "openapi": "3.0.0",
        "info": {"title": "Demo", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "summary": "List all pets",
                    "responses": {"200": {"description": "OK"}}
                }
            }
        }
    }"""
    _patch_httpx_client(
        monkeypatch,
        _FakeHttpxResponse(status_code=200, text=spec_text),
    )

    out = fetch_openapi_spec_from_url(
        {"url": "https://api.example.com/openapi.json"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is True
    assert out["source_url"] == "https://api.example.com/openapi.json"
    assert len(out["operations"]) == 1
    op = out["operations"][0]
    # Discovery keys are underscore-prefixed (see parse_openapi_spec).
    assert op["_path"] == "/pets"
    assert op["_method"].upper() == "GET"
    assert op["_summary"] == "List all pets"


def test_fetch_openapi_spec_from_url_parses_remote_yaml(monkeypatch) -> None:
    """YAML OpenAPI fallback when JSON.parse fails."""
    spec_text = """openapi: 3.0.0
info:
  title: Demo
  version: 1.0.0
servers:
  - url: https://api.example.com
paths:
  /pets:
    get:
      operationId: listPets
      summary: List all pets
      responses:
        '200':
          description: OK
"""
    _patch_httpx_client(
        monkeypatch,
        _FakeHttpxResponse(status_code=200, text=spec_text),
    )

    out = fetch_openapi_spec_from_url(
        {"url": "https://api.example.com/openapi.yaml"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )

    assert out["ok"] is True
    assert len(out["operations"]) == 1
    assert out["operations"][0]["_path"] == "/pets"


def test_fetch_openapi_spec_from_url_http_404(monkeypatch) -> None:
    """A 404 surfaces as a structured error, not a crash."""
    _patch_httpx_client(
        monkeypatch,
        _FakeHttpxResponse(status_code=404, text="Not Found"),
    )

    out = fetch_openapi_spec_from_url(
        {"url": "https://api.example.com/missing.json"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )
    assert out["ok"] is False
    assert "404" in out["error"]


def test_fetch_openapi_spec_from_url_non_dict_payload(monkeypatch) -> None:
    """If the URL returns a plain string or array (not an OpenAPI object),
    we report it rather than crashing in the parser."""
    _patch_httpx_client(
        monkeypatch,
        _FakeHttpxResponse(status_code=200, text='"just a string"'),
    )

    out = fetch_openapi_spec_from_url(
        {"url": "https://api.example.com/odd.json"},
        _make_intake(),
        _empty_accumulator(),
        _empty_field_status(),
    )
    assert out["ok"] is False
    assert "not a JSON/YAML object" in out["error"]
