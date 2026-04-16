"""Tests for Action Gateway data models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    ExecuteRequest,
    ExecuteResponse,
    HealthResponse,
    ToolDefinition,
    ToolResult,
    ToolsResponse,
)


class TestToolDefinition:
    """Tests for ToolDefinition model."""

    def test_valid_read_tool(self):
        tool = ToolDefinition(
            name="get_account_balance",
            description="Retrieve account balance for a user.",
            input_schema={"type": "object", "properties": {"account_id": {"type": "string"}}},
            category="read",
        )
        assert tool.name == "get_account_balance"
        assert tool.category == "read"

    def test_valid_write_tool(self):
        tool = ToolDefinition(
            name="update_profile",
            description="Update user profile information.",
            input_schema={},
            category="write",
        )
        assert tool.category == "write"

    def test_valid_identity_tool(self):
        tool = ToolDefinition(
            name="verify_kyc",
            description="Verify user KYC status.",
            input_schema={},
            category="identity",
        )
        assert tool.category == "identity"

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ToolDefinition(
                name="",
                description="Some description.",
                input_schema={},
                category="read",
            )
        assert "name" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()

    def test_whitespace_only_name_raises(self):
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="   ",
                description="Some description.",
                input_schema={},
                category="read",
            )

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="my_tool",
                description="",
                input_schema={},
                category="read",
            )

    def test_whitespace_only_description_raises(self):
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="my_tool",
                description="   ",
                input_schema={},
                category="read",
            )

    def test_invalid_category_raises(self):
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="my_tool",
                description="A tool.",
                input_schema={},
                category="unknown",
            )

    def test_serialization_round_trip(self):
        tool = ToolDefinition(
            name="search_products",
            description="Search for products in catalogue.",
            input_schema={"type": "object"},
            category="read",
        )
        data = tool.model_dump()
        restored = ToolDefinition(**data)
        assert restored == tool

    def test_empty_input_schema_allowed(self):
        tool = ToolDefinition(
            name="ping",
            description="Ping the service.",
            input_schema={},
            category="read",
        )
        assert tool.input_schema == {}


class TestToolResult:
    """Tests for ToolResult model."""

    def test_valid_success_result(self):
        result = ToolResult(
            tool_use_id="tu_123",
            tool_name="get_balance",
            result={"balance": 1000},
            success=True,
            result_text="Balance is 1000.",
        )
        assert result.success is True
        assert result.error is None

    def test_valid_failure_result(self):
        result = ToolResult(
            tool_use_id="tu_456",
            tool_name="get_balance",
            result={},
            success=False,
            error="Service unavailable",
        )
        assert result.success is False
        assert result.error == "Service unavailable"

    def test_default_result_text(self):
        result = ToolResult(
            tool_use_id="tu_789",
            tool_name="ping",
            result={},
            success=True,
        )
        assert result.result_text == ""

    def test_default_error_is_none(self):
        result = ToolResult(
            tool_use_id="tu_789",
            tool_name="ping",
            result={},
            success=True,
        )
        assert result.error is None

    def test_serialization_round_trip(self):
        result = ToolResult(
            tool_use_id="tu_001",
            tool_name="lookup",
            result={"key": "value"},
            success=True,
            result_text="Found it.",
            error=None,
        )
        data = result.model_dump()
        restored = ToolResult(**data)
        assert restored == result


class TestExecuteRequest:
    """Tests for ExecuteRequest model."""

    def test_valid_request(self):
        req = ExecuteRequest(
            tool_name="get_balance",
            tool_use_id="tu_001",
            input_params={"account_id": "acc_123"},
            session_id="session_abc",
        )
        assert req.tool_name == "get_balance"
        assert req.session_id == "session_abc"

    def test_default_session_id(self):
        req = ExecuteRequest(
            tool_name="get_balance",
            tool_use_id="tu_001",
            input_params={},
        )
        assert req.session_id == ""

    def test_empty_input_params_allowed(self):
        req = ExecuteRequest(
            tool_name="ping",
            tool_use_id="tu_002",
            input_params={},
        )
        assert req.input_params == {}

    def test_serialization_round_trip(self):
        req = ExecuteRequest(
            tool_name="search",
            tool_use_id="tu_003",
            input_params={"q": "test"},
            session_id="sess_1",
        )
        data = req.model_dump()
        restored = ExecuteRequest(**data)
        assert restored == req


class TestExecuteResponse:
    """Tests for ExecuteResponse model."""

    def test_valid_success_response(self):
        resp = ExecuteResponse(
            tool_use_id="tu_001",
            tool_name="get_balance",
            success=True,
            result={"balance": 500},
            result_text="Balance retrieved.",
        )
        assert resp.success is True
        assert resp.error is None

    def test_valid_failure_response(self):
        resp = ExecuteResponse(
            tool_use_id="tu_002",
            tool_name="get_balance",
            success=False,
            result={},
            error="Timeout",
        )
        assert resp.success is False
        assert resp.error == "Timeout"

    def test_default_result_text(self):
        resp = ExecuteResponse(
            tool_use_id="tu_003",
            tool_name="ping",
            success=True,
            result={},
        )
        assert resp.result_text == ""

    def test_default_error_none(self):
        resp = ExecuteResponse(
            tool_use_id="tu_003",
            tool_name="ping",
            success=True,
            result={},
        )
        assert resp.error is None

    def test_serialization_round_trip(self):
        resp = ExecuteResponse(
            tool_use_id="tu_004",
            tool_name="lookup",
            success=True,
            result={"data": 42},
            result_text="Done.",
            error=None,
        )
        data = resp.model_dump()
        restored = ExecuteResponse(**data)
        assert restored == resp


class TestToolsResponse:
    """Tests for ToolsResponse model."""

    def test_valid_tools_list(self):
        tools = [
            ToolDefinition(
                name="tool_a",
                description="Tool A description.",
                input_schema={},
                category="read",
            ),
            ToolDefinition(
                name="tool_b",
                description="Tool B description.",
                input_schema={},
                category="write",
            ),
        ]
        resp = ToolsResponse(tools=tools)
        assert len(resp.tools) == 2

    def test_empty_tools_list(self):
        resp = ToolsResponse(tools=[])
        assert resp.tools == []

    def test_serialization_round_trip(self):
        tool = ToolDefinition(
            name="my_tool",
            description="Does something useful.",
            input_schema={"type": "object"},
            category="identity",
        )
        resp = ToolsResponse(tools=[tool])
        data = resp.model_dump()
        restored = ToolsResponse(**data)
        assert restored == resp


class TestHealthResponse:
    """Tests for HealthResponse model."""

    def test_valid_health_response(self):
        resp = HealthResponse(
            status="healthy",
            adapters={"onest": True, "crm": False},
        )
        assert resp.status == "healthy"
        assert resp.adapters["onest"] is True
        assert resp.adapters["crm"] is False

    def test_empty_adapters(self):
        resp = HealthResponse(status="degraded", adapters={})
        assert resp.adapters == {}

    def test_serialization_round_trip(self):
        resp = HealthResponse(
            status="healthy",
            adapters={"connector_x": True},
        )
        data = resp.model_dump()
        restored = HealthResponse(**data)
        assert restored == resp
