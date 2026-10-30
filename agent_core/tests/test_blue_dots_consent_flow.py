"""The Blue Dots consent flow: what the fetch lifts, and what the write sends."""
import importlib.util
from pathlib import Path

import pytest
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
    return _endpoint("save_profile")


def _endpoint(name):
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == name:
                return ep
    raise AssertionError(f"{name} not found")


WRITE_ENDPOINTS = ["save_profile", "update_profile"]


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_compliance_values_come_from_session(endpoint):
    body = _endpoint(endpoint)["body_template"]
    assert body["compliance"] == [
        {"key": "user_terms", "value": "{user_terms}"},
        {"key": "user_privacy", "value": "{user_privacy}"},
        {"key": "profile_creation", "value": "{profile_consent}"},
    ]


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_the_dead_consent_fields_are_gone(endpoint):
    body = _endpoint(endpoint)["body_template"]
    assert "terms_accepted" not in body
    assert "privacy_accepted" not in body


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_consent_params_are_session_sourced_booleans(endpoint):
    params = {p["name"]: p for p in _endpoint(endpoint)["params"]}
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
    assert r.resolve("consent", {"current_question": _ASKED_AGE, "has_age": "false",
                                 "profile_consent": True}).id == "age"


def test_nothing_is_pending_before_the_bot_has_asked_anything():
    _, wf = _load()
    assert PendingResolver(wf).resolve("consent", {"current_question": ""}) is None


def test_age_is_not_asked_when_it_arrived_with_the_consent():
    _, wf = _load()
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": "false", "age": 25}
    assert PendingResolver(wf).resolve("consent", state).id == "consent"


def _write_phase_entries(wf, phase):
    """Rules that ENTER a participant-write phase from elsewhere.

    Self-routes and `close_return_to` returns are re-entries: the caller only
    reaches those having already passed the gate, so they are not entry points.
    """
    for sid, sub in wf.subagents.items():
        if sid == phase:
            continue
        for i, rule in enumerate(sub.routing):
            if rule.next_subagent_id != phase:
                continue
            if any(c.field == "close_return_to" for c in rule.conditions):
                continue
            yield sid, i, sub.routing


_CONSENT_FLAGS = {"user_terms", "user_privacy", "profile_consent"}

# The phase holding each participant write, and every phase that can enter it.
WRITE_PHASES = {
    "profile_setup": {"job_match", "job_details", "apply_confirm", "service_match",
                      "consent", "consent_reask"},
    "profile_update": {"trade_ask", "profile_resolve", "job_match", "apply_confirm",
                       "service_match", "consent", "consent_reask"},
}


@pytest.mark.parametrize("phase, entrants", WRITE_PHASES.items())
def test_every_entry_into_a_write_phase_requires_every_consent_flag(phase, entrants):
    _, wf = _load()
    seen = list(_write_phase_entries(wf, phase))
    assert {sid for sid, _, _ in seen} == entrants
    for sid, i, routing in seen:
        rule = routing[i]
        fields = {c.field for c in rule.conditions}
        # A rule either checks the flags, or IS the grant and writes them all true.
        granted = all(rule.session_writes.get(f) is True for f in _CONSENT_FLAGS)
        missing = set() if granted else _CONSENT_FLAGS - fields
        assert not missing, f"{sid} rule {i} enters {phase} without checking {missing}"
        # An age on file or one the caller just stated; either satisfies the upstream.
        assert fields & {"has_age", "age"}, f"{sid} rule {i} enters {phase} unaged"


@pytest.mark.parametrize("phase", list(WRITE_PHASES))
def test_every_subagent_that_enters_a_write_phase_can_also_reach_consent(phase):
    _, wf = _load()
    for sid, _, routing in _write_phase_entries(wf, phase):
        targets = [r.next_subagent_id for r in routing]
        assert "consent" in targets, f"{sid} can enter {phase} but never consent"


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


_HAS_PROFILE = {**_ALL_GIVEN, "profile_item_id": "p1"}


@pytest.mark.parametrize("sid", ["trade_ask", "profile_resolve", "job_match",
                                 "apply_confirm", "service_match"])
def test_asking_to_update_with_consent_on_file_reaches_the_update_phase(sid):
    nxt, _ = _route(sid, dict(_HAS_PROFILE), intent="update_profile_request")
    assert nxt == "profile_update"


