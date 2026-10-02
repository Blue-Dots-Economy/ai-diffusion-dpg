"""Scripted scenario runner for the Blue Dots bridge (Spec D §9.2).

Drives the bridge streaming path turn by turn, runs the automatic checks on each
reply and writes a JSON report. Replies live only in the report, never on stdout.

Known gaps (Spec D §9.3 metrics the bridge does not expose; listed under ``not_collected`` in
the report summary): llm_ttft_ms, input tokens and the digit-rewrite rate. They live in the
agent_core ``orchestrator.stream_turn_complete`` log extras (``digits_rewritten``,
``foreign_script_words``) and in the OTel metrics.

With ``--agent-container`` the runner also reads ``llm_calls``, ``predispatch_tool`` and
``predispatch_outcome`` (Spec E §10) from that container's logs after each turn. agent_core's log
format does not render ``extra=`` fields, so they are read from the "STREAM TURN COMPLETE"
message (stream and sync banners, last banner block, extras line only). A failed scrape records
None and never fails the run; replies are never printed.

Limits: the scrape assumes sequential, single-session use. It reads ``docker logs --since`` the
turn start at one-second granularity with no session filter, so concurrent runs against the same
agent container can read each other's turns.

An order check result of None means skipped (no spoken-pay marker in the reply); it is counted
under ``skipped_counts``, never as a pass.

Usage:
    python -m eval.scenarios.run --bridge http://127.0.0.1:18008 \
        --scenarios eval/scenarios/blue_dots.yaml --out report.json [--redis-container dpg_redis] [--agent-container dpg_agent_core]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import yaml

from eval.scenarios.checks import foreign_script_words, run_checks


NOT_COLLECTED = [
    "llm_ttft_ms: OTel metrics (agent_core), not exposed by the bridge",
    "input_tokens: OTel metrics (agent_core), not exposed by the bridge",
    "digit_rewrite_rate: `orchestrator.stream_turn_complete` log extras `digits_rewritten` / "
    "`foreign_script_words`, and OTel `agent_core.output_guard.digits_rewritten_total`",
]


_EXTRAS_LINE = re.compile(
    r"^\s*llm_calls=(\S+)\s+predispatch_tool=(\S+)\s+predispatch_outcome=(\S+)\s+predispatch_ms=(\S+)\s*$",
    re.M)
_BANNER_HEADER = re.compile(r"^\s*(?:STREAM )?TURN COMPLETE\b", re.M)
_RESPONSE_LINE = re.compile(r"^\s*response:", re.M)


def _val(raw: str) -> str | None:
    return None if raw == "None" else raw


def parse_turn_extras(log_text: str) -> dict:
    """Return the last turn-complete record's llm_calls, predispatch_tool and predispatch_outcome.

    Args:
        log_text: Agent container log text.

    Returns:
        Dict with those three keys; any missing or ``None`` value is None. ``llm_calls`` is an int.
    """
    out: dict = {"llm_calls": None, "predispatch_tool": None, "predispatch_outcome": None}
    headers = list(_BANNER_HEADER.finditer(log_text or ""))
    if not headers:
        return out
    block = log_text[headers[-1].start():]
    # The extras line sits before the reply line; never look past it (replies are untrusted text).
    resp = _RESPONSE_LINE.search(block)
    if resp:
        block = block[:resp.start()]
    m = _EXTRAS_LINE.search(block)
    if not m:
        return out
    calls, tool, outcome, _ms = m.groups()
    try:
        out["llm_calls"] = int(calls)
    except ValueError:
        pass
    out["predispatch_tool"] = _val(tool)
    out["predispatch_outcome"] = _val(outcome)
    return out


def _scrape_turn_extras(container: str, since_epoch: float) -> dict:
    """Read the extras for the turn that started at ``since_epoch``; all None on any error."""
    try:
        res = subprocess.run(["docker", "logs", "--since", str(int(since_epoch)), container],
                             capture_output=True, text=True, timeout=10, check=False)
        return parse_turn_extras(res.stdout + res.stderr)
    except Exception:  # noqa: BLE001 - the scrape must never fail the run
        return parse_turn_extras("")


def _split_by_outcome(turns: list[dict]) -> dict:
    """p50/p95 first_sentence_ms for turns where pre-dispatch fired vs not."""
    out = {}
    for name, fired in (("fired", True), ("not_fired", False)):
        vals = [t["first_sentence_ms"] for t in turns
                if t.get("first_sentence_ms") is not None
                and (t.get("predispatch_outcome") == "fired") is fired]
        out[name] = {"n": len(vals), "p50": int(statistics.median(vals)) if vals else None,
                     "p95": _pct(vals, 0.95)}
    return out


def _stream_turn(client: httpx.Client, bridge: str, line: str, phone: str, call_id: str) -> tuple[str, int | None, int]:
    """POST one caller line; return (reply, first_sentence_ms, total_ms)."""
    body = {"model": "blue-dots", "stream": True,
            "messages": [{"role": "user", "content": line}],
            "metadata": {"caller_phone": phone, "call_id": call_id}}
    start = time.monotonic()
    first_ms: int | None = None
    parts: list[str] = []
    with client.stream("POST", f"{bridge}/v1/chat/completions", json=body) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"].get("content")
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if delta:
                if first_ms is None:
                    first_ms = int((time.monotonic() - start) * 1000)
                parts.append(delta)
    return "".join(parts), first_ms, int((time.monotonic() - start) * 1000)


def _redis(container: str, *args: str) -> str:
    out = subprocess.run(["docker", "exec", container, "redis-cli", *args],
                         capture_output=True, text=True, timeout=10, check=False)
    return out.stdout.strip()


def _offered_rows(container: str, phone: str, call_id: str) -> list[dict]:
    """Rows of the fetch_jobs result last served to the model (best effort)."""
    served = _redis(container, "HGET", f"session:{phone}:{call_id}", "served_tool_results")
    try:
        h = json.loads(served).get("fetch_jobs")
    except (ValueError, AttributeError):
        return []
    if not h:
        return []
    for key in _redis(container, "--scan", "--pattern", f"*:fetch_jobs:{h}").splitlines()[:1]:
        try:
            data = json.loads(_redis(container, "GET", key)).get("data")
        except ValueError:
            return []
        rows = data if isinstance(data, list) else (data or {}).get("items")
        return [r for r in rows or [] if isinstance(r, dict)]
    return []


def _session_facts(container: str | None, phone: str, call_id: str) -> dict:
    """Per-row spoken-pay markers of the offered rows, for the order check."""
    if not container:
        return {}
    rows = _offered_rows(container, phone, call_id)
    markers = [next((str(r[k]) for k in ("salary_spoken", "stipend_spoken", "task_rate_spoken") if r.get(k)), None)
               for r in rows]
    return {"offered_markers": markers} if any(markers) else {}


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def run(bridge: str, scenarios: list[dict], redis_container: str | None,
        agent_container: str | None = None) -> dict:
    """Run every scenario and return the report dict."""
    phones: dict[str, str] = {}
    results: list[dict] = []
    first_ms_all: list[int] = []
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        for sc in scenarios:
            sid = sc["id"]
            ref = sc.get("reuse_phone_of")
            phone = phones.get(ref) if ref else None
            phone = phone or "9197" + "".join(random.choices("0123456789", k=8))
            phones[sid] = phone
            call_id = f"scn-{sid}-{uuid.uuid4().hex[:8]}"
            turns = []
            for line in sc["lines"]:
                turn_start = time.time()
                try:
                    reply, first_ms, total_ms = _stream_turn(client, bridge, line, phone, call_id)
                except httpx.HTTPError as exc:
                    turns.append({"line": line, "error": type(exc).__name__})
                    break
                facts = _session_facts(redis_container, phone, call_id)
                extras = _scrape_turn_extras(agent_container, turn_start) if agent_container else {}
                if first_ms is not None:
                    first_ms_all.append(first_ms)
                turns.append({"line": line, "reply": reply, "first_sentence_ms": first_ms, "total_ms": total_ms, **extras,
                              "checks": run_checks(reply, facts), "foreign_script_words": None if sc.get("language") == "english" else foreign_script_words(reply)})
            results.append({"id": sid, "phone": phone, "call_id": call_id, "turns": turns,
                            "llm_calls": [t.get("llm_calls") for t in turns]})
    checked = [t for r in results for t in r["turns"] if "checks" in t]
    names = sorted({k for t in checked for k in t["checks"]})
    return {
        "scenarios": results,
        "summary": {
            "turns": len(checked),
            "errors": sum(1 for r in results for t in r["turns"] if "error" in t),
            "pass_counts": {n: sum(1 for t in checked if t["checks"].get(n) is True) for n in names},
            "fail_counts": {n: sum(1 for t in checked if t["checks"].get(n) is False) for n in names},
            "skipped_counts": {n: sum(1 for t in checked if t["checks"].get(n) is None) for n in names},
            "not_collected": NOT_COLLECTED,
            "first_sentence_ms_p50": int(statistics.median(first_ms_all)) if first_ms_all else None,
            "first_sentence_ms_p95": _pct(first_ms_all, 0.95),
            "first_sentence_ms_by_predispatch": _split_by_outcome(checked),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--redis-container", default=None)
    ap.add_argument("--agent-container", default=None,
                    help="agent_core container; reads llm_calls and the pre-dispatch outcome from its logs. "
                         "Assumes sequential, single-session use: docker logs --since the turn start "
                         "(1 s granularity), no session filter")
    a = ap.parse_args()
    scenarios = yaml.safe_load(Path(a.scenarios).read_text(encoding="utf-8"))
    report = run(a.bridge.rstrip("/"), scenarios, a.redis_container, a.agent_container)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
