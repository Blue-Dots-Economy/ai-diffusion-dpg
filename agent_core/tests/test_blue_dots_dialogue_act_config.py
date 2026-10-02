"""The Blue Dots domain config validates; pending questions resolve as designed."""
from pathlib import Path

import pytest

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.understanding.config import DialogueActConfig
from src.understanding.pending import PendingResolver
from src.workflow_loader import AgentWorkflowLoader

BLUE_DOTS = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"


def _load():
    cfg = load_merged_config(BLUE_DOTS)
    MergedConfig.validate_full(cfg)
    wf = AgentWorkflowLoader().load(config=cfg, tool_registry=ToolRegistry(cfg, OfflineGateway(cfg)))
    return cfg, wf


def test_blue_dots_config_loads():
    cfg, _ = _load()
    da = DialogueActConfig.from_config(cfg)
    assert {"consent_response", "age", "trade", "location", "name"} <= set(da.slots)
    assert da.slots["age"].min == 5 and da.slots["age"].max == 99


@pytest.mark.parametrize("step, state, expected", [
    ("opening", {}, "consent"),
    ("opening", {"consent_response": "granted"}, "age"),
    ("opening", {"consent_given": True, "has_age": True}, None),
    # Live shape: Memory Layer seeds int age and has returned it as "0".
    ("opening", {"opening_phrase_emitted": True, "consent_response": "granted", "consent_given": True,
                 "has_age": "false", "age": "0"}, "age"),
    ("opening", {"consent_response": "granted", "consent_given": True, "age": 0}, "age"),
    ("profile_resolve", {"profile_item_id": "p1", "subagent_entry_count": {"profile_resolve": 1}},
     "use_saved_details"),
    ("profile_resolve", {"profile_item_id": "p1", "subagent_entry_count": {"profile_resolve": 2}}, "trade"),
    ("profile_resolve", {"profile_item_id": "", "subagent_entry_count": {"profile_resolve": 1}}, "trade"),
    ("job_match", {}, "select_job"),
    ("apply_confirm", {"applications_submitted": 0}, "submit_confirm"),
    ("apply_confirm", {"applications_submitted": 1}, "closing_offer"),
])
def test_pending_resolution(step, state, expected):
    _, wf = _load()
    p = PendingResolver(wf).resolve(step, state)
    assert (p.id if p else None) == expected


def test_off_track_rule_precedes_catch_all_everywhere():
    _, wf = _load()
    for sid, sub in wf.subagents.items():
        if sub.is_terminal or not sub.routing:
            continue
        intents = [r.intent for r in sub.routing]
        if "*" in intents:
            assert "off_track" in intents and intents.index("off_track") < intents.index("*"), sid

