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
