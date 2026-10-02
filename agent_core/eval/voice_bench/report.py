"""Report aggregation and markdown rendering (spec §6.7).

Pass rate = pass / (pass + fail + unscored + error); ``n/a`` is excluded. Every figure carries ``n``.
Output (which may contain Devanagari) goes to files only, never to stdout.
"""
from __future__ import annotations

import math
from collections import defaultdict

from eval.voice_bench import SUITE_VERSION
from eval.voice_bench.checks import turn_latencies
from eval.voice_bench.records import VERDICT_STATUSES, CallRecord
from eval.voice_bench.suite import TCS

CALL_TCS = [tc for tc in TCS if TCS[tc].method != "nlu"]   # TC01..TC21
_COMPARE_KEYS = ("suite_version", "seed_version", "judge_model")
_EXCERPTS_PER_TC = 3
_LAT_ROWS = (("p50", "p50"), ("p90", "p90"), ("p95", "p95"), ("max", "max"), (">3s", "over3s"), (">5s", "over5s"))
_SPLITS = (("all", "all turns"), ("non_tool", "non-tool turns"), ("tool", "tool turns"))
_OUT_OF_SCOPE = ("STT and TTS quality: opening-line intelligibility, pitch, level, ASR mishearings, speaking rate "
                 "and barge-in timing (these need audio)")


