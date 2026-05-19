"""project_state — accumulator-dict persistence for the deterministic wizard.

The accumulator is a plain dict keyed by runtime block name; each value is the
domain-YAML structure for that block (nested dicts). Persisted to
`_meta/accumulator.json` under the project directory. Read by the renderer,
tool handlers, and read-only API endpoints.

Replaces the storage half of the old `ConfigAccumulator` class. The old
wizard's per-block status enum (PENDING/DRAFT/STALE/COMPLETE) is dropped —
block completion is now derived from `field_status.json` (see block_status.py).

Belongs to the dev-kit deterministic wizard. See:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BLOCKS: tuple[str, ...] = (
    "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
    "action_gateway", "reach_layer", "observability_layer",
)
_BLOCKS_SET = frozenset(BLOCKS)


def empty_accumulator() -> dict[str, dict]:
    """Return a fresh accumulator with one empty dict per block."""
    return {block: {} for block in BLOCKS}


def save_accumulator(path: Path, accumulator: dict[str, dict]) -> None:
    """Persist the accumulator dict to disk as JSON.

    Args:
        path: Target file path (typically `<slug>/_meta/accumulator.json`).
        accumulator: The accumulator dict — one entry per block.

    Raises:
        ValueError: If any top-level key isn't a known block name.
    """
    unknown = set(accumulator) - _BLOCKS_SET
    if unknown:
        raise ValueError(f"unknown blocks in accumulator: {sorted(unknown)}")
    from dev_kit.agent._atomic_io import write_atomic_text
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic_text(path, json.dumps(accumulator, indent=2, ensure_ascii=False, sort_keys=True))


def load_accumulator(path: Path) -> dict[str, dict]:
    """Load the accumulator dict from disk.

    Args:
        path: Source file path.

    Returns:
        The deserialised accumulator dict, with empty entries for any missing
        blocks. If the file doesn't exist, returns a fresh empty accumulator.

    Raises:
        ValueError: If the file is corrupt JSON or contains unknown block names.
    """
    if not path.exists():
        return empty_accumulator()
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "accumulator load failed",
            extra={"operation": "load_accumulator", "status": "failure",
                   "error": str(exc), "path": str(path)},
        )
        raise ValueError(f"Corrupt JSON in accumulator file {path}: {exc}") from exc
    unknown = set(payload) - _BLOCKS_SET
    if unknown:
        raise ValueError(f"unknown blocks in accumulator file {path}: {sorted(unknown)}")
    result = empty_accumulator()
    result.update(payload)
    return result


__all__ = ["BLOCKS", "empty_accumulator", "save_accumulator", "load_accumulator"]
