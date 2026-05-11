"""Tests for aggregate.py: percentile + summary math."""
import json
from pathlib import Path

from aggregate import collect_rows, find_latest_call_dir, percentile, summarise


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
    assert s["silence_to_ttft_p50"] == 0.0
    assert s["tpot_mean_ms"] == 0.0
    assert s["bot_speaking_p50"] == 0.0


def test_collect_rows_walks_per_call_subdirs(tmp_path: Path):
    """collect_rows finds .jsonl inside per-call subdirectories."""
    call_a = tmp_path / "20260512T100000Z_call-a"
    call_a.mkdir()
    (call_a / "turns.jsonl").write_text(
        json.dumps({"turn": 1, "ttft_ms": 100}) + "\n", encoding="utf-8"
    )
    call_b = tmp_path / "20260512T110000Z_call-b"
    call_b.mkdir()
    (call_b / "turns.jsonl").write_text(
        json.dumps({"turn": 1, "ttft_ms": 250}) + "\n", encoding="utf-8"
    )
    rows = collect_rows(tmp_path)
    assert sorted(r["ttft_ms"] for r in rows) == [100, 250]


def test_find_latest_call_dir_picks_newest(tmp_path: Path):
    """find_latest_call_dir returns the lexicographically last subdir."""
    (tmp_path / "20260512T100000Z_call-a").mkdir()
    (tmp_path / "20260512T110000Z_call-b").mkdir()
    (tmp_path / "20260511T235959Z_call-old").mkdir()
    latest = find_latest_call_dir(tmp_path)
    assert latest is not None
    assert latest.name == "20260512T110000Z_call-b"


def test_find_latest_call_dir_returns_none_when_empty(tmp_path: Path):
    """find_latest_call_dir returns None if there are no subdirectories."""
    assert find_latest_call_dir(tmp_path) is None


def test_summarise_includes_new_pipecat_metrics():
    """summarise returns the Pipecat-only metrics added in the migration."""
    rows = [
        {
            "ttft_ms": 100, "total_response_ms": 500, "cost_usd": 0.001,
            "silence_to_ttft_ms": 2500, "tpot_ms": 30, "bot_speaking_ms": 1000,
            "user_speech_duration_ms": 2000,
            "turn": 1, "call_sid": "a",
        },
        {
            "ttft_ms": 200, "total_response_ms": 800, "cost_usd": 0.002,
            "silence_to_ttft_ms": 3000, "tpot_ms": 35, "bot_speaking_ms": 1500,
            "user_speech_duration_ms": 2200,
            "turn": 2, "call_sid": "a",
        },
    ]
    s = summarise(rows)
    assert "silence_to_ttft_p50" in s
    assert "silence_to_ttft_p99" in s
    assert "tpot_mean_ms" in s
    assert "bot_speaking_p50" in s
    assert s["silence_to_ttft_p50"] > 0
    assert s["tpot_mean_ms"] == round((30 + 35) / 2, 1)
    assert s["bot_speaking_p50"] > 0


def test_summarise_handles_null_tpot():
    """Rows with tpot_ms=None (1-chunk turns) are excluded from tpot_mean."""
    rows = [
        {"ttft_ms": 100, "tpot_ms": None, "cost_usd": 0.0, "call_sid": "a"},
        {"ttft_ms": 200, "tpot_ms": 30, "cost_usd": 0.0, "call_sid": "a"},
    ]
    s = summarise(rows)
    # Only the one row with non-None tpot contributes
    assert s["tpot_mean_ms"] == 30.0