def test_blue_dots_journey_routes_end_to_end():
    """Real TurnUnderstander + real routing over the Blue Dots workflow, scripted NLU (spec §12)."""
    from eval.nlu.offline import StaticToolCache
    from src.understanding.dialogue_act_nlu import DialogueActNLUBase
    from src.understanding.models import DialogueActResult
    from src.understanding.understander import TurnContext, TurnUnderstander
    from tests.test_stream_turn import _make_agent_core

    class _Scripted(DialogueActNLUBase):
        def __init__(self, results):
            self._results = list(results)

        def classify(self, user_message):
            return self._results.pop(0), None, 1

    def R(*acts, relation="answers_pending", option=None, **slots):
        return DialogueActResult(acts=acts, relation=relation, option=option, slots=slots)

    seeds = {"opening_phrase_emitted": True, "trade": "", "location": "", "name": "", "age": 0,
             "profile_item_id": "", "applications_submitted": 0}
    jobs = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
            {"item_id": "j2", "role": "Welder", "company": "Titan"}]
    # (subagent the turn starts in, scripted NLU result, tool effects landing after routing)
    script = [
        ("opening", R("affirm", consent_response="granted"), {}),
        ("opening", R("provide_info", age=25), {}),
        ("profile_resolve", R("provide_info", trade="welder"), {}),
        ("profile_resolve", R("provide_info", location="bengaluru"), {}),
        ("job_match", R("other", relation="unrelated"), {}),
        ("job_match", R("other", relation="unrelated"), {}),
        ("job_match", R("other", relation="unclear"), {}),                       # 3rd → off_track
        ("job_match", R("select", option=1), {}),
        ("profile_setup", R("provide_info", name="Arun"), {"profile_item_id": "p1"}),  # save_profile mapping
        ("profile_setup", R("affirm", relation="answers_other"), {}),
        ("apply_confirm", R("acknowledge", relation="unclear"), {}),             # thank-you before submit
        ("apply_confirm", R("affirm"), {"applications_submitted": 1}),           # apply_job mapping
        ("apply_confirm", R("acknowledge"), {}),
    ]
    cfg, wf = _load()
    und = TurnUnderstander(DialogueActConfig.from_config(cfg), wf, _Scripted([r for _, r, _ in script]))
    agent = _make_agent_core(workflow=wf)
    state, sid, trail = dict(seeds), "opening", []
    for expected_step, _, tool_effects in script:
        assert sid == expected_step, trail
        u = und.understand(TurnContext(subagent_id=sid, state=dict(state), session=dict(state), segments=["x"],
                                       recent=[], tool_cache=StaticToolCache({"fetch_jobs": jobs})))
        for w in u.writes:
            state[w.key] = w.value
        nxt, rule = agent._resolve_next_subagent(current_subagent=wf.subagents[sid], nlu_result=u.nlu_result,
                                                 session=state)
        state.update(rule.session_writes if rule else {})
        state.update(tool_effects)
        trail.append((u.nlu_result.intent, nxt))
        sid = nxt
    intents = [i for i, _ in trail]
    assert trail[6] == ("off_track", "job_match")
    assert intents[7] == "job_pick" and state["selected_job_item_id"] == "j1"
    assert trail[10] == ("any_input", "apply_confirm")        # no apply, no hang-up on a thank-you
    assert intents[11] == "apply_now"
    assert sid == "ended"
    assert (state["trade"], state["location"], state["name"], state["consent_response"]) == (
        "Welder", "Bengaluru", "Arun", "granted")



def _understand_once(step, state, result):
    from src.understanding.dialogue_act_nlu import DialogueActNLUBase
    from src.understanding.understander import TurnContext, TurnUnderstander

    class _One(DialogueActNLUBase):
        def classify(self, user_message):
            return result, None, 1

    cfg, wf = _load()
    und = TurnUnderstander(DialogueActConfig.from_config(cfg), wf, _One())
    return und.understand(TurnContext(subagent_id=step, state=dict(state), session=dict(state),
                                      segments=["x"], recent=[]))


def test_age_given_with_consent_is_accepted():
    """F4: 'हाँ, मेरी उम्र 25 है' while consent is pending keeps both slots."""
    from src.understanding.models import DialogueActResult
    u = _understand_once("opening", {}, DialogueActResult(
        acts=("affirm", "provide_info"), relation="answers_pending",
        slots={"consent_response": "granted", "age": 25}))
    assert u.pending_id == "consent"
    assert u.accepted_slots == {"consent_response": "granted", "age": 25}
    assert {w.key: w.value for w in u.writes if w.key in ("consent_response", "age")} == {
        "consent_response": "granted", "age": 25}


def test_termination_rule_follows_off_track_and_comments_say_so():
    """M4: termination_intent is the first rule after off_track; the comments match the order."""
    _, wf = _load()
    for sid, sub in wf.subagents.items():
        intents = [r.intent for r in (sub.routing or [])]
        if "off_track" in intents and "termination_intent" in intents:
            assert intents.index("termination_intent") == intents.index("off_track") + 1, sid
    raw = (BLUE_DOTS / "agent_core.yaml").read_text(encoding="utf-8")
    assert "This must be the FIRST\n" not in raw
    assert raw.count("first rule after off_track") == 5


