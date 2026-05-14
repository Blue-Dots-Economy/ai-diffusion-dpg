"""history — append-only jsonl chat history for the deterministic wizard.

Each turn (user + assistant) appends one HistoryEntry per role. Persisted to
`<project>/_meta/history.jsonl`. Replaces the old wizard's checkpoint-based
history reconstruction.

Belongs to the dev-kit deterministic wizard.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryEntry:
    """One chat turn entry.

    Attributes:
        role: Speaker role — "user" or "assistant".
        content: The message text for this turn.
        phase: Wizard phase active when this turn was recorded.
        timestamp: UTC ISO-8601 string for when this entry was created.
    """

    role: str           # "user" | "assistant"
    content: str
    phase: str          # wizard phase at the time this turn happened
    timestamp: str      # UTC ISO-8601


def _history_path(project_path: Path) -> Path:
    return project_path / "_meta" / "history.jsonl"


def append_turn(project_path: Path, entry: HistoryEntry) -> None:
    """Append a single history entry to the project's history.jsonl.

    Creates the `_meta/` directory if needed.

    Args:
        project_path: Project directory (e.g., `dev-kit/configs/<slug>/`).
        entry: The HistoryEntry to write.
    """
    p = _history_path(project_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(entry), ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_history(project_path: Path) -> list[HistoryEntry]:
    """Load all history entries for a project.

    Args:
        project_path: Project directory.

    Returns:
        Ordered list of HistoryEntry. Empty list if no history file exists.
    """
    p = _history_path(project_path)
    if not p.exists():
        return []
    out: list[HistoryEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            out.append(HistoryEntry(**payload))
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "history line parse failure — skipping",
                extra={"operation": "load_history", "status": "skipped",
                       "error": str(exc), "line_preview": line[:80]},
            )
    return out


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


__all__ = ["HistoryEntry", "append_turn", "load_history", "utc_now_iso"]
