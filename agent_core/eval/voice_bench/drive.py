# agent_core/eval/voice_bench/drive.py
"""Drive one benchmarked call (spec §6.4): legs × turns against the bridge, with retry and voiding."""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from eval.voice_bench import observe
from eval.voice_bench.caller import Caller, CallerLLMError, persona_broken, stage_direction
from eval.voice_bench.records import CallRecord, Leg, TurnRecord
from eval.voice_bench.suite import Persona


@dataclass
class DriveDeps:
    bridge: Any
    tap: Any
    redis_container: str
    scraper: Any
    caller: Caller
    judge_llm: Any
    cleanup: Callable[[], None]
    read_session: Callable = field(default=observe.read_session)


def _seed(*parts) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _leg(deps: DriveDeps, persona: Persona, run_idx: int, leg_idx: int, phone: str, max_turns: int) -> Leg:
    call_id = f"vb-{persona.id}-{run_idx}-{leg_idx}-{uuid.uuid4().hex[:6]}"
    turns: list[TurnRecord] = []
    history: list[tuple[str, str]] = []
    line = persona.legs[leg_idx].opening_line
    for i in range(max_turns):
        if i > 0:
            try:
                line = deps.caller.next_line(persona, leg_idx, history, _seed(persona.id, run_idx, leg_idx, i))
            except CallerLLMError as e:
                return Leg(call_id, turns, "error", str(e))
            except Exception as e:
                return Leg(call_id, turns, "error", f"caller_llm_{type(e).__name__}")
            if line == "<END>":
                return Leg(call_id, turns, "caller")
        since_ms, since_s = int(time.time() * 1000), int(time.time())
        bt = deps.bridge.turn(line, phone, call_id)
        tap = deps.tap.take(since_ms)
        session = deps.read_session(deps.redis_container, phone, call_id)
        banner = observe.parse_banner(deps.scraper.since(since_s))
        turns.append(TurnRecord(idx=i, caller=line, reply=bt.reply, status_phrase=bt.status_phrase,
                                t_first_content_ms=bt.t_first_content_ms, t_first_reply_ms=bt.t_first_reply_ms,
                                t_total_ms=bt.t_total_ms, session=session, tap=tap, banner=banner,
                                session_ended=bt.session_ended, error=bt.error))
        history.append((line, bt.reply))
        if bt.error:
            return Leg(call_id, turns, "error")
        if bt.session_ended:
            return Leg(call_id, turns, "bot")
    return Leg(call_id, turns, "max_turns")


def _attempt(deps, persona, run_idx, phone, max_turns) -> list[Leg]:
    legs = []
    for leg_idx in range(len(persona.legs)):
        lg = _leg(deps, persona, run_idx, leg_idx, phone, max_turns)
        legs.append(lg)
        if lg.ended_by == "error":
            break
    return legs


def _err(legs: list[Leg]) -> str | None:
    return next((e for lg in legs for e in [lg.error, *(t.error for t in lg.turns)] if e), None)


def _broken(deps, persona, run_idx, legs) -> bool:
    lines = [t.caller for lg in legs for t in lg.turns]
    return any(stage_direction(x) for x in lines) or persona_broken(deps.judge_llm, persona, lines, _seed(persona.id, run_idx, "audit"))


def drive_call(deps: DriveDeps, persona: Persona, run_idx: int, phone: str, max_turns: int, meta: dict) -> CallRecord:
    """Run one (scenario, run): retry once on bridge error, void-and-rerun once on persona break."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    attempts, void_reason = 1, None
    deps.cleanup()
    try:
        legs = _attempt(deps, persona, run_idx, phone, max_turns)
        if _err(legs):
            deps.cleanup()
            attempts += 1
            legs = _attempt(deps, persona, run_idx, phone, max_turns)
        elif _broken(deps, persona, run_idx, legs):
            deps.cleanup()
            attempts += 1
            legs = _attempt(deps, persona, run_idx, phone, max_turns)
            void_reason = "broken_twice" if _broken(deps, persona, run_idx, legs) else "rerun_ok"
    finally:
        deps.cleanup()
    return CallRecord(target=meta["target"], target_commit=meta["target_commit"], scenario=persona.id, run=run_idx,
                      phone=phone, suite_version=meta["suite_version"], seed_version=meta["seed_version"],
                      caller_model=meta["caller_model"], judge_model=meta["judge_model"], legs=legs,
                      attempts=attempts, voided=void_reason is not None, error=_err(legs),
                      started_at=started, void_reason=void_reason)