def test_language_switch_derived_from_request_change_language():
    from src.understanding.models import DialogueActResult
    u = _understand_once("job_match", {}, DialogueActResult(
        acts=("request_change",), relation="new_topic", topic="language",
        slots={"language_preference": "english"}))
    assert u.nlu_result.intent == "language_switch_request"
    assert u.nlu_result.entities["language_preference"] == "english"


def test_language_switch_unsupported_value_is_rejected():
    from src.understanding.models import DialogueActResult
    u = _understand_once("job_match", {}, DialogueActResult(
        acts=("request_change",), relation="new_topic", topic="language",
        slots={"language_preference": "tamil"}))
    assert "language_preference" not in u.nlu_result.entities


@pytest.mark.parametrize("cases_file", ["scenarios.jsonl", "synthetic.jsonl"])
def test_eval_case_pending_matches_the_resolver(cases_file):
    """An eval case's expected pending is what the real resolver gives its state.

    The live session carries the seeded age as the string "0" (sc-D1-*); the
    resolver returned None for it, so the age slot was dropped as not_pending.
    """
    from eval.nlu.cases import load_cases
    _, wf = _load()
    path = Path(__file__).resolve().parents[1] / "eval" / "nlu" / "cases" / cases_file
    wrong = {}
    for c in load_cases(path):
        if "pending" not in c.expect:
            continue
        p = PendingResolver(wf).resolve(c.step, c.state)
        if (p.id if p else None) != c.expect["pending"]:
            wrong[c.id] = p.id if p else None
    assert wrong == {}


# ── D3: a goodbye before anything is done is confirmed once, then ends ──────

def _route(step, state, result):
    """Understand one scripted turn in ``step`` and route it; returns (intent, next, rule, state)."""
    from tests.test_stream_turn import _make_agent_core
    u = _understand_once(step, state, result)
    state = dict(state)
    for w in u.writes:
        state[w.key] = w.value
    _, wf = _load()
    agent = _make_agent_core(workflow=wf)
    nxt, rule = agent._resolve_next_subagent(current_subagent=wf.subagents[step], nlu_result=u.nlu_result,
                                             session=state)
    state.update(rule.session_writes if rule else {})
    counts = dict(state.get("subagent_entry_count") or {})
    counts[nxt] = counts.get(nxt, 0) + 1
    state["subagent_entry_count"] = counts
    return u.nlu_result.intent, nxt, rule, state


_CALL = {"opening_phrase_emitted": True, "consent_response": "granted", "consent_given": True,
         "has_age": "false", "age": 0, "applications_submitted": 0, "profile_item_id": ""}


def _close():
    from src.understanding.models import DialogueActResult
    return DialogueActResult(acts=("close",), relation="new_topic")


def _act(act):
    from src.understanding.models import DialogueActResult
    return DialogueActResult(acts=(act,), relation="answers_pending")


def test_blocked_close_is_visible_to_routing():
    """'अभी व्यस्त हूँ, बाद में बात करेंगे' in opening, nothing applied: not a silent any_input."""
    u = _understand_once("opening", _CALL, _close())
    assert u.gate_blocked
    assert u.nlu_result.intent == "termination_blocked"


def test_blocked_close_routes_to_confirm_close():
    intent, nxt, rule, state = _route("opening", _CALL, _close())
    assert (intent, nxt) == ("termination_blocked", "confirm_close")
    assert state["close_return_to"] == "opening"
    _, wf = _load()
    sub = wf.subagents["confirm_close"]
    assert not sub.is_terminal
    assert sub.fixed_opening == "क्या मैं कॉल यहीं ख़त्म करूँ?"
    assert PendingResolver(wf).resolve("confirm_close", state).id == "close_confirm"


@pytest.mark.parametrize("answer", [_act("affirm"), _close()])
def test_confirmed_close_ends_the_call(answer):
    _, _, _, state = _route("opening", _CALL, _close())
    intent, nxt, _, _ = _route("confirm_close", state, answer)
    _, wf = _load()
    assert intent == "termination_intent"
    assert nxt == "ended" and wf.subagents[nxt].is_terminal


