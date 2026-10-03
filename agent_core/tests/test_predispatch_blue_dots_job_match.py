"""Spec E §5.4: the enabled Blue Dots ``fetch_jobs`` rule fires against the REAL job_match tool list.

job_match offers exactly one tool, ``fetch_jobs``. Removing it would leave the
main LLM no tools, so the definitions stay and every main-LLM call this turn
sends ``tool_choice="none"`` (provider supports forcing a choice). The workflow,
tool registry, tool policies and pre-dispatch inputs come from the Blue Dots
config; the LLM, memory and gateway transport are mocked. Both paths. Belongs
to the Agent Core block in the DPG framework.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.chat_provider.base import Capabilities
from src.chat_provider.types import ToolResultBlock, ToolUseBlock
from src.models import ContextBundle, NLUResult, ToolResult
from src.predispatch.runner import PREDISPATCH_ID
from src.remember import RememberTool
from src.tool_registry import ToolRegistry
from src.tool_results import ToolResultPolicies
from src.workflow_loader import AgentWorkflowLoader

from tests.fakes import fake_understander
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
# A mid-conversation turn: the opening phrase is already spoken.
SESSION = {"current_subagent_id": "job_match", "opening_phrase_emitted": True,
           "stored_trade": "Welder", "location": "Bangalore"}
QUERY = "Welder jobs in Bengaluru"
JOBS_JSON = json.dumps({"items": [{"item_id": "J-1", "role": "Welder"}]})
# fetch_jobs' agent-facing params in dev-kit/configs/blue-dots/action_gateway.yaml.
FETCH_SCHEMA = {"type": "object", "properties": {"query_text": {"type": "string"},
                                                 "offset": {"type": "integer"}},
                "required": ["query_text"]}


class _BlueDotsGateway(OfflineGateway):
    """OfflineGateway with fetch_jobs' real agent parameters."""

    def list_available_tools(self) -> list[dict]:
        return [dict(t, input_schema=FETCH_SCHEMA) if t["name"] == "fetch_jobs" else t
                for t in super().list_available_tools()]


def _force_caps() -> Capabilities:
    return Capabilities(supports_tools=True, supports_streaming=True, supports_prompt_cache=False,
                        supports_image_input=False, supports_audio_input=False,
                        supports_structured_output=True, supports_force_tool_choice=True)


def _jobs(tc) -> ToolResult:
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True,
                      result_text=JOBS_JSON, projected=True)


def _wire_blue_dots(agent):
    """Give a mocked AgentCore the real Blue Dots workflow, registry and pre-dispatch inputs."""
    cfg = load_merged_config(BD)
    reg = ToolRegistry(cfg, _BlueDotsGateway(cfg))
    wf = AgentWorkflowLoader().load(config=cfg, tool_registry=reg)
    assert [t["name"] for t in wf.resolve_tools_for("job_match")] == ["fetch_jobs"]
    agent._workflow = wf
    agent._tool_registry = reg
    agent._tool_policies = ToolResultPolicies.from_config(cfg)
    agent._remember = RememberTool.from_config(cfg)
    agent._init_predispatch(cfg)
    agent._manager_agent._tool_call_caps = {}
    agent._manager_agent._grounded_params = {}
    agent._manager_agent.last_llm_calls = 0
    agent._llm.capabilities = _force_caps()
    agent._understander = fake_understander(NLUResult(intent="any_input", entities={}, confidence=0.9))
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    return agent


def _assert_pair(messages):
    use, result = messages[-2], messages[-1]
    assert isinstance(use.content[0], ToolUseBlock) and use.content[0].tool_use_id == PREDISPATCH_ID
    assert use.content[0].tool_name == "fetch_jobs" and use.content[0].input == {"query_text": QUERY}
    assert isinstance(result.content[0], ToolResultBlock) and result.content[0].content == JOBS_JSON


def _outcome(caplog, message):
    recs = [r for r in caplog.records if r.getMessage() == message]
    assert len(recs) == 1
    return recs[0]


@pytest.mark.asyncio
async def test_stream_job_match_fetch_jobs_fires_under_tool_choice_none(caplog):
    agent = _wire_blue_dots(_make_agent_core())
    agent._async_memory.context_bundle.return_value = ContextBundle(
        session=dict(SESSION), profile={}, tool_results=[])
    agent._async_memory.apply_tool_results = AsyncMock()
    agent._async_gateway.execute = AsyncMock(side_effect=lambda tc, *a, **k: _jobs(tc))
    requests: list = []

    async def stream(request, *, abort_event=None):
        requests.append(request)
        yield "Here you go. "

    agent._llm.stream = stream
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        await _collect_events(agent, _make_turn_input())

    assert [c.args[0].tool_name for c in agent._async_gateway.execute.await_args_list] == ["fetch_jobs"]
    assert len(requests) == 1                                    # exactly one main-LLM call
    assert [t.name for t in requests[0].tools] == ["fetch_jobs"]
    assert requests[0].tool_choice == "none"
    _assert_pair(requests[0].messages)
    r = _outcome(caplog, "orchestrator.stream_turn_complete")
    assert (r.llm_calls, r.predispatch_tool, r.predispatch_outcome) == (1, "fetch_jobs", "fired")


def test_sync_job_match_fetch_jobs_fires_under_tool_choice_none(caplog):
    from tests.test_orchestrator import _make_agent, _turn_input
    agent = _wire_blue_dots(_make_agent(session_data=dict(SESSION),
                                        nlu_result=NLUResult(intent="any_input", entities={},
                                                             confidence=0.9)))
    gw = agent._manager_agent._gateway = MagicMock()
    gw.execute.side_effect = lambda tc, *a, **k: _jobs(tc)
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        agent.process_turn(_turn_input())

    assert [c.args[0].tool_name for c in gw.execute.call_args_list] == ["fetch_jobs"]
    assert agent._llm.call.call_count == 1                       # exactly one main-LLM call
    req = agent._llm.call.call_args.args[0]
    assert [t.name for t in req.tools] == ["fetch_jobs"] and req.tool_choice == "none"
    _assert_pair(req.messages)
    assert agent._manager_agent.run_turn.call_args.kwargs["tool_choice"] == "none"
    r = _outcome(caplog, "orchestrator.turn_complete")
    assert (r.llm_calls, r.predispatch_tool, r.predispatch_outcome) == (1, "fetch_jobs", "fired")
