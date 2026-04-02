"""
agent_core/tests/test_http_clients.py

Unit tests for all five HTTP client wrappers in src/http_clients/.
All httpx network calls are mocked — no real HTTP traffic.

Clients under test:
  - MemoryLayerHttpClient   (src/http_clients/memory_layer.py)
  - TrustLayerHttpClient    (src/http_clients/trust_layer.py)
  - LearningLayerHttpClient (src/http_clients/learning_layer.py)
  - HttpKnowledgeEngineClient (src/http_clients/knowledge_engine.py)
  - ActionGatewayHttpClient (src/http_clients/action_gateway.py)

Coverage:
  - Normal: successful HTTP response mapped to correct return type
  - Failure: TimeoutException → safe fallback returned, no raise
  - Failure: HTTPStatusError → safe fallback returned, no raise
  - Failure: generic Exception → safe fallback returned, no raise
  - Init: None config raises ValueError
  - Validation: None required param raises ValueError
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
import httpx

from src.http_clients.memory_layer import MemoryLayerHttpClient
from src.http_clients.trust_layer import TrustLayerHttpClient
from src.http_clients.learning_layer import LearningLayerHttpClient
from src.http_clients.knowledge_engine import HttpKnowledgeEngineClient
from src.http_clients.action_gateway import ActionGatewayHttpClient
from src.models import ContextBundle, TrustCheckResult, TurnEvent, ToolCall, ToolResult, RetrievalChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "memory_client": {"endpoint": "http://localhost:8002", "timeout_ms": 3000},
    "trust_client": {"endpoint": "http://localhost:8003", "timeout_ms": 2000},
    "learning_client": {"endpoint": "http://localhost:8004", "timeout_ms": 2000},
    "ke_client": {"endpoint": "http://localhost:8001/retrieve", "timeout_ms": 8000},
    "action_gateway_client": {"endpoint": "http://localhost:9999/onest/market_lookup", "timeout_ms": 5000},
    "connectors": {
        "read": [
            {
                "name": "onest_market_lookup",
                "description": "Search ONEST live job market data by trade and location.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "trade": {"type": "string", "description": "Trade to search for"},
                        "location": {"type": "string", "description": "City or district"},
                    },
                    "required": ["trade"],
                },
            }
        ],
        "write": [],
        "identity": [],
    },
}


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx Response object."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_http_error(status_code: int = 500) -> httpx.HTTPStatusError:
    """Build a mock httpx.HTTPStatusError."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    return httpx.HTTPStatusError("error", request=MagicMock(), response=resp)


# ===========================================================================
# MemoryLayerHttpClient — init only (full interface tested in test_memory_http_client.py)
# ===========================================================================

class TestMemoryLayerHttpClientInit:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="config must not be None"):
            MemoryLayerHttpClient(None)

    def test_defaults_used_when_keys_absent(self):
        client = MemoryLayerHttpClient({})
        assert client._endpoint == "http://localhost:8002"

    def test_endpoint_read_from_config(self):
        client = MemoryLayerHttpClient(_BASE_CONFIG)
        assert "8002" in client._endpoint

    def test_timeout_converted_to_seconds(self):
        client = MemoryLayerHttpClient(_BASE_CONFIG)
        assert client._timeout_s == pytest.approx(3.0)


# ===========================================================================
# TrustLayerHttpClient
# ===========================================================================

class TestTrustLayerHttpClientInit:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="config must not be None"):
            TrustLayerHttpClient(None)


