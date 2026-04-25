"""Tests for AgentCoreLLMProcessor — FrameProcessor bridging STT to Agent Core HTTP."""
import asyncio
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
    assert body["channel"] == "voice"
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

    async def _stream(_session_id, user_id=None):
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
    """On DoneEvent.session_ended=True, terminal word is pushed.

    GH-199: telephony.close_call is NOT invoked here — the EndFrame pushed
    immediately after drives Vobiz's REST DELETE hangup via the serializer
    and the natural pipeline shutdown closes the WebSocket. Calling
    close_call eagerly races the EndFrame and was the bug.
    """
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
    assert telephony.closed is False, (
        "close_call must not run on the happy path — it races the EndFrame "
        "and prevents the Vobiz REST DELETE hangup (GH-199)."
    )


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
    """Empty terminal_word logs a warning and appends nothing.

    GH-199: close_call is not invoked here either — EndFrame still drives
    the teardown via the pipeline + serializer.
    """
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

    assert telephony.closed is False
    # Nothing text-bearing was pushed.
    assert all(getattr(f, "text", "") == "" for f in pushed)
    assert any(
        "terminal_word" in rec.getMessage() or "terminal word" in rec.getMessage()
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# GH-152: barge-in / InterruptionFrame handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_interruption_pushes_acknowledgement_when_bot_speaking(session_config):
    """_start_interruption speaks the configured template when bot was mid-TTS."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    session_config["telephony_adapter"]["agent_core"]["barge_in_acknowledgement"] = "ठीक है।"
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=MagicMock()
    )
    proc._bot_speaking = True  # simulate: bot was playing audio when user barged in
    pushed = []
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._start_interruption()

    assert proc._interrupted is True
    assert any(
        isinstance(f, TTSSpeakFrame) and f.text == "ठीक है।" for f in pushed
    )


@pytest.mark.asyncio
async def test_start_interruption_silent_when_bot_not_speaking(session_config):
    """No ack when user-turn-start fires outside of bot TTS (e.g. idle, post-done)."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    session_config["telephony_adapter"]["agent_core"]["barge_in_acknowledgement"] = "ठीक है।"
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=MagicMock()
    )
    # _bot_speaking defaults False; explicit here for clarity.
    proc._bot_speaking = False
    pushed = []
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._start_interruption()

    # Flag still flips (so in-flight SSE loop exits) but no ack spoken.
    assert proc._interrupted is True
    assert not any(isinstance(f, TTSSpeakFrame) for f in pushed)


@pytest.mark.asyncio
async def test_start_interruption_empty_ack_stays_silent(session_config):
    """No acknowledgement configured → no TTSSpeakFrame pushed, flag still set."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    # barge_in_acknowledgement defaults to ""
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=MagicMock()
    )
    proc._bot_speaking = True  # even if bot was speaking, empty ack stays silent
    pushed = []
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._start_interruption()

    assert proc._interrupted is True
    assert not any(isinstance(f, TTSSpeakFrame) for f in pushed)


@pytest.mark.asyncio
async def test_bot_speaking_frame_tracking(session_config):
    """BotStartedSpeakingFrame/BotStoppedSpeakingFrame toggle the _bot_speaking flag."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=MagicMock()
    )
    proc.push_frame = AsyncMock()

    assert proc._bot_speaking is False

    await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert proc._bot_speaking is True

    await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert proc._bot_speaking is False


