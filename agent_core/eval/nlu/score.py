# agent_core/eval/nlu/score.py
"""Scoring and the §11.5 switch-over gate for the NLU replay harness."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from eval.nlu.adapters import Prediction
from eval.nlu.cases import EvalCase

_GATED_TAGS = ("consent", "age", "termination")
_APPLY_INTENT = "apply_now"


def _canon(v) -> str:
    return str(v).strip().casefold()


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def score(cases: list[EvalCase], preds: dict[str, list[Prediction]], mode: str) -> dict:
    """Score predictions (one list per case, one entry per repeat).

    Args:
        cases: The cases.
        preds: case id → predictions.
        mode: ``intent`` or ``dialogue_act`` (acts/relation/topic scored only for the latter).

    Returns:
        Report dict: fields, tags (incl. per-tag termination_fp and apply_fp), confusion, termination_false_positives,
        latency_ms {p50, p95}, fallback_rate, agreement, precision_by_act_pending.
    """
    fields: dict[str, list[int]] = defaultdict(list)
    tags: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    prec: dict[str, list[int]] = defaultdict(list)
    latencies: list[int] = []
    fallbacks = 0
    term_fp = 0
    agree = []
    total = 0
    for case in cases:
        runs = preds.get(case.id) or []
        agree.append(int(len({p.intent for p in runs}) <= 1 and bool(runs)))
        exp = case.expect
        for p in runs:
            total += 1
            latencies.append(p.latency_ms)
            fallbacks += int(p.fallback is not None)
            ok = int(p.intent == exp["intent"])
            fields["intent"].append(ok)
            confusion[exp["intent"]][p.intent] += 1
            fp = int(not exp.get("terminate", False) and p.terminate)
            term_fp += fp
            if "terminate" in exp:
                fields["terminate"].append(int(p.terminate == exp["terminate"]))
            for k, v in (exp.get("slots") or {}).items():
                slot_ok = int(k in p.slots and _canon(p.slots[k]) == _canon(v))
                fields["slots"].append(slot_ok)
                for t in case.tags:
                    tags[t]["slot"].append(slot_ok)
            if "option_id" in exp:
                fields["option"].append(int(p.option_id == exp["option_id"]))
            if mode == "dialogue_act":
                for key, attr in (("acts", "acts"), ("relation", "relation"), ("topic", "topic"),
                                  ("pending", "pending")):
                    if key in exp:
                        got = list(p.acts) if key == "acts" else getattr(p, attr)
                        fields[key].append(int(got == exp[key]))
                # Decision precision, not act-label precision: the derived intent is
                # correct among predictions carrying this act under this pending.
                for act in p.acts:
                    prec[f"{act}|{p.pending or 'none'}"].append(ok)
            # An application the caller did not ask for (e.g. apply on a thank-you).
            apply_fp = int(p.intent == _APPLY_INTENT and exp["intent"] != _APPLY_INTENT)
            for t in case.tags:
                tags[t]["intent"].append(ok)
                tags[t]["fp"].append(fp)
                tags[t]["apply_fp"].append(apply_fp)
    return {
        "mode": mode, "cases": len(cases), "runs": total,
        "fields": {k: {"accuracy": round(sum(v) / len(v), 4), "n": len(v)} for k, v in fields.items()},
        "tags": {t: {"intent_accuracy": round(sum(d["intent"]) / len(d["intent"]), 4) if d["intent"] else None,
                     "slot_accuracy": round(sum(d["slot"]) / len(d["slot"]), 4) if d["slot"] else None,
                     "termination_fp": sum(d["fp"]), "apply_fp": sum(d["apply_fp"]),
                     "n": len(d["intent"])} for t, d in tags.items()},
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "termination_false_positives": term_fp,
        "latency_ms": {"p50": int(median(latencies)) if latencies else 0, "p95": _pct(latencies, 0.95)},
        "fallback_rate": round(fallbacks / total, 4) if total else 0.0,
        "agreement": round(sum(agree) / len(agree), 4) if agree else 0.0,
        "precision_by_act_pending": {k: {"precision": round(sum(v) / len(v), 4), "n": len(v)}
                                     for k, v in prec.items()},
    }


def gate(intent_report: dict, da_report: dict) -> list[str]:
    """§11.5 switch-over gate; returns one message per failed condition (empty = pass).

    Args:
        intent_report: ``score`` output for intent mode.
        da_report: ``score`` output for dialogue_act mode on the same cases.

    Returns:
        Failure messages.
    """
    fails: list[str] = []

    def acc(rep: dict, field: str) -> float:
        return (rep.get("fields", {}).get(field) or {}).get("accuracy", 0.0)

    for field in ("intent", "slots"):
        if acc(da_report, field) < acc(intent_report, field):
            fails.append(f"{field} accuracy {acc(da_report, field)} < intent mode {acc(intent_report, field)}")
    for tag in _GATED_TAGS:
        for fld in ("intent_accuracy", "slot_accuracy"):
            old = (intent_report.get("tags", {}).get(tag) or {}).get(fld)
            new = (da_report.get("tags", {}).get(tag) or {}).get(fld)
            if old is not None and (new is None or new < old):
                fails.append(f"tag '{tag}' {fld} regressed: {new} < {old}")
    if (da_report.get("tags", {}).get("acknowledge") or {}).get("termination_fp", 0) > 0:
        fails.append("termination false positives on acknowledge cases")
    if (da_report.get("tags", {}).get("acknowledge") or {}).get("apply_fp", 0) > 0:
        fails.append("apply_now false positives on acknowledge cases")
    if da_report["latency_ms"]["p50"] > intent_report["latency_ms"]["p50"] + 50:
        fails.append(f"p50 {da_report['latency_ms']['p50']} ms > intent-mode p50 + 50 ms")
    return fails
