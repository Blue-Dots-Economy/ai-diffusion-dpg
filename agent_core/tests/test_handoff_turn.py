"""A `human_request` turn hands the caller off via Trust and speaks a fixed line (identity/handoff spec §4).

Built from the real Blue Dots workflow plus a test override that enables handoff and adds a minimal
`handoff` subagent (Task 7 adds the real one), in the style of test_blue_dots_dialogue_act_config.py.
"""
from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.models import ContextBundle, DoneEvent, NLUResult, SentenceEvent, TrustCheckResult
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.workflow_loader import AgentWorkflowLoader
from tests.fakes import fake_understander
from tests.test_blue_dots_dialogue_act_config import BLUE_DOTS, _CALL
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

ASK = "इंसान से बात कराओ"
HANDOFF_LINES = {
    "delivered": "मैंने आपकी बात टीम तक पहुँचा दी है, वे आपसे संपर्क करेंगे। क्या मैं और कुछ मदद करूँ, या कॉल यहीं ख़त्म करूँ?",
    "failed": "अभी मैं आपकी बात आगे नहीं भेज पाई — मैं ही आपकी मदद कर सकती हूँ। आप क्या जानना चाहते हैं?",
    "already": "आपकी बात पहले ही टीम तक पहुँच चुकी है।",
}
IDENTITY = {"name": "ब्लू डॉट्स सहायक", "kind": "ai_assistant", "operator": "Blue Dots",
            "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "human_handoff": "request",
            "no_handoff_line": "अभी इस कॉल पर कोई इंसान उपलब्ध नहीं है।"}
HANDOFF = {"lines": HANDOFF_LINES, "summary_turns": 6}
_HANDOFF_SUBAGENT = {
    "id": "handoff", "name": "Human Handoff", "description": "Fixed handoff line.",
    "is_start": False, "is_terminal": False, "opening_phrase": HANDOFF_LINES["failed"], "special_handler": None, "tools": [],
    "system_prompt": "Speak the handoff line.", "routing": [{"intent": "*", "next_subagent_id": "opening"}],
}


def _workflow(with_handoff: bool):
    cfg = copy.deepcopy(load_merged_config(BLUE_DOTS))
    if with_handoff:
        cfg["identity"], cfg["handoff"] = IDENTITY, HANDOFF
        cfg["agent_workflow"]["subagents"].append(copy.deepcopy(_HANDOFF_SUBAGENT))
    MergedConfig.validate_full(cfg)
    return AgentWorkflowLoader().load(config=cfg, tool_registry=ToolRegistry(cfg, OfflineGateway(cfg)))


async def _must_not_stream(*a, **k):
    raise AssertionError("the model must not be called")
    yield  # pragma: no cover


class _Turn:
    """One orchestrator over the real workflow; records escalate calls and session writes."""

    def __init__(self, *, with_handoff: bool = True, identity: dict | None = IDENTITY,
                 handoff: dict | None = HANDOFF, workflow_handoff: bool | None = None):
        self.agent = _make_agent_core(workflow=_workflow(with_handoff if workflow_handoff is None
                                                         else workflow_handoff))
        a = self.agent
        a._config["preprocessing"]["language_normalisation"]["default_language"] = "hindi"
        a._config["observability"] = {"domain": "blue_dot"}
        if identity is not None:
            a._config["identity"] = identity
        if handoff is not None:
            a._config["handoff"] = handoff
        a._language_normaliser = MagicMock()
        a._language_normaliser.normalise.return_value = (ASK, "hindi")
        a._understander = fake_understander(NLUResult(intent="human_request", entities={}, confidence=1.0))
        a._llm.call = MagicMock(side_effect=AssertionError("the model must not be called"))
        self.escalate_result: dict = {"queued": True, "delivered": True, "reason": "delivered",
                                      "ticket_id": "TKT-1"}
        self.escalate_raises: Exception | None = None
        self.escalate_calls: list[dict] = []
        self.session: dict = {**_CALL, "current_subagent_id": "job_match"}
        self.writes: dict = {}

    def _escalate(self, session_id, escalation_reason, user_message, workflow_step, handoff=None):
        self.escalate_calls.append({"session_id": session_id, "escalation_reason": escalation_reason,
                                    "user_message": user_message, "workflow_step": workflow_step,
                                    "handoff": handoff})
        if self.escalate_raises:
            raise self.escalate_raises
        return self.escalate_result

    def _record_write(self, session_id, user_id, scope, key, value):
        self.writes[key] = value

    async def stream(self, llm=_must_not_stream):
        a = self.agent
        a._llm.stream = llm
        a._async_trust.escalate = AsyncMock(side_effect=self._escalate)
        a._async_memory.write = AsyncMock(side_effect=self._record_write)
        a._async_memory.context_bundle.return_value = ContextBundle(session=dict(self.session), profile={})
        events = await _collect_events(a, _make_turn_input(channel="voice", user_message=ASK))
        text = " ".join(e.text for e in events if isinstance(e, SentenceEvent))
        return text, [e for e in events if isinstance(e, DoneEvent)][-1]

    def sync(self):
        a = self.agent
        a._trust.check_input.return_value = TrustCheckResult(passed=True, action="allow")
        a._trust.escalate = MagicMock(side_effect=self._escalate)
        a._memory.write = MagicMock(side_effect=self._record_write)
        a._memory.context_bundle.return_value = ContextBundle(session=dict(self.session), profile={})
        return a.process_turn(_make_turn_input(channel="cli", user_message=ASK))