class TestTrustCheckInput:
    def test_success_returns_trust_check_result(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"passed": True, "action": "allow", "reason": None})):
            result = client.check_input("s1", "hello")
        assert isinstance(result, TrustCheckResult)
        assert result.passed is True
        assert result.action == "allow"

    def test_blocked_response_returned_correctly(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"passed": False, "action": "block", "reason": "harmful"})):
            result = client.check_input("s1", "bad input")
        assert result.passed is False
        assert result.action == "block"

    def test_timeout_fails_open(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            result = client.check_input("s1", "hello")
        assert result.passed is True
        assert result.action == "allow"

    def test_http_error_fails_open(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=_mock_http_error(500)):
            result = client.check_input("s1", "hello")
        assert result.passed is True

    def test_generic_error_fails_open(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            result = client.check_input("s1", "hello")
        assert result.passed is True

    def test_none_session_id_raises(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with pytest.raises(ValueError):
            client.check_input(None, "hello")


class TestTrustCheckOutput:
    def test_success_returns_allow(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"passed": True, "action": "allow", "reason": None})):
            result = client.check_output("s1", "Good response.")
        assert result.passed is True

    def test_timeout_fails_open(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            result = client.check_output("s1", "response")
        assert result.passed is True

    def test_none_session_id_raises(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with pytest.raises(ValueError):
            client.check_output(None, "resp")


class TestTrustCheckConsent:
    def test_success_returns_bool(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"granted": True})):
            result = client.check_consent("s1", "job_apply")
        assert result is True

    def test_timeout_fails_open(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            result = client.check_consent("s1", "job_apply")
        assert result is True

    def test_none_session_id_raises(self):
        client = TrustLayerHttpClient(_BASE_CONFIG)
        with pytest.raises(ValueError):
            client.check_consent(None, "connector")


# ===========================================================================
# LearningLayerHttpClient
# ===========================================================================

class TestLearningLayerHttpClientInit:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="config must not be None"):
            LearningLayerHttpClient(None)


class TestLearningEmitTurn:
    def _make_event(self) -> dict:
        return {
            "session_id": "s1",
            "response_text": "response",
            "tool_calls": [],
            "trust_input_result": {"passed": True, "action": "allow", "reason": None},
            "trust_output_result": {"passed": True, "action": "allow", "reason": None},
            "model_used": "claude-haiku-4-5-20251001",
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 300,
            "timestamp_ms": 1700000000,
        }

    def test_success_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"status": "ok"})):
            client.emit_turn(self._make_event())

    def test_none_event_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        client.emit_turn(None)  # must not raise

    def test_timeout_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            client.emit_turn(self._make_event())

    def test_http_error_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=_mock_http_error(500)):
            client.emit_turn(self._make_event())

    def test_generic_exception_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            client.emit_turn(self._make_event())


class TestLearningEmitSignal:
    def test_success_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"status": "ok"})):
            client.emit_signal("drop_off", {"turn": 3})

    def test_none_signal_type_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        client.emit_signal(None, {})  # must not raise

    def test_timeout_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            client.emit_signal("test", {})

    def test_generic_exception_does_not_raise(self):
        client = LearningLayerHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            client.emit_signal("test", {})


# ===========================================================================
# HttpKnowledgeEngineClient
# ===========================================================================

class TestKEHttpClientInit:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="config must not be None"):
            HttpKnowledgeEngineClient(None)