@pytest.mark.parametrize("missing", ["user_terms", "user_privacy", "profile_consent", "has_age"])
def test_a_missing_flag_diverts_the_update_to_consent(missing):
    state = {**_HAS_PROFILE, missing: False}
    nxt, rule = _route("job_match", state, intent="update_profile_request")
    assert nxt == "consent"
    assert rule.session_writes["consent_return_to"] == "profile_update"
    # The destination the update returns to must survive the detour.
    assert rule.session_writes["update_return_to"] == "job_match"


def test_consent_returns_to_the_update_phase_that_sent_it():
    state = {**_HAS_PROFILE, "consent_return_to": "profile_update"}
    assert _route("consent", state)[0] == "profile_update"


def test_granting_at_the_update_gate_does_not_divert_into_profile_setup():
    """The grant rules are shared; without the return check a grant creates a second profile."""
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": True, "profile_item_id": "p1",
             "consent_return_to": "profile_update"}
    for phase in ("consent", "consent_reask"):
        nxt, rule = _route(phase, dict(state))
        assert nxt == "profile_update", phase
        assert all(rule.session_writes[f] is True
                   for f in ("user_terms", "user_privacy", "profile_consent")), phase


@pytest.mark.parametrize("sid", ["trade_ask", "profile_resolve", "job_match",
                                 "apply_confirm", "service_match"])
def test_a_caller_with_no_profile_is_never_sent_to_the_update_phase(sid):
    """profile_update cannot run without an item_id; its own write requires one."""
    state = {**_ALL_GIVEN, "profile_item_id": ""}
    nxt, _ = _route(sid, state, intent="update_profile_request")
    assert nxt != "profile_update", sid


@pytest.mark.parametrize("sid", ["trade_ask", "profile_resolve", "job_match", "service_match"])
def test_an_update_request_without_a_profile_does_not_ask_for_permission(sid):
    """Nothing is about to be written, so there is nothing to ask permission for."""
    state = {"profile_item_id": "", "user_terms": False, "user_privacy": False,
             "profile_consent": False, "has_age": False}
    nxt, rule = _route(sid, state, intent="update_profile_request")
    assert rule.session_writes.get("consent_return_to") != "profile_update", sid


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


def test_the_second_refusal_ends_the_call():
    state = {"consent_response": "declined", "consent_declines": 1,
             "consent_return_to": "profile_setup"}
    assert _route("consent_reask", state)[0] == "consent_declined"


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


def test_the_age_block_line_says_where_the_age_came_from():
    _, wf = _load()
    line = wf.subagents["u18_blocked"].opening_phrase
    assert "जानकारी के अनुसार" in line
    assert "अठारह" in line
    assert "उन्नीस" not in line
    assert "पोर्टल" in line


# ── Fix pass: findings from the whole-branch review ──────────────────────────

def test_bootstrap_keeps_session_values_from_a_failed_fetch():
    """C1: the U18 rejection arrives as a 400; its body is the only place the code lives."""
    from src.models import ToolResult
    from src.session_bootstrap import SessionBootstrap

    cfg = {"session_bootstrap": {"timeout_ms": 2000, "steps": [{"type": "tool", "tool": "fetch_profile"}]}}
    boot = SessionBootstrap.from_config(cfg, policies={})

    class _Bundle:
        session = {}
        tool_results = []

    bundle = _Bundle()
    boot.run_sync(
        bundle,
        execute=lambda call: ToolResult(tool_use_id="", tool_name="fetch_profile", result={},
                                        success=False, error="U18_NOT_ALLOWED",
                                        session_values={"fetch_error": "U18_NOT_ALLOWED"}),
        check_consent=lambda tool: True,
        write_session=lambda k, v: None,
        apply_tool_results=lambda batch: None,
    )
    assert bundle.session.get("fetch_error") == "U18_NOT_ALLOWED"


def test_granting_with_an_age_on_file_leaves_consent_at_once():
    """C2a: the grant rule must not sit above the return rules for an already-aged caller."""
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": True, "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "profile_setup"


def test_the_consent_pending_shuts_once_consent_is_held():
    """C2b: current_question still holds the consent question on the turn after the grant."""
    _, wf = _load()
    state = {"current_question": _ASKED_CONSENT, "user_terms": True,
             "user_privacy": True, "profile_consent": True}
    resolved = PendingResolver(wf).resolve("consent", state)
    assert resolved is None or resolved.id != "consent"


