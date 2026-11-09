"""F56: the model must not supply values for fields the framework collects.

Measured on a live call: a seeker who said only their name, trade and city was
sent to save_profile with work_experience="Returning after a break",
experience_years="1 Year" and job_nature="Full-time" — none uttered, all three
empty in session. The prompt already forbade this and was ignored, so the fix
is to stop using the model's value rather than to check it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.tool_guard import apply_session_only


@dataclass
class _Call:
    tool_name: str = "save_profile"
    input_params: dict = field(default_factory=dict)


SPEC = {
    "job_nature": ["job_nature"],
    "work_experience": ["work_experience"],
    "location": ["location", "stored_location"],
}


def test_invented_value_is_dropped_when_session_never_collected_it():
    tc = _Call(input_params={"name": "अजय सिंह", "job_nature": "Full-time",
                             "work_experience": "Returning after a break"})
    dropped = apply_session_only(tc, SPEC, {"name": "अजय सिंह"})
    assert "job_nature" not in tc.input_params
    assert "work_experience" not in tc.input_params
    assert sorted(dropped) == ["job_nature", "work_experience"]
    # A param outside the spec is never touched.
    assert tc.input_params["name"] == "अजय सिंह"


def test_session_value_wins_over_whatever_the_model_supplied():
    tc = _Call(input_params={"job_nature": "Internship"})
    apply_session_only(tc, SPEC, {"job_nature": "Full-time"})
    assert tc.input_params["job_nature"] == "Full-time"


def test_session_value_is_added_even_when_the_model_omitted_it():
    tc = _Call(input_params={})
    apply_session_only(tc, SPEC, {"job_nature": "Full-time"})
    assert tc.input_params["job_nature"] == "Full-time"


def test_first_non_empty_key_wins_so_this_call_beats_the_stored_profile():
    tc = _Call(input_params={})
    apply_session_only(tc, SPEC, {"location": "Bengaluru", "stored_location": "Ghaziabad"})
    assert tc.input_params["location"] == "Bengaluru"


def test_it_falls_back_to_the_stored_value_when_this_call_has_none():
    tc = _Call(input_params={})
    apply_session_only(tc, SPEC, {"location": "", "stored_location": "Ghaziabad"})
    assert tc.input_params["location"] == "Ghaziabad"


def test_empty_and_none_session_values_count_as_absent():
    tc = _Call(input_params={"job_nature": "Full-time", "work_experience": "x"})
    apply_session_only(tc, SPEC, {"job_nature": "", "work_experience": None})
    assert "job_nature" not in tc.input_params
    assert "work_experience" not in tc.input_params


def test_no_spec_and_no_session_are_both_no_ops():
    tc = _Call(input_params={"job_nature": "Full-time"})
    assert apply_session_only(tc, {}, {"job_nature": "x"}) == []
    assert tc.input_params["job_nature"] == "Full-time"
    tc2 = _Call(input_params={"job_nature": "Full-time"})
    assert apply_session_only(tc2, SPEC, None) == ["job_nature"]
    assert "job_nature" not in tc2.input_params


def test_a_call_without_input_params_is_left_alone():
    class _Odd:
        tool_name = "save_profile"
    apply_session_only(_Odd(), SPEC, {"job_nature": "Full-time"})   # must not raise
