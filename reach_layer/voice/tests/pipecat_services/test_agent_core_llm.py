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

    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="ses1", user_id="+911234567890")
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
    assert body["user_id"] == "+911234567890"


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


@pytest.mark.asyncio
@respx.mock
async def test_process_turn_payload_includes_user_id():
    """user_id (caller E.164) must appear in the Agent Core request payload."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    captured = {}

    def capture(request):
        import json as _json
        captured["payload"] = _json.loads(request.content)
        return httpx.Response(200, json={
            "response_text": "hello",
            "was_escalated": False,
            "was_tool_used": False,
            "model_used": "claude-sonnet-4-6",
        })

    respx.post("http://agent_core:8000/process_turn").mock(side_effect=capture)

    config = {
        "telephony_adapter": {
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "fallback_phrase": "sorry",
                "greeting": "hello",
            }
        }
    }
    processor = AgentCoreLLMProcessor(
        config,
        call_sid="call-123",
        session_id="sess-abc",
        user_id="+919876543210",
    )
    processor.push_frame = AsyncMock()
    frame = TranscriptionFrame(text="मुझे जॉब चाहिए", user_id="", timestamp="")
    await processor._handle_transcription(frame)

    assert captured["payload"]["user_id"] == "+919876543210"


def test_missing_base_url_raises(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    bad_config = {"telephony_adapter": {"agent_core": {"base_url": ""}}}
    with pytest.raises(ValueError, match="base_url"):
        AgentCoreLLMProcessor(bad_config, call_sid="CA1", session_id="s1")


# ---------------------------------------------------------------------------
# session-mode (assembly_mode=session) — submit_input + SSE streaming
# ---------------------------------------------------------------------------


@pytest.fixture
def session_config():
    return {
        "telephony_adapter": {
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "fallback_phrase": "Sorry, I could not process that.",
            }
        },
        "reach_layer": {"channels": {"voice": {"assembly_mode": "session"}}},
    }


def _make_channel(events):
    """Build a fake ReachLayerBase exposing submit_input + subscribe_events."""
    ch = MagicMock()
    ch.submit_input = AsyncMock(return_value=None)

    async def _stream(_session_id):
        for ev in events:
            yield ev

    ch.subscribe_events = _stream
    return ch


def test_session_mode_without_channel_raises(session_config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    with pytest.raises(ValueError, match="session"):
        AgentCoreLLMProcessor(
            session_config, call_sid="CA1", session_id="s1"
        )


@pytest.mark.asyncio
async def test_session_mode_pushes_one_speak_frame_per_sentence(session_config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent, SignalEvent

    events = [
        SignalEvent(stage="llm_call", status="start"),
        SentenceEvent(text="नमस्ते।", sentence_index=0),
        SentenceEvent(text="मैं आपकी मदद कैसे कर सकता हूँ?", sentence_index=1),
        DoneEvent(turn_status="completed", was_escalated=False),
    ]
    channel = _make_channel(events)

    pushed = []
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="मदद चाहिए", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    speak = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak) == 2
    assert speak[0].text == "नमस्ते।"
    assert speak[1].text == "मैं आपकी मदद कैसे कर सकता हूँ?"
    assert not any(isinstance(f, EndFrame) for f in pushed)
    channel.submit_input.assert_awaited_once_with("s1", "मदद चाहिए", None)


@pytest.mark.asyncio
async def test_session_mode_escalation_pushes_end_frame(session_config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    events = [
        SentenceEvent(text="Transferring you to an agent.", sentence_index=0),
        DoneEvent(turn_status="completed", was_escalated=True),
    ]
    channel = _make_channel(events)

    pushed = []
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="help", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    types = [type(f) for f in pushed]
    assert TTSSpeakFrame in types
    assert EndFrame in types
    assert types.index(EndFrame) > types.index(TTSSpeakFrame)


@pytest.mark.asyncio
async def test_session_mode_submit_input_failure_pushes_fallback(session_config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    channel = MagicMock()
    channel.submit_input = AsyncMock(side_effect=httpx.ConnectError("boom"))
    channel.subscribe_events = AsyncMock()  # should never be called

    pushed = []
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="hi", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    speak = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak) == 1
    assert speak[0].text == "Sorry, I could not process that."
    channel.subscribe_events.assert_not_called()


@pytest.mark.asyncio
async def test_session_mode_no_sentences_pushes_fallback(session_config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent

    # SSE stream closes with only a DoneEvent — no SentenceEvents at all.
    channel = _make_channel([DoneEvent(turn_status="completed")])

    pushed = []
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="hi", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    speak = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak) == 1
    assert speak[0].text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_direct_mode_default_does_not_require_channel(config):
    """Backwards-compatible default: no assembly_mode in config → direct, no channel needed."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    # Should construct without channel and without raising.
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    assert proc._assembly_mode == "direct"


# ---------------------------------------------------------------------------
# GH-137 Task 14 — terminal word + telephony close on DoneEvent.session_ended
# ---------------------------------------------------------------------------


def _make_fake_telephony():
    """Fake telephony with an async close_call recording the reason."""
    tel = MagicMock()
    tel.closed = False
    tel.close_reason = None

    async def _close(*, reason: str = "normal"):
        tel.closed = True
        tel.close_reason = reason

    tel.close_call = _close
    return tel


@pytest.mark.asyncio
async def test_done_event_session_ended_appends_terminal_word(config):
    """On DoneEvent.session_ended=True, terminal word is pushed and telephony close is invoked."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "Goodbye"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    assert any(getattr(f, "text", "") == "Goodbye" for f in pushed)
    assert telephony.closed is True
    assert telephony.close_reason == "session_end"


@pytest.mark.asyncio
async def test_done_event_session_ended_false_does_not_append_or_close(config):
    """When session_ended=False the processor must not append terminal word or close the call."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "Goodbye"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=False, turn_status="completed"))

    assert not any(getattr(f, "text", "") == "Goodbye" for f in pushed)
    assert telephony.closed is False


@pytest.mark.asyncio
async def test_done_event_session_ended_empty_terminal_word_logs_warning(config, caplog):
    """Empty terminal_word still triggers close but logs a warning and appends nothing."""
    import logging as _logging
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": ""},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with caplog.at_level(_logging.WARNING, logger="src.pipecat_services.agent_core_llm"):
        await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    assert telephony.closed is True
    # Nothing text-bearing was pushed.
    assert all(getattr(f, "text", "") == "" for f in pushed)
    assert any(
        "terminal_word" in rec.getMessage() or "terminal word" in rec.getMessage()
        for rec in caplog.records
    )
