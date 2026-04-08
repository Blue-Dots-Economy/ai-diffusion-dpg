# telephony_adapter/tests/test_agent_core_service.py
import pytest
import respx
import httpx
from src.agent_core_service import AgentCoreLLMService


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "fallback_phrase": "Sorry, I could not process that.",
            }
        }
    }


@pytest.mark.asyncio
async def test_process_turn_returns_response_text(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "मैं आपकी मदद कर सकता हूँ।",
                "was_escalated": False,
                "was_tool_used": False,
                "model_used": "claude-haiku",
                "latency_ms": 300,
            })
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn(
            session_id="s1",
            user_message="मुझे काम चाहिए",
            call_sid="CA123",
            caller_id="+911234567890",
        )

    assert result.response_text == "मैं आपकी मदद कर सकता हूँ।"
    assert result.was_escalated is False


@pytest.mark.asyncio
async def test_process_turn_sends_correct_payload(config):
    with respx.mock:
        route = respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "ok",
                "was_escalated": False,
                "was_tool_used": False,
                "model_used": "",
                "latency_ms": 0,
            })
        )
        svc = AgentCoreLLMService(config)
        await svc.process_turn("s1", "hello", "CA1", "+91999")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["session_id"] == "s1"
    assert body["user_message"] == "hello"
    assert body["channel"] == "telephony"
    assert body["user_id"] == "CA1"  # call_sid used as opaque user_id


@pytest.mark.asyncio
async def test_process_turn_timeout_returns_fallback(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "hello", "CA1", "+91999")

    assert result.response_text == "Sorry, I could not process that."
    assert result.was_escalated is False


@pytest.mark.asyncio
async def test_process_turn_http_500_returns_fallback(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(500, json={"detail": "internal error"})
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "hello", "CA1", "+91999")

    assert result.response_text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_process_turn_escalation_flag_propagated(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "transferring you now",
                "was_escalated": True,
                "was_tool_used": False,
                "model_used": "",
                "latency_ms": 0,
            })
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "help", "CA1", "+91999")

    assert result.was_escalated is True


@pytest.mark.asyncio
async def test_missing_config_raises():
    with pytest.raises(ValueError, match="base_url"):
        AgentCoreLLMService({})


@pytest.mark.asyncio
async def test_process_turn_connect_error_returns_fallback(config):
    """ConnectError from Agent Core must return fallback, not raise."""
    import httpx as _httpx
    import respx

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            side_effect=_httpx.ConnectError("refused")
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "hello", "CA1", "+91999")

    assert result.response_text == "Sorry, I could not process that."
    assert result.was_escalated is False
