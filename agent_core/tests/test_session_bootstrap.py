"""Unit tests for the session bootstrap unit (session-bootstrap spec §5)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.models import ToolResult
from src.session_bootstrap import LATCH, SessionBootstrap
from src.tool_results import ToolResultPolicies

CONFIG = {
    "connectors": {"read": [{"name": "fetch_profile", "cache": {"scope": "session", "ttl_seconds": 1800}}]},
    "session_bootstrap": {"timeout_ms": 500, "steps": [{"type": "tool", "tool": "fetch_profile"}]},
}
POL = ToolResultPolicies.from_config(CONFIG)


def bundle(session=None):
    return SimpleNamespace(session=dict(session or {}), profile={}, tool_results=[])


def ok_result(**session_values):
    return ToolResult(tool_use_id="bootstrap-0", tool_name="fetch_profile", result={}, success=True,
                      result_text=json.dumps({"items": [{"item_id": "p1"}]}), projected=True,
                      session_values=session_values)


class Rec:
    def __init__(self):
        self.writes, self.batches, self.calls = [], [], []


def sync_deps(rec, result=None, consent=True, raise_exc=None):
    def execute(tc):
        rec.calls.append(tc)
        if raise_exc:
            raise raise_exc
        return result or ok_result(has_age=True, profile_item_id="p1")
    return dict(execute=execute, check_consent=lambda t: consent,
                write_session=lambda k, v: rec.writes.append((k, v)),
                apply_tool_results=lambda b: rec.batches.append(b))


def test_from_config_none_without_section():
    assert SessionBootstrap.from_config({}, POL) is None


def test_needed_respects_latch():
    boot = SessionBootstrap.from_config(CONFIG, POL)
    assert boot.needed(bundle()) is True
    assert boot.needed(bundle({LATCH: True})) is False


def test_sync_success_writes_values_cache_and_latch_first():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec))
    assert report.outcomes == {"fetch_profile": "ok"}
    assert rec.writes[0] == (LATCH, True)                     # latch before anything else
    assert ("has_age", True) in rec.writes and b.session["has_age"] is True
    assert b.session["profile_item_id"] == "p1" and b.session[LATCH] is True
    assert len(b.tool_results) == 1 and b.tool_results[0]["origin"] == "bootstrap"
    assert rec.batches[0]["puts"][0]["origin"] == "bootstrap"
    assert rec.calls[0].tool_use_id == "bootstrap-0" and rec.calls[0].input_params == {}


def test_sync_failure_sets_latch_writes_nothing_else():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=False, error="boom")
    report = boot.run_sync(b, **sync_deps(rec, result=bad))
    assert report.outcomes == {"fetch_profile": "failed"}
    assert rec.writes == [(LATCH, True)] and b.tool_results == [] and rec.batches == []


def test_sync_exception_never_raises():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec, raise_exc=RuntimeError("down")))
    assert report.outcomes == {"fetch_profile": "failed"} and b.session[LATCH] is True


def test_step_line_mark_reflects_outcomes(caplog):
    boot = SessionBootstrap.from_config(CONFIG, POL)
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=False, error="boom")
    with caplog.at_level("INFO"):
        boot.run_sync(bundle(), **sync_deps(Rec(), result=bad))
    line = next(r.getMessage() for r in caplog.records if "[STEP 1b]" in r.getMessage())
    assert "✗" in line and "✓" not in line
    caplog.clear()
    with caplog.at_level("INFO"):
        boot.run_sync(bundle(), **sync_deps(Rec()))
    line = next(r.getMessage() for r in caplog.records if "[STEP 1b]" in r.getMessage())
    assert "✓" in line and "✗" not in line


def test_turn_log_status_reflects_outcomes(caplog):
    """Test that session_bootstrap.turn log status is 'success' only when all outcomes are 'ok'."""
    boot = SessionBootstrap.from_config(CONFIG, POL)
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=False, error="boom")
    # Test failure case
    with caplog.at_level("INFO", logger="src.session_bootstrap"):
        boot.run_sync(bundle(), **sync_deps(Rec(), result=bad))
    record = next(r for r in caplog.records if r.getMessage() == "session_bootstrap.turn")
    assert record.status == "failure"
    caplog.clear()
    # Test success case
    with caplog.at_level("INFO", logger="src.session_bootstrap"):
        boot.run_sync(bundle(), **sync_deps(Rec()))
    record = next(r for r in caplog.records if r.getMessage() == "session_bootstrap.turn")
    assert record.status == "success"


def test_consent_skip():
    cfg = {**CONFIG, "session_bootstrap": {"steps": [{"type": "tool", "tool": "fetch_profile", "requires_consent": True}]}}
    boot, rec, b = SessionBootstrap.from_config(cfg, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec, consent=False))
    assert report.outcomes == {"fetch_profile": "skipped_consent"} and rec.calls == []


def test_sync_late_result_discarded(monkeypatch):
    import src.session_bootstrap as sb
    times = iter([0.0, 0.0, 10.0, 10.0, 10.0])          # start, pre-step check, after call (past 0.5s), ...
    monkeypatch.setattr(sb.time, "monotonic", lambda: next(times, 10.0))
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec))
    assert report.outcomes == {"fetch_profile": "timeout"}
    assert "has_age" not in b.session and b.tool_results == [] and rec.batches == []


async def test_async_success_and_timeout_discard():
    boot = SessionBootstrap.from_config(CONFIG, POL)
    rec, b = Rec(), bundle()

    async def execute(tc):
        return ok_result(has_age=True)

    async def noop(*a):
        rec.writes.append(a) if len(a) == 2 else rec.batches.append(a[0])

    async def consent(t):
        return True

    report = await boot.run_async(b, execute=execute, check_consent=consent, write_session=noop,
                                  apply_tool_results=noop)
    assert report.outcomes == {"fetch_profile": "ok"} and b.session["has_age"] is True

    rec2, b2 = Rec(), bundle()

    async def slow(tc):
        await asyncio.sleep(2)
        return ok_result(has_age=True)

    report2 = await boot.run_async(b2, execute=slow, check_consent=consent, write_session=noop,
                                   apply_tool_results=noop)
    assert report2.outcomes == {"fetch_profile": "timeout"}
    assert "has_age" not in b2.session and b2.tool_results == [] and b2.session[LATCH] is True
