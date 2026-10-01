"""Tests for the termination gate, intent derivation and off-track counter."""
from src.understanding.config import DialogueActConfig
from src.understanding.models import DialogueActResult
from src.understanding.postprocess import derive_intent, gate_passes, next_off_track


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["search", "salary"],
        "act_intents": [
            {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "apply_now"},
            {"acts": ["deny"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "decline"},
            {"acts": ["select"], "pending": "select_job", "intent": "job_pick"},
            {"acts": ["request_change"], "topic": "search", "intent": "explore_more"},
            {"acts": ["close"], "intent": "termination_intent", "gated": True},
        ],
        "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                        {"field": "applications_submitted", "operator": "gt", "value": 0}]},
        "off_track": {"threshold": 3, "intent": "off_track"},
    }}})


def D(*acts, relation="answers_pending", topic=None):
    return DialogueActResult(acts=tuple(acts), relation=relation, topic=topic)


def test_affirm_to_submit_is_apply_now():
    assert derive_intent(D("affirm"), "submit_confirm", _cfg(), gate_ok=False, resolved=False)[0] == "apply_now"


def test_thank_you_while_submit_pending_is_never_apply_or_terminate():
    for d in (D("acknowledge", relation="unclear"), D("acknowledge", relation="answers_pending"),
              D("affirm", relation="unclear")):
        intent, _ = derive_intent(d, "submit_confirm", _cfg(), gate_ok=False, resolved=False)
        assert intent == "any_input"


def test_affirm_answering_something_else_is_not_apply_now():
    intent, _ = derive_intent(D("affirm", relation="answers_other"), "submit_confirm", _cfg(),
                              gate_ok=False, resolved=False)
    assert intent == "any_input"


def test_select_needs_a_resolved_reference():
    assert derive_intent(D("select"), "select_job", _cfg(), gate_ok=False, resolved=True)[0] == "job_pick"
    assert derive_intent(D("select"), "select_job", _cfg(), gate_ok=False, resolved=False)[0] == "any_input"


def test_multi_act_row_matches_subset():
    intent, _ = derive_intent(D("select", "affirm"), "select_job", _cfg(), gate_ok=False, resolved=True)
    assert intent == "job_pick"


def test_topic_row():
    d = D("request_change", relation="new_topic", topic="search")
    assert derive_intent(d, "select_job", _cfg(), gate_ok=False, resolved=False)[0] == "explore_more"


def test_close_is_gated():
    intent, rule = derive_intent(D("close"), "select_job", _cfg(), gate_ok=False, resolved=False)
    assert intent == "any_input" and rule is None
    intent, rule = derive_intent(D("close"), "closing_offer", _cfg(), gate_ok=True, resolved=False)
    assert intent == "termination_intent" and rule.gated


def test_gate_passes_on_pending_or_condition():
    c = _cfg()
    assert gate_passes(c, "closing_offer", {})
    assert gate_passes(c, "select_job", {"applications_submitted": 1})
    assert not gate_passes(c, "select_job", {"applications_submitted": 0})
    assert not gate_passes(c, None, {})


def test_off_track_counter():
    c = _cfg()
    assert next_off_track(0, "unrelated", c, is_fallback=False) == (1, False)
    assert next_off_track(2, "unclear", c, is_fallback=False) == (3, True)
    assert next_off_track(2, "answers_pending", c, is_fallback=False) == (0, False)
    assert next_off_track(2, "new_topic", c, is_fallback=False) == (2, False)
    assert next_off_track(2, "unclear", c, is_fallback=True) == (2, False)