# ── stream path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_human_request_delivered_speaks_line_and_routes():
    t = _Turn()
    text, done = await t.stream()
    assert text == HANDOFF_LINES["delivered"] and done.session_ended is False
    call = t.escalate_calls[0]
    assert (call["session_id"], call["escalation_reason"], call["user_message"], call["workflow_step"]) == (
        "sess-1", "human_request", ASK, "job_match")
    payload = call["handoff"]
    assert payload["reason"] == "human_request" and payload["use_case"] == "blue_dot"
    assert payload["caller"]["phone"] == "user-1" and payload["call_id"] == "sess-1"
    assert payload["context"]["step"] == "job_match" and payload["context"]["last_caller_turn"] == ASK
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-1"
    assert t.writes["close_return_to"] == "job_match"
    assert t.writes["current_subagent_id"] == "handoff"
    t.agent._async_learning.emit_signal.assert_awaited_once()
    kind, data = t.agent._async_learning.emit_signal.await_args.args
    assert kind == "handoff" and data["outcome"] == "delivered" and data["ticket_id"] == "TKT-1"
    assert {"session_id", "turn_id", "timestamp_ms", "reason", "latency_ms"} <= set(data)
    assert "handoff" not in data and "summary" not in data          # never the payload


@pytest.mark.asyncio
async def test_human_request_failed_when_trust_unreachable():
    t = _Turn()
    t.escalate_result = {"queued": False}                            # no 'delivered' key
    text, done = await t.stream()
    assert text == HANDOFF_LINES["failed"] and t.writes["handoff_status"] == "failed"
    assert done.session_ended is False and t.writes["current_subagent_id"] == "handoff"


@pytest.mark.asyncio
async def test_human_request_exception_is_failed_not_error():
    t = _Turn()
    t.escalate_raises = RuntimeError("boom")
    text, done = await t.stream()
    assert text == HANDOFF_LINES["failed"] and done.error_type in (None, "")
    assert t.writes["handoff_status"] == "failed"


@pytest.mark.asyncio
async def test_second_request_after_delivery_is_already_and_no_new_escalation():
    t = _Turn()
    t.session["handoff_status"] = "delivered"
    text, done = await t.stream()
    assert text == HANDOFF_LINES["already"] and t.escalate_calls == []
    assert t.writes["close_return_to"] == "job_match" and t.writes["current_subagent_id"] == "handoff"
    assert "handoff_status" not in t.writes and done.session_ended is False


@pytest.mark.asyncio
async def test_second_request_after_failure_retries():
    t = _Turn()
    t.session["handoff_status"] = "failed"
    t.escalate_result = {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-2"}
    text, _ = await t.stream()
    assert text == HANDOFF_LINES["delivered"] and len(t.escalate_calls) == 1
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-2"


async def _llm_reply(*a, **k):
    yield "जी, बताइए। "


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"with_handoff": False, "identity": None, "handoff": None},          # default blue-dots
    {"identity": {**IDENTITY, "human_handoff": "none"}},                   # handoff off
    {"handoff": None, "workflow_handoff": False, "identity": IDENTITY},    # no handoff block / phase
    {"workflow_handoff": False},                                           # no `handoff` phase
])
async def test_handoff_disabled_does_not_escalate(kwargs):
    t = _Turn(**kwargs)
    text, _ = await t.stream(llm=_llm_reply)
    assert t.escalate_calls == [] and text not in HANDOFF_LINES.values()
    assert "handoff_status" not in t.writes and t.writes.get("current_subagent_id") != "handoff"


# ── sync path ─────────────────────────────────────────────────────────────────

def test_sync_human_request_delivered_speaks_line_and_routes():
    t = _Turn()
    result = t.sync()
    assert result.response_text == HANDOFF_LINES["delivered"] and result.session_ended is False
    assert t.escalate_calls[0]["handoff"]["reason"] == "human_request"
    assert t.escalate_calls[0]["workflow_step"] == "job_match"
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-1"
    assert t.writes["close_return_to"] == "job_match" and t.writes["current_subagent_id"] == "handoff"
    t.agent._learning.emit_signal.assert_called_once()
    assert t.agent._learning.emit_signal.call_args.args[0] == "handoff"


def test_sync_human_request_exception_is_failed():
    t = _Turn()
    t.escalate_raises = RuntimeError("boom")
    result = t.sync()
    assert result.response_text == HANDOFF_LINES["failed"] and result.error_type in (None, "")
    assert t.writes["handoff_status"] == "failed"


def test_sync_second_request_after_delivery_is_already():
    t = _Turn()
    t.session["handoff_status"] = "delivered"
    result = t.sync()
    assert result.response_text == HANDOFF_LINES["already"] and t.escalate_calls == []


def test_sync_handoff_disabled_does_not_escalate():
    t = _Turn(identity={**IDENTITY, "human_handoff": "none"})
    t.agent._llm.call = MagicMock()                                  # the normal turn may use the model
    t.agent._manager_agent.run_turn.return_value = ("जी, बताइए।", [], [])
    result = t.sync()
    assert result.response_text not in HANDOFF_LINES.values()
    assert t.escalate_calls == [] and "handoff_status" not in t.writes
