"""The offered list stops being on offer once the caller asks for something else.

A tool's rows stay available to the option resolver until something replaces
them, so an ordinal spoken after a change of subject still resolved against the
old list — measured on both the job and the services paths, each sending the
wrong thing. These cover the decision to drop them.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.orchestrator import SERVED_TOOL_RESULTS_KEY
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


def _agent(options_tool="fetch_jobs"):
    """An agent whose current subagent declares one pending offering a list."""
    agent = _make_agent_core()
    agent._dialogue_cfg = SimpleNamespace(abandons_offered_acts=("request_change",))
    pending = SimpleNamespace(
        id="submit_confirm",
        options_from=SimpleNamespace(tool=options_tool) if options_tool else None,
    )
    agent._workflow.subagents["apply_confirm"] = SimpleNamespace(pending=[pending])
    agent._workflow.subagents["opening"] = SimpleNamespace(pending=[pending])
    return agent


def _bundle(served=None):
    return SimpleNamespace(
        session={SERVED_TOOL_RESULTS_KEY: served} if served is not None else {},
        profile={},
    )


def test_a_change_of_subject_drops_the_offered_list():
    agent, cache = _agent(), _Cache()
    bundle = _bundle({"fetch_jobs": "h1", "fetch_profile": "h2"})

    agent._abandon_offered(_understanding("request_change"), "apply_confirm", bundle, cache)

    assert cache.abandoned == ["fetch_jobs"]
    assert bundle.session[SERVED_TOOL_RESULTS_KEY] == {"fetch_profile": "h2"}


def test_a_turn_that_also_selects_is_choosing_not_leaving():
    agent, cache = _agent(), _Cache()

    agent._abandon_offered(
        _understanding("request_change", "select"), "apply_confirm", _bundle(), cache)

    assert cache.abandoned == []


def test_an_unlisted_act_leaves_the_offer_alone():
    agent, cache = _agent(), _Cache()

    agent._abandon_offered(_understanding("affirm"), "apply_confirm", _bundle(), cache)

    assert cache.abandoned == []


def test_nothing_happens_when_no_pending_offers_a_list():
    agent, cache = _agent(options_tool=None), _Cache()

    agent._abandon_offered(_understanding("request_change"), "opening", _bundle(), cache)

    assert cache.abandoned == []


def test_it_does_not_need_the_pending_to_resolve_this_turn():
    """The turn that changes the subject routinely resolves no pending."""
    agent, cache = _agent(), _Cache()
    agent._pending_resolver = SimpleNamespace(resolve=lambda *_: None)

    agent._abandon_offered(_understanding("request_change"), "apply_confirm", _bundle(), cache)

    assert cache.abandoned == ["fetch_jobs"]


def test_served_is_left_alone_when_the_cache_had_nothing_to_drop():
    agent, cache = _agent(), _Cache(had=False)
    bundle = _bundle({"fetch_jobs": "h1"})

    agent._abandon_offered(_understanding("request_change"), "apply_confirm", bundle, cache)

    assert bundle.session[SERVED_TOOL_RESULTS_KEY] == {"fetch_jobs": "h1"}


def test_a_missing_served_map_is_not_an_error():
    agent, cache = _agent(), _Cache()

    agent._abandon_offered(_understanding("request_change"), "apply_confirm", _bundle(), cache)

    assert cache.abandoned == ["fetch_jobs"]


def test_a_failure_never_reaches_the_turn():
    agent, cache = _agent(), _Cache()
    agent._workflow = SimpleNamespace(
        subagents=property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))))

    try:
        agent._abandon_offered(_understanding("request_change"), "apply_confirm", _bundle(), cache)
    except RuntimeError:
        raise AssertionError("a resolver failure must not break the turn")
    assert cache.abandoned == []
