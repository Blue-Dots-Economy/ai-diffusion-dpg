"""Results cache: one JSON per (target commit, suite version, scenario, run) (spec §6.8)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from eval.voice_bench import SUITE_VERSION
from eval.voice_bench.records import CallRecord


class ResultStore:
    """Atomic, resumable result storage.

    Args:
        root: results_dir from config.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root) / f"suite-v{SUITE_VERSION}"

    def path(self, target_commit: str, scenario: str, run: int) -> Path:
        return self._root / target_commit / f"{scenario}-r{run}.json"

    def has(self, target_commit: str, scenario: str, run: int) -> bool:
        """True only for a complete, parseable record."""
        p = self.path(target_commit, scenario, run)
        if not p.exists():
            return False
        try:
            CallRecord.from_json(p.read_text(encoding="utf-8"))
            return True
        except (ValueError, KeyError, TypeError):
            return False

    def save(self, rec: CallRecord) -> Path:
        p = self.path(rec.target_commit, rec.scenario, rec.run)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(rec.to_json(), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self, target_commit: str, scenario: str, run: int) -> CallRecord:
        return CallRecord.from_json(self.path(target_commit, scenario, run).read_text(encoding="utf-8"))

    def load_target(self, target_commit: str) -> list[CallRecord]:
        d = self._root / target_commit
        return [CallRecord.from_json(p.read_text(encoding="utf-8")) for p in sorted(d.glob("T*-r*.json"))]

    def write_meta(self, target_commit: str, meta: dict) -> None:
        p = self._root / target_commit / "meta.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def read_meta(self, target_commit: str) -> dict:
        p = self._root / target_commit / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
