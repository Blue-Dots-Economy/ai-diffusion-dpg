"""Tests for PendingResolver."""
from types import SimpleNamespace

from src.understanding.pending import PendingResolver
from src.workflow_loader import PendingQuestion, RoutingCondition

UNSET = [None, ""]


def _wf():
    opening = SimpleNamespace(pending=[
        PendingQuestion("consent", "हाँ/नहीं", when=(RoutingCondition("consent_response", "in", UNSET),)),
        PendingQuestion("age", "उम्र", when=(RoutingCondition("has_age", "not_eq", True),
                                            RoutingCondition("age", "in", [None, "", 0]))),
    ])
    job = SimpleNamespace(pending=[PendingQuestion("select_job")])
    return SimpleNamespace(subagents={"opening": opening, "job_match": job,
                                      "ended": SimpleNamespace(pending=[])})


def test_first_matching_candidate_wins():
    r = PendingResolver(_wf())
    assert r.resolve("opening", {}).id == "consent"
    assert r.resolve("opening", {"consent_response": "granted"}).id == "age"


def test_returning_caller_with_flags_has_nothing_pending():
    r = PendingResolver(_wf())
    assert r.resolve("opening", {"consent_response": "granted", "has_age": True}) is None


def test_entry_without_when_always_matches():
    assert PendingResolver(_wf()).resolve("job_match", {}).id == "select_job"


def test_unknown_subagent_or_no_pending_is_none():
    r = PendingResolver(_wf())
    assert r.resolve("ended", {}) is None
    assert r.resolve("nope", {}) is None
