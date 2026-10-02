"""Scripted scenario runner for the Blue Dots bridge (Spec D §9.2).

Drives the bridge streaming path turn by turn, runs the automatic checks on each
reply and writes a JSON report. Replies live only in the report, never on stdout.

Usage:
    python -m eval.scenarios.run --bridge http://127.0.0.1:18008 \
        --scenarios eval/scenarios/blue_dots.yaml --out report.json [--redis-container dpg_redis]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import yaml

from eval.scenarios.checks import foreign_script_words, run_checks


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


def _session_facts(container: str | None, phone: str, call_id: str, reply: str, expect_list: bool) -> dict:
    """Facts for the order check. Only set when the reply names the jobs in Latin script,
    because stored labels are Latin and a Devanagari reply cannot be matched to them."""
    if not container or not expect_list:
        return {}
    rows = _offered_rows(container, phone, call_id)
    if not rows:
        return {}
    named = any(str(r.get("company", "")) and str(r["company"]) in reply for r in rows)
    first = str(rows[0].get("company", ""))
    return {"spoken_first_label": first} if named and first else {}


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def run(bridge: str, scenarios: list[dict], redis_container: str | None) -> dict:
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
            for i, line in enumerate(sc["lines"]):
                last = i == len(sc["lines"]) - 1
                try:
                    reply, first_ms, total_ms = _stream_turn(client, bridge, line, phone, call_id)
                except httpx.HTTPError as exc:
                    turns.append({"line": line, "error": type(exc).__name__})
                    break
                facts = _session_facts(redis_container, phone, call_id, reply, bool(sc.get("expect_list")) and last)
                if first_ms is not None:
                    first_ms_all.append(first_ms)
                turns.append({"line": line, "reply": reply, "first_sentence_ms": first_ms, "total_ms": total_ms,
                              "checks": run_checks(reply, facts), "foreign_script_words": foreign_script_words(reply)})
            results.append({"id": sid, "phone": phone, "call_id": call_id, "turns": turns})
    checked = [t for r in results for t in r["turns"] if "checks" in t]
    names = sorted({k for t in checked for k in t["checks"]})
    return {
        "scenarios": results,
        "summary": {
            "turns": len(checked),
            "errors": sum(1 for r in results for t in r["turns"] if "error" in t),
            "pass_counts": {n: sum(1 for t in checked if t["checks"].get(n)) for n in names},
            "first_sentence_ms_p50": int(statistics.median(first_ms_all)) if first_ms_all else None,
            "first_sentence_ms_p95": _pct(first_ms_all, 0.95),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--redis-container", default=None)
    a = ap.parse_args()
    scenarios = yaml.safe_load(Path(a.scenarios).read_text(encoding="utf-8"))
    report = run(a.bridge.rstrip("/"), scenarios, a.redis_container)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
