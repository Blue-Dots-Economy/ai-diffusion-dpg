from src.predispatch.rules import is_empty, resolve_args, select

TABLES = {"city_canonical": {"Bangalore": "Bengaluru", "Bombay": "Mumbai"}, "name_placeholders": ["unknown", "n/a"]}
JOBS_SCHEMA = {"properties": {"query_text": {"type": "string"}, "offset": {"type": "integer"}}, "required": ["query_text"]}
APPLY_SCHEMA = {"properties": {"profile_item_id": {"type": "string"},
                               "job_item_id": {"type": "string", "format": "uuid"}},
                "required": ["profile_item_id", "job_item_id"]}
PROFILE_SCHEMA = {"properties": {"name": {"type": "string"}, "gender": {"type": "string", "enum": ["Male", "Female", "Other"]},
                                 "experience_years": {"type": "string"}},
                  "required": ["name"]}
JOBS_RULE = {"tool": "fetch_jobs", "unless_fresh": True,
             "args": {"query_text": {"template": "{trade|stored_trade} jobs in {location|stored_location}",
                                     "normalise": {"location": "city_canonical"}}}}
UUID = "3f2b8c1e-9a4d-4e2f-8b1a-0c9d8e7f6a5b"


def test_is_empty():
    assert all(is_empty(v) for v in (None, "", [], 0, 0.0, "0"))
    assert not any(is_empty(v) for v in ("x", 5, False, True, [1]))


def test_template_with_fallback_and_normalise():
    args, why = resolve_args(JOBS_RULE, {"stored_trade": "Welder", "location": "Bangalore"}, TABLES, JOBS_SCHEMA)
    assert (args, why) == ({"query_text": "Welder jobs in Bengaluru"}, "")


def test_normalise_passthrough_when_not_in_table():
    args, _ = resolve_args(JOBS_RULE, {"trade": "Plumber", "location": "Hubballi"}, TABLES, JOBS_SCHEMA)
    assert args == {"query_text": "Plumber jobs in Hubballi"}


def test_missing_required_placeholder_blocks():
    assert resolve_args(JOBS_RULE, {"trade": "Welder"}, TABLES, JOBS_SCHEMA) == (None, "skipped_missing_arg")


def test_session_binding_uuid_validation():
    rule = {"tool": "apply_job", "args": {"profile_item_id": {"from": "session", "key": "profile_item_id"},
                                          "job_item_id": {"from": "session", "key": "selected_job_item_id"}}}
    ok, _ = resolve_args(rule, {"profile_item_id": "p1", "selected_job_item_id": UUID}, TABLES, APPLY_SCHEMA)
    assert ok == {"profile_item_id": "p1", "job_item_id": UUID}
    assert resolve_args(rule, {"profile_item_id": "p1", "selected_job_item_id": "तीसरा"}, TABLES, APPLY_SCHEMA) == \
        (None, "skipped_invalid_arg")
    assert resolve_args(rule, {"selected_job_item_id": UUID}, TABLES, APPLY_SCHEMA) == (None, "skipped_missing_arg")


def test_optional_args_omitted_enum_map_coercion_and_reject():
    rule = {"tool": "save_profile", "args": {
        "name": {"from": "session", "key": "name", "reject": "name_placeholders"},
        "gender": {"from": "session", "key": "gender", "normalise": {"male": "Male", "female": "Female"}},
        "experience_years": {"from": "session", "key": "experience_years"}}}
    ok, _ = resolve_args(rule, {"name": "अजय सिंह", "gender": "male", "experience_years": 3}, TABLES, PROFILE_SCHEMA)
    assert ok == {"name": "अजय सिंह", "gender": "Male", "experience_years": "3"}
    ok2, _ = resolve_args(rule, {"name": "अजय सिंह"}, TABLES, PROFILE_SCHEMA)
    assert ok2 == {"name": "अजय सिंह"}
    assert resolve_args(rule, {"name": "Unknown"}, TABLES, PROFILE_SCHEMA) == (None, "skipped_invalid_arg")
    assert resolve_args(rule, {"name": "x", "gender": "robot"}, TABLES, PROFILE_SCHEMA) == (None, "skipped_invalid_arg")


def test_literal_and_builtin_normalise():
    rule = {"tool": "t", "args": {"a": {"from": "literal", "value": "fixed"},
                                  "b": {"from": "session", "key": "b", "normalise": "title"}}}
    assert resolve_args(rule, {"b": "welder"}, TABLES, None) == ({"a": "fixed", "b": "Welder"}, "")


def _sel(rules, **kw):
    base = dict(intent="any_input", state={}, session={}, tables=TABLES,
                tool_schemas={"fetch_jobs": JOBS_SCHEMA, "apply_job": APPLY_SCHEMA},
                write_tools={"apply_job"}, has_fresh=lambda t: False)
    base.update(kw)
    return select(rules, **base)


def test_select_first_eligible_rule():
    s = _sel([JOBS_RULE], session={"trade": "Welder", "location": "Bengaluru"})
    assert (s.tool, s.args, s.outcome, s.is_write) == ("fetch_jobs", {"query_text": "Welder jobs in Bengaluru"}, "fired", False)


def test_unless_fresh_skips():
    s = _sel([JOBS_RULE], session={"trade": "Welder", "location": "Bengaluru"}, has_fresh=lambda t: t == "fetch_jobs")
    assert (s.tool, s.outcome) == (None, "skipped_fresh")


def test_on_intent_and_when_gate():
    rule = {"tool": "apply_job", "enabled": True, "on_intent": ["apply_now"],
            "when": [{"field": "applications_submitted", "operator": "eq", "value": 0}],
            "args": {"profile_item_id": {"from": "session", "key": "p"}, "job_item_id": {"from": "session", "key": "j"}}}
    sess = {"p": "p1", "j": UUID}
    assert _sel([rule], intent="any_input", session=sess, state={"applications_submitted": 0}).outcome is None
    assert _sel([rule], intent="apply_now", session=sess, state={"applications_submitted": 1}).outcome is None
    s = _sel([rule], intent="apply_now", session=sess, state={"applications_submitted": 0})
    assert (s.tool, s.is_write, s.outcome) == ("apply_job", True, "fired")


def test_disabled_rule_reports_disabled_and_never_fires():
    rule = {"tool": "apply_job", "enabled": False, "on_intent": ["apply_now"],
            "args": {"profile_item_id": {"from": "session", "key": "p"}, "job_item_id": {"from": "session", "key": "j"}}}
    s = _sel([rule], intent="apply_now", session={"p": "p1", "j": UUID})
    assert (s.tool, s.outcome) == (None, "disabled")


def test_no_matching_rule_no_outcome():
    assert _sel([], session={}).outcome is None


def test_select_never_raises_on_bad_rule():
    s = _sel([{"tool": "fetch_jobs", "when": [{"field": None}], "args": "nonsense"}])
    assert s.tool is None and s.outcome == "error"
