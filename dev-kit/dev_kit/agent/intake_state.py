"""IntakeState — typed intake captured before downstream phases run.

Persisted to `_meta/intake_state.json` under the project directory. Read by
the phase driver, FIELD_RULES handlers, and the renderer.

Belongs to the dev-kit deterministic wizard. See:
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §4
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Channel = Literal["web", "voice"]


@dataclass
class IntakeState:
    """The 12 intake fields plus bookkeeping.

    5 fields come from the project creation form (project_name,
    domain_description, selected_channels, default_language,
    supported_languages). 7 binary flags come from chat.
    """

    # Capabilities
    has_kb: bool
    has_external_tools: bool

    # Conversation pattern
    is_multi_turn: bool
    needs_persistent_user_data: bool
    is_companion_style: bool

    # Operational
    needs_consent: bool
    has_hitl: bool

    # Channels and languages (project creation form)
    selected_channels: list[Channel]
    default_language: str
    supported_languages: list[str]

    # Context (project creation form, LLM-only)
    domain_description: str
    project_name: str

    # Bookkeeping
    completed: bool = False
    updated_at: str = ""

    def __post_init__(self) -> None:
        # Validate Channel literal manually since dataclass doesn't enforce it.
        for ch in self.selected_channels:
            if ch not in ("web", "voice"):
                raise ValueError(
                    f"Invalid channel {ch!r}; only 'web' and 'voice' allowed"
                )

    def touch(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()


def save_intake_state(path: Path, state: IntakeState) -> None:
    """Persist intake state to disk as JSON.

    Args:
        path: Target file path (typically `<slug>/_meta/intake_state.json`).
        state: The IntakeState to save.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def load_intake_state(path: Path) -> IntakeState:
    """Load intake state from disk.

    Args:
        path: Source file path.

    Returns:
        The deserialised IntakeState.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"intake state not found at {path}")
    payload = json.loads(path.read_text())
    return IntakeState(**payload)
