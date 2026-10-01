"""Snapshot tests for the <caller_turn> body."""
from src.models import NLUResult
from src.understanding.caller_turn import render_caller_turn
from src.understanding.models import (DialogueActResult, ResolvedReference, SlotRejection, SlotUpdate,
                                      TurnUnderstanding, UnresolvedReference)

NR = NLUResult(intent="job_pick", entities={}, confidence=1.0)


def test_full_render():
    u = TurnUnderstanding(
        nlu_result=NR,
        dialogue=DialogueActResult(acts=("select", "correct"), relation="answers_pending",
                                   signals=("pay_disappointment",)),
        pending_id="select_job",
        resolved=ResolvedReference(1, "7f3c", "Welder · Flipkart", "item_id"),
        updates=[SlotUpdate("trade", "Electrician", "Welder")],
        rejected_slots=[SlotRejection("age", 150, "normalise:out_of_range"),
                        SlotRejection("consent", "granted", "not_pending")],
        signals=["pay_disappointment"])
    assert render_caller_turn(u) == (
        "acts: select, correct · relation: answers_pending\n"
        "pending: select_job (now answered)\n"
        "resolved: option 1 — Welder · Flipkart (item_id 7f3c)\n"
        "updated: trade Electrician → Welder (caller corrected)\n"
        'not accepted: age "150" (out_of_range)\n'
        "signals: pay_disappointment"
    )


def test_off_script_keeps_question_open():
    u = TurnUnderstanding(nlu_result=NR, pending_id="age",
                          dialogue=DialogueActResult(acts=("ask",), relation="new_topic", topic="salary"))
    assert render_caller_turn(u) == ("acts: ask · relation: new_topic · topic: salary\n"
                                     "open: age — still unanswered")


def test_unresolved_and_changed_without_correct():
    u = TurnUnderstanding(nlu_result=NR, pending_id="select_job",
                          dialogue=DialogueActResult(acts=("select",), relation="answers_pending"),
                          unresolved=UnresolvedReference(4, 3, "out_of_range"),
                          updates=[SlotUpdate("location", "Pune", "Hubli")])
    text = render_caller_turn(u)
    assert "caller referred to option 4; 3 offered" in text
    assert "updated: location Pune → Hubli" in text and "corrected" not in text


def test_off_track_line():
    u = TurnUnderstanding(nlu_result=NR, pending_id="age", off_track_tripped=True,
                          dialogue=DialogueActResult(acts=("other",), relation="unrelated"))
    assert render_caller_turn(u).endswith("re-ask the open question simply, or offer to end the call")


def test_fallback_and_none():
    u = TurnUnderstanding(nlu_result=NR, fallback_reason="provider_error:timeout")
    assert render_caller_turn(u) == "understanding unavailable this turn"
    assert render_caller_turn(None) == ""
    assert render_caller_turn(TurnUnderstanding(nlu_result=NR)) == ""     # intent mode: no dialogue
