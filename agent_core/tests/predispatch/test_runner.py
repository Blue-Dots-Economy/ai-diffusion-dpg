import asyncio

import pytest

from src.models import ToolResult
from src.predispatch.rules import Selection
from src.predispatch.runner import PREDISPATCH_ID, run_async, run_sync
from src.tool_guard import GuardVerdict


def _res(tc, ok=True):
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=ok,
                      result_text='[{"item_id": "j1"}]' if ok else "", error=None if ok else "boom")


READ = Selection(tool="fetch_jobs", args={"query_text": "Welder jobs in Bengaluru"}, outcome="fired", is_write=False)
WRITE = Selection(tool="apply_job", args={"profile_item_id": "p1", "job_item_id": "j"}, outcome="fired", is_write=True)


async def _go(tc):
    return GuardVerdict("go")


@pytest.mark.asyncio
async def test_read_success_injects_and_removes():
    async def ex(tc):
        assert tc.tool_use_id == PREDISPATCH_ID and tc.input_params == READ.args
        return _res(tc)
    r = await run_async(READ, guard=_go, execute=ex, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("fired", True, True) and r.tool_result.success


@pytest.mark.asyncio
async def test_no_selection_is_noop():
    r = await run_async(Selection(tool=None, outcome="skipped_fresh"), guard=_go, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.tool_call) == ("skipped_fresh", False, None)


@pytest.mark.asyncio
async def test_guard_refusal_falls_back():
    async def refuse(tc):
        return GuardVerdict("refuse", _res(tc, ok=False))
    r = await run_async(WRITE, guard=refuse, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("refused_guard", False, False)


@pytest.mark.asyncio
async def test_cache_hit_injects():
    async def hit(tc):
        return GuardVerdict("hit", _res(tc))
    r = await run_async(READ, guard=hit, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("cache_hit", True, True)


@pytest.mark.asyncio
async def test_read_failure_falls_back_write_failure_injects():
    async def fail(tc):
        return _res(tc, ok=False)
    r = await run_async(READ, guard=_go, execute=fail, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("failed", False, False)
    w = await run_async(WRITE, guard=_go, execute=fail, timeout_s=1.5)
    assert (w.outcome, w.inject, w.remove_tool) == ("failed", True, True)


@pytest.mark.asyncio
async def test_timeout_read_falls_back_write_injects_failure():
    async def slow(tc):
        await asyncio.sleep(1)
        return _res(tc)
    r = await run_async(READ, guard=_go, execute=slow, timeout_s=0.05)
    assert (r.outcome, r.inject, r.remove_tool) == ("timeout", False, False)


@pytest.mark.asyncio
async def test_write_gets_no_asyncio_budget():
    """A write runs to completion past ``timeout_s``: a local cancel cannot undo an applied upstream write."""
    async def slow(tc):
        await asyncio.sleep(0.15)
        return _res(tc)
    w = await run_async(WRITE, guard=_go, execute=slow, timeout_s=0.01)
    assert (w.outcome, w.inject, w.remove_tool, w.tool_result.success) == ("fired", True, True, True)


@pytest.mark.parametrize("path", ["async", "sync"])
@pytest.mark.asyncio
async def test_write_gateway_timeout_injects_timeout(path):
    """The write timeout now comes from the gateway's own timeout result."""
    def timed_out(tc):
        return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=False,
                          result_text="", error=f"gateway_timeout: {tc.tool_name}")
    if path == "async":
        async def ex(tc):
            return timed_out(tc)
        w = await run_async(WRITE, guard=_go, execute=ex, timeout_s=1.5)
    else:
        w = run_sync(WRITE, guard=lambda tc: GuardVerdict("go"), execute=timed_out)
    assert (w.outcome, w.inject, w.remove_tool, w.tool_result.success) == ("timeout", True, True, False)


@pytest.mark.asyncio
async def test_internal_error_is_did_not_fire():
    async def boom(tc):
        raise RuntimeError("x")
    r = await run_async(READ, guard=boom, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("error", False, False)


@pytest.mark.asyncio
async def test_write_cache_hit_injects():
    async def hit(tc):
        return GuardVerdict("hit", _res(tc))
    w = await run_async(WRITE, guard=hit, execute=None, timeout_s=1.5)
    assert (w.outcome, w.inject, w.remove_tool) == ("cache_hit", True, True)


@pytest.mark.asyncio
async def test_read_execute_raises_falls_back():
    async def boom(tc):
        raise RuntimeError("upstream error")
    r = await run_async(READ, guard=_go, execute=boom, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("error", False, False)


@pytest.mark.asyncio
async def test_write_execute_raises_injects_failure():
    async def boom(tc):
        raise RuntimeError("upstream error")
    w = await run_async(WRITE, guard=_go, execute=boom, timeout_s=1.5)
    assert (w.outcome, w.inject, w.remove_tool, w.tool_result.success) == ("error", True, True, False)


def test_sync_mirrors_async():
    r = run_sync(READ, guard=lambda tc: GuardVerdict("go"), execute=lambda tc: _res(tc))
    assert (r.outcome, r.inject, r.remove_tool) == ("fired", True, True)
    w = run_sync(WRITE, guard=lambda tc: GuardVerdict("go"), execute=lambda tc: _res(tc, ok=False))
    assert (w.outcome, w.inject, w.remove_tool) == ("failed", True, True)


def test_sync_guard_refusal():
    def refuse(tc):
        return GuardVerdict("refuse", _res(tc, ok=False))
    r = run_sync(WRITE, guard=refuse, execute=None)
    assert (r.outcome, r.inject, r.remove_tool) == ("refused_guard", False, False)


def test_sync_cache_hit():
    def hit(tc):
        return GuardVerdict("hit", _res(tc))
    r = run_sync(WRITE, guard=hit, execute=None)
    assert (r.outcome, r.inject, r.remove_tool) == ("cache_hit", True, True)


def test_sync_guard_exception():
    def boom(tc):
        raise RuntimeError("x")
    r = run_sync(READ, guard=boom, execute=None)
    assert (r.outcome, r.inject, r.remove_tool) == ("error", False, False)


def test_sync_write_execute_raises_injects_failure():
    def boom(tc):
        raise RuntimeError("upstream error")
    w = run_sync(WRITE, guard=lambda tc: GuardVerdict("go"), execute=boom)
    assert (w.outcome, w.inject, w.remove_tool, w.tool_result.success) == ("error", True, True, False)


def test_sync_read_execute_raises_falls_back():
    def boom(tc):
        raise RuntimeError("upstream error")
    r = run_sync(READ, guard=lambda tc: GuardVerdict("go"), execute=boom)
    assert (r.outcome, r.inject, r.remove_tool, r.tool_result) == ("error", False, False, None)


@pytest.mark.asyncio
async def test_guard_hit_without_result_is_error_no_inject():
    async def hit_none(tc):
        return GuardVerdict("hit", None)
    r = await run_async(READ, guard=hit_none, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool, r.tool_result) == ("error", False, False, None)
    s = run_sync(WRITE, guard=lambda tc: GuardVerdict("hit", None), execute=None)
    assert (s.outcome, s.inject, s.remove_tool, s.tool_result) == ("error", False, False, None)
