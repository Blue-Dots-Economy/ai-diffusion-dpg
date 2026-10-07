"""Tests for src.tool_guard.check_tool_call (Spec E §5.1)."""
from src.models import ToolCall, ToolResult
from src.tool_guard import check_tool_call


def _tc(name="apply_job", **params):
    return ToolCall(tool_name=name, tool_use_id="t1", input_params=params)


def _hit(tc):
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={},
                      success=True, result_text="[]")


BASE = dict(cap=None, used=0, grounded_spec=None, messages=[], stored_results={},
            session_grounded=None, consent_ok=None, cache_lookup=lambda tc: None)


def test_go_when_nothing_blocks():
    assert check_tool_call(_tc(), **BASE).kind == "go"


def test_consent_refusal_first():
    v = check_tool_call(_tc(), **{**BASE, "consent_ok": False, "cap": 1, "used": 1})
    assert v.kind == "refuse" and v.result.error == "consent_required"
    assert v.result.success is False
    assert v.result.result_text == (
        "consent_required: the caller has not given consent for this action")


def test_consent_granted_falls_through():
    assert check_tool_call(_tc(), **{**BASE, "consent_ok": True}).kind == "go"


def test_cap_refusal_text_is_unchanged():
    v = check_tool_call(_tc(), **{**BASE, "cap": 1, "used": 1})
    assert v.kind == "refuse" and v.result.error == "REFUSED"
    assert v.result.result_text == (
        "Refused: apply_job has already run this turn and its effect cannot be "
        "undone. One per turn. If the caller meant a different one, ask which, "
        "and call it on the next turn.")


def test_ungrounded_refusal_with_no_evidence():
    v = check_tool_call(_tc(job_item_id="made-up"),
                        **{**BASE, "grounded_spec": {"job_item_id": ["fetch_jobs"]},
                           "stored_results": {"fetch_jobs": ["[]"]}})
    assert v.kind == "refuse"
    assert v.result.result_text.startswith("Refused: job_item_id did not come from any tool result")


def test_ungrounded_error_code_is_configurable():
    v = check_tool_call(_tc(job_item_id="made-up"),
                        **{**BASE, "grounded_spec": {"job_item_id": ["fetch_jobs"]},
                           "stored_results": {"fetch_jobs": ["[]"]},
                           "ungrounded_error": "UNGROUNDED_PARAMETER"})
    assert v.result.error == "UNGROUNDED_PARAMETER"


def test_session_grounded_value_grounds_the_call():
    v = check_tool_call(_tc(job_item_id="abc"),
                        **{**BASE, "grounded_spec": {"job_item_id": ["fetch_jobs"]},
                           "stored_results": {"fetch_jobs": ["[]"]},
                           "session_grounded": {"job_item_id": ["abc"]}})
    assert v.kind == "go"


def test_cache_hit_after_guards():
    v = check_tool_call(_tc("fetch_jobs"), **{**BASE, "cache_lookup": _hit})
    assert v.kind == "hit" and v.result.result_text == "[]"


def test_cap_beats_cache_hit():
    v = check_tool_call(_tc("fetch_jobs"), **{**BASE, "cap": 1, "used": 1, "cache_lookup": _hit})
    assert v.kind == "refuse"


# ---------------------------------------------------------------------------
# requires_pending — the tool may only run as the answer to a named question
# ---------------------------------------------------------------------------


def test_no_requirement_declared_is_unaffected():
    assert check_tool_call(_tc(), **{**BASE, "pending_id": "anything"}).kind == "go"


def test_runs_when_the_required_question_is_open():
    v = check_tool_call(_tc(), **{
        **BASE, "requires_pending": "submit_confirm", "pending_id": "submit_confirm"})
    assert v.kind == "go"


def test_refused_when_a_different_question_is_open():
    v = check_tool_call(_tc(), **{
        **BASE, "requires_pending": "submit_confirm", "pending_id": "select_job"})
    assert v.kind == "refuse"
    assert v.result.success is False
    assert v.result.result_text == (
        "Refused: apply_job may only run as the answer to the 'submit_confirm' "
        "question, and that question is not open (pending=select_job). Ask it, "
        "wait for the caller's answer, and call this tool on the turn that "
        "answers it.")


def test_refused_when_no_question_is_open():
    v = check_tool_call(_tc(), **{**BASE, "requires_pending": "submit_confirm"})
    assert v.kind == "refuse"
    assert "pending=none" in v.result.result_text


def test_consent_is_still_checked_before_the_question():
    v = check_tool_call(_tc(), **{
        **BASE, "consent_ok": False, "requires_pending": "submit_confirm"})
    assert v.result.error == "consent_required"


def test_the_question_is_checked_before_the_cap():
    v = check_tool_call(_tc(), **{
        **BASE, "cap": 1, "used": 1, "requires_pending": "submit_confirm",
        "pending_id": "select_job"})
    assert "may only run as the answer" in v.result.result_text