def test_a_caller_can_end_the_call_from_the_consent_phase():
    """I1: termination_blocked is what a goodbye derives to here, and it had nowhere to go."""
    assert _route("consent", {}, intent="termination_blocked")[0] == "confirm_close"
    assert _route("consent", {"subagent_entry_count": {"confirm_close": 1}},
                  intent="termination_blocked")[0] == "ended"


def test_the_saved_details_anchor_matches_the_line_actually_spoken():
    """I2: the anchor must appear in fixed_opening, which short-circuits the model entirely."""
    _, wf = _load()
    spoken = wf.subagents["profile_resolve"].fixed_opening.format(
        stored_trade="Welder", stored_location="Bengaluru")
    pending = next(p for p in wf.subagents["profile_resolve"].pending if p.id == "use_saved_details")
    needles = next(c.value for c in pending.when if c.field == "current_question")
    assert any(n in spoken for n in needles), f"{needles} not in {spoken!r}"


def test_the_consent_flags_are_visible_to_the_prompt():
    """I3: the two phrasings branch on flags the model cannot otherwise see."""
    cfg, _ = _load()
    visible = set(cfg["agent"]["prompt_session_fields"])
    assert {"user_terms", "user_privacy", "profile_consent", "has_age"} <= visible


def test_consent_is_a_destination_in_the_close_return_tables():
    """I5: otherwise a handoff from consent resumes at `opening` and restarts the journey."""
    _, wf = _load()
    for sid in ("confirm_close", "handoff"):
        targets = {r.next_subagent_id for r in wf.subagents[sid].routing}
        assert "consent" in targets, sid


def test_a_refusal_does_not_follow_the_caller_into_the_next_call():
    """I6: session adoption carries state over; a prior refusal must not count against a new call."""
    spec = importlib.util.spec_from_file_location(
        "_ml", Path(__file__).resolve().parents[2] / "memory_layer" / "src" / "memory_layer.py")
    src = spec.origin and Path(spec.origin).read_text(encoding="utf-8")
    block = src[src.index("_SESSION_LIFECYCLE_FIELDS"):src.index("})", src.index("_SESSION_LIFECYCLE_FIELDS"))]
    for field in ("consent_declines", "fetch_error"):
        assert f'"{field}"' in block, field


_CONSENT_ANCHORS = ["अनुमति", "रिकॉर्ड", "सेव"]


def _anchor_words(sa):
    return [w for w in _CONSENT_ANCHORS if w in (sa.fixed_opening or "")]


def test_consent_speaks_its_question_without_the_model():
    _, wf = _load()
    sa = wf.subagents["consent"]
    assert sa.fixed_opening, "the entry turn must not depend on the model asking"
    assert sa.fixed_opening_requires == ["consent_return_to"]


def test_the_consent_question_carries_a_pending_anchor():
    _, wf = _load()
    sa = wf.subagents["consent"]
    pending = next(p for p in sa.pending if p.id == "consent")
    anchored = next(c for c in pending.when if c.field == "current_question")
    assert set(_anchor_words(sa)) & set(anchored.value), (
        "the spoken question must contain a word the pending matches on")


def test_every_gate_site_writes_the_field_the_consent_opening_requires():
    _, wf = _load()
    required = wf.subagents["consent"].fixed_opening_requires[0]
    for sid in ("job_match", "job_details", "apply_confirm", "service_match"):
        for rule in wf.subagents[sid].routing:
            if rule.next_subagent_id == "consent":
                assert (rule.session_writes or {}).get(required), (
                    f"{sid} enters consent without writing {required}")


def test_the_first_refusal_goes_to_its_own_reask_phase():
    state = {"consent_response": "declined", "consent_declines": 0,
             "consent_return_to": "profile_setup"}
    nxt, rule = _route("consent", state)
    assert nxt == "consent_reask"
    assert rule.session_writes["consent_declines"] == 1
    assert rule.session_writes["consent_response"] == ""


def test_the_reask_is_spoken_without_the_model():
    _, wf = _load()
    sa = wf.subagents["consent_reask"]
    assert sa.fixed_opening, "the re-ask must not depend on the model"
    assert _anchor_words(sa), "the re-ask must carry a pending anchor"
    assert sa.fixed_opening_requires == ["consent_return_to"]


def test_the_reask_names_what_refusing_costs():
    _, wf = _load()
    line = wf.subagents["consent_reask"].fixed_opening
    assert "आवेदन" in line, "the caller must learn no application can be sent"


def test_refusing_the_reask_ends_the_call():
    nxt, _ = _route("consent_reask", {"consent_response": "declined",
                                      "consent_declines": 1})
    assert nxt == "consent_declined"


