"""A `human_request` turn hands the caller off via Trust and speaks a fixed line (identity/handoff spec §4).

Built from the real Blue Dots workflow (which ships a `handoff` phase with human_handoff none) plus a
test override that enables handoff, in the style of test_blue_dots_dialogue_act_config.py.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.models import ContextBundle, DoneEvent, NLUResult, SentenceEvent, SignalEvent, TrustCheckResult
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
# A person-request turn speaks the AI disclosure first (spec §3.2); `already` carries none.
SPOKEN = {k: f"{IDENTITY['disclosure']} {HANDOFF_LINES[k]}" for k in ("delivered", "failed")}
HANDOFF = {"lines": HANDOFF_LINES, "summary_turns": 6}
SHIPPED = load_merged_config(BLUE_DOTS)


def _workflow(with_handoff: bool | str):
    """The real workflow with handoff enabled, with its `handoff` phase removed, or as shipped."""
    cfg = copy.deepcopy(SHIPPED)
    if with_handoff == "shipped":
        pass
    elif with_handoff:
        cfg["identity"], cfg["handoff"] = IDENTITY, HANDOFF
    else:
        cfg["agent_workflow"]["subagents"] = [s for s in cfg["agent_workflow"]["subagents"] if s["id"] != "handoff"]
    MergedConfig.validate_full(cfg)
    return AgentWorkflowLoader().load(config=cfg, tool_registry=ToolRegistry(cfg, OfflineGateway(cfg)))


class _Turn:
    """One orchestrator over the real workflow; records escalate calls and session writes."""

    def __init__(self, *, with_handoff: bool | str = True, identity: dict | None = IDENTITY,
                 handoff: dict | None = HANDOFF, workflow_handoff: bool | None = None,
                 language: str = "hindi"):
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
        a._language_normaliser.normalise.return_value = (ASK, language)
        a._understander = fake_understander(NLUResult(intent="human_request", entities={}, confidence=1.0))
        # Recorded, not raised: a swallowed exception must not hide a model call.
        a._llm.call = MagicMock()
        self.llm_streams: list = []
        self.escalate_result: dict = {"queued": True, "delivered": True, "reason": "delivered",
                                      "ticket_id": "TKT-1"}
        self.escalate_raises: Exception | None = None
        self.escalate_calls: list[dict] = []
        self.session: dict = {**_CALL, "current_subagent_id": "job_match"}
        self.writes: dict = {}
        self.write_log: list[tuple[str, object]] = []
        self.events: list = []
        self.status_at_escalate: list = []

    def _escalate(self, session_id, escalation_reason, user_message, workflow_step, handoff=None):
        self.status_at_escalate.append(self.writes.get("handoff_status"))
        self.escalate_calls.append({"session_id": session_id, "escalation_reason": escalation_reason,
                                    "user_message": user_message, "workflow_step": workflow_step,
                                    "handoff": handoff})
        if self.escalate_raises:
            raise self.escalate_raises
        return self.escalate_result

    def _record_write(self, session_id, user_id, scope, key, value):
        self.writes[key] = value
        self.write_log.append((key, value))

    @property
    def llm_calls(self) -> int:
        return len(self.llm_streams) + self.agent._llm.call.call_count

    async def _recording_stream(self, *a, **k):
        self.llm_streams.append(k)
        return
        yield  # pragma: no cover

    def _arm_stream(self, llm=None, escalate=None):
        a = self.agent
        a._llm.stream = llm or self._recording_stream
        a._async_trust.escalate = AsyncMock(side_effect=escalate or self._escalate)
        a._async_memory.write = AsyncMock(side_effect=self._record_write)
        a._async_memory.context_bundle.return_value = ContextBundle(session=dict(self.session), profile={})

    async def stream(self, llm=None):
        a = self.agent
        self._arm_stream(llm)
        events = await _collect_events(a, _make_turn_input(channel="voice", user_message=ASK))
        self.events = events
        for _ in range(3):                                           # let fire-and-forget tasks run
            await asyncio.sleep(0)
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
    assert text == SPOKEN["delivered"] and done.session_ended is False
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
    assert t.writes["subagent_entry_count"]["handoff"] == 1
    assert t.writes["handoff_line"] == "delivered"                   # the line spoken this turn
    assert t.llm_calls == 0


@pytest.mark.asyncio
async def test_human_request_failed_when_trust_unreachable():
    t = _Turn()
    t.escalate_result = {"queued": False}                            # no 'delivered' key
    text, done = await t.stream()
    assert text == SPOKEN["failed"] and t.writes["handoff_status"] == "failed"
    assert done.session_ended is False and t.writes["current_subagent_id"] == "handoff"
    assert t.writes["handoff_line"] == "failed"


@pytest.mark.asyncio
async def test_human_request_exception_is_failed_not_error(caplog):
    t = _Turn()
    t.escalate_raises = RuntimeError("secret-https://hooks.example/abc")
    with caplog.at_level(logging.DEBUG):
        text, done = await t.stream()
    assert text == SPOKEN["failed"] and done.error_type in (None, "")
    assert t.writes["handoff_status"] == "failed"
    assert "hooks.example" not in caplog.text                        # exception type only
    failed = [r for r in caplog.records if r.getMessage() == "orchestrator.handoff_escalate_failed"]
    assert failed and failed[0].error == "RuntimeError"


@pytest.mark.asyncio
async def test_second_request_after_delivery_is_already_and_no_new_escalation():
    t = _Turn()
    t.session["handoff_status"] = "delivered"
    text, done = await t.stream()
    assert text == HANDOFF_LINES["already"] and t.escalate_calls == []
    assert t.writes["close_return_to"] == "job_match" and t.writes["current_subagent_id"] == "handoff"
    assert "handoff_status" not in t.writes and done.session_ended is False
    assert t.writes["handoff_line"] == "already"                     # status stays delivered, line does not
    kind, data = t.agent._async_learning.emit_signal.await_args.args
    assert kind == "handoff" and data["outcome"] == "already" and data["reason"] == "already"


@pytest.mark.asyncio
async def test_second_request_after_failure_retries():
    t = _Turn()
    t.session["handoff_status"] = "failed"
    t.escalate_result = {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-2"}
    text, _ = await t.stream()
    assert text == SPOKEN["delivered"] and len(t.escalate_calls) == 1
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-2"


async def _llm_reply(*a, **k):
    yield "जी, बताइए। "


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"with_handoff": False, "identity": None, "handoff": None},          # no identity / handoff at all
    {"with_handoff": "shipped", "identity": SHIPPED["identity"],           # blue-dots as shipped
     "handoff": SHIPPED["handoff"]},
    {"identity": {**IDENTITY, "human_handoff": "none"}},                   # handoff off
    {"handoff": None, "workflow_handoff": False, "identity": IDENTITY},    # no handoff block / phase
    {"workflow_handoff": False},                                           # no `handoff` phase
    {"handoff": None},                                                     # phase present, no block
])
async def test_handoff_disabled_does_not_escalate(kwargs):
    t = _Turn(**kwargs)
    text, _ = await t.stream(llm=_llm_reply)
    assert t.escalate_calls == [] and text not in HANDOFF_LINES.values()
    assert "handoff_status" not in t.writes and t.writes.get("current_subagent_id") != "handoff"


@pytest.mark.asyncio
async def test_non_default_language_speaks_configured_line_verbatim():
    t = _Turn(language="english")                                    # default_language is hindi
    text, _ = await t.stream()
    assert text == SPOKEN["delivered"] and t.llm_calls == 0


@pytest.mark.asyncio
async def test_request_from_confirm_close_keeps_the_real_return_phase():
    t = _Turn()
    t.session.update(current_subagent_id="confirm_close", close_return_to="job_match")
    text, _ = await t.stream()
    assert text == SPOKEN["delivered"]
    assert "close_return_to" not in t.writes                         # still job_match
    assert t.escalate_calls[0]["workflow_step"] == "job_match"
    assert t.escalate_calls[0]["handoff"]["context"]["step"] == "job_match"


@pytest.mark.asyncio
async def test_request_from_handoff_phase_reports_the_return_phase():
    t = _Turn()
    t.session.update(current_subagent_id="handoff", close_return_to="profile_setup",
                     handoff_status="failed", subagent_entry_count={"handoff": 1})
    await t.stream()
    assert "close_return_to" not in t.writes
    assert t.escalate_calls[0]["workflow_step"] == "profile_setup"
    assert t.escalate_calls[0]["handoff"]["context"]["step"] == "profile_setup"
    assert t.writes["subagent_entry_count"]["handoff"] == 2


@pytest.mark.asyncio
async def test_handoff_signal_task_is_tracked_until_done():
    t = _Turn()
    release = asyncio.Event()

    async def slow_emit(*a, **k):
        await release.wait()

    t.agent._async_learning.emit_signal = AsyncMock(side_effect=slow_emit)
    await t.stream()
    assert len(t.agent._bg_tasks) == 1                               # held while pending
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    assert t.agent._bg_tasks == set()                                # dropped once done
    t.agent._async_learning.emit_signal.assert_awaited_once()


# ── sync path ─────────────────────────────────────────────────────────────────

def test_sync_human_request_delivered_speaks_line_and_routes():
    t = _Turn()
    result = t.sync()
    assert result.response_text == SPOKEN["delivered"] and result.session_ended is False
    assert t.escalate_calls[0]["handoff"]["reason"] == "human_request"
    assert t.escalate_calls[0]["workflow_step"] == "job_match"
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-1"
    assert t.writes["close_return_to"] == "job_match" and t.writes["current_subagent_id"] == "handoff"
    t.agent._learning.emit_signal.assert_called_once()
    assert t.agent._learning.emit_signal.call_args.args[0] == "handoff"


def test_sync_spoken_delivered_and_failed_lines_start_with_disclosure():
    assert SPOKEN["delivered"].startswith(IDENTITY["disclosure"])
    t = _Turn()
    assert t.sync().response_text.startswith(IDENTITY["disclosure"])
    t = _Turn()
    t.escalate_result = {"queued": False}
    assert t.sync().response_text == SPOKEN["failed"]


def test_sync_human_request_exception_is_failed():
    t = _Turn()
    t.escalate_raises = RuntimeError("boom")
    result = t.sync()
    assert result.response_text == SPOKEN["failed"] and result.error_type in (None, "")
    assert t.writes["handoff_status"] == "failed"


def test_sync_human_request_failed_when_trust_unreachable():
    t = _Turn()
    t.escalate_result = {"queued": False}
    result = t.sync()
    assert result.response_text == SPOKEN["failed"] and t.writes["handoff_status"] == "failed"
    assert t.writes["current_subagent_id"] == "handoff"


def test_sync_second_request_after_failure_retries():
    t = _Turn()
    t.session["handoff_status"] = "failed"
    t.escalate_result = {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-2"}
    result = t.sync()
    assert result.response_text == SPOKEN["delivered"] and len(t.escalate_calls) == 1
    assert t.writes["handoff_ticket_id"] == "TKT-2"


def test_sync_non_default_language_speaks_configured_line_verbatim():
    t = _Turn(language="english")
    assert t.sync().response_text == SPOKEN["delivered"] and t.llm_calls == 0


def test_sync_second_request_after_delivery_is_already():
    t = _Turn()
    t.session["handoff_status"] = "delivered"
    result = t.sync()
    assert result.response_text == HANDOFF_LINES["already"] and t.escalate_calls == []
    assert t.writes["handoff_line"] == "already" and "handoff_status" not in t.writes


def test_sync_handoff_disabled_does_not_escalate():
    t = _Turn(identity={**IDENTITY, "human_handoff": "none"})
    t.agent._llm.call = MagicMock()                                  # the normal turn may use the model
    t.agent._manager_agent.run_turn.return_value = ("जी, बताइए।", [], [])
    result = t.sync()
    assert result.response_text not in HANDOFF_LINES.values()
    assert t.escalate_calls == [] and "handoff_status" not in t.writes


# ── pending marker, cancellation, recent_turns, hold line (final-review fixes) ─

def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.mark.asyncio
async def test_pending_marker_written_before_escalate():
    t = _Turn()
    await t.stream()
    assert t.status_at_escalate == ["pending"]
    keys = [k for k, _ in t.write_log]
    assert keys.index("handoff_pending_at") < keys.index("handoff_ticket_id")
    assert t.writes["handoff_status"] == "delivered"


def test_sync_pending_marker_written_before_escalate():
    t = _Turn()
    t.sync()
    assert t.status_at_escalate == ["pending"]
    assert t.writes["handoff_status"] == "delivered"


@pytest.mark.asyncio
async def test_cancel_mid_escalate_still_records_result():
    t = _Turn()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_escalate(*a, **k):
        started.set()
        await release.wait()
        return {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-9"}

    t._arm_stream(escalate=slow_escalate)
    turn = asyncio.ensure_future(_collect_events(t.agent, _make_turn_input(channel="voice", user_message=ASK)))
    await started.wait()
    assert t.writes["handoff_status"] == "pending"
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert len(t.agent._bg_tasks) >= 1                               # escalate task still held
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert t.writes["handoff_status"] == "delivered" and t.writes["handoff_ticket_id"] == "TKT-9"


@pytest.mark.asyncio
async def test_recent_pending_is_already_and_sends_nothing():
    t = _Turn()
    t.session.update(handoff_status="pending", handoff_pending_at=str(_now_ms() - 5_000))
    text, _ = await t.stream()
    assert text == HANDOFF_LINES["already"] and t.escalate_calls == []
    assert t.writes["handoff_line"] == "already" and "handoff_status" not in t.writes
    assert not any(isinstance(e, SignalEvent) and e.stage == "tool_start" for e in t.events)


def test_sync_recent_pending_is_already():
    t = _Turn()
    t.session.update(handoff_status="pending", handoff_pending_at=_now_ms() - 5_000)
    assert t.sync().response_text == HANDOFF_LINES["already"] and t.escalate_calls == []


@pytest.mark.asyncio
async def test_stale_pending_retries():
    t = _Turn()
    t.session.update(handoff_status="pending", handoff_pending_at=str(_now_ms() - 30_000))
    text, _ = await t.stream()
    assert text == SPOKEN["delivered"] and len(t.escalate_calls) == 1


def test_sync_stale_pending_retries():
    t = _Turn()
    t.session.update(handoff_status="pending", handoff_pending_at=_now_ms() - 31_000)
    assert t.sync().response_text == SPOKEN["delivered"] and len(t.escalate_calls) == 1


@pytest.mark.asyncio
async def test_pending_never_arms_close_confirm_marker():
    t = _Turn()
    seen: list = []

    async def peek(*a, **k):
        seen.append(t.writes.get("handoff_line"))
        return {"queued": False}

    t._arm_stream(escalate=peek)
    await _collect_events(t.agent, _make_turn_input(channel="voice", user_message=ASK))
    assert seen == ["pending"] and t.writes["handoff_line"] == "failed"


@pytest.mark.asyncio
async def test_handoff_turn_lands_in_recent_turns_and_current_question():
    t = _Turn()
    t.session["recent_turns"] = [{"caller": "नमस्ते", "bot": "जी, बताइए।", "interrupted": False}]
    await t.stream()
    turns = t.writes["recent_turns"]
    assert turns[-1] == {"caller": ASK, "bot": SPOKEN["delivered"], "interrupted": False}
    assert turns[0]["caller"] == "नमस्ते"
    assert t.writes["current_question"] == SPOKEN["delivered"]


def test_sync_handoff_turn_lands_in_recent_turns_and_current_question():
    t = _Turn()
    t.sync()
    assert t.writes["recent_turns"][-1] == {"caller": ASK, "bot": SPOKEN["delivered"],
                                            "interrupted": False}
    assert t.writes["current_question"] == SPOKEN["delivered"]


@pytest.mark.asyncio
async def test_hold_signal_yielded_before_the_handoff_line():
    t = _Turn()
    await t.stream()
    kinds = [("start" if isinstance(e, SignalEvent) and e.stage == "tool_start" else
              "line" if isinstance(e, SentenceEvent) else None) for e in t.events]
    assert "start" in kinds and kinds.index("start") < kinds.index("line")
    start = next(e for e in t.events if isinstance(e, SignalEvent) and e.stage == "tool_start")
    assert start.tools == ["request_human"]


@pytest.mark.asyncio
async def test_no_hold_signal_after_delivery():
    t = _Turn()
    t.session["handoff_status"] = "delivered"
    await t.stream()
    assert not any(isinstance(e, SignalEvent) and e.stage == "tool_start" for e in t.events)


def test_blue_dots_bridge_has_a_hold_phrase_for_request_human():
    import yaml
    reach = yaml.safe_load((BLUE_DOTS / "reach_layer.yaml").read_text(encoding="utf-8"))
    phrases = reach["reach_layer"]["channels"]["bridge"]["tool_status_phrases"]
    assert phrases["request_human"] == "एक मिनट।"
