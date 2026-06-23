"""
agent_core/tests/test_orchestration_server.py

Unit tests for the orchestration server (src/servers/orchestration_server.py).

Covers:
- Normal:   /health returns ok; /process_turn delegates to AgentCore
- Edge:     create_orchestration_app(None) raises ValueError
- Failure:  AgentCore exception returns safe fallback response
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.servers.orchestration_server import create_orchestration_app
from src.models import TurnResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_agent_core() -> MagicMock:
    ac = MagicMock()
    ac.process_turn.return_value = TurnResult(
        session_id="s1",
        turn_id="t1",
        response_text="Here are some jobs.",
        was_escalated=False,
        was_tool_used=False,
        model_used="claude-test",
        latency_ms=200,
    )
    return ac


@pytest.fixture
def client():
    app = create_orchestration_app(_make_mock_agent_core())
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_orchestration_app — None raises ValueError
# ---------------------------------------------------------------------------

class TestCreateOrchestrationApp:
    def test_none_agent_core_raises_value_error(self):
        with pytest.raises(ValueError, match="agent_core must not be None"):
            create_orchestration_app(None)

    def test_valid_agent_core_creates_app(self):
        app = create_orchestration_app(_make_mock_agent_core())
        assert app is not None


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /process_turn — normal execution
# ---------------------------------------------------------------------------

class TestProcessTurnNormal:
    def test_returns_200(self, client):
        response = client.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "I need a job"},
        )
        assert response.status_code == 200

    def test_response_contains_session_id(self, client):
        response = client.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.json()["session_id"] == "s1"

    def test_response_contains_response_text(self, client):
        response = client.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.json()["response_text"] == "Here are some jobs."

    def test_response_has_required_keys(self, client):
        response = client.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        data = response.json()
        for key in ("session_id", "response_text", "was_escalated", "was_tool_used", "model_used", "latency_ms"):
            assert key in data

    def test_agent_core_process_turn_called(self):
        mock_ac = _make_mock_agent_core()
        c = TestClient(create_orchestration_app(mock_ac))
        c.post("/process_turn", json={"session_id": "s1", "user_message": "hello"})
        mock_ac.process_turn.assert_called_once()


# ---------------------------------------------------------------------------
# POST /process_turn — failure scenarios
# ---------------------------------------------------------------------------

class TestProcessTurnFailure:
    def test_agent_core_exception_returns_200_with_fallback_text(self):
        mock_ac = MagicMock()
        mock_ac.process_turn.side_effect = RuntimeError("downstream error")
        c = TestClient(create_orchestration_app(mock_ac))
        response = c.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "response_text" in data
        assert data["response_text"] != ""

    def test_agent_core_error_response_contains_error_metadata(self):
        mock_ac = MagicMock()
        mock_ac.process_turn.return_value = TurnResult(
            session_id="s1",
            turn_id="t1",
            response_text="",
            was_escalated=False,
            was_tool_used=False,
            model_used="claude-test",
            latency_ms=200,
            error_type="api_error",
            error_message="Anthropic API Error: Rate limited",
        )
        c = TestClient(create_orchestration_app(mock_ac))
        response = c.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error_type"] == "api_error"
        assert data["error_message"] == "Anthropic API Error: Rate limited"

    def test_agent_core_exception_propagates_custom_error_fields(self):
        from src.chat_provider.base import ProviderAPIError
        mock_ac = MagicMock()
        mock_ac.process_turn.side_effect = ProviderAPIError(
            "Service Unavailable",
            error_type="timeout",
            error_message="OpenAI timeout",
        )
        c = TestClient(create_orchestration_app(mock_ac))
        response = c.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error_type"] == "timeout"
        assert data["error_message"] == "OpenAI timeout"

    def test_agent_core_exception_fallback_error_message(self):
        from src.chat_provider.base import ProviderAPIError
        mock_ac = MagicMock()
        mock_ac.process_turn.side_effect = ProviderAPIError("Something went wrong")
        c = TestClient(create_orchestration_app(mock_ac))
        response = c.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error_type"] == "api_error"
        assert data["error_message"] == "Something went wrong"
