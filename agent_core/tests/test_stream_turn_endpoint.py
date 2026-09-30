"""/stream_turn through the TurnAssembler (Agent Core block, orchestration server).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.2
"""

import asyncio
import json
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.models import DoneEvent, SentenceEvent
from src.servers.orchestration_server import create_orchestration_app
from src.turn_assembler import TurnAssembler, TurnStatus

from tests.test_turn_assembler_interrupt import _SlowAgent, _cfg


def _app(agent, config=None):
    assembler = TurnAssembler(agent_core=agent, config=config or _cfg())
    return create_orchestration_app(agent, turn_assembler=assembler), assembler


def _events(resp):
    return [json.loads(l[len("data: "):]) for l in resp.text.splitlines() if l.startswith("data: ")]


def test_stream_turn_goes_through_assembler():
    agent = _SlowAgent(tool_s=0.01)
    app, assembler = _app(agent)
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={
            "session_id": "s1", "user_message": "hello", "channel": "bridge", "user_id": "u1"})
    assert resp.status_code == 200
    evs = _events(resp)
    assert evs[-1]["type"] == "done" and evs[-1]["turn_status"] == "completed"
    assert "s1" in assembler._sessions
    assert agent.calls == ["hello"]


async def test_disconnect_after_done_is_not_an_interrupt():
    """Review focus 1: reading DoneEvent then closing must not interrupt.

    Drives the SSE body directly (TestClient would read it to the end) and
    closes it right after the done chunk, as a client hanging up would.
    """
    agent = _SlowAgent(tool_s=0.01)
    app, assembler = _app(agent)
    detached = []
    real_detach = assembler.detach

    def spy_detach(turn, reason):
        detached.append(reason)
        real_detach(turn, reason)

    assembler.detach = spy_detach
    route = next(r for r in app.routes if getattr(r, "path", "") == "/stream_turn")
    from src.servers.orchestration_server import ProcessTurnRequest
    resp = await route.endpoint(ProcessTurnRequest(
        session_id="s1", user_message="hello", channel="bridge", user_id="u1"))
    body = resp.body_iterator
    chunk = ""
    while '"type": "done"' not in chunk and '"type":"done"' not in chunk:
        chunk = await asyncio.wait_for(body.__anext__(), 2)
        chunk = chunk.decode() if isinstance(chunk, bytes) else chunk
    await body.aclose()                          # client went away after done
    turn = assembler._sessions["s1"].current_turn
    await asyncio.wait_for(turn.invocation_task, 2)
    assert json.loads(chunk[len("data: "):].strip())["turn_status"] == "completed"
    assert turn.status == TurnStatus.COMPLETED
    assert not turn.abort_event.is_set()
    assert detached == []
    assert turn.record.persist_task is None


async def test_generator_close_before_done_detaches():
    """Starlette closing the SSE body early must interrupt, cooperatively."""
    agent = _SlowAgent(tool_s=0.3)
    app, assembler = _app(agent)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/stream_turn")
    from src.servers.orchestration_server import ProcessTurnRequest
    resp = await route.endpoint(ProcessTurnRequest(
        session_id="s1", user_message="hello", channel="bridge", user_id="u1"))
    body = resp.body_iterator
    await body.__anext__()                       # first event read
    await body.aclose()                          # client went away
    turn = assembler._sessions["s1"].current_turn
    assert turn.status == TurnStatus.INTERRUPTED
    assert not turn.invocation_task.cancelled()
    await asyncio.wait_for(turn.invocation_task, 2)
    assert agent.stopped == ["hello"]


def test_without_assembler_direct_path_unchanged():
    agent = MagicMock()

    async def mock_stream(ti, **kwargs):
        yield DoneEvent(turn_status="completed")

    agent.stream_turn = mock_stream
    app = create_orchestration_app(agent)
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={"session_id": "s1", "user_message": "hi"})
    assert _events(resp)[-1]["turn_status"] == "completed"


def test_empty_message_rejected_with_assembler():
    app, _ = _app(_SlowAgent())
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={"session_id": "s1", "user_message": "  "})
    assert resp.status_code == 422
