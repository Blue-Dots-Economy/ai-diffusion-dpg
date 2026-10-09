"""The offered list stops being on offer once the caller asks for something else."""

from __future__ import annotations

from types import SimpleNamespace

from src.orchestrator import RECENT_TOOL_EXCHANGES_KEY, SERVED_TOOL_RESULTS_KEY
from tests.test_stream_turn import _make_agent_core


def _understanding(*acts):
    return SimpleNamespace(dialogue=SimpleNamespace(acts=list(acts)))


class _Cache:
    """Records which tool was abandoned, if any."""

    def __init__(self, had=True):
        self.had = had
        self.abandoned: list[str] = []

    def abandon(self, tool):
        self.abandoned.append(tool)
        return self.had


def _agent():
    agent = _make_agent_core()
    agent._dialogue_cfg = SimpleNamespace(abandons_offered_acts=("request_change",))
    agent._option_tools = frozenset({"fetch_jobs", "fetch_services"})
    return agent


def _bundle(served=None, exchanges=None):
    session = {}
    if served is not None:
        session[SERVED_TOOL_RESULTS_KEY] = served
    if exchanges is not None:
        session[RECENT_TOOL_EXCHANGES_KEY] = exchanges
    return SimpleNamespace(session=session, profile={})


def _exchange(tool):
    return {"tool_uses": [{"name": tool}], "tool_results": [{"content": "rows"}]}


def test_a_change_of_subject_drops_the_offered_list():
    agent, cache = _agent(), _Cache()
    bundle = _bundle({"fetch_jobs": "h1", "fetch_profile": "h2"})

    dropped = agent._abandon_offered(
        _understanding("request_change"), "apply_confirm", bundle, cache)

    assert dropped is True, "the caller persists on this, so it must be reported"
    assert cache.abandoned == ["fetch_jobs"], "a profile lookup is not an offered list"
    assert bundle.session[SERVED_TOOL_RESULTS_KEY] == {"fetch_profile": "h2"}


def test_a_turn_that_also_selects_is_choosing_not_leaving():
    agent, cache = _agent(), _Cache()

    assert agent._abandon_offered(
        _understanding("request_change", "select"), "apply_confirm", _bundle(), cache) is False
    assert cache.abandoned == []


def test_an_unlisted_act_leaves_the_offer_alone():
    agent, cache = _agent(), _Cache()

    agent._abandon_offered(_understanding("affirm"), "apply_confirm", _bundle(), cache)

    assert cache.abandoned == []


def test_nothing_happens_when_no_list_was_ever_read_out():
    agent, cache = _agent(), _Cache()

    assert agent._abandon_offered(
        _understanding("request_change"), "opening", _bundle(), cache) is False
    assert cache.abandoned == []


def test_it_drops_what_was_read_out_whatever_phase_the_caller_is_in_now():
    """The caller has left the phase that offered the list by then."""
    agent, cache = _agent(), _Cache()
    bundle = _bundle({"fetch_services": "h1"})

    assert agent._abandon_offered(
        _understanding("request_change"), "profile_setup", bundle, cache) is True
    assert cache.abandoned == ["fetch_services"]


def test_nothing_is_persisted_when_the_cache_had_nothing_to_drop():
    agent, cache = _agent(), _Cache(had=False)
    bundle = _bundle({"fetch_jobs": "h1"})

    dropped = agent._abandon_offered(
        _understanding("request_change"), "apply_confirm", bundle, cache)

    assert dropped is False
    assert bundle.session[SERVED_TOOL_RESULTS_KEY] == {"fetch_jobs": "h1"}


def test_a_missing_served_map_is_not_an_error():
    agent, cache = _agent(), _Cache()

    assert agent._abandon_offered(
        _understanding("request_change"), "apply_confirm", _bundle(), cache) is False


def test_a_failure_never_reaches_the_turn():
    agent, cache = _agent(), _Cache()
    agent._dialogue_cfg = SimpleNamespace(
        abandons_offered_acts=property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))))

    try:
        agent._abandon_offered(_understanding("request_change"), "apply_confirm", _bundle(), cache)
    except RuntimeError:
        raise AssertionError("a resolver failure must not break the turn")
    assert cache.abandoned == []


def test_the_abandoned_tools_exchanges_leave_the_replay():
    """The model reads the rows from the replay, not only the resolver."""
    agent, cache = _agent(), _Cache()
    bundle = _bundle(
        {"fetch_jobs": "h1"},
        [_exchange("fetch_jobs"), _exchange("fetch_profile")],
    )

    agent._abandon_offered(_understanding("request_change"), "profile_setup", bundle, cache)

    kept = bundle.session[RECENT_TOOL_EXCHANGES_KEY]
    assert [tu["name"] for ex in kept for tu in ex["tool_uses"]] == ["fetch_profile"]


def test_an_absent_exchange_list_is_not_an_error():
    agent, cache = _agent(), _Cache()
    bundle = _bundle({"fetch_jobs": "h1"})

    assert agent._abandon_offered(
        _understanding("request_change"), "profile_setup", bundle, cache) is True
    assert bundle.session[RECENT_TOOL_EXCHANGES_KEY] == []
