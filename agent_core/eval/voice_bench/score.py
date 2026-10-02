"""Per-call scoring: applicable TCs → verdicts (spec §6.5)."""
from __future__ import annotations

from eval.voice_bench.checks import DETERMINISTIC, CheckCtx
from eval.voice_bench.drive import _seed
from eval.voice_bench.judge import judge_tc
from eval.voice_bench.records import CallRecord, Verdict
from eval.voice_bench.suite import Persona, applicable_tcs

JUDGED = ("TC01", "TC10", "TC12", "TC14", "TC16", "TC17")


def score_call(rec: CallRecord, persona: Persona, judge_llm, places: dict, no_idle_handling: bool) -> dict[str, Verdict]:
    """Score every applicable TC; store into rec.verdicts and return it."""
    tcs = applicable_tcs(persona)
    if rec.error:
        out = {tc: Verdict("error", reason=f"bridge: {rec.error}") for tc in tcs}
    elif rec.void_reason == "broken_twice":
        out = {tc: Verdict("unscored", reason="persona broken twice") for tc in tcs}
    else:
        ctx = CheckCtx(rec, persona, places, no_idle_handling)
        out = {}
        for tc in tcs:
            if tc in DETERMINISTIC:
                out[tc] = DETERMINISTIC[tc](ctx)
            elif tc in JUDGED:
                out[tc] = judge_tc(judge_llm, tc, rec, persona, _seed(rec.target_commit, rec.scenario, rec.run, tc))
    rec.verdicts = out
    return out
