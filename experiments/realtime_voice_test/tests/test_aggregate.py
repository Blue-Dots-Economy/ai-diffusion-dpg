"""Tests for aggregate.py: percentile + summary math."""
import json
from pathlib import Path

from aggregate import collect_rows, percentile, summarise


def test_percentile_basic():
    """50th percentile of [10, 20, 30, 40, 50] is 30 (interpolated)."""
    assert percentile([10, 20, 30, 40, 50], 50) == 30
    assert percentile([10, 20, 30, 40, 50], 99) == 49.6  # interpolated
    assert percentile([], 50) == 0.0
    assert percentile([42], 50) == 42


def test_collect_rows_reads_jsonl(tmp_path: Path):
    """collect_rows reads all .jsonl files under a directory."""
    f1 = tmp_path / "call_a.jsonl"
    f1.write_text(json.dumps({"turn": 1, "ttft_ms": 100}) + "\n"
                  + json.dumps({"turn": 2, "ttft_ms": 200}) + "\n",
                  encoding="utf-8")
    f2 = tmp_path / "call_b.jsonl"
    f2.write_text(json.dumps({"turn": 1, "ttft_ms": 150}) + "\n",
                  encoding="utf-8")
    rows = collect_rows(tmp_path)
    ttfts = sorted(r["ttft_ms"] for r in rows)
    assert ttfts == [100, 150, 200]


def test_summarise_computes_percentiles_and_avg(tmp_path: Path):
    """summarise returns p50, p99 of ttft and total_response, plus avg cost."""
    rows = [
        {"ttft_ms": 100, "total_response_ms": 500, "cost_usd": 0.001, "turn": 1, "call_sid": "a"},
        {"ttft_ms": 200, "total_response_ms": 800, "cost_usd": 0.002, "turn": 2, "call_sid": "a"},
        {"ttft_ms": 300, "total_response_ms": 900, "cost_usd": 0.003, "turn": 1, "call_sid": "b"},
    ]
    s = summarise(rows)
    assert s["count_turns"] == 3
    assert s["count_calls"] == 2
    assert s["ttft_p50"] == 200
    assert s["total_response_p50"] == 800
    assert s["avg_cost_usd"] == round((0.001 + 0.002 + 0.003) / 3, 8)


def test_summarise_empty():
    """Empty input produces zeroed summary, doesn't crash."""
    s = summarise([])
    assert s["count_turns"] == 0
    assert s["count_calls"] == 0
    assert s["ttft_p50"] == 0.0
