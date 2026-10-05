# agent_core/src/understanding/history.py
"""
agent_core/src/understanding/history.py

The ``recent_turns`` session list read by the NLU frame (NLU dialogue-acts
spec §6.9, §7.5). One entry per exchange. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any

RECENT_TURNS_KEY = "recent_turns"


def append_recent_turn(existing: Any, *, caller: str, bot: str, interrupted: bool,
                       history_turns: int) -> list[dict]:
    """Append one exchange and keep the last ``history_turns``.

    Args:
        existing: Current ``recent_turns`` value (anything non-list is reset).
        caller: What the caller said.
        bot: What the caller heard (full reply, or the emitted part if interrupted).
        interrupted: True when the bot's reply was cut off.
        history_turns: How many exchanges to keep; 0 keeps none.

    Returns:
        The new list.
    """
    if history_turns <= 0:
        return []
    entries = [e for e in existing if isinstance(e, dict)] if isinstance(existing, list) else []
    entries.append({"caller": caller or "", "bot": bot or "", "interrupted": bool(interrupted)})
    return entries[-history_turns:]
