# reach_layer/voice/tests/test_base.py
import pytest
from src.base import (
    TelephonyAdapterBase,
    TelephonyTurnInput,
    TelephonyTurnResult,
    TelephonyError,
    STTError,
    TTSError,
)


def test_telephony_turn_input_fields():
    t = TelephonyTurnInput(
        session_id="s1",
        call_sid="c1",
        caller_id="+911234567890",
        user_message="hello",
        channel="voice",
        timestamp_ms=1000,
    )
    assert t.session_id == "s1"
    assert t.call_sid == "c1"
    assert t.caller_id == "+911234567890"
    assert t.channel == "voice"


def test_telephony_turn_result_fields():
    r = TelephonyTurnResult(
        session_id="s1",
        call_sid="c1",
        response_text="hi",
        was_escalated=False,
    )
    assert r.response_text == "hi"
    assert r.was_escalated is False
    assert r.latency_ms == 0


def test_abstract_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TelephonyAdapterBase()


def test_telephony_error_is_exception():
    err = TelephonyError("something failed")
    assert isinstance(err, Exception)
    assert "something failed" in str(err)


def test_stt_error_is_exception():
    err = STTError("transcription failed")
    assert isinstance(err, Exception)
    assert "transcription failed" in str(err)


def test_tts_error_is_exception():
    err = TTSError("synthesis failed")
    assert isinstance(err, Exception)
    assert "synthesis failed" in str(err)


def test_stt_error_not_tts_error():
    assert not issubclass(STTError, TTSError)
    assert not issubclass(TTSError, STTError)
