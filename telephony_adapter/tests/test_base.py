# telephony_adapter/tests/test_base.py
import pytest
from src.base import (
    TelephonyAdapterBase,
    TelephonyTurnInput,
    TelephonyTurnResult,
    TelephonyError,
)


def test_telephony_turn_input_fields():
    t = TelephonyTurnInput(
        session_id="s1",
        call_sid="c1",
        caller_id="+911234567890",
        user_message="hello",
        channel="telephony",
        timestamp_ms=1000,
    )
    assert t.session_id == "s1"
    assert t.call_sid == "c1"
    assert t.caller_id == "+911234567890"
    assert t.channel == "telephony"


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
