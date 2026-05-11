"""Cache comparison harness for OpenAI.

OpenAI's caching is automatic and prefix-based — there are no
breakpoints to set, so the multi-tier `none/mono/tiered` distinction
from the Anthropic harness is meaningless here. Instead this script
compares two modes:

  auto    — production behaviour. OpenAI caches the longest matching
            prefix automatically.
  nocache — defeats auto-caching by prepending a unique random seed to
            the system prompt on every call. Gives us the uncached
            baseline.

Defaults to ONE turn per cell with a single constant user message
(USER_MESSAGE in prompts.py). Use --turns 2 (or run twice within ~5
minutes) to see warm-cache behaviour on `auto`.

Reads the same prompts.py the Anthropic harness uses, so content sent
to gpt-4.1 / gpt-5.4-mini is byte-identical to what's sent to Sonnet/Haiku.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from prompts import USER_MESSAGE, system_text_openai


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ──────────────────────────────────────────────────────────────────────
# Pricing — verified from openai.com/api/pricing (2026-05) per 1M tokens
# Cached input is 50% of regular input. No write premium.
# Update when new model rates ship.
# ──────────────────────────────────────────────────────────────────────

# Pricing — per 1M tokens. Sources:
#  ✓ verified from developers.openai.com/api/docs/pricing (2026-05):
#      gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano
#  ⚠ from third-party trackers / earlier in this project (less verified):
#      gpt-4.1, gpt-4.1-mini, gpt-5-mini, gpt-4o, gpt-4o-mini
# When in doubt, treat the cost numbers as directional. Verify rates
# against OpenAI's current pricing page before quoting numbers externally.
PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    # Verified
    "gpt-5.5":               {"input": 5.00,  "cached_input": 0.50,  "output": 30.00},
    "gpt-5.4":               {"input": 2.50,  "cached_input": 0.25,  "output": 15.00},
    "gpt-5.4-mini":          {"input": 0.75,  "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano":          {"input": 0.20,  "cached_input": 0.02,  "output": 1.25},
    # Cross-checked vs prior runs / third-party trackers
    "gpt-4.1":               {"input": 2.00,  "cached_input": 0.50,  "output": 8.00},
    "gpt-4.1-mini":          {"input": 0.40,  "cached_input": 0.10,  "output": 1.60},
    "gpt-5-mini":            {"input": 0.25,  "cached_input": 0.025, "output": 2.00},
    "gpt-4o":                {"input": 2.50,  "cached_input": 1.25,  "output": 10.00},
    "gpt-4o-mini":           {"input": 0.15,  "cached_input": 0.075, "output": 0.60},
    # Audio-capable models (text-only mode used here). Pricing is for the
    # TEXT input/output rates only; audio I/O is billed differently and
    # not relevant for this benchmark. Rates are best-effort placeholders
    # — verify against the OpenAI pricing page before quoting cost numbers.
    "gpt-audio-1.5":         {"input": 10.00, "cached_input": 1.00,  "output": 20.00},
    "gpt-audio":             {"input": 2.50,  "cached_input": 0.25,  "output": 10.00},
    "gpt-audio-mini":        {"input": 0.50,  "cached_input": 0.05,  "output": 2.00},
    # Original test ID — kept for backward compatibility with prior runs
    "gpt-5.4-mini-2026-03-17": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
}


# ──────────────────────────────────────────────────────────────────────
# Spec-band rates — used only when --with-band is passed. Rough.
# ──────────────────────────────────────────────────────────────────────

# Spec-band params — rough per-class typical ranges. NOT measured.
# Used only to flag "is this call wildly out of line?" — not as an SLO.
# Tier conventions:
#   frontier (5.5, 5.4, 4.1, 4o):   slower TTFT,  fewer tok/sec
#   mini    (5.4-mini, 5-mini,
#            4.1-mini, 4o-mini):    medium
#   nano    (5.4-nano):             fastest TTFT, most tok/sec
SPEC_BAND_PARAMS: dict[str, dict[str, float]] = {
    "gpt-5.5":               {"ttft_floor_ms": 1000, "ttft_ceiling_ms": 2800, "tok_per_sec_high": 70,  "tok_per_sec_low": 20},
    "gpt-5.4":               {"ttft_floor_ms": 800,  "ttft_ceiling_ms": 2400, "tok_per_sec_high": 80,  "tok_per_sec_low": 25},
    "gpt-5.4-mini":          {"ttft_floor_ms": 400,  "ttft_ceiling_ms": 1200, "tok_per_sec_high": 120, "tok_per_sec_low": 40},
    "gpt-5.4-nano":          {"ttft_floor_ms": 300,  "ttft_ceiling_ms": 900,  "tok_per_sec_high": 160, "tok_per_sec_low": 60},
    "gpt-4.1":               {"ttft_floor_ms": 700,  "ttft_ceiling_ms": 2000, "tok_per_sec_high": 80,  "tok_per_sec_low": 25},
    "gpt-4.1-mini":          {"ttft_floor_ms": 400,  "ttft_ceiling_ms": 1300, "tok_per_sec_high": 110, "tok_per_sec_low": 40},
    "gpt-5-mini":            {"ttft_floor_ms": 400,  "ttft_ceiling_ms": 1300, "tok_per_sec_high": 110, "tok_per_sec_low": 40},
    "gpt-4o":                {"ttft_floor_ms": 700,  "ttft_ceiling_ms": 2000, "tok_per_sec_high": 90,  "tok_per_sec_low": 30},
    "gpt-4o-mini":           {"ttft_floor_ms": 400,  "ttft_ceiling_ms": 1300, "tok_per_sec_high": 110, "tok_per_sec_low": 40},
    "gpt-audio-1.5":         {"ttft_floor_ms": 700,  "ttft_ceiling_ms": 2200, "tok_per_sec_high": 80,  "tok_per_sec_low": 25},
    "gpt-audio":             {"ttft_floor_ms": 700,  "ttft_ceiling_ms": 2200, "tok_per_sec_high": 80,  "tok_per_sec_low": 25},
    "gpt-audio-mini":        {"ttft_floor_ms": 400,  "ttft_ceiling_ms": 1300, "tok_per_sec_high": 110, "tok_per_sec_low": 40},
    "gpt-5.4-mini-2026-03-17": {"ttft_floor_ms": 400, "ttft_ceiling_ms": 1200, "tok_per_sec_high": 120, "tok_per_sec_low": 40},
}


def compute_spec_band(model: str, output_tokens: int) -> tuple[int, int]:
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


def estimate_cost_usd(model: str, *, uncached_input: int, cached_input: int,
                      output_tokens: int) -> float:
    p = PRICING_USD_PER_1M.get(model)
    if p is None:
        return 0.0
    return (
        uncached_input * p["input"]        / 1_000_000
        + cached_input * p["cached_input"] / 1_000_000
        + output_tokens * p["output"]      / 1_000_000
    )


# ──────────────────────────────────────────────────────────────────────
# Per-call result row
# ──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class TurnResult:
    provider: str
    model: str
    mode: str
    turn: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    latency_ms: int
    ttft_ms: int
    tokens_per_sec: float
    spec_band_low_ms: int
    spec_band_high_ms: int
    delta_vs_band_ms: int
    cost_usd: float
    user_message: str

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def compute_tokens_per_sec(output_tokens: int, latency_ms: int,
                           ttft_ms: int) -> float:
    """Generation rate in tokens/sec, measured from first token to last.

    Returns 0.0 when generation took zero or negative measured time
    (reasoning models that don't stream often hit this — TTFT == lat).
    """
    gen_ms = latency_ms - ttft_ms
    if gen_ms <= 0 or output_tokens <= 0:
        return 0.0
    return output_tokens / (gen_ms / 1000.0)


# ──────────────────────────────────────────────────────────────────────
# Streaming call
# ──────────────────────────────────────────────────────────────────────

def run_one_call(client: OpenAI, *, model: str, system_text: str,
                 user_text: str, max_tokens: int) -> TurnResult:
    start = time.perf_counter()
    ttft_ms: int | None = None
    usage = None

    # Newer OpenAI models (gpt-5.x, o-series) reject `max_tokens` and require
    # `max_completion_tokens`. The older `max_tokens` is deprecated but still
    # works on gpt-4.x. Use the newer parameter universally; gpt-4.x accepts it.
    stream = client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        stream=True,
        stream_options={"include_usage": True},
    )
    for chunk in stream:
        if (
            ttft_ms is None
            and chunk.choices
            and chunk.choices[0].delta
            and chunk.choices[0].delta.content
        ):
            ttft_ms = int((time.perf_counter() - start) * 1000)
        if chunk.usage is not None:
            usage = chunk.usage

    latency_ms = int((time.perf_counter() - start) * 1000)
    if ttft_ms is None:
        ttft_ms = latency_ms

    if usage is None:
        in_tok = cached = out_tok = 0
    else:
        in_tok = usage.prompt_tokens or 0
        out_tok = usage.completion_tokens or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0

    uncached_in = max(in_tok - cached, 0)
    band = compute_spec_band(model, out_tok)
    tps = compute_tokens_per_sec(out_tok, latency_ms, ttft_ms)

    return TurnResult(
        provider="openai",
        model=model,
        mode="",
        turn=0,
        input_tokens=in_tok,
        cached_tokens=cached,
        output_tokens=out_tok,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        tokens_per_sec=tps,
        spec_band_low_ms=band[0],
        spec_band_high_ms=band[1],
        delta_vs_band_ms=delta_vs_band(latency_ms, band),
        cost_usd=estimate_cost_usd(
            model, uncached_input=uncached_in,
            cached_input=cached, output_tokens=out_tok,
        ),
        user_message=user_text,
    )


# ──────────────────────────────────────────────────────────────────────
# Cell runner
# ──────────────────────────────────────────────────────────────────────

def run_cell(client: OpenAI, *, model: str, mode: str,
             max_tokens: int, turns: int) -> list[TurnResult]:
    print(f"\n=== model={model}  mode={mode} ===", file=sys.stderr)
    base_system = system_text_openai(disable_cache=False)
    results: list[TurnResult] = []
    for i in range(1, turns + 1):
        # In nocache mode, vary the prompt prefix every call so OpenAI's
        # auto-cache cannot match anything.
        if mode == "nocache":
            sys_text = f"[turn-seed:{uuid.uuid4()}] {base_system}"
        else:
            sys_text = base_system
        r = run_one_call(client, model=model, system_text=sys_text,
                         user_text=USER_MESSAGE, max_tokens=max_tokens)
        r.mode = mode
        r.turn = i
        results.append(r)
        print(json.dumps(r.to_json(), ensure_ascii=False), flush=True)
    return results


def write_results(results: list[TurnResult], model: str, mode: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = model.replace("/", "_")
    path = RESULTS_DIR / f"openai_{safe_model}_{mode}_{ts}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    return path


# ──────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────

def _fmt_band(low: int, high: int) -> str:
    return "—" if (low == 0 and high == 0) else f"{low:>4,} – {high:>5,}"


def _fmt_delta(delta: int) -> str:
    if delta == 0:
        return "in band"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta} ms"


def _percentile(values: list[float], pct: float) -> float:
    """Inclusive linear interpolation percentile. Empty list → 0."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def print_summary(all_results: list[TurnResult]) -> None:
    print("\n" + "=" * 165, file=sys.stderr)
    header = (
        f"{'model':<32} {'mode':<6} {'t':<3} "
        f"{'in':>5} {'cached':>7} {'out':>4} "
        f"{'lat':>6} {'ttft':>5} {'tok/s':>6} "
        f"{'spec band (ms)':>16} {'Δ vs band':>11} "
        f"{'cost$':>9}"
    )
    print(header, file=sys.stderr)
    print("-" * 165, file=sys.stderr)
    for r in all_results:
        print(
            f"{r.model:<32} {r.mode:<6} {r.turn:<3} "
            f"{r.input_tokens:>5} {r.cached_tokens:>7} {r.output_tokens:>4} "
            f"{r.latency_ms:>6} {r.ttft_ms:>5} {r.tokens_per_sec:>6.1f} "
            f"{_fmt_band(r.spec_band_low_ms, r.spec_band_high_ms):>16} "
            f"{_fmt_delta(r.delta_vs_band_ms):>11} "
            f"{r.cost_usd:>9.6f}",
            file=sys.stderr,
        )

    # Per-cell aggregates over warm turns only (turns 2..N).
    print("\n" + "=" * 165, file=sys.stderr)
    print("Per-cell aggregates (turns 2+ only — turn 1 is always cold):",
          file=sys.stderr)
    print(
        f"{'model':<32} {'mode':<6} "
        f"{'p50_lat':>8} {'p99_lat':>8} "
        f"{'p50_ttft':>9} {'p99_ttft':>9} "
        f"{'p50_tps':>8} {'p99_tps':>8} "
        f"{'avg_cached':>11} {'avg_cost$':>11}",
        file=sys.stderr,
    )
    print("-" * 165, file=sys.stderr)

    by_cell: dict[tuple[str, str], list[TurnResult]] = {}
    for r in all_results:
        by_cell.setdefault((r.model, r.mode), []).append(r)

    for (model, mode), rs in by_cell.items():
        steady = [r for r in rs if r.turn >= 2]
        if not steady:
            continue
        lats = [float(r.latency_ms) for r in steady]
        ttfts = [float(r.ttft_ms) for r in steady]
        tpss = [r.tokens_per_sec for r in steady]
        p50_lat = _percentile(lats, 50)
        p99_lat = _percentile(lats, 99)
        p50_ttft = _percentile(ttfts, 50)
        p99_ttft = _percentile(ttfts, 99)
        p50_tps = _percentile(tpss, 50)
        p99_tps = _percentile(tpss, 99)
        avg_cached = sum(r.cached_tokens for r in steady) / len(steady)
        avg_cost = sum(r.cost_usd for r in steady) / len(steady)
        print(
            f"{model:<32} {mode:<6} "
            f"{p50_lat:>8.0f} {p99_lat:>8.0f} "
            f"{p50_ttft:>9.0f} {p99_ttft:>9.0f} "
            f"{p50_tps:>8.1f} {p99_tps:>8.1f} "
            f"{avg_cached:>11.0f} {avg_cost:>11.6f}",
            file=sys.stderr,
        )


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+",
        default=[
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "gpt-5-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            # Audio models (gpt-audio*, gpt-realtime*) cannot run text-only —
            # they reject any request without an audio modality in input
            # or output. Excluded from the default benchmark.
        ],
        help="One or more OpenAI model IDs to benchmark.",
    )
    parser.add_argument(
        "--modes", nargs="+",
        choices=["nocache", "auto"],
        default=["auto"],
        help="Cache modes to test. Default: auto only (production behaviour).",
    )
    parser.add_argument(
        "--turns", type=int, default=10,
        help="Calls per cell. Default 10. Need ≥10 for meaningful p99.",
    )
    parser.add_argument("--max-tokens", type=int, default=200)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = OpenAI()
    all_results: list[TurnResult] = []
    for model in args.models:
        for mode in args.modes:
            results = run_cell(client, model=model, mode=mode,
                               max_tokens=args.max_tokens, turns=args.turns)
            all_results.extend(results)
            path = write_results(results, model, mode)
            print(f"  wrote {path.relative_to(REPO_ROOT)}", file=sys.stderr)

    print_summary(all_results)


if __name__ == "__main__":
    main()
