# agent_core/tests/eval/test_nlu_eval.py
"""Tests for the NLU replay harness (no real LLM calls)."""
import json
from unittest.mock import MagicMock

from eval.nlu.adapters import Prediction, predict_dialogue_act, predict_intent
from eval.nlu.cases import EvalCase, load_cases
from eval.nlu.score import gate, score
from src.models import NLUResult
from src.understanding.models import DialogueActResult, ResolvedReference, TurnUnderstanding

CASE = {"id": "ack-01", "tags": ["acknowledge"], "step": "apply_confirm",
        "state": {"applications_submitted": 1}, "caller_now": ["ठीक है धन्यवाद"],
        "recent": [{"caller": "हाँ", "bot": "आवेदन भेज दिया है।", "interrupted": False}],
        "expect": {"acts": ["acknowledge"], "intent": "any_input", "terminate": False}}


def test_load_cases_roundtrip_and_validation(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps(CASE, ensure_ascii=False) + "\n\n", encoding="utf-8")
    cases = load_cases(p)
    assert cases[0].id == "ack-01" and cases[0].caller_now == ["ठीक है धन्यवाद"]
    p.write_text('{"id": "x"}\n', encoding="utf-8")
    try:
        load_cases(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "x" in str(e)


def test_predict_dialogue_act_uses_offered_rows_and_reports_resolution():
    case = EvalCase.from_dict({**CASE, "id": "sel", "step": "job_match", "offered_tool": "fetch_jobs",
                               "offered": [{"item_id": "j1", "role": "Welder"}],
                               "expect": {"intent": "job_pick", "option_id": "j1", "terminate": False}})
    und = MagicMock()
    und.understand.return_value = TurnUnderstanding(
        nlu_result=NLUResult("job_pick", {}, "neutral", 1.0),
        dialogue=DialogueActResult(acts=("select",), relation="answers_pending"), pending_id="select_job",
        resolved=ResolvedReference(1, "j1", "Welder", "item_id"), latency_ms=900)
    pred = predict_dialogue_act(case, und)
    ctx = und.understand.call_args.args[0]
    assert ctx.tool_cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "j1", "role": "Welder"}]
    assert ctx.tool_cache.latest_entry("other") is None
    assert (pred.intent, pred.option_id, pred.terminate, pred.acts) == ("job_pick", "j1", False, ("select",))


def test_predict_intent_maps_unrouted_intents_and_termination():
    case = EvalCase.from_dict(CASE)
    nlu = MagicMock()
    nlu.process.return_value = NLUResult("termination_intent", {"trade": "welder"}, "neutral", 0.95)
    wf = MagicMock(nlu_intent_set={"apply_confirm": ["any_input", "termination_intent"]})
    pred = predict_intent(case, nlu, wf, entity_map={}, routed={"termination_intent", "apply_now"},
                          resolves_to="selected_job_item_id")
    assert pred.intent == "termination_intent" and pred.terminate is True and pred.slots == {"trade": "welder"}
    kwargs = nlu.process.call_args.kwargs
    assert kwargs["current_question"] == "आवेदन भेज दिया है।" and kwargs["normalised_input"] == "ठीक है धन्यवाद"
    nlu.process.return_value = NLUResult("profile_answer", {}, "neutral", 0.9)
    assert predict_intent(case, nlu, wf, {}, {"apply_now"}, "x").intent == "any_input"


def _p(intent, terminate=False, ms=1000, acts=("acknowledge",), pending="closing_offer", slots=None):
    return Prediction(intent=intent, terminate=terminate, slots=slots or {}, option_id=None, acts=acts,
                      relation="answers_pending", topic=None, pending=pending, latency_ms=ms, fallback=None)


def test_score_counts_termination_false_positives_and_tags():
    cases = [EvalCase.from_dict(CASE)]
    rep = score(cases, {"ack-01": [_p("termination_intent", terminate=True), _p("any_input")]}, mode="intent")
    assert rep["fields"]["intent"]["accuracy"] == 0.5
    assert rep["termination_false_positives"] == 1
    assert rep["tags"]["acknowledge"]["intent_accuracy"] == 0.5
    assert rep["agreement"] == 0.0
    assert rep["latency_ms"]["p50"] == 1000


def test_slot_accuracy_is_canonical():
    case = EvalCase.from_dict({**CASE, "expect": {"intent": "any_input", "slots": {"age": 22, "trade": "Welder"}}})
    rep = score([case], {"ack-01": [_p("any_input", slots={"age": "22", "trade": "welder "})]}, mode="intent")
    assert rep["fields"]["slots"]["accuracy"] == 1.0


def test_gate_reports_each_failed_condition():
    base = {"fields": {"intent": {"accuracy": 0.8}, "slots": {"accuracy": 0.9}},
            "tags": {"consent": {"intent_accuracy": 1.0}, "age": {"intent_accuracy": 1.0},
                     "termination": {"intent_accuracy": 1.0}, "acknowledge": {"termination_fp": 0}},
            "latency_ms": {"p50": 1000}}
    worse = json.loads(json.dumps(base))
    worse["fields"]["intent"]["accuracy"] = 0.7
    worse["tags"]["acknowledge"]["termination_fp"] = 1
    worse["latency_ms"]["p50"] = 1100
    fails = gate(base, worse)
    assert len(fails) == 3 and any("p50" in f for f in fails)
    assert gate(base, base) == []
