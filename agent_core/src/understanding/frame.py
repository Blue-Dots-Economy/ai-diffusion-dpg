"""
agent_core/src/understanding/frame.py

Renders the per-turn NLU user message: <frame>, <recent>, <caller_now>
(NLU dialogue-acts spec §5.2). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.workflow_loader import PendingQuestion

_EMPTY = (None, "", [], {})


def offered_rows(entry: dict | None) -> list[dict]:
    """Rows of a stored tool-result entry: the list itself, or ``data["items"]``.

    Args:
        entry: A ``TurnToolCache.latest_entry`` dict, or None.

    Returns:
        Dict rows in stored order; non-dict rows are skipped.
    """
    data = (entry or {}).get("data")
    rows = data if isinstance(data, list) else (data.get("items") if isinstance(data, dict) else None)
    return [r for r in (rows or []) if isinstance(r, dict)]


def option_label(row: dict, fields: tuple[str, ...]) -> str:
    """Join a row's display fields with " · ", skipping empty ones."""
    return " · ".join(str(row[f]) for f in fields if row.get(f) not in _EMPTY)


class FrameBuilderBase(ABC):
    """Interface for rendering the NLU user message."""

    @abstractmethod
    def build(self, *, step: str, pending: PendingQuestion | None, rows: list[dict],
              known: list[tuple[str, Any]], recent: list[dict], segments: list[str]) -> str:
        """Render the user message for one NLU call."""


class FrameBuilder(FrameBuilderBase):
    """Default renderer.

    Args:
        reply_cap: Max characters kept from each recent bot reply (tail kept,
            because the question is at the end).
    """

    def __init__(self, reply_cap: int = 600) -> None:
        self._cap = max(1, reply_cap)

    def _tail(self, text: str) -> str:
        text = (text or "").strip()
        return text if len(text) <= self._cap else "…" + text[-self._cap:]

    def build(self, *, step: str, pending: PendingQuestion | None, rows: list[dict],
              known: list[tuple[str, Any]], recent: list[dict], segments: list[str]) -> str:
        """Render the user message for one NLU call.

        Args:
            step: Subagent the caller is in (before routing).
            pending: Resolved pending question, or None.
            rows: Offered rows (only rendered when ``pending.options_from``).
            known: (label, value) pairs; empty values are skipped.
            recent: Last exchanges, oldest first.
            segments: This turn's utterances; all but the last are marked interrupted.

        Returns:
            The rendered message.
        """
        lines = ["<frame>", f"step: {step}"]
        if pending is None:
            lines.append("pending: none")
        else:
            lines.append(f"pending: {pending.id}" + (f" — {pending.expects}" if pending.expects else ""))
            if pending.options_from and rows:
                lines.append("offered:")
                lines += [f"  {i}. {option_label(r, pending.options_from.fields)}"
                          for i, r in enumerate(rows, start=1)]
        shown = [f"{k}={v}" for k, v in known if v not in _EMPTY]
        if shown:
            lines.append("known: " + " · ".join(shown))
        lines.append("</frame>")
        if recent:
            lines.append("<recent>")
            for e in recent:
                if e.get("caller"):
                    lines.append(f"caller: {str(e['caller']).strip()}")
                if e.get("bot"):
                    mark = "[interrupted] " if e.get("interrupted") else ""
                    lines.append(f"bot: {mark}{self._tail(str(e['bot']))}")
            lines.append("</recent>")
        lines.append("<caller_now>")
        segs = [s for s in segments if str(s).strip()]
        lines += [("[interrupted] " if i < len(segs) - 1 else "") + str(s).strip()
                  for i, s in enumerate(segs)]
        lines.append("</caller_now>")
        return "\n".join(lines)
