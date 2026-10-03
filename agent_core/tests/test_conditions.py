"""Tests for the shared routing-condition evaluator."""
from src.conditions import all_conditions, evaluate_condition
from src.workflow_loader import RoutingCondition


def C(field, op, value):
    return RoutingCondition(field=field, operator=op, value=value)


def test_eq_and_not_eq():
    assert evaluate_condition(C("a", "eq", True), {"a": True})
    assert evaluate_condition(C("a", "not_eq", 1), {"a": 2})


def test_in_treats_missing_and_empty_as_unset():
    unset = C("consent_response", "in", [None, ""])
    assert evaluate_condition(unset, {})
    assert evaluate_condition(unset, {"consent_response": ""})
    assert not evaluate_condition(unset, {"consent_response": "granted"})


def test_in_scalar_value():
    assert evaluate_condition(C("a", "in", "x"), {"a": "x"})


def test_gt_lt_coerce_and_fail_closed():
    assert evaluate_condition(C("n", "gt", 0), {"n": "2"})
    assert evaluate_condition(C("n", "lt", 19), {"n": 16})
    assert not evaluate_condition(C("n", "gt", 0), {"n": "abc"})
    assert not evaluate_condition(C("n", "gt", 0), {})


def test_dotted_field_reads_nested_dict_default_zero():
    cond = C("subagent_entry_count.job_match", "gt", 0)
    assert evaluate_condition(cond, {"subagent_entry_count": {"job_match": 1}})
    assert not evaluate_condition(cond, {"subagent_entry_count": {}})
    assert not evaluate_condition(cond, {"subagent_entry_count": "bad"})


def test_unknown_operator_is_false():
    assert not evaluate_condition(C("a", "regex", "x"), {"a": "x"})


def test_all_conditions_empty_is_true():
    assert all_conditions([], {})
    assert not all_conditions([C("a", "eq", 1), C("b", "eq", 2)], {"a": 1, "b": 3})


def test_contains_str_value():
    cond = C("current_question", "contains", "आवेदन भेज दूँ")
    assert evaluate_condition(cond, {"current_question": "क्या मैं इस नौकरी के लिए आवेदन भेज दूँ?"})
    assert not evaluate_condition(cond, {"current_question": "इसके बारे में और बात करें?"})


def test_contains_list_value_matches_any():
    cond = C("current_question", "contains", ["आवेदन भेज दूँ", "आवेदन कर दूँ"])
    assert evaluate_condition(cond, {"current_question": "क्या मैं इसके लिए आवेदन कर दूँ?"})
    assert not evaluate_condition(cond, {"current_question": "किसी एक के बारे में और जानना चाहेंगे?"})
    assert not evaluate_condition(C("q", "contains", []), {"q": "anything"})


def test_contains_missing_or_none_field_is_false():
    cond = C("current_question", "contains", "x")
    assert not evaluate_condition(cond, {})
    assert not evaluate_condition(cond, {"current_question": None})
    assert not evaluate_condition(C("q", "contains", ""), {})


def test_contains_non_str_field_is_stringified():
    assert evaluate_condition(C("n", "contains", "12"), {"n": 3120})
    assert not evaluate_condition(C("n", "contains", "9"), {"n": 3120})
