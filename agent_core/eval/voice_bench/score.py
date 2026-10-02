"""Per-call scoring: applicable TCs → verdicts (spec §6.5)."""
from __future__ import annotations

from eval.voice_bench.checks import DETERMINISTIC, CheckCtx
from eval.voice_bench.drive import _seed
from eval.voice_bench.judge import judge_tc
from eval.voice_bench.records import CallRecord, Verdict
from eval.voice_bench.suite import Persona, applicable_tcs

JUDGED = ("TC01", "TC10", "TC12", "TC14", "TC16", "TC17")
UNSCORED_VOIDS = {"broken_twice": "persona broken twice", "broken_after_retry": "persona broken after retry"}


def score_call(rec: CallRecord, persona: Persona, judge_llm, places: dict, no_idle_handling: bool) -> dict[str, Verdict]:
    """Score every applicable TC; store into rec.verdicts and return it."""
    tcs = applicable_tcs(persona)
    if rec.harness_error:
        out = {tc: Verdict("error", reason=f"harness: {rec.harness_error}") for tc in tcs}
    elif rec.error:
        out = {tc: Verdict("error", reason=f"bridge: {rec.error}") for tc in tcs}
    elif rec.void_reason in UNSCORED_VOIDS:
        out = {tc: Verdict("unscored", reason=UNSCORED_VOIDS[rec.void_reason]) for tc in tcs}
    else:
        ctx = CheckCtx(rec, persona, places, no_idle_handling)
        out = {}
        for tc in tcs:
            if tc in DETERMINISTIC:
                try:
                    out[tc] = DETERMINISTIC[tc](ctx)
                except Exception as e:  # noqa: BLE001 - class name only
                    out[tc] = Verdict("error", reason=f"check: {type(e).__name__}")
            elif tc in JUDGED:
                out[tc] = judge_tc(judge_llm, tc, rec, persona, _seed(rec.target_commit, rec.scenario, rec.run, tc))
            else:
                out[tc] = Verdict("unscored", reason="no checker")
    rec.verdicts = out
    return out