@pytest.mark.parametrize("step", ["opening", "profile_resolve", "job_match", "profile_setup",
                                  "apply_confirm", "clarification"])
def test_deny_returns_to_the_phase_and_a_second_close_ends(step):
    _, nxt, _, state = _route(step, _CALL, _close())
    assert nxt == "confirm_close"
    intent, nxt, _, state = _route("confirm_close", state, _act("deny"))
    assert intent != "termination_intent" and nxt == step
    intent, nxt, _, _ = _route(step, state, _close())
    assert (intent, nxt) == ("termination_blocked", "ended")


def test_other_content_on_confirm_returns_to_the_phase():
    from src.understanding.models import DialogueActResult
    _, _, _, state = _route("job_match", _CALL, _close())
    _, nxt, _, _ = _route("confirm_close", state,
                          DialogueActResult(acts=("ask",), relation="new_topic", topic="salary"))
    assert nxt == "job_match"


def test_close_after_an_application_still_ends_directly():
    state = {**_CALL, "applications_submitted": 1, "profile_item_id": "p1"}
    intent, nxt, _, _ = _route("apply_confirm", state, _close())
    assert (intent, nxt) == ("termination_intent", "ended")


@pytest.mark.asyncio
async def test_confirm_then_end_over_stream_turn():
    """Stream path: the blocked close speaks the fixed question and stays open; 'हाँ' then ends."""
    from unittest.mock import MagicMock

    from src.models import ContextBundle, DoneEvent, NLUResult, SentenceEvent
    from tests.fakes import fake_understander
    from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

    async def must_not_run(*a, **k):
        raise AssertionError("the model must not be called")
        yield  # pragma: no cover

    async def turn(current, intent, extra=None):
        _, wf = _load()
        agent = _make_agent_core(workflow=wf)
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("x", "hindi")
        agent._understander = fake_understander(NLUResult(intent=intent, entities={}, confidence=1.0))
        agent._llm.stream = must_not_run
        agent._async_memory.context_bundle.return_value = ContextBundle(
            session={**_CALL, "current_subagent_id": current, **(extra or {})}, profile={})
        events = await _collect_events(agent, _make_turn_input(channel="voice"))
        spoken = " ".join(e.text for e in events if isinstance(e, SentenceEvent))
        done = [e for e in events if isinstance(e, DoneEvent)]
        return spoken, done[-1].session_ended

    spoken, ended = await turn("opening", "termination_blocked")
    assert spoken == "क्या मैं कॉल यहीं ख़त्म करूँ?" and ended is False
    spoken, ended = await turn("confirm_close", "termination_intent",
                               {"close_return_to": "opening", "subagent_entry_count": {"confirm_close": 1}})
    assert ended is True


@pytest.mark.parametrize("relation", ["answers_pending", "unclear"])
def test_affirm_on_close_confirm_ends_despite_mislabelled_relation(relation):
    """A bare 'ठीक है' may be labelled answers_pending or unclear; on the close question it confirms."""
    from src.understanding.models import DialogueActResult
    _, _, _, state = _route("opening", _CALL, _close())
    intent, nxt, _, _ = _route("confirm_close", state, DialogueActResult(acts=("affirm",), relation=relation))
    assert (intent, nxt) == ("termination_intent", "ended")


@pytest.mark.parametrize("acts, relation", [
    (("affirm",), "new_topic"), (("affirm",), "answers_other"),
    (("affirm", "ask"), "new_topic"), (("affirm", "ask"), "answers_pending"),
    (("affirm", "request_change"), "answers_pending"),
])
def test_affirm_that_opens_a_topic_does_not_end_the_call(acts, relation):
    """'हाँ, एक बात और पूछनी है' on the close question returns to the phase."""
    from src.understanding.models import DialogueActResult
    _, _, _, state = _route("opening", _CALL, _close())
    intent, nxt, _, _ = _route("confirm_close", state, DialogueActResult(acts=acts, relation=relation))
    assert intent != "termination_intent" and nxt == "opening"


