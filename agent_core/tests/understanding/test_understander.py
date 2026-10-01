"""End-to-end tests of TurnUnderstander with a scripted NLU."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.understanding.config import DialogueActConfig
from src.understanding.models import DialogueActResult, StateWrite
from src.understanding.precedence import PROVENANCE_KEY
from src.understanding.understander import TurnContext, TurnUnderstander
from src.workflow_loader import OptionsFrom, PendingQuestion, RoutingCondition

CONFIG = {
    "entity_to_profile_field": {"consent": "consent_response"},
    "entity_persistence": {"scope": "session"},
    "preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["salary", "search"], "signals": ["pay_disappointment"],
        "slots": {"consent": {"type": "enum", "values": ["granted", "declined"], "accept_when_pending": ["consent"]},
                  "age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
                  "trade": {"type": "string", "normalise": "title"}},
        "known_fields": ["consent", "trade"],
        "act_intents": [
            {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "apply_now"},
            {"acts": ["select"], "pending": "select_job", "intent": "job_pick"},
            {"acts": ["close"], "intent": "termination_intent", "gated": True},
        ],
        "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                        {"field": "applications_submitted", "operator": "gt", "value": 0}]},
        "off_track": {"threshold": 2, "intent": "off_track"},
    }},
}
WF = SimpleNamespace(subagents={
    "opening": SimpleNamespace(pending=[PendingQuestion(
        "consent", "हाँ/नहीं", when=(RoutingCondition("consent_response", "in", [None, ""]),))]),
    "job_match": SimpleNamespace(pending=[PendingQuestion(
        "select_job", "एक नौकरी", options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"),
        resolves_to="selected_job_item_id")]),
    "apply_confirm": SimpleNamespace(pending=[
        PendingQuestion("closing_offer", when=(RoutingCondition("applications_submitted", "gt", 0),)),
        PendingQuestion("submit_confirm")]),
})


def _u(result: DialogueActResult, reason=None):
    nlu = MagicMock()
    nlu.classify.return_value = (result, reason, 7)
    return TurnUnderstander(DialogueActConfig.from_config(CONFIG), WF, nlu), nlu


def _ctx(subagent, state=None, session=None, cache=None, segments=("x",)):
    return TurnContext(subagent_id=subagent, state=state or {}, session=session or {},
                       segments=list(segments), recent=[], tool_cache=cache)


def test_consent_answer_writes_mapped_key_and_provenance():
    und, nlu = _u(DialogueActResult(acts=("affirm",), relation="answers_pending",
                                    slots={"consent": "granted", "age": None, "trade": None}))
    u = und.understand(_ctx("opening"))
    assert u.pending_id == "consent" and u.nlu_result.intent == "any_input"
    assert StateWrite("session", "consent_response", "granted") in u.writes
    assert StateWrite("session", PROVENANCE_KEY, ["consent_response"]) in u.writes
    assert "pending: consent — हाँ/नहीं" in nlu.classify.call_args.args[0]


def test_select_resolves_latest_job_and_derives_job_pick():
    cache = MagicMock()
    cache.latest_entry.return_value = {"data": [{"item_id": "j1", "role": "Welder", "company": "Flipkart"}]}
    und, nlu = _u(DialogueActResult(acts=("select",), relation="answers_pending",
                                    slots={"consent": None, "age": None, "trade": None}, option=1))
    u = und.understand(_ctx("job_match", cache=cache))
    cache.latest_entry.assert_called_once_with("fetch_jobs")
    assert u.nlu_result.intent == "job_pick" and u.resolved.id == "j1"
    assert StateWrite("session", "selected_job_item_id", "j1") in u.writes
    assert "1. Welder · Flipkart" in nlu.classify.call_args.args[0]


def test_select_without_store_is_unresolved_and_not_job_pick():
    und, _ = _u(DialogueActResult(acts=("select",), relation="answers_pending", option=1,
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("job_match", cache=None))
    assert u.nlu_result.intent == "any_input" and u.unresolved.reason == "no_options"


def test_thank_you_after_submit_question_never_applies_or_ends():
    und, _ = _u(DialogueActResult(acts=("acknowledge",), relation="unclear",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 0}))
    assert u.pending_id == "submit_confirm" and u.nlu_result.intent == "any_input"


def test_close_after_application_terminates_with_full_confidence():
    und, _ = _u(DialogueActResult(acts=("close",), relation="answers_pending",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 1}))
    assert u.nlu_result.intent == "termination_intent" and u.nlu_result.confidence == 1.0


def test_off_track_trips_and_resets():
    und, _ = _u(DialogueActResult(acts=("other",), relation="unrelated",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("job_match", session={"off_track_count": 1}))
    assert u.nlu_result.intent == "off_track" and u.off_track_tripped
    assert StateWrite("session", "off_track_count", 0) in u.writes


def test_fallback_is_any_input_with_no_writes():
    und, _ = _u(DialogueActResult.fallback(), reason="provider_error:timeout")
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 1}))
    assert u.fallback_reason == "provider_error:timeout"
    assert u.nlu_result.intent == "any_input" and u.nlu_result.confidence == 0.0 and u.writes == []


def test_out_of_scope_consent_is_rejected_not_written():
    und, _ = _u(DialogueActResult(acts=("affirm",), relation="answers_other",
                                  slots={"consent": "granted", "age": None, "trade": "welder"}))
    u = und.understand(_ctx("job_match"))
    assert not any(w.key == "consent_response" for w in u.writes)
    assert StateWrite("session", "trade", "Welder") in u.writes
    assert [r.reason for r in u.rejected_slots] == ["not_pending"]


def test_understand_never_raises():
    nlu = MagicMock()
    nlu.classify.side_effect = RuntimeError("boom")
    und = TurnUnderstander(DialogueActConfig.from_config(CONFIG), WF, nlu)
    u = und.understand(_ctx("opening"))
    assert u.fallback_reason == "exception" and u.nlu_result.intent == "any_input"


def test_from_config_none_in_intent_mode():
    assert TurnUnderstander.from_config({}, WF, chat_provider=MagicMock()) is None


def test_telemetry_failure_never_raises_and_keeps_understanding(monkeypatch):
    """F1: a failing counter / log in _finish must not turn a good understanding into a raise or fallback."""
    import src.understanding.understander as mod

    def _boom(*a, **k):
        raise RuntimeError("otel down")

    monkeypatch.setattr(mod, "_counter", _boom)
    und, _ = _u(DialogueActResult(acts=("affirm",), relation="answers_pending",
                                  slots={"consent": "granted", "age": None, "trade": None}))
    u = und.understand(_ctx("opening"))
    assert u.fallback_reason is None
    assert StateWrite("session", "consent_response", "granted") in u.writes


def test_understanding_error_log_carries_latency(caplog):
    """F1: the nlu.understanding_error record carries latency_ms."""
    import logging
    nlu = MagicMock()
    nlu.classify.side_effect = RuntimeError("boom")
    und = TurnUnderstander(DialogueActConfig.from_config(CONFIG), WF, nlu)
    with caplog.at_level(logging.ERROR, logger="src.understanding.understander"):
        und.understand(_ctx("opening"))
    recs = [r for r in caplog.records if r.getMessage() == "nlu.understanding_error"]
    assert recs and isinstance(recs[0].latency_ms, int)


import pytest  # noqa: E402


@pytest.mark.parametrize("bad", ["abc", "2.0", [1], {}, True])
@pytest.mark.parametrize("relation, expected", [("answers_pending", 0), ("unrelated", 1)])
def test_corrupt_off_track_count_is_healed_not_fallback(bad, relation, expected):
    """F2: an unparseable off_track_count counts as 0 and the healed int is written."""
    und, _ = _u(DialogueActResult(acts=("affirm",), relation=relation,
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("job_match", session={"off_track_count": bad}))
    assert u.fallback_reason is None
    writes = [w for w in u.writes if w.key == "off_track_count"]
    assert writes == [StateWrite("session", "off_track_count", expected)]
    assert type(writes[0].value) is int


def test_corrupt_nlu_extras_is_replaced_with_dict():
    """F2: a non-dict nlu_extras counts as {} and the extras are written as a dict."""
    und, _ = _u(DialogueActResult(acts=("provide_info",), relation="answers_other",
                                  slots={"consent": None, "age": None, "trade": None},
                                  extras=(("tool", "drill"),)))
    u = und.understand(_ctx("job_match", session={"nlu_extras": "x"}))
    assert u.fallback_reason is None
    assert StateWrite("session", "nlu_extras", {"tool": "drill"}) in u.writes
