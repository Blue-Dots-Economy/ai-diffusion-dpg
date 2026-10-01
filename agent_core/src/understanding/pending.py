"""
agent_core/src/understanding/pending.py

Picks what the bot is waiting for, from the subagent's declared pending
questions and current state (NLU dialogue-acts spec §7.2). Deterministic; no
LLM. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.conditions import all_conditions
from src.workflow_loader import PendingQuestion


class PendingResolverBase(ABC):
    """Interface for pending-question resolution."""

    @abstractmethod
    def resolve(self, subagent_id: str, state: dict) -> PendingQuestion | None:
        """Return the pending question for this subagent and state, or None."""


class PendingResolver(PendingResolverBase):
    """First declared candidate whose ``when`` conditions all hold.

    Args:
        workflow: Loaded workflow; only ``subagents[id].pending`` is read.
    """

    def __init__(self, workflow: Any) -> None:
        self._workflow = workflow

    def resolve(self, subagent_id: str, state: dict) -> PendingQuestion | None:
        """Return the pending question for this subagent and state, or None.

        Args:
            subagent_id: Subagent the caller is currently in (before routing).
            state: Merged routing state (session + profile + NLU-owned values).

        Returns:
            The first matching PendingQuestion, or None when the subagent is
            unknown, declares none, or none match.
        """
        sub = (getattr(self._workflow, "subagents", None) or {}).get(subagent_id)
        for candidate in getattr(sub, "pending", None) or []:
            if all_conditions(candidate.when, state or {}):
                return candidate
        return None
