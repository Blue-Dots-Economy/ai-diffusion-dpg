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


# --- consent recorded in session (consent_session_fields) -------------------

from src.tool_guard import combine_consent, session_consent_ok  # noqa: E402

FIELDS = ["user_terms", "user_privacy", "profile_consent"]
ALL_TRUE = {"user_terms": True, "user_privacy": True, "profile_consent": True}


def test_no_fields_means_no_session_check():
    assert session_consent_ok([], {}) is None
    assert session_consent_ok(None, ALL_TRUE) is None


def test_consent_on_file_passes_without_asking_again():
    assert session_consent_ok(FIELDS, ALL_TRUE) is True


def test_string_true_from_an_upstream_counts():
    assert session_consent_ok(FIELDS, {k: "True" for k in FIELDS}) is True


def test_a_first_time_caller_with_nothing_in_session_is_refused():
    assert session_consent_ok(FIELDS, {}) is False
    assert session_consent_ok(FIELDS, None) is False


def test_any_one_flag_missing_or_false_refuses():
    for f in FIELDS:
        assert session_consent_ok(FIELDS, {**ALL_TRUE, f: False}) is False
        assert session_consent_ok(FIELDS, {k: v for k, v in ALL_TRUE.items() if k != f}) is False
    # Truthy but not true is not consent.
    assert session_consent_ok(FIELDS, {**ALL_TRUE, "profile_consent": "yes"}) is False


def test_session_refusal_reaches_the_guard_as_consent_required():
    v = check_tool_call(_tc("save_profile"),
                        **{**BASE, "consent_ok": session_consent_ok(FIELDS, {})})
    assert v.kind == "refuse" and v.result.error == "consent_required"


def test_combine_consent():
    assert combine_consent(None, None) is None
    assert combine_consent(True, None) is True
    assert combine_consent(None, True) is True
    assert combine_consent(True, False) is False
    assert combine_consent(False, None) is False