class TestKERetrieve:
    """Tests for HttpKnowledgeEngineClient.retrieve() — new interface replacing assemble_prompt."""

    def test_success_returns_list_of_chunks(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({
            "session_id": "s1",
            "chunks": [
                {"text": "ITI centres in Hubli", "doc_type": "institute", "source": "training_institutes.csv", "always_include": False},
            ],
        })):
            chunks = client.retrieve(
                session_id="s1",
                user_message="ITI kahan hai",
                profile={},
                session={},
            )
        assert isinstance(chunks, list)
        assert len(chunks) == 1
        assert isinstance(chunks[0], RetrievalChunk)
        assert chunks[0].text == "ITI centres in Hubli"
        assert chunks[0].doc_type == "institute"

    def test_always_include_chunk_flag_preserved(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({
            "session_id": "s1",
            "chunks": [
                {"text": "Market framing", "doc_type": "always_include", "source": "", "always_include": True},
            ],
        })):
            chunks = client.retrieve("s1", "hello", {}, {})
        assert chunks[0].always_include is True

    def test_empty_user_message_returns_empty(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        chunks = client.retrieve("s1", "", {}, {})
        assert chunks == []

    def test_timeout_returns_empty_list(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            chunks = client.retrieve("s1", "hello", {}, {})
        assert chunks == []

    def test_http_error_returns_empty_list(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=_mock_http_error(503)):
            chunks = client.retrieve("s1", "hello", {}, {})
        assert chunks == []

    def test_generic_exception_returns_empty_list(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            chunks = client.retrieve("s1", "hello", {}, {})
        assert chunks == []

    def test_nlu_params_forwarded_in_payload(self):
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        captured = {}

        def mock_post(url, json=None, timeout=None):
            captured.update(json or {})
            return _mock_response({"session_id": "s1", "chunks": []})

        with patch("httpx.post", side_effect=mock_post):
            client.retrieve(
                session_id="s1",
                user_message="kaam chahiye",
                profile={"trade": "electrician"},
                session={"current_node": "market_truth"},
                intent="market_truth_query",
                entities={"location": "Hubli"},
                detected_language="hinglish",
            )
        assert captured["intent"] == "market_truth_query"
        assert captured["entities"] == {"location": "Hubli"}
        assert captured["detected_language"] == "hinglish"

    def test_non_list_chunks_response_returns_empty(self):
        """Malformed response with non-list chunks returns [] safely."""
        client = HttpKnowledgeEngineClient(_BASE_CONFIG)
        with patch("httpx.post", return_value=_mock_response({"session_id": "s1", "chunks": "not a list"})):
            chunks = client.retrieve("s1", "hello", {}, {})
        assert chunks == []


# ===========================================================================
# ActionGatewayHttpClient
# ===========================================================================

class TestActionGatewayHttpClientInit:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="config must not be None"):
            ActionGatewayHttpClient(None)


class TestActionGatewayListTools:
    def test_returns_list_of_dicts(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        tools = client.list_available_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 1
        assert "name" in tools[0]

    def test_onest_tool_present(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        names = [t["name"] for t in client.list_available_tools()]
        assert "onest_market_lookup" in names


class TestActionGatewayExecute:
    def _make_tool_call(self) -> ToolCall:
        return ToolCall(
            tool_name="onest_market_lookup",
            tool_use_id="tu_1",
            input_params={"trade": "electrician", "location": "Hubli"},
        )

    def _mock_client(self, mock_post_return=None, mock_post_side_effect=None):
        """Return a context-manager-compatible httpx.Client mock."""
        mock_client_cls = MagicMock()
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_client_cls.return_value.__exit__.return_value = False
        if mock_post_side_effect is not None:
            mock_http.post.side_effect = mock_post_side_effect
        else:
            mock_http.post.return_value = mock_post_return
        return mock_client_cls

    def test_success_returns_tool_result(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        inner_result = {"salary_range": "12000-18000", "market_signal": "growing"}
        gw_response = {"tool_use_id": "tu_1", "result": inner_result, "success": True, "result_text": ""}
        mock_cls = self._mock_client(mock_post_return=_mock_response(gw_response))
        with patch("httpx.Client", mock_cls):
            result = client.execute(self._make_tool_call(), "s1")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.result == inner_result

    def test_timeout_returns_failure_result(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        mock_cls = self._mock_client(mock_post_side_effect=httpx.TimeoutException("timeout"))
        with patch("httpx.Client", mock_cls):
            result = client.execute(self._make_tool_call(), "s1")
        assert result.success is False
        assert "timeout" in result.error.lower()

    def test_http_error_returns_failure_result(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=_mock_http_error(503)):
            result = client.execute(self._make_tool_call(), "s1")
        assert result.success is False

    def test_generic_exception_returns_failure_result(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            result = client.execute(self._make_tool_call(), "s1")
        assert result.success is False

    def test_unknown_tool_returns_failure_result(self):
        # Source sends any tool name to the gateway and returns failure on errors.
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        unknown = ToolCall(tool_name="unknown_tool", tool_use_id="tu_x", input_params={})
        mock_cls = self._mock_client(mock_post_side_effect=RuntimeError("gateway rejected"))
        with patch("httpx.Client", mock_cls):
            result = client.execute(unknown, "s1")
        assert result.success is False

    def test_none_tool_call_raises(self):
        client = ActionGatewayHttpClient(_BASE_CONFIG)
        with pytest.raises(ValueError):
            client.execute(None, "s1")