def test_granting_at_the_reask_moves_on_like_the_first_ask():
    state = {"consent_response": "granted", "has_age": True,
             "consent_declines": 1, "consent_return_to": "profile_setup"}
    nxt, rule = _route("consent_reask", state)
    assert nxt == "profile_setup"
    for flag in ("user_terms", "user_privacy", "profile_consent"):
        assert rule.session_writes[flag] is True


def test_an_under_age_caller_is_blocked_from_the_reask_too():
    nxt, _ = _route("consent_reask", {"age": 16, "consent_declines": 1})
    assert nxt == "u18_blocked"


def test_the_reask_keeps_its_own_consent_pending():
    _, wf = _load()
    assert any(p.id == "consent" for p in wf.subagents["consent_reask"].pending)


def test_the_age_pending_does_not_need_the_question_to_be_echoed():
    """consent exists to collect consent and age; a stated age must land either way."""
    _, wf = _load()
    pending = next(p for p in wf.subagents["consent"].pending if p.id == "age")
    assert not [c for c in pending.when if c.field == "current_question"], (
        "a caller who answers the age question off-script loses their answer")
    # Only after permission is held, so a stray number earlier is not read as an age.
    assert {c.field for c in pending.when} == {"profile_consent", "has_age", "age"}


def test_the_opening_never_instructs_the_model_to_ask_for_consent_or_age():
    """The redesign moved both out of the opening; stale prompt text re-created them."""
    _, wf = _load()
    sa = wf.subagents["opening"]
    banned = ("MANDATORY", "Ask only for what is false or missing",
              "user_terms", "has_age", "re-consent")
    for phrase in banned:
        assert phrase not in sa.system_prompt, (
            f"opening prompt still steers the model toward consent/age: {phrase!r}")
    assert "consent" not in sa.description.lower()
    assert "age" not in sa.description.lower()


def test_a_caller_with_nothing_on_file_gets_a_spoken_first_turn():
    """Turn 1 was model-written for new callers, and asked for consent in 43% of calls."""
    _, wf = _load()
    sa = wf.subagents["trade_ask"]
    assert sa.fixed_opening
    assert sa.fixed_opening_requires == ["journey_id"]
    for word in ("अनुमति", "सेव", "उम्र"):
        assert word not in sa.fixed_opening, f"the first turn must not raise {word!r}"


def test_the_opening_sends_a_new_caller_to_that_turn_and_a_returning_one_past_it():
    assert _route("opening", {"stored_trade": ""})[0] == "trade_ask"
    assert _route("opening", {"stored_trade": "Welder"})[0] == "profile_resolve"


def test_the_first_turn_hands_straight_on():
    assert _route("trade_ask", {"trade": "Welder"})[0] == "profile_resolve"


def test_a_caller_can_still_end_the_call_from_the_first_turn():
    assert _route("trade_ask", {}, intent="termination_intent")[0] == "ended"
    assert _route("trade_ask", {}, intent="termination_blocked")[0] == "confirm_close"


def test_the_consent_prompts_do_not_mix_english_into_the_line_to_speak():
    """An instruction written half in English came back with "behalf" inside the Hindi reply."""
    _, wf = _load()
    for sid in ("consent", "consent_reask"):
        prompt = wf.subagents[sid].system_prompt
        assert "behalf" not in prompt, (
            f"{sid}: an English phrase inside the sentence to speak is copied through")
        assert "हिंदी" in prompt or "Hindi only" in prompt, (
            f"{sid}: the prompt must say the spoken reply is Hindi only")


def test_the_first_turn_does_not_swallow_a_request_for_help():
    """trade_ask sits in front of profile_resolve, so it must route what that phase routes."""
    assert _route("trade_ask", {}, intent="services_request")[0] == "service_match"


def test_the_first_turn_passes_on_every_intent_profile_resolve_acts_on():
    _, wf = _load()
    def targets(sid):
        return {r.intent: r.next_subagent_id for r in wf.subagents[sid].routing
                if r.intent != "*" and r.next_subagent_id not in (sid, "ended", "confirm_close")}
    missing = {i: t for i, t in targets("profile_resolve").items()
               if t != "job_match" and i not in targets("trade_ask")}
    assert not missing, f"trade_ask swallows {missing} before profile_resolve can act"


_REFUSED_TWICE = {"user_terms": False, "user_privacy": False, "profile_consent": False,
                  "has_age": False, "profile_item_id": "", "consent_declines": 1,
                  "consent_given": False}


