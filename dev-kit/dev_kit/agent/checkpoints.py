"""
dev-kit/dev_kit/agent/checkpoints.py

Saves and restores conversation state snapshots for the DPG conversation agent.

Each checkpoint stores the full accumulator state, conversation history,
and a human-readable summary at a phase boundary. Checkpoints live under
<project_path>/_meta/checkpoints/<phase_name>/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dev_kit.agent.accumulator import ConfigAccumulator


def save_checkpoint(
    project_path: Path,
    phase: str,
    accumulator: ConfigAccumulator,
    history: list[dict],
) -> None:
    """Save a checkpoint snapshot for the given phase.

    Args:
        project_path: Root directory of the project (configs/<slug>/).
        phase: Phase identifier, e.g. "01_overview".
        accumulator: Current config accumulator.
        history: Current conversation message history.
    """
    checkpoint_dir = project_path / "_meta" / "checkpoints" / phase
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    (checkpoint_dir / "accumulator.json").write_text(
        json.dumps(accumulator.to_dict(), ensure_ascii=False, indent=2)
    )
    (checkpoint_dir / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2)
    )
    (checkpoint_dir / "summary.txt").write_text(build_summary(phase, accumulator))
    (checkpoint_dir / "timestamp.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat()})
    )


def restore_checkpoint(project_path: Path, phase: str) -> tuple[ConfigAccumulator, str]:
    """Restore accumulator and summary from a checkpoint.

    Args:
        project_path: Root directory of the project.
        phase: Phase identifier to restore.

    Returns:
        Tuple of (restored accumulator, summary text).

    Raises:
        FileNotFoundError: If the checkpoint directory does not exist.
    """
    checkpoint_dir = project_path / "_meta" / "checkpoints" / phase
    if not checkpoint_dir.exists():
        raise FileNotFoundError(f"Checkpoint not found: {phase!r} at {checkpoint_dir}")

    acc = ConfigAccumulator.from_dict(
        json.loads((checkpoint_dir / "accumulator.json").read_text())
    )
    summary = (checkpoint_dir / "summary.txt").read_text()
    return acc, summary


def list_checkpoints(project_path: Path) -> list[dict]:
    """List all saved checkpoints for a project, sorted by phase name.

    Args:
        project_path: Root directory of the project.

    Returns:
        List of dicts with 'phase', 'created_at', 'summary' keys.
    """
    checkpoints_dir = project_path / "_meta" / "checkpoints"
    if not checkpoints_dir.exists():
        return []
    result = []
    for phase_dir in sorted(checkpoints_dir.iterdir()):
        if not phase_dir.is_dir():
            continue
        timestamp = {}
        ts_file = phase_dir / "timestamp.json"
        if ts_file.exists():
            timestamp = json.loads(ts_file.read_text())
        summary = ""
        summary_file = phase_dir / "summary.txt"
        if summary_file.exists():
            summary = summary_file.read_text()
        result.append({
            "phase": phase_dir.name,
            "created_at": timestamp.get("created_at", ""),
            "summary": summary,
        })
    return result


def build_summary(phase: str, accumulator: ConfigAccumulator) -> str:
    """Build a deterministic human-readable summary from the accumulator state.

    Used in system prompts to give the LLM context about prior phases
    without replaying the full conversation history.

    Args:
        phase: Phase identifier.
        accumulator: Config accumulator at checkpoint time.

    Returns:
        Multi-line summary string.
    """
    return f"Checkpoint: {phase}\n{accumulator.summary()}"
