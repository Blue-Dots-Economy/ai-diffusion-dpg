"""Cache-strategy comparison harness for #311 (Anthropic).

Runs direct Anthropic API calls (no DPG, no FastAPI) using a captured
KKB production prompt from prompts.py and compares three cache layouts:

  none    — single uncached system block (latency baseline)
  mono    — Tier 1 + Tier 2 combined, ONE cache_control at the end
  tiered  — Tier 1 and Tier 2 as separate blocks, EACH with cache_control
            (this is what production does today)

Default is ONE turn per cell with a SINGLE constant user message
(USER_MESSAGE in prompts.py) — only the cache strategy varies across
cells, so cells are directly comparable.

To see warm-cache behaviour, pass --turns 2. Or run the script twice
within 5 minutes — the second run picks up Sonnet's warm cache.

Captures per-call: input_tokens, cache_creation, cache_read,
output_tokens, latency_ms, ttft_ms, cost_usd, and (optionally with
--with-band) a model-rate-based expected-latency band plus the delta
versus that band.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from prompts import USER_MESSAGE, system_blocks_anthropic


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ──────────────────────────────────────────────────────────────────────
# Pricing — verified from docs.claude.com (2026-05) per 1M tokens
# ──────────────────────────────────────────────────────────────────────

PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {
        "input": 3.00, "cache_write_5m": 3.75, "cache_read": 0.30, "output": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00, "cache_write_5m": 1.25, "cache_read": 0.10, "output": 5.00,
    },
}


# ──────────────────────────────────────────────────────────────────────
# Spec-band rates — rough per-model expected latencies. Used ONLY when
# --with-band is passed. These are NOT measured from your environment;
# they are ballpark based on documented Anthropic rates. Treat the band
# as "is this in the expected ballpark for this model+output_size?",
# not as a precise SLO.
# ──────────────────────────────────────────────────────────────────────

SPEC_BAND_PARAMS: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {
        "ttft_floor_ms": 1500, "ttft_ceiling_ms": 3000,
        "tok_per_sec_high": 100, "tok_per_sec_low": 30,
    },
    "claude-haiku-4-5-20251001": {
        "ttft_floor_ms": 500, "ttft_ceiling_ms": 1200,
        "tok_per_sec_high": 150, "tok_per_sec_low": 50,
    },
}


def compute_spec_band(model: str, output_tokens: int) -> tuple[int, int]:
    """Return (low_ms, high_ms) expected latency for this model+out_tokens."""
    p = SPEC_BAND_PARAMS.get(model)
    if p is None:
        return (0, 0)
    low = int(p["ttft_floor_ms"] + (output_tokens * 1000) / p["tok_per_sec_high"])
    high = int(p["ttft_ceiling_ms"] + (output_tokens * 1000) / p["tok_per_sec_low"])
    return low, high


def delta_vs_band(latency_ms: int, band: tuple[int, int]) -> int:
    low, high = band
    if low == 0 and high == 0:
        return 0
    if latency_ms < low:
        return latency_ms - low
    if latency_ms > high:
        return latency_ms - high
    return 0


def estimate_cost_usd(model: str, *, input_tokens: int, cache_creation: int,
                      cache_read: int, output_tokens: int) -> float:
    p = PRICING_USD_PER_1M.get(model)
    if p is None:
        return 0.0
    return (
        input_tokens   * p["input"]          / 1_000_000
        + cache_creation * p["cache_write_5m"] / 1_000_000
        + cache_read     * p["cache_read"]     / 1_000_000
        + output_tokens  * p["output"]         / 1_000_000
    )


# ──────────────────────────────────────────────────────────────────────
# Per-call result row
# ──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class TurnResult:
    provider: str
    model: str
    strategy: str
    turn: int
    input_tokens: int
    output_tokens: int
    cache_creation: int
    cache_read: int
    latency_ms: int
    ttft_ms: int
    spec_band_low_ms: int
    spec_band_high_ms: int
    delta_vs_band_ms: int
    cost_usd: float
    user_message: str

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


# ──────────────────────────────────────────────────────────────────────
# Single streaming call — captures TTFT and final usage
# ──────────────────────────────────────────────────────────────────────

def run_one_call(client: Anthropic, *, model: str, blocks: list[dict],
                 user_text: str, max_tokens: int) -> TurnResult:
    start = time.perf_counter()
    ttft_ms: int | None = None
    usage = None

    with client.messages.stream(
        model=model, max_tokens=max_tokens, system=blocks,
        messages=[{"role": "user", "content": user_text}],
    ) as stream:
        for event in stream:
            if (
                ttft_ms is None
                and getattr(event, "type", None) == "content_block_delta"
            ):
                ttft_ms = int((time.perf_counter() - start) * 1000)
        final_message = stream.get_final_message()
        usage = final_message.usage

    latency_ms = int((time.perf_counter() - start) * 1000)
    if ttft_ms is None:
        ttft_ms = latency_ms

    in_tok = usage.input_tokens or 0
    out_tok = usage.output_tokens or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0

    band = compute_spec_band(model, out_tok)

    return TurnResult(
        provider="anthropic",
        model=model,
        strategy="",
        turn=0,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_creation=cw,
        cache_read=cr,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        spec_band_low_ms=band[0],
        spec_band_high_ms=band[1],
        delta_vs_band_ms=delta_vs_band(latency_ms, band),
        cost_usd=estimate_cost_usd(
            model, input_tokens=in_tok, cache_creation=cw,
            cache_read=cr, output_tokens=out_tok,
        ),
        user_message=user_text,
    )


# ──────────────────────────────────────────────────────────────────────
# Cell runner
# ──────────────────────────────────────────────────────────────────────

def run_cell(client: Anthropic, *, model: str, strategy: str,
             max_tokens: int, turns: int) -> list[TurnResult]:
    print(f"\n=== model={model}  strategy={strategy} ===", file=sys.stderr)
    blocks = system_blocks_anthropic(strategy)
    results: list[TurnResult] = []
    for i in range(1, turns + 1):
        r = run_one_call(client, model=model, blocks=blocks,
                         user_text=USER_MESSAGE, max_tokens=max_tokens)
        r.strategy = strategy
        r.turn = i
        results.append(r)
        print(json.dumps(r.to_json(), ensure_ascii=False), flush=True)
    return results


def write_results(results: list[TurnResult], model: str, strategy: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = model.replace("/", "_")
    path = RESULTS_DIR / f"anthropic_{safe_model}_{strategy}_{ts}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    return path


# ──────────────────────────────────────────────────────────────────────
# Summary printer
# ──────────────────────────────────────────────────────────────────────

def _fmt_band(low: int, high: int) -> str:
    return "—" if (low == 0 and high == 0) else f"{low:>4,} – {high:>5,}"


def _fmt_delta(delta: int) -> str:
    if delta == 0:
        return "in band"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta} ms"


def print_summary(all_results: list[TurnResult]) -> None:
    print("\n" + "=" * 150, file=sys.stderr)
    header = (
        f"{'model':<32} {'strat':<7} {'t':<2} "
        f"{'in':>5} {'cW':>5} {'cR':>5} {'out':>4} "
        f"{'lat':>6} {'ttft':>5} {'spec band (ms)':>16} {'Δ vs band':>11} "
        f"{'cost$':>9}"
    )
    print(header, file=sys.stderr)
    print("-" * 150, file=sys.stderr)
    for r in all_results:
        print(
            f"{r.model:<32} {r.strategy:<7} {r.turn:<2} "
            f"{r.input_tokens:>5} {r.cache_creation:>5} {r.cache_read:>5} "
            f"{r.output_tokens:>4} {r.latency_ms:>6} {r.ttft_ms:>5} "
            f"{_fmt_band(r.spec_band_low_ms, r.spec_band_high_ms):>16} "
            f"{_fmt_delta(r.delta_vs_band_ms):>11} "
            f"{r.cost_usd:>9.6f}",
            file=sys.stderr,
        )

    # Per-cell aggregates over warm turns only (turns 2..N).
    print("\n" + "=" * 150, file=sys.stderr)
    print("Per-cell aggregates (turns 2+ only — turn 1 is always cold):",
          file=sys.stderr)
    print(
        f"{'model':<32} {'strat':<7} {'p50_lat':>8} {'p50_ttft':>9} "
        f"{'avg_cR':>8} {'avg_cW':>8} {'in_band':>9} {'avg_cost$':>11}",
        file=sys.stderr,
    )
    print("-" * 150, file=sys.stderr)

    by_cell: dict[tuple[str, str], list[TurnResult]] = {}
    for r in all_results:
        by_cell.setdefault((r.model, r.strategy), []).append(r)

    for (model, strat), rs in by_cell.items():
        steady = [r for r in rs if r.turn >= 2]
        if not steady:
            continue
        lats = sorted(r.latency_ms for r in steady)
        ttfts = sorted(r.ttft_ms for r in steady)
        p50_lat = lats[len(lats) // 2]
        p50_ttft = ttfts[len(ttfts) // 2]
        avg_cR = sum(r.cache_read for r in steady) / len(steady)
        avg_cW = sum(r.cache_creation for r in steady) / len(steady)
        in_band = sum(1 for r in steady if r.delta_vs_band_ms == 0)
        avg_cost = sum(r.cost_usd for r in steady) / len(steady)
        print(
            f"{model:<32} {strat:<7} {p50_lat:>8} {p50_ttft:>9} "
            f"{avg_cR:>8.0f} {avg_cW:>8.0f} {in_band}/{len(steady):>7} "
            f"{avg_cost:>11.6f}",
            file=sys.stderr,
        )


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+",
        default=["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"],
        help="One or more Anthropic model IDs. Default: Sonnet + Haiku.",
    )
    parser.add_argument(
        "--strategies", nargs="+",
        choices=["none", "mono", "tiered"],
        default=["none", "mono", "tiered"],
    )
    parser.add_argument(
        "--turns", type=int, default=5,
        help="Calls per cell. Default 5 (turn 1 cold + turns 2-5 warm).",
    )
    parser.add_argument("--max-tokens", type=int, default=200)
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = Anthropic()
    all_results: list[TurnResult] = []
    for model in args.models:
        for strategy in args.strategies:
            results = run_cell(client, model=model, strategy=strategy,
                               max_tokens=args.max_tokens, turns=args.turns)
            all_results.extend(results)
            path = write_results(results, model, strategy)
            print(f"  wrote {path.relative_to(REPO_ROOT)}", file=sys.stderr)

    print_summary(all_results)


if __name__ == "__main__":
    main()
