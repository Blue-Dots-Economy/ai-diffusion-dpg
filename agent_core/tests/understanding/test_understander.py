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


def test_select_prefers_last_served_entry_over_newest():
    """F3: search A, search B, re-call A (cache hit) — the caller heard A, so option 1 is A's row."""
    from src.tool_results import ToolResultPolicies, TurnToolCache
    pol = ToolResultPolicies.from_config({"connectors": {"read": [
        {"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600}}]}})
    now = 1000.0
    a = {"tool": "fetch_jobs", "args_hash": "ha", "data": [{"item_id": "a1", "role": "Welder", "company": "A"}],
         "fetched_at": now - 60, "expires_at": now + 500, "origin": "turn", "scope": "session"}
    b = dict(a, args_hash="hb", data=[{"item_id": "b1", "role": "Fitter", "company": "B"}], fetched_at=now - 10)
    cache = TurnToolCache(pol, [a, b], {}, now=lambda: now)
    und, nlu = _u(DialogueActResult(acts=("select",), relation="answers_pending", option=1,
                                    slots={"consent": None, "age": None, "trade": None}))
    ctx = TurnContext(subagent_id="job_match", state={}, session={}, segments=["pehla"], recent=[],
                      tool_cache=cache, served={"fetch_jobs": "ha"})
    u = und.understand(ctx)
    assert u.resolved.id == "a1"
    assert "1. Welder · A" in nlu.classify.call_args.args[0]


def test_select_falls_back_to_latest_when_served_entry_gone():
    cache = MagicMock()
    cache.entry.return_value = None
    cache.latest_entry.return_value = {"data": [{"item_id": "j9", "role": "Welder", "company": "X"}]}
    und, _ = _u(DialogueActResult(acts=("select",), relation="answers_pending", option=1,
                                  slots={"consent": None, "age": None, "trade": None}))
    ctx = TurnContext(subagent_id="job_match", state={}, session={}, segments=["x"], recent=[],
                      tool_cache=cache, served={"fetch_jobs": "gone"})
    u = und.understand(ctx)
    cache.entry.assert_called_once_with("fetch_jobs", "gone")
    assert u.resolved.id == "j9"


def test_static_tool_cache_entry_is_none():
    from eval.nlu.offline import StaticToolCache
    assert StaticToolCache({"fetch_jobs": [{"item_id": "j"}]}).entry("fetch_jobs", "h") is None


def test_understanding_log_carries_no_slot_values_or_caller_text(caplog):
    """M6: the nlu.understanding record is PII-free (keys and reasons only); no eval capture by default."""
    import logging
    caller = "मेरा नाम Ramesh Sharma, उम्र 150, ट्रेड plumbersecret"
    und, _ = _u(DialogueActResult(acts=("provide_info",), relation="answers_other",
                                  slots={"consent": "granted", "age": 150, "trade": "plumbersecret"}))
    with caplog.at_level(logging.DEBUG, logger="src.understanding.understander"):
        u = und.understand(_ctx("job_match", segments=(caller,)))
    assert u.writes and u.rejected_slots                      # one written, some rejected
    recs = [r for r in caplog.records if r.name == "src.understanding.understander"]
    assert [r.getMessage() for r in recs] == ["nlu.understanding"]
    skip = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
    blob = recs[0].getMessage() + repr({k: v for k, v in recs[0].__dict__.items() if k not in skip})
    for secret in ("Ramesh", "plumbersecret", "Plumbersecret", "150", "granted", caller):
        assert secret not in blob, secret
    assert "trade" in blob                                    # keys are logged