@pytest.mark.asyncio
async def test_second_blocked_close_after_a_deny_ends_over_stream_turn():
    """Stream path: close -> question -> deny (back to the phase) -> close again ends with session_ended."""
    from unittest.mock import MagicMock

    from src.models import ContextBundle, DoneEvent, NLUResult, SentenceEvent
    from tests.fakes import fake_understander
    from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

    async def must_not_run(*a, **k):
        raise AssertionError("the model must not be called")
        yield  # pragma: no cover

    async def turn(current, intent, extra):
        _, wf = _load()
        agent = _make_agent_core(workflow=wf)
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("x", "hindi")
        agent._understander = fake_understander(NLUResult(intent=intent, entities={}, confidence=1.0))
        agent._llm.stream = must_not_run
        agent._async_memory.context_bundle.return_value = ContextBundle(
            session={**_CALL, "current_subagent_id": current, **extra}, profile={})
        events = await _collect_events(agent, _make_turn_input(channel="voice"))
        return (" ".join(e.text for e in events if isinstance(e, SentenceEvent)),
                [e for e in events if isinstance(e, DoneEvent)][-1].session_ended)

    # After the deny the caller is back in `opening`, confirm_close already entered once.
    _, ended = await turn("opening", "termination_blocked",
                          {"close_return_to": "opening", "subagent_entry_count": {"confirm_close": 1}})
    assert ended is True


# ── #439: a yes to job_match's own submit question applies; a yes to "tell more?" does not ──

_JOB_APPLY_Q = [
    "फ्लिपकार्ट में वेल्डर, बेंगलुरु, सैलरी पंद्रह हज़ार। क्या मैं इस नौकरी के लिए आवेदन भेज दूँ?",
    "यह नौकरी आपके लिए अच्छी है। क्या मैं इसके लिए आवेदन कर दूँ?",
    "टाइटन में वेल्डर की नौकरी है। क्या मैं इस नौकरी के लिए आवेदन करूँ?",
    "क्या मैं इस नौकरी के लिए आपका आवेदन भेज दूँ?",
    "क्या मैं इसी नौकरी के लिए आवेदन कर दूँ?",
]
_JOB_MORE_Q = "आपके लिए यह जॉब है — वेल्डर, फ्लिपकार्ट, बेंगलुरु, सैलरी पंद्रह हज़ार। इसके बारे में और बात करें?"
_JOB_CALL = {**_CALL, "trade": "Welder", "location": "Bengaluru"}


@pytest.mark.parametrize("question", _JOB_APPLY_Q)
def test_job_match_apply_question_pends_submit_confirm(question):
    _, wf = _load()
    assert PendingResolver(wf).resolve("job_match", {**_JOB_CALL, "current_question": question}).id == "submit_confirm"


@pytest.mark.parametrize("profile, expected_next", [("p1", "apply_confirm"), ("", "profile_setup")])
def test_yes_to_job_match_apply_question_applies(profile, expected_next):
    state = {**_JOB_CALL, "profile_item_id": profile, "current_question": _JOB_APPLY_Q[0]}
    u = _understand_once("job_match", state, _act("affirm"))
    assert u.pending_id == "submit_confirm"
    intent, nxt, _, _ = _route("job_match", state, _act("affirm"))
    assert (intent, nxt) == ("apply_now", expected_next)


def test_yes_to_tell_more_question_does_not_apply():
    state = {**_JOB_CALL, "profile_item_id": "p1", "current_question": _JOB_MORE_Q}
    u = _understand_once("job_match", state, _act("affirm"))
    assert u.pending_id == "select_job"
    intent, nxt, _, _ = _route("job_match", state, _act("affirm"))
    assert (intent, nxt) == ("any_input", "job_match")


def test_no_to_job_match_apply_question_stays_in_job_match():
    """decline has no job_match rule: the catch-all keeps the caller here, the call does not end."""
    state = {**_JOB_CALL, "profile_item_id": "p1", "current_question": _JOB_APPLY_Q[0]}
    intent, nxt, _, _ = _route("job_match", state, _act("deny"))
    assert (intent, nxt) == ("decline", "job_match")
