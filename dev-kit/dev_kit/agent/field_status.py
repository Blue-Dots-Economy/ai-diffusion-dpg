"""field_status.json — per-chat-field tracking of pending/answered/needs_re_asking/not_applicable.

Persisted to `<slug>/_meta/field_status.json`. Read and updated by the phase
driver, the on_intake_update handler, and the end-of-turn router.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §3
(field status lifecycle) and §8 (build_skeleton initialisation).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELD_STATUS_VALUES = {"pending", "answered", "needs_re_asking", "not_applicable"}


def save_field_status(path: Path, status: dict[str, str]) -> None:
    """Persist field statuses to disk after validating every value.

    Args:
        path: Target file path (typically ``<slug>/_meta/field_status.json``).
            Parent directories are created if absent.
        status: Dict mapping dotted field paths to status strings. Every value
            must be in ``FIELD_STATUS_VALUES``.

    Raises:
        ValueError: If any status value is not in ``FIELD_STATUS_VALUES``.
    """
    for k, v in status.items():
        if v not in FIELD_STATUS_VALUES:
            raise ValueError(
                f"Invalid field status {v!r} for {k!r}; "
                f"allowed: {sorted(FIELD_STATUS_VALUES)}"
            )
    from dev_kit.agent._atomic_io import write_atomic_text
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic_text(path, json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True))


def load_field_status(path: Path) -> dict[str, str]:
    """Return field statuses from disk; empty dict if file is missing or not a dict.

    Handles missing files and valid JSON that is not a dict gracefully — those
    return an empty dict.  Corrupt (unparseable) JSON is treated as a real
    failure and raises ``ValueError`` so callers can propagate it as an HTTP 500.

    Args:
        path: Source file path (typically ``<slug>/_meta/field_status.json``).

    Returns:
        The deserialised status dict, or ``{}`` if the file is absent or
        contains a non-dict JSON value.

    Raises:
        ValueError: If the file exists but contains corrupt (unparseable) JSON.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "load_field_status corrupt",
            extra={
                "operation": "load_field_status",
                "status": "failure",
                "error": str(exc),
                "path": str(path),
            },
        )
        raise ValueError(f"Corrupt JSON in field_status file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        logger.warning(
            "load_field_status non-dict",
            extra={
                "operation": "load_field_status",
                "status": "failure",
                "error": f"expected dict, got {type(payload).__name__}",
                "path": str(path),
            },
        )
        return {}
    return payload


__all__ = ["FIELD_STATUS_VALUES", "save_field_status", "load_field_status"]