@pytest.mark.asyncio
async def test_session_loop_exits_on_mid_stream_interruption(session_config):
    """Mid-stream flag set → no further SentenceEvents reach TTS."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    # Yield 3 sentences then Done. The processor should stop pushing at the
    # 2nd one because we flip _interrupted=True between yields.
    proc_ref = {}

    async def _stream(_session_id, user_id=None):
        yield SentenceEvent(text="sentence one", sentence_index=0)
        # Simulate pipecat firing InterruptionFrame after first sentence.
        proc_ref["proc"]._interrupted = True
        yield SentenceEvent(text="sentence two", sentence_index=1)
        yield SentenceEvent(text="sentence three", sentence_index=2)
        yield DoneEvent(turn_status="completed")

    channel = MagicMock()
    channel.submit_input = AsyncMock(return_value=None)
    channel.subscribe_events = _stream

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc_ref["proc"] = proc
    pushed = []
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    texts = [f.text for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert "sentence one" in texts
    # Only the first sentence should have reached TTS.
    assert "sentence two" not in texts
    assert "sentence three" not in texts


@pytest.mark.asyncio
async def test_new_turn_clears_stale_interrupted_flag(session_config):
    """A fresh TranscriptionFrame resets the flag so the next turn isn't blocked."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    events = [
        SentenceEvent(text="ok", sentence_index=0),
        DoneEvent(turn_status="completed"),
    ]
    channel = MagicMock()
    channel.submit_input = AsyncMock(return_value=None)

    async def _stream(_session_id, user_id=None):
        for ev in events:
            yield ev

    channel.subscribe_events = _stream

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    # Simulate a prior barge-in having set the flag before the new turn starts.
    proc._interrupted = True
    pushed = []
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    texts = [f.text for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert "ok" in texts


@pytest.mark.asyncio
async def test_done_event_session_ended_pushes_endframe_after_terminal_word(config):
    """GH-199: pipeline must see EndFrame downstream so the Vobiz serializer
    can issue its REST DELETE hangup. EndFrame must come after the terminal
    word so TTS can finish speaking."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame, TextFrame

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "धन्यवाद"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    text_idx = next(
        i for i, f in enumerate(pushed)
        if isinstance(f, TextFrame) and getattr(f, "text", "") == "धन्यवाद"
    )
    end_idx = next(i for i, f in enumerate(pushed) if isinstance(f, EndFrame))
    assert end_idx > text_idx, "EndFrame must be pushed after the terminal-word TextFrame"


@pytest.mark.asyncio
async def test_done_event_session_ended_empty_terminal_word_still_pushes_endframe(config):
    """GH-199: empty terminal_word still triggers EndFrame so the leg drops."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame

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

    await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    assert any(isinstance(f, EndFrame) for f in pushed)


@pytest.mark.asyncio
async def test_done_event_session_ended_false_does_not_push_endframe(config):
    """GH-199: non-terminal turn must not push EndFrame (would tear down pipeline)."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "धन्यवाद"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=False, turn_status="completed"))

    assert not any(isinstance(f, EndFrame) for f in pushed)
    assert proc._interrupted is False


@pytest.mark.asyncio
async def test_session_mode_stale_sentences_appear_before_done_interrupted(session_config):
    """After Done(turn_status='interrupted'), no further SentenceEvents from
    the cancelled turn appear in the TTS frame stream — guaranteed structurally
    by per-turn queues in TurnAssembler (#224 acceptance #5 regression).

    Sentences emitted BEFORE the Done(interrupted) are allowed to reach TTS
    (the interrupt happened mid-generation); the new lifecycle simply guarantees
    that no SentenceEvent with the cancelled turn_id can appear AFTER the
    Done(interrupted) marker on the SSE stream (per-turn queue cutoff).

    This test verifies the processor correctly stops consuming SSE when it sees
    Done(interrupted), and that if a sentence were to somehow arrive after
    Done (which TurnAssembler's per-turn queues prevent), it would not reach TTS.
    """
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    # Event stream: sentences from interrupted turn, then Done, then stale
    # sentences (which should not reach TTS due to the break on Done).
    events = [
        SentenceEvent(text="sentence-1", sentence_index=0, turn_id="t-old"),
        SentenceEvent(text="sentence-2", sentence_index=1, turn_id="t-old"),
        DoneEvent(turn_status="interrupted", turn_id="t-old"),
        # Per-turn queue guarantee: these never appear on the SSE stream in
        # practice, but test that processor correctly ignores them if they do.
        SentenceEvent(text="stale-1", sentence_index=2, turn_id="t-old"),
        SentenceEvent(text="stale-2", sentence_index=3, turn_id="t-old"),
    ]
    channel = _make_channel(events)

    pushed = []
    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    speak_texts = [f.text for f in pushed if isinstance(f, TTSSpeakFrame)]

    # Pre-Done sentences reach TTS (they were emitted during the interrupted turn).
    assert "sentence-1" in speak_texts, (
        "pre-Done sentence missing from TTS frames"
    )
    assert "sentence-2" in speak_texts, (
        "pre-Done sentence missing from TTS frames"
    )

    # Stale sentences that arrive after Done(interrupted) must NOT reach TTS.
    # The processor's break on DoneEvent guarantees this.
    assert "stale-1" not in speak_texts, (
        f"stale-1 unexpectedly reached TTS; processor should break on Done(interrupted)"
    )
    assert "stale-2" not in speak_texts, (
        f"stale-2 unexpectedly reached TTS; processor should break on Done(interrupted)"
    )


# ---------------------------------------------------------------------------
# GH-202: serialise opening_phrase consumer with first user turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_phrase_task_cancelled_before_first_turn(session_config):
    """The first user turn cancels and joins a still-running opening-phrase task
    before opening its own SSE subscribe — preventing concurrent consumers
    of the session's shared event queue."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    events = [
        SentenceEvent(text="hi", sentence_index=0),
        DoneEvent(turn_status="completed"),
    ]
    channel = _make_channel(events)

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock()

    # Simulate a still-running opening-phrase consumer that would otherwise
    # race with the per-turn subscribe.
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hanging_opening_phrase():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    op_task = asyncio.create_task(_hanging_opening_phrase())
    await started.wait()
    proc.set_opening_phrase_task(op_task)

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    assert op_task.done(), "opening-phrase task should be cancelled by first turn"
    assert cancelled.is_set(), "cancellation should reach the running task"
    # submit_input ran, processor took over the session.
    channel.submit_input.assert_awaited_once_with("s1", "hello", None)


@pytest.mark.asyncio
async def test_already_completed_opening_phrase_task_is_noop(session_config):
    """If the opening-phrase task already finished, joining is a fast no-op."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    events = [
        SentenceEvent(text="hi", sentence_index=0),
        DoneEvent(turn_status="completed"),
    ]
    channel = _make_channel(events)

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock()

    async def _already_done():
        return None

    op_task = asyncio.create_task(_already_done())
    await op_task  # ensure it finished before we register
    proc.set_opening_phrase_task(op_task)

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    channel.submit_input.assert_awaited_once_with("s1", "hello", None)


@pytest.mark.asyncio
async def test_no_opening_phrase_task_is_noop(session_config):
    """Without a registered task, _handle_transcription_session runs unchanged."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    events = [
        SentenceEvent(text="hi", sentence_index=0),
        DoneEvent(turn_status="completed"),
    ]
    channel = _make_channel(events)

    proc = AgentCoreLLMProcessor(
        session_config, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock()

    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )

    channel.submit_input.assert_awaited_once_with("s1", "hello", None)


@pytest.mark.asyncio
async def test_opening_phrase_join_timeout_does_not_block_turn(session_config):
    """If a misbehaving opening-phrase task ignores cancellation, the join
    times out and the user's first turn proceeds anyway."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent, SentenceEvent

    cfg = {**session_config}
    cfg["channels"] = {"voice": {"opening_phrase_join_timeout_ms": 50}}

    events = [
        SentenceEvent(text="hi", sentence_index=0),
        DoneEvent(turn_status="completed"),
    ]
    channel = _make_channel(events)

    proc = AgentCoreLLMProcessor(
        cfg, call_sid="CA1", session_id="s1", channel=channel
    )
    proc.push_frame = AsyncMock()

    async def _ignores_cancel():
        # Suppress CancelledError to simulate misbehaviour.
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(60)

    op_task = asyncio.create_task(_ignores_cancel())
    await asyncio.sleep(0)  # let it start
    proc.set_opening_phrase_task(op_task)

    t0 = asyncio.get_event_loop().time()
    await proc.process_frame(
        TranscriptionFrame(text="hello", user_id="", timestamp=""),
        FrameDirection.DOWNSTREAM,
    )
    elapsed = asyncio.get_event_loop().time() - t0

    assert elapsed < 1.0, (
        f"first turn was blocked by misbehaving opening-phrase task: {elapsed}s"
    )
    channel.submit_input.assert_awaited_once_with("s1", "hello", None)

    # Cleanup the still-running task so pytest doesn't warn.
    op_task.cancel()
    try:
        await op_task
    except (asyncio.CancelledError, Exception):
        pass
