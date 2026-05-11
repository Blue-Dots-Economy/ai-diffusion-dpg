"""Tests for the bridge's per-turn state machine."""
from bridge import TurnAccumulator


def test_accumulator_emits_turn_on_response_done():
    """Sequential events produce one finished turn on response.done."""
    acc = TurnAccumulator(
        call_sid="abc",
        session_id="sess",
        model="gpt-realtime-mini",
        voice="alloy",
        prompt_name="SHORT_HINDI",
    )

    acc.observe(now_ms=1000, event={"type": "input_audio_buffer.speech_started"})
    acc.observe(now_ms=3500, event={"type": "input_audio_buffer.speech_stopped"})
    acc.observe(now_ms=3700, event={"type": "response.created"})

    # No turn complete yet.
    assert acc.pop_finished_turn() is None

    acc.observe(now_ms=4100, event={
        "type": "response.audio.delta",
        "delta": "AAAA",
    })
    acc.note_audio_out_bytes(100)

    acc.observe(now_ms=4500, event={
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "नमस्ते",
    })
    acc.observe(now_ms=4600, event={
        "type": "response.audio_transcript.done",
        "transcript": "नमस्ते जी",
    })

    acc.observe(now_ms=5500, event={
        "type": "response.done",
        "response": {
            "usage": {
                "input_token_details": {
                    "text_tokens": 30,
                    "audio_tokens": 100,
                    "cached_tokens": 0,
                },
                "output_token_details": {
                    "text_tokens": 20,
                    "audio_tokens": 80,
                },
            },
        },
    })

    turn = acc.pop_finished_turn()
    assert turn is not None
    assert turn["turn"] == 1
    assert turn["call_sid"] == "abc"
    assert turn["t_speech_started_ms"] == 1000
    assert turn["t_speech_stopped_ms"] == 3500
    assert turn["t_response_created_ms"] == 3700
    assert turn["t_first_audio_byte_ms"] == 4100
    assert turn["t_response_done_ms"] == 5500
    assert turn["ttft_ms"] == 600       # 4100 - 3500
    assert turn["total_response_ms"] == 2000  # 5500 - 3500
    assert turn["response_decision_ms"] == 200  # 3700 - 3500
    assert turn["transcript_in"] == "नमस्ते"
    assert turn["transcript_out"] == "नमस्ते जी"
    assert turn["input_text_tokens"] == 30
    assert turn["input_audio_tokens"] == 100
    assert turn["input_cached_tokens"] == 0
    assert turn["output_text_tokens"] == 20
    assert turn["output_audio_tokens"] == 80
    assert turn["audio_out_bytes"] == 100
    assert turn["cost_usd"] > 0


def test_accumulator_handles_two_turns():
    """Two consecutive user turns produce two JSONL rows."""
    acc = TurnAccumulator(
        call_sid="c", session_id="s", model="m", voice="v", prompt_name="p",
    )

    # Turn 1
    acc.observe(1000, {"type": "input_audio_buffer.speech_started"})
    acc.observe(2000, {"type": "input_audio_buffer.speech_stopped"})
    acc.observe(2200, {"type": "response.created"})
    acc.observe(2400, {"type": "response.audio.delta", "delta": "AA"})
    acc.observe(3000, {"type": "response.done",
                       "response": {"usage": {
                           "input_token_details": {"text_tokens": 1},
                           "output_token_details": {"audio_tokens": 1},
                       }}})

    t1 = acc.pop_finished_turn()
    assert t1["turn"] == 1

    # Turn 2
    acc.observe(5000, {"type": "input_audio_buffer.speech_started"})
    acc.observe(6000, {"type": "input_audio_buffer.speech_stopped"})
    acc.observe(6200, {"type": "response.created"})
    acc.observe(6400, {"type": "response.audio.delta", "delta": "BB"})
    acc.observe(7000, {"type": "response.done",
                       "response": {"usage": {
                           "input_token_details": {"text_tokens": 1},
                           "output_token_details": {"audio_tokens": 1},
                       }}})

    t2 = acc.pop_finished_turn()
    assert t2["turn"] == 2


def test_accumulator_audio_in_bytes_accumulated_per_turn():
    """note_audio_in_bytes accumulates within a turn, resets across turns."""
    acc = TurnAccumulator(call_sid="c", session_id="s", model="m",
                          voice="v", prompt_name="p")

    acc.observe(100, {"type": "input_audio_buffer.speech_started"})
    acc.note_audio_in_bytes(50)
    acc.note_audio_in_bytes(70)
    acc.observe(1000, {"type": "input_audio_buffer.speech_stopped"})
    acc.observe(1200, {"type": "response.created"})
    acc.observe(1400, {"type": "response.audio.delta", "delta": "AA"})
    acc.observe(2000, {"type": "response.done",
                       "response": {"usage": {
                           "input_token_details": {"text_tokens": 1},
                           "output_token_details": {"audio_tokens": 1},
                       }}})

    t = acc.pop_finished_turn()
    assert t["audio_in_bytes"] == 120


def test_pop_finished_turn_returns_none_when_no_complete_turn():
    """Calling pop with no turn complete returns None, not an error."""
    acc = TurnAccumulator(call_sid="c", session_id="s", model="m",
                          voice="v", prompt_name="p")
    assert acc.pop_finished_turn() is None
