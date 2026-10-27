"""The Blue Dots consent flow: what the fetch lifts, and what the write sends."""
import importlib.util
from pathlib import Path

import yaml


def _load_response_path():
    """Load Action Gateway's path resolver by file, so `src` keeps meaning agent_core's."""
    path = (Path(__file__).resolve().parents[2]
            / "action_gateway" / "src" / "adapters" / "response_path.py")
    spec = importlib.util.spec_from_file_location("_ag_response_path", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.resolve


resolve = _load_response_path()

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
AG = yaml.safe_load((BD / "action_gateway.yaml").read_text(encoding="utf-8"))
ML = yaml.safe_load((BD / "memory_layer.yaml").read_text(encoding="utf-8"))


def _seeker_mappings():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "seeker_fetch":
                return {m["target"]: m for m in t["response"]["session_mapping"]}
    raise AssertionError("seeker_fetch not found")


def test_fetch_lifts_the_per_profile_consent_flag():
    m = _seeker_mappings()["profile_consent"]
    assert m["source"] == "items[lifecycle_status=live].profile_consent_accepted"
    assert not m.get("on_error")


def test_profile_consent_resolves_from_the_same_item_as_profile_item_id():
    m = _seeker_mappings()
    assert (m["profile_consent"]["source"].rsplit(".", 1)[0]
            == m["profile_item_id"]["source"].rsplit(".", 1)[0])


def test_fetch_lifts_the_upstream_error_code():
    m = _seeker_mappings()["fetch_error"]
    assert m["source"] == "error"
    assert m["on_error"] is True


def test_consent_flags_are_declared_with_a_false_default():
    schema = ML["state"]["session"]["schema"]
    for field in ("profile_consent", "user_terms", "user_privacy", "has_age"):
        assert schema[field]["type"] == "enum", field
        assert schema[field]["values"] == ["true", "false"], field
        assert schema[field]["default"] == "false", field


def test_fetch_error_is_declared():
    assert ML["state"]["session"]["schema"]["fetch_error"] == {"type": "string", "default": ""}


def test_consent_return_to_is_declared():
    assert ML["state"]["session"]["schema"]["consent_return_to"] == {"type": "string", "default": ""}


_LIVE_BODY = {
    "user_id": "d1455336-fb14-4fc0-a8ef-547213a00831",
    "compliance": [{"key": "user_terms", "value": True},
                   {"key": "user_privacy", "value": True},
                   {"key": "has_age", "value": True}],
    "items": [
        {"item_id": "72e45bfc", "lifecycle_status": "live", "profile_consent_accepted": False},
        {"item_id": "ecf7a8a2", "lifecycle_status": "live", "profile_consent_accepted": True},
    ],
}


def test_newest_live_item_wins_and_its_consent_travels_with_it():
    m = _seeker_mappings()
    assert resolve(_LIVE_BODY, m["profile_item_id"]["source"]) == "72e45bfc"
    assert resolve(_LIVE_BODY, m["profile_consent"]["source"]) is False


def test_an_unknown_caller_yields_no_item_and_no_consent():
    body = {"user_id": None, "compliance": [{"key": "user_terms", "value": False},
                                            {"key": "user_privacy", "value": False},
                                            {"key": "has_age", "value": False}], "items": []}
    m = _seeker_mappings()
    assert resolve(body, m["profile_item_id"]["source"]) is None
    assert resolve(body, m["profile_consent"]["source"]) is None


def test_an_older_upstream_without_the_field_reads_as_absent():
    body = {"items": [{"item_id": "a", "lifecycle_status": "live"}]}
    assert resolve(body, _seeker_mappings()["profile_consent"]["source"]) is None


def _save_profile():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "save_profile":
                return ep
    raise AssertionError("save_profile not found")


def test_compliance_values_come_from_session():
    body = _save_profile()["body_template"]
    assert body["compliance"] == [
        {"key": "user_terms", "value": "{user_terms}"},
        {"key": "user_privacy", "value": "{user_privacy}"},
        {"key": "profile_creation", "value": "{profile_consent}"},
    ]


def test_the_dead_consent_fields_are_gone():
    body = _save_profile()["body_template"]
    assert "terms_accepted" not in body
    assert "privacy_accepted" not in body


def test_consent_params_are_session_sourced_booleans():
    params = {p["name"]: p for p in _save_profile()["params"]}
    for name in ("user_terms", "user_privacy", "profile_consent"):
        assert params[name]["source"] == "session", name
        assert params[name]["type"] == "boolean", name
        assert params[name].get("required") is False, name


from src.understanding.pending import PendingResolver  # noqa: E402
from tests.test_blue_dots_dialogue_act_config import _load  # noqa: E402

_ASKED_CONSENT = "आपकी जानकारी सेव करने की अनुमति चाहिए; यह बातचीत रिकॉर्ड की जा सकती है। ठीक है?"
_ASKED_AGE = "आपकी उम्र क्या है?"


def test_consent_subagent_exists_and_is_not_terminal():
    _, wf = _load()
    sub = wf.subagents["consent"]
    assert sub.is_terminal is False
    assert sub.tools == []


def test_consent_pending_needs_the_consent_question_to_have_been_asked():
    _, wf = _load()
    r = PendingResolver(wf)
    assert r.resolve("consent", {"current_question": _ASKED_CONSENT}).id == "consent"
    assert r.resolve("consent", {"current_question": _ASKED_AGE, "has_age": "false"}).id == "age"


def test_nothing_is_pending_before_the_bot_has_asked_anything():
    _, wf = _load()
    assert PendingResolver(wf).resolve("consent", {"current_question": ""}) is None


def test_age_is_not_asked_when_it_arrived_with_the_consent():
    _, wf = _load()
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": "false", "age": 25}
    assert PendingResolver(wf).resolve("consent", state).id == "consent"


def _profile_setup_entries(wf):
    """Rules that ENTER profile_setup from elsewhere.

    Self-routes and `close_return_to` returns are re-entries: the caller only
    reaches those having already passed the gate, so they are not entry points.
    """
    for sid, sub in wf.subagents.items():
        if sid == "profile_setup":
            continue
        for i, rule in enumerate(sub.routing):
            if rule.next_subagent_id != "profile_setup":
                continue
            if any(c.field == "close_return_to" for c in rule.conditions):
                continue
            yield sid, i, sub.routing


_CONSENT_FLAGS = {"user_terms", "user_privacy", "profile_consent"}


def test_every_entry_into_profile_setup_requires_every_consent_flag():
    _, wf = _load()
    seen = list(_profile_setup_entries(wf))
    assert {sid for sid, _, _ in seen} == {
        "job_match", "job_details", "apply_confirm", "service_match", "consent"}
    for sid, i, routing in seen:
        fields = {c.field for c in routing[i].conditions}
        missing = _CONSENT_FLAGS - fields
        assert not missing, f"{sid} rule {i} enters profile_setup without checking {missing}"
        # An age on file or one the caller just stated; either satisfies the upstream.
        assert fields & {"has_age", "age"}, f"{sid} rule {i} enters profile_setup unaged"


def test_every_subagent_that_enters_profile_setup_can_also_reach_consent():
    _, wf = _load()
    for sid, _, routing in _profile_setup_entries(wf):
        targets = [r.next_subagent_id for r in routing]
        assert "consent" in targets, f"{sid} can enter profile_setup but never consent"


import pytest  # noqa: E402

from src.models import NLUResult  # noqa: E402
from tests.test_stream_turn import _make_agent_core  # noqa: E402

# profile_item_id "" is the create path; absent would read as not_eq "" and route to apply.
_ALL_GIVEN = {"user_terms": True, "user_privacy": True, "profile_consent": True,
              "has_age": True, "profile_item_id": ""}


def _route(sid, state, intent="*"):
    _, wf = _load()
    return _make_agent_core(workflow=wf)._resolve_next_subagent(
        current_subagent=wf.subagents[sid],
        nlu_result=NLUResult(intent=intent, entities={}, confidence=1.0),
        session=state)


@pytest.mark.parametrize("missing", ["user_terms", "user_privacy", "profile_consent", "has_age"])
def test_a_missing_flag_diverts_to_consent(missing):
    state = {**_ALL_GIVEN, missing: False}
    nxt, rule = _route("job_match", state, intent="apply_now")
    assert nxt == "consent"
    assert rule.session_writes["consent_return_to"] == "profile_setup"


def test_every_flag_given_reaches_profile_setup_directly():
    assert _route("job_match", dict(_ALL_GIVEN), intent="apply_now")[0] == "profile_setup"


def test_consent_returns_to_the_phase_that_sent_it():
    state = {**_ALL_GIVEN, "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "profile_setup"


def test_a_brand_new_caller_is_sent_to_consent():
    state = {"user_terms": False, "user_privacy": False, "profile_consent": False,
             "has_age": False, "profile_item_id": ""}
    assert _route("job_match", state, intent="apply_now")[0] == "consent"


def test_granting_writes_every_flag_the_write_needs():
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": True, "consent_return_to": "profile_setup"}
    _, rule = _route("consent", state)
    assert rule.session_writes["user_terms"] is True
    assert rule.session_writes["user_privacy"] is True
    assert rule.session_writes["profile_consent"] is True


def test_the_first_refusal_asks_again():
    state = {"consent_response": "declined", "consent_declines": 0,
             "consent_return_to": "profile_setup"}
    nxt, rule = _route("consent", state)
    assert nxt == "consent"
    assert rule.session_writes["consent_declines"] == 1


def test_the_second_refusal_ends_the_call():
    state = {"consent_response": "declined", "consent_declines": 1,
             "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "consent_declined"


def test_declines_are_counted_separately_from_visits():
    assert ML["state"]["session"]["schema"]["consent_declines"] == {"type": "int", "default": 0}


def test_the_opening_asks_nothing_about_consent_or_age():
    _, wf = _load()
    assert [p.id for p in wf.subagents["opening"].pending] == []


def test_the_opening_routes_a_caller_onward_without_consent():
    assert _route("opening", {"profile_item_id": "p1"})[0] == "profile_resolve"


def test_a_volunteered_consent_before_any_question_is_not_recorded():
    _, wf = _load()
    assert PendingResolver(wf).resolve("opening", {"current_question": ""}) is None


def test_an_age_the_caller_states_is_enough_to_leave_consent():
    """has_age only ever comes from the upstream fetch; a spoken age must also satisfy the gate."""
    state = {"user_terms": True, "user_privacy": True, "profile_consent": True,
             "age": 25, "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "profile_setup"


def test_an_upstream_u18_rejection_ends_the_call_at_the_opening():
    assert _route("opening", {"fetch_error": "U18_NOT_ALLOWED"})[0] == "u18_blocked"


def test_an_under_19_answer_in_the_consent_phase_ends_the_call():
    state = {"current_question": _ASKED_AGE, "age": 17, "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "u18_blocked"


def test_other_fetch_failures_do_not_block_the_call():
    assert _route("opening", {"fetch_error": "INTERNAL_ERROR"})[0] == "profile_resolve"


def test_the_under_19_line_says_where_the_age_came_from():
    _, wf = _load()
    line = wf.subagents["u18_blocked"].opening_phrase
    assert "जानकारी के अनुसार" in line
    assert "उन्नीस" in line
    assert "पोर्टल" in line
