"""Read all results/*.jsonl → summary table.

Usage:
    uv run python aggregate.py                          # summarise everything
    uv run python aggregate.py --dir results            # explicit dir
    uv run python aggregate.py --prompt-name KKB_PERSONA  # filter
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results"


def percentile(values: list[float], pct: float) -> float:
    """Inclusive linear-interpolation percentile. Empty list → 0.0."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def collect_rows(results_dir: Path) -> list[dict[str, Any]]:
    """Read every JSONL row under results_dir."""
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(str(results_dir / "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute headline aggregates across all rows."""
    if not rows:
        return {
            "count_turns": 0, "count_calls": 0,
            "ttft_p50": 0.0, "ttft_p99": 0.0,
            "total_response_p50": 0.0, "total_response_p99": 0.0,
            "avg_cost_usd": 0.0,
            "avg_user_speech_duration_ms": 0.0,
        }
    ttfts = [float(r["ttft_ms"]) for r in rows if "ttft_ms" in r]
    totals = [float(r["total_response_ms"]) for r in rows if "total_response_ms" in r]
    costs = [float(r["cost_usd"]) for r in rows if "cost_usd" in r]
    speech = [float(r["user_speech_duration_ms"]) for r in rows
              if "user_speech_duration_ms" in r]
    call_sids = {r.get("call_sid") for r in rows if r.get("call_sid")}
    return {
        "count_turns": len(rows),
        "count_calls": len(call_sids),
        "ttft_p50": round(percentile(ttfts, 50), 1),
        "ttft_p99": round(percentile(ttfts, 99), 1),
        "total_response_p50": round(percentile(totals, 50), 1),
        "total_response_p99": round(percentile(totals, 99), 1),
        "avg_cost_usd": round(sum(costs) / len(costs), 8) if costs else 0.0,
        "avg_user_speech_duration_ms":
            round(sum(speech) / len(speech), 1) if speech else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_RESULTS,
                        help="Folder containing call_*.jsonl files.")
    parser.add_argument("--prompt-name", default="",
                        help="Filter to one prompt name.")
    args = parser.parse_args()

    rows = collect_rows(args.dir)
    if args.prompt_name:
        rows = [r for r in rows if r.get("prompt_name") == args.prompt_name]

    s = summarise(rows)

    print(f"Summary — {args.dir} "
          f"{('(filter: ' + args.prompt_name + ')') if args.prompt_name else ''}")
    print(f"  calls collected:          {s['count_calls']}")
    print(f"  turns collected:          {s['count_turns']}")
    print(f"  ttft_ms p50:              {s['ttft_p50']:>8.1f}")
    print(f"  ttft_ms p99:              {s['ttft_p99']:>8.1f}")
    print(f"  total_response_ms p50:    {s['total_response_p50']:>8.1f}")
    print(f"  total_response_ms p99:    {s['total_response_p99']:>8.1f}")
    print(f"  avg cost/turn:            ${s['avg_cost_usd']:>10.6f}")
    print(f"  avg user_speech_dur ms:   {s['avg_user_speech_duration_ms']:>8.1f}")


if __name__ == "__main__":
    main()
