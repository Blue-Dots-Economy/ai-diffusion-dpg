"""Combine all JSONL result files into a single CSV for Excel.

Usage:
    uv run python to_csv.py                       # all files
    uv run python to_csv.py --provider openai     # only openai_*.jsonl
    uv run python to_csv.py --since 20260508      # only files dated on/after
    uv run python to_csv.py --out my_results.csv  # custom output path

Default output: results/all_results.csv (over-writes any prior export).

Columns are taken from whatever fields each JSONL row has — the script
unions all keys across all rows so Anthropic + OpenAI runs can sit in
the same CSV (with empty cells where a field doesn't apply to a row).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["openai", "anthropic"],
                        help="Only include files for one provider.")
    parser.add_argument("--since",
                        help="Only include files with timestamp >= this "
                             "value (YYYYMMDD or YYYYMMDDTHHMMSSZ).")
    parser.add_argument("--out", default=str(RESULTS_DIR / "all_results.csv"),
                        help="Output CSV path.")
    args = parser.parse_args()

    pattern = f"{args.provider}_*.jsonl" if args.provider else "*.jsonl"
    files = sorted(glob.glob(str(RESULTS_DIR / pattern)))
    if args.since:
        files = [f for f in files if args.since in os.path.basename(f)
                 or os.path.basename(f).split("_")[-1].split(".")[0] >= args.since]

    if not files:
        print("No matching JSONL files found.", file=sys.stderr)
        sys.exit(1)

    rows: list[dict] = []
    fieldnames: list[str] = []
    seen: set[str] = set()
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                rec["__source_file"] = os.path.basename(path)
                rows.append(rec)
                for k in rec.keys():
                    if k not in seen:
                        seen.add(k)
                        fieldnames.append(k)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Wrote {len(rows)} rows from {len(files)} files → {out_path}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
