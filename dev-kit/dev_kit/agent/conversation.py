"""conversation — public chat/history surface for the deterministic wizard.

Thin wrapper over phase_driver + history modules. Stateless: every call
loads state from disk via project_state / field_status / history helpers.

Belongs to the dev-kit. The legacy ``ConversationEngine`` class — which
wrapped ``ConfigAccumulator`` + history + checkpoint loading — has been
removed. Callers should depend on the functions below.
"""
from __future__ import annotations

from pathlib import Path

from dev_kit.agent import phase_driver
from dev_kit.agent.history import HistoryEntry, load_history


def chat_turn(
    projects_root: Path,
    slug: str,
    user_message: str,
    *,
    llm_call,
) -> str:
    """Run a single chat turn for ``slug`` via the deterministic phase driver.

    Args:
        projects_root: Parent directory holding all project slugs.
        slug: Project identifier.
        user_message: The user's text for this turn.
        llm_call: Sync ``(system_prompt, messages) -> LLMResponse`` callable.
            ``messages`` follows the Anthropic message-format list shape (see
            ``phase_driver.run_turn``).

    Returns:
        The assistant reply text.

    Raises:
        FileNotFoundError: If ``<projects_root>/<slug>/_meta/intake_state.json``
            is missing (legacy project).
        ValueError: If state JSON is corrupt or the resolved phase is unknown.
    """
    return phase_driver.run_turn(
        user_message,
        slug,
        projects_root=projects_root,
        llm_call=llm_call,
    )


def get_history(projects_root: Path, slug: str) -> list[HistoryEntry]:
    """Return the full chat history for a project.

    Args:
        projects_root: Parent directory holding all project slugs.
        slug: Project identifier.

    Returns:
        Ordered list of HistoryEntry objects (oldest first). Empty if no
        history has been recorded yet.
    """
    return load_history(projects_root / slug)


__all__ = ["chat_turn", "get_history"]
