"""Tests for slot normalisation and pending-scoped acceptance."""
from src.understanding.config import DialogueActConfig
from src.understanding.postprocess import accept_slots, normalise_slots


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act", "slots": {
        "age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
        "consent": {"type": "enum", "values": ["granted", "declined"], "accept_when_pending": ["consent"]},
        "trade": {"type": "string", "normalise": "title"},
        "city": {"type": "string", "normalise": "lower"},
    }}}})


def test_int_digits_and_range():
    ok, rej = normalise_slots({"age": "22", "trade": None}, _cfg())
    assert ok == {"age": 22} and rej == []
    ok, rej = normalise_slots({"age": 150}, _cfg())
    assert ok == {} and rej[0].reason == "normalise:out_of_range" and rej[0].value == 150
    ok, rej = normalise_slots({"age": "बाईस"}, _cfg())
    assert ok == {} and rej[0].reason == "normalise:not_integer"
    ok, rej = normalise_slots({"age": True}, _cfg())
    assert rej[0].reason == "normalise:not_integer"
    ok, rej = normalise_slots({"age": 0}, _cfg())
    assert rej[0].reason == "normalise:out_of_range"       # a seeded-looking 0 never passes


def test_enum_and_strings():
    ok, rej = normalise_slots({"consent": "granted", "trade": "  welder  ", "city": "Bengaluru"}, _cfg())
    assert ok == {"consent": "granted", "trade": "Welder", "city": "bengaluru"}
    ok, rej = normalise_slots({"consent": "maybe", "trade": "   "}, _cfg())
    assert ok == {} and {r.reason for r in rej} == {"normalise:not_in_enum", "normalise:empty"}


def test_unknown_slot_ignored():
    ok, rej = normalise_slots({"bogus": "x"}, _cfg())
    assert ok == {} and rej == []


def test_accept_when_pending():
    slots = {"consent": "granted", "age": 25, "trade": "Welder"}
    ok, rej = accept_slots(slots, _cfg(), pending_id="consent", acts=("affirm",))
    assert ok == {"consent": "granted", "trade": "Welder"}
    assert [(r.slot, r.reason) for r in rej] == [("age", "not_pending")]


def test_correct_act_overrides_accept_when_pending():
    ok, rej = accept_slots({"age": 26}, _cfg(), pending_id="select_job", acts=("correct",))
    assert ok == {"age": 26} and rej == []


def test_no_pending_rejects_scoped_slots():
    ok, rej = accept_slots({"consent": "granted"}, _cfg(), pending_id=None, acts=("affirm",))
    assert ok == {} and rej[0].reason == "not_pending"
