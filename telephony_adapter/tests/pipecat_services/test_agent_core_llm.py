"""Tests for AgentCoreLLMProcessor — FrameProcessor bridging STT to Agent Core HTTP."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    EndFrame,
    Frame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection


def _make_ac_response(response_text: str, was_escalated: bool = False) -> dict:
    return {
        "session_id": "s1",
        "response_text": response_text,
        "was_escalated": was_escalated,
        "was_tool_used": False,
        "model_used": "claude-haiku",
        "latency_ms": 200,
    }


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
async def test_transcription_frame_triggers_ac_and_pushes_tts_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []

    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json=_make_ac_response("नमस्ते"))
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "नमस्ते"


@pytest.mark.asyncio
async def test_sends_correct_payload_to_agent_core(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    import json

    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="ses1")
    proc.push_frame = AsyncMock()

    with respx.mock:
        route = respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json=_make_ac_response("ok"))
        )
        await proc.process_frame(
            TranscriptionFrame(text="मुझे मदद चाहिए", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    body = json.loads(route.calls[0].request.content)
    assert body["session_id"] == "ses1"
    assert body["user_message"] == "मुझे मदद चाहिए"
    assert body["channel"] == "telephony"
    assert body["user_id"] == "CA1"


@pytest.mark.asyncio
async def test_escalation_pushes_speak_frame_then_end_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(
                200, json=_make_ac_response("Transferring you now.", was_escalated=True)
            )
        )
        await proc.process_frame(
            TranscriptionFrame(text="help", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    types = [type(f) for f in pushed]
    assert TTSSpeakFrame in types
    assert EndFrame in types
    # EndFrame must come after TTSSpeakFrame
    assert types.index(EndFrame) > types.index(TTSSpeakFrame)


@pytest.mark.asyncio
async def test_http_timeout_pushes_fallback_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_http_500_pushes_fallback_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(500, json={"detail": "error"})
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert speak_frames[0].text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_non_transcription_frame_is_passed_through(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    other_frame = TextFrame(text="unrelated")
    await proc.process_frame(other_frame, FrameDirection.DOWNSTREAM)

    assert other_frame in pushed


def test_missing_base_url_raises(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    bad_config = {"telephony_adapter": {"agent_core": {"base_url": ""}}}
    with pytest.raises(ValueError, match="base_url"):
        AgentCoreLLMProcessor(bad_config, call_sid="CA1", session_id="s1")
