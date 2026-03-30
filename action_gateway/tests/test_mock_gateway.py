"""
action_gateway/tests/test_mock_gateway.py

Tests for MockActionGateway.

All HTTP calls to the mock ONEST server are intercepted via httpx.MockTransport
so no real network is needed.
"""

from __future__ import annotations

import json
import pytest
import httpx
from unittest.mock import patch, MagicMock

from src.mock_gateway import MockActionGateway


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "action_gateway": {
        "connectors": {
            "onest_market_lookup": {
                "endpoint": "http://localhost:9999/onest/market_lookup",
                "timeout_ms": 5000,
            }
        }
    }
}

_ELECTRICIAN_RESPONSE = {
    "trade": "electrician",
    "salary_range": "₹15k–₹28k",
    "market_signal": "steady signal 12% QoQ",
    "top_employers": ["Hubli Distribution Co", "Karnataka Power"],
    "source": "ONEST",
    "location_queried": "Hubli",
}


def _ok_transport(payload: dict) -> httpx.MockTransport:
    """Returns a transport that always responds 200 with given JSON."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return httpx.MockTransport(handler)


def _error_transport(status_code: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_raises_if_config_is_none(self) -> None:
        with pytest.raises(ValueError, match="config must not be None"):
            MockActionGateway(None)

    def test_uses_default_endpoint_when_not_configured(self) -> None:
        gw = MockActionGateway({})
        assert "9999" in gw._onest_endpoint

    def test_reads_endpoint_from_config(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        assert gw._onest_endpoint == "http://localhost:9999/onest/market_lookup"

    def test_reads_timeout_from_config(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        assert gw._timeout_s == 5.0


# ---------------------------------------------------------------------------
# list_available_tools
# ---------------------------------------------------------------------------

class TestListAvailableTools:
    def test_returns_list(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        tools = gw.list_available_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 2  # onest_market_lookup + onest_apply

    def test_tool_names_include_market_lookup_and_apply(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        names = {t["name"] for t in gw.list_available_tools()}
        assert "onest_market_lookup" in names
        assert "onest_apply" in names

    def test_tool_has_required_input_schema_field(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        tools_by_name = {t["name"]: t for t in gw.list_available_tools()}
        tool = tools_by_name["onest_market_lookup"]
        assert "input_schema" in tool
        assert "trade" in tool["input_schema"]["properties"]

    def test_returns_copy_not_reference(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        t1 = gw.list_available_tools()
        t2 = gw.list_available_tools()
        assert t1 is not t2


# ---------------------------------------------------------------------------
# execute — input validation
# ---------------------------------------------------------------------------

class TestExecuteValidation:
    def test_raises_if_tool_call_is_none(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with pytest.raises(ValueError, match="tool_call must not be None"):
            gw.execute(None, "session-1")

    def test_raises_if_session_id_is_none(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with pytest.raises(ValueError, match="session_id must not be None"):
            gw.execute({"tool_name": "onest_market_lookup", "tool_use_id": "id1", "input_params": {}}, None)


# ---------------------------------------------------------------------------
# execute — unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    def test_unknown_tool_returns_failure(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        result = gw.execute(
            {"tool_name": "does_not_exist", "tool_use_id": "x", "input_params": {}},
            "session-1",
        )
        assert result["success"] is False
        assert "unknown_tool" in result["error"]

    def test_unknown_tool_preserves_tool_use_id(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        result = gw.execute(
            {"tool_name": "does_not_exist", "tool_use_id": "abc-123", "input_params": {}},
            "session-1",
        )
        assert result["tool_use_id"] == "abc-123"


# ---------------------------------------------------------------------------
# execute — onest_market_lookup success
# ---------------------------------------------------------------------------

class TestOnestLookupSuccess:
    def test_success_result_has_expected_keys(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = _ELECTRICIAN_RESPONSE
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            result = gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-1",
                    "input_params": {"trade": "electrician", "location": "Hubli"},
                },
                "session-1",
            )

        assert result["success"] is True
        assert result["tool_use_id"] == "tu-1"
        assert result["tool_name"] == "onest_market_lookup"
        assert result["result"] == _ELECTRICIAN_RESPONSE
        assert result["error"] is None

    def test_default_distance_km_is_50(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        captured = {}

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = _ELECTRICIAN_RESPONSE
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-2",
                    "input_params": {"trade": "electrician"},
                },
                "session-1",
            )
            _, kwargs = mock_post.call_args
            captured["json"] = kwargs.get("json", mock_post.call_args[1].get("json", {}))

        assert captured["json"].get("distance_km") == 50


# ---------------------------------------------------------------------------
# execute — onest_market_lookup timeout
# ---------------------------------------------------------------------------

class TestOnestLookupTimeout:
    def test_timeout_returns_failure(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            result = gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-3",
                    "input_params": {"trade": "electrician"},
                },
                "session-1",
            )
        assert result["success"] is False
        assert "timeout" in result["error"]

    def test_timeout_preserves_tool_use_id(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            result = gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-timeout",
                    "input_params": {"trade": "electrician"},
                },
                "session-1",
            )
        assert result["tool_use_id"] == "tu-timeout"


# ---------------------------------------------------------------------------
# execute — onest_market_lookup HTTP error
# ---------------------------------------------------------------------------

class TestOnestLookupHTTPError:
    def test_http_500_returns_failure(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        http_error = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = http_error
            mock_post.return_value = mock_response

            result = gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-4",
                    "input_params": {"trade": "electrician"},
                },
                "session-1",
            )

        assert result["success"] is False
        assert "500" in result["error"]


# ---------------------------------------------------------------------------
# execute — onest_market_lookup generic error
# ---------------------------------------------------------------------------

class TestOnestLookupGenericError:
    def test_connection_error_returns_failure(self) -> None:
        gw = MockActionGateway(_BASE_CONFIG)
        with patch("httpx.post", side_effect=ConnectionError("refused")):
            result = gw.execute(
                {
                    "tool_name": "onest_market_lookup",
                    "tool_use_id": "tu-5",
                    "input_params": {"trade": "welder"},
                },
                "session-1",
            )
        assert result["success"] is False
        assert result["error"] is not None
