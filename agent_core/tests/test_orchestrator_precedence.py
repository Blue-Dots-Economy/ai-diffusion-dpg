"""NLU-owned session values win over the stored profile; nothing else changes."""
from src.models import ContextBundle
from src.orchestrator import AgentCore
from src.understanding.precedence import PROVENANCE_KEY
from tests.test_orchestrator import _make_agent


def _bundle(session, profile):
    return ContextBundle(session=session, profile=profile, journey=None)


def test_routing_state_profile_first_unless_nlu_owned():
    b = _bundle({"trade": "Welder", "age": 0, PROVENANCE_KEY: ["trade"]},
                {"trade": "Electrician", "age": 25})
    state = AgentCore._routing_state(b)
    assert state["trade"] == "Welder" and state["age"] == 25


def test_routing_state_without_provenance_is_todays_merge():
    b = _bundle({"trade": "Welder"}, {"trade": "Electrician"})
    assert AgentCore._routing_state(b)["trade"] == "Electrician"


def test_profile_context_and_tool_values_honour_provenance():
    agent = _make_agent()
    b = _bundle({"trade": "Welder", PROVENANCE_KEY: ["trade"]}, {"trade": "Electrician"})
    assert agent._build_profile_context(b, {"trade": "trade"})["trade"] == "Welder"
    assert AgentCore._tool_session_values(b)["trade"] == "Welder"