def pct(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile; None on empty."""
    if not values:
        return None
    s = sorted(values)
    return s[max(1, math.ceil(q * len(s))) - 1]


def _lat(values: list[int]) -> dict:
    return {"n": len(values), "p50": pct(values, 0.5), "p90": pct(values, 0.9), "p95": pct(values, 0.95),
            "max": max(values) if values else None, "over3s": sum(v > 3000 for v in values),
            "over5s": sum(v > 5000 for v in values)}


def _latency(records: list[CallRecord], which: int) -> dict:
    split: dict[str, list[int]] = {"all": [], "non_tool": [], "tool": []}
    for rec in records:
        for row in turn_latencies(rec):
            v, is_tool = row[which], row[2]
            if v is None:
                continue
            split["all"].append(v)
            split["tool" if is_tool else "non_tool"].append(v)
    return {k: _lat(v) for k, v in split.items()}


def _one(records: list[CallRecord], attr: str):
    vals = {getattr(r, attr) for r in records}
    return vals.pop() if len(vals) == 1 else (sorted(map(str, vals)) if vals else None)


def summarise(records: list[CallRecord], meta: dict) -> dict:
    """Aggregate one target's call records plus its meta (name, nlu, unmeasurable)."""
    tc = {t: {s: 0 for s in VERDICT_STATUSES} for t in CALL_TCS}
    failures = []
    llm_calls = []
    for rec in records:
        for name, v in rec.verdicts.items():
            if name in tc:
                tc[name][v.status] += 1
            if v.status in ("fail", "error"):
                failures.append({"tc": name, "scenario": rec.scenario, "run": rec.run, "turn": v.turn,
                                 "quote": v.quote, "reason": v.reason})
        for lg in rec.legs:
            for t in lg.turns:
                n = (t.banner or {}).get("llm_calls")
                if isinstance(n, (int, float)) and not isinstance(n, bool):
                    llm_calls.append(n)
    for counts in tc.values():
        n = counts["pass"] + counts["fail"] + counts["unscored"] + counts["error"]
        counts["n"] = n
        counts["rate"] = round(counts["pass"] / n, 4) if n else None
    failures.sort(key=lambda f: (f["tc"], f["scenario"], f["run"], f["turn"] if f["turn"] is not None else -1))
    commit = meta.get("commit") or (records[0].target_commit if records else None)
    return {
        "target": meta.get("name") or (records[0].target if records else None), "commit": commit,
        "n_calls": len(records),
        "suite_version": _one(records, "suite_version") if records else SUITE_VERSION,
        "seed_version": _one(records, "seed_version"), "judge_model": _one(records, "judge_model"),
        "tc": tc, "latency": _latency(records, 0), "latency_reply": _latency(records, 1),
        "llm_calls_mean": round(sum(llm_calls) / len(llm_calls), 2) if llm_calls else None,
        "llm_calls_n": len(llm_calls),
        "failures": failures, "nlu": meta.get("nlu"), "unmeasurable": meta.get("unmeasurable"),
    }


def comparable(summaries: list[dict]) -> list[str]:
    """Reasons the summaries cannot be compared (empty = comparable). Targets with no calls are ignored."""
    reasons = []
    for key in _COMPARE_KEYS:
        reasons += [f"{key} mixed within {s.get('target')}: {s[key]}" for s in summaries if isinstance(s.get(key), list)]
        vals = {str(s.get(key)): s.get("target") for s in summaries if s.get("n_calls") and s.get(key) is not None}
        if len(vals) > 1:
            reasons.append(f"{key} differs: " + ", ".join(f"{t}={v}" for v, t in sorted(vals.items())))
    return reasons


# ---- markdown -------------------------------------------------------------------------------------------------
def _cell(c: dict) -> str:
    return "—" if not c["n"] else f"{round(c['rate'] * 100)}% ({c['pass']}/{c['n']})"


def _delta(a: dict, b: dict) -> str:
    if not a["n"] or not b["n"]:
        return "—"
    return f"{round((b['rate'] - a['rate']) * 100):+d}"


def _fmt(v) -> str:
    return "—" if v is None else str(v)


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    return out + ["| " + " | ".join(r) + " |" for r in rows]


def _latency_section(summaries: list[dict], key: str, title: str) -> list[str]:
    out = [f"## Latency ({title}, ms)", ""]
    for split, caption in _SPLITS:
        out.append(f"**{caption}**" + (" — tool turns hit local emulated TEI; compare like for like"
                                       if split == "tool" else ""))
        out.append("")
        head = ["metric"] + [f"{s['target']} (n={s[key][split]['n']})" for s in summaries]
        rows = [[label] + [_fmt(s[key][split][k]) for s in summaries] for label, k in _LAT_ROWS]
        out += _table(head, rows) + [""]
    return out


def _nlu_line(s: dict) -> str:
    nlu = s.get("nlu")
    if s.get("unmeasurable") and not nlu:
        return f"- **{s['target']}**: unmeasurable — {s['unmeasurable']}"
    if not nlu:
        return f"- **{s['target']}**: not run"
    if nlu.get("unmeasurable"):
        return f"- **{s['target']}**: unmeasurable — {nlu['unmeasurable']}"
    rep = nlu.get("report") or {}
    fields = rep.get("fields") or {}
    parts = [f"adapter {nlu.get('adapter', '?')}"]
    for name, label in (("intent", "intent accuracy"), ("slots", "slot accuracy")):
        f = fields.get(name)
        if f:
            parts.append(f"{label} {round(f['accuracy'] * 100)}% (n={f['n']})")
    parts.append(f"termination false positives {_fmt(rep.get('termination_false_positives'))}")
    lat = rep.get("latency_ms") or {}
    parts.append(f"latency p50 {_fmt(lat.get('p50'))} ms / p95 {_fmt(lat.get('p95'))} ms")
    return f"- **{s['target']}**: " + "; ".join(parts)


def _failures_section(summaries: list[dict]) -> list[str]:
    out = ["## Failures", ""]
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in summaries:
        for f in s["failures"]:
            by[(f["tc"], s["target"])].append(f)
    if not by:
        return out + ["None.", ""]
    for (tc, target), fs in sorted(by.items()):
        out.append(f"### {tc} · {target} ({len(fs)})")
        for f in fs[:_EXCERPTS_PER_TC]:
            where = f"{f['scenario']} r{f['run']}" + (f" t{f['turn']}" if f["turn"] is not None else "")
            quote = f'"{f["quote"]}"' if f["quote"] else "(no quote)"
            out.append(f"- **{tc} · {where}** — {quote} — {f['reason'] or '(no reason)'}")
        out.append("")
    return out


def render_markdown(summaries: list[dict]) -> str:
    """Render the comparison report (spec §6.7) as markdown."""
    out = [f"# voice-bench report (suite v{SUITE_VERSION})", ""]
    for s in summaries:
        stamp = (f"- **{s['target']}** · commit `{s['commit']}` · n={s['n_calls']} calls · "
                 f"seed v{_fmt(s['seed_version'])} · judge {_fmt(s['judge_model'])}")
        if s.get("unmeasurable"):
            stamp += f" · **unmeasurable:** {s['unmeasurable']}"
        out.append(stamp)
        out += [f"  - **Warning ({s['target']}):** {w}" for w in s.get("warnings") or []]
    reasons = comparable(summaries)
    if reasons:
        out += ["", "> **Warning: targets are not comparable.** " + "; ".join(reasons)]
    out += ["", "## Test cases", ""]
    head = ["TC"] + [s["target"] for s in summaries] + ["Δ pp (last two)"]
    rows = []
    for tc in CALL_TCS:
        cells = [_cell(s["tc"][tc]) for s in summaries]
        d = _delta(summaries[-2]["tc"][tc], summaries[-1]["tc"][tc]) if len(summaries) >= 2 else "—"
        rows.append([tc, *cells, d])
    out += _table(head, rows) + [""]
    llm = ", ".join(f"{s['target']} {_fmt(s['llm_calls_mean'])} (n={s['llm_calls_n']} turns)" for s in summaries)
    out += [f"Mean LLM calls per turn: {llm}", ""]
    out += _latency_section(summaries, "latency", "time to first content")
    out += _latency_section(summaries, "latency_reply", "time to first reply")
    out += ["## NLU (TC22)", ""] + [_nlu_line(s) for s in summaries] + [""]
    out += _failures_section(summaries)
    out += ["## Notes", "",
            "- **Latency boundary (spec §7):** latency is our share of the caller's wait, from the bridge request to "
            "its first streamed content. Field figures also include STT, turn detection, TTS and the phone network "
            "both ways, so their targets are a ceiling for ours.",
            "- **Tool turns** run against a local, emulated TEI; latency claims rest on non-tool turns.",
            f"- **Out of scope:** {_OUT_OF_SCOPE}.",
            "- Pass rate = pass / (pass + fail + unscored + error); n/a is excluded. Every figure shows its n.",
            "- Latency includes turns from calls that were re-run after a persona break.",
            "- These results show direction, not statistical significance.", ""]
    return "\n".join(out)
