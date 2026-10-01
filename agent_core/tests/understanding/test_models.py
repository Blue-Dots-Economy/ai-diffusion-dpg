"""Tests for understanding result models."""
import pytest

from src.schema.config import _DIALOGUE_ACTS, _DIALOGUE_RELATIONS
from src.understanding.models import ACTS, RELATIONS, DialogueActResult

KW = dict(slot_names=("age", "trade"), topics=("salary",), signals=("pay_disappointment",))


def test_constants_match_schema():
    assert ACTS == _DIALOGUE_ACTS and RELATIONS == _DIALOGUE_RELATIONS


def test_from_parsed_happy_path():
    r = DialogueActResult.from_parsed({
        "acts": ["select", "affirm"], "relation": "answers_pending", "topic": None,
        "slots": {"age": None, "trade": "Welder"},
        "reference": {"option": 1, "spoken": "पहले वाला"},
        "signals": ["pay_disappointment", "made_up"], "extras": [{"key": "tool", "value": "drill"}],
    }, **KW)
    assert r.acts == ("select", "affirm") and r.option == 1 and r.spoken_reference == "पहले वाला"
    assert r.slots == {"age": None, "trade": "Welder"}
    assert r.signals == ("pay_disappointment",)            # unknown signal dropped
    assert r.extras == (("tool", "drill"),)


def test_from_parsed_fills_missing_slots_with_none_and_drops_unknown_slots():
    r = DialogueActResult.from_parsed({"acts": ["other"], "relation": "unclear",
                                       "slots": {"bogus": "x"}}, **KW)
    assert r.slots == {"age": None, "trade": None}


def test_from_parsed_unknown_topic_becomes_none_and_more_than_3_acts_truncate():
    r = DialogueActResult.from_parsed({"acts": ["ask", "ask", "ask", "ask"], "relation": "new_topic",
                                       "topic": "weather"}, **KW)
    assert r.topic is None and len(r.acts) == 3


@pytest.mark.parametrize("parsed", [
    None, [], {"acts": [], "relation": "unclear"}, {"acts": ["shout"], "relation": "unclear"},
    {"acts": ["other"], "relation": "sideways"}, {"acts": ["other"], "relation": "unclear", "slots": []},
])
def test_from_parsed_rejects_bad_shapes(parsed):
    with pytest.raises(ValueError):
        DialogueActResult.from_parsed(parsed, **KW)


def test_bool_option_is_not_an_int():
    r = DialogueActResult.from_parsed({"acts": ["select"], "relation": "answers_pending",
                                       "reference": {"option": True, "spoken": None}}, **KW)
    assert r.option is None


def test_fallback_shape():
    f = DialogueActResult.fallback()
    assert f.acts == ("other",) and f.relation == "unclear" and f.slots == {} and f.option is None