def test_a_second_refusal_asks_what_else_instead_of_ending_the_call():
    _, wf = _load()
    phase = wf.subagents["consent_declined"]
    assert phase.is_terminal is False
    assert "कॉल यहीं ख़त्म करूँ" in phase.system_prompt
    assert any(p.id == "closing_offer" for p in phase.pending)


def test_the_offer_after_a_refusal_reaches_the_job_search():
    for intent in ("explore_more", "job_pick", "apply_now"):
        assert _route("consent_declined", dict(_REFUSED_TWICE), intent=intent)[0] == "job_match", intent


def test_the_offer_after_a_refusal_reaches_the_services():
    nxt, rule = _route("consent_declined", dict(_REFUSED_TWICE), intent="services_request")
    assert nxt == "service_match"
    assert rule.session_writes["services_in_play"] == "true"


def test_the_offer_after_a_refusal_ends_the_call_when_nothing_else_is_wanted():
    for intent in ("termination_intent", "services_declined", "decline"):
        assert _route("consent_declined", dict(_REFUSED_TWICE), intent=intent)[0] == "ended", intent


def test_a_second_refusal_clears_the_answer_so_the_question_can_be_asked_again():
    """A stale "declined" would fire the decline rule before the caller spoke."""
    state = {**_REFUSED_TWICE, "consent_response": "declined"}
    _, rule = _route("consent", state)
    assert rule.next_subagent_id == "consent_declined"
    assert rule.session_writes["consent_response"] == ""


def test_a_caller_who_refused_twice_can_still_ask_to_apply():
    assert _route("job_match", dict(_REFUSED_TWICE), intent="apply_now")[0] == "consent"


def test_a_refusal_on_a_later_visit_skips_the_re_ask():
    state = {**_REFUSED_TWICE, "consent_response": "declined"}
    assert _route("consent", state)[0] == "consent_declined"


def test_a_refusal_routes_on_the_act_not_only_the_extracted_slot():
    """acts=deny on the consent pending derives `decline`; the slot is filled less reliably."""
    assert _route("consent", {"consent_declines": 0}, intent="decline")[0] == "consent_reask"
    assert _route("consent", {"consent_declines": 1}, intent="decline")[0] == "consent_declined"
    assert _route("consent_reask", {"consent_declines": 1}, intent="decline")[0] == "consent_declined"


def test_a_refusal_counted_once_is_counted_again_on_the_act_route():
    _, rule = _route("consent", {"consent_declines": 0}, intent="decline")
    assert rule.session_writes["consent_declines"] == 1
    assert rule.session_writes["consent_response"] == ""


@pytest.mark.parametrize("sid", ["job_match", "job_details", "service_match",
                                 "profile_update"])
def test_a_phase_with_no_write_tool_may_not_claim_one_ran(sid):
    """The caller hangs up believing they applied; nothing was sent."""
    _, wf = _load()
    phase = wf.subagents[sid]
    assert not ({"save_profile", "update_profile", "apply_job", "connect_service"}
                & set(phase.tools or []))
    assert "never say an application has been or is being sent" in phase.system_prompt.lower(), (
        f"{sid} has no write tool but its prompt does not forbid claiming a send")


def test_the_decline_phase_is_not_allowed_to_hang_up():
    """It asks a question now; ending the call there drops the line mid-question."""
    cfg = yaml.safe_load((BD / "agent_core.yaml").read_text(encoding="utf-8"))
    names = cfg["conversation"]["session_end_eval"]["subagents"]
    assert "consent_declined" not in names
    assert {"ended", "u18_blocked"} <= set(names)


def test_no_prompt_tells_the_model_to_end_the_call_on_a_refusal():
    cfg = yaml.safe_load((BD / "agent_core.yaml").read_text(encoding="utf-8"))
    prompt = cfg["conversation"]["session_end_eval"]["prompt"]
    assert "REFUSES consent" not in prompt


def test_the_shared_flow_description_matches_the_phases_that_exist():
    """agent_system_prompt goes into every phase, so a stale line there is spoken anywhere."""
    cfg = yaml.safe_load((BD / "agent_core.yaml").read_text(encoding="utf-8"))
    shared = cfg["agent_workflow"]["agent_system_prompt"]
    for stale in ("Under 19", "Five phases", "a refusal goes to ended"):
        assert stale not in shared, f"stale flow text still reaches every phase: {stale!r}"
