"""IntakeState — typed intake captured before downstream phases run.

Persisted to `_meta/intake_state.json` under the project directory. Read by
the phase driver, FIELD_RULES handlers, and the renderer.

Belongs to the dev-kit deterministic wizard. See:
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §4
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Channel = Literal["web", "voice"]

# The 7 binary flags the wizard captures via update_intake during the tier phase.
# When all 7 are present in IntakeState.binary_flags_seen, intake is complete.
BINARY_INTAKE_FIELDS: frozenset[str] = frozenset({
    "has_kb", "has_external_tools", "is_multi_turn",
    "needs_persistent_user_data", "is_companion_style",
    "needs_consent", "has_hitl",
})


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
    # Names of binary intake fields the wizard has captured via update_intake.
    # When this set contains all 7 BINARY_INTAKE_FIELDS, intake is complete.
    # Stored as list so it round-trips through JSON; use set semantics in code.
    binary_flags_seen: list[str] = field(default_factory=list)
    # Set during the knowledge phase when the operator confirms documents
    # live in Azure Blob Storage. Drives the deploy form's decision to
    # ask for AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY / AZURE_CONTAINER_NAME.
    # Credentials themselves NEVER touch chat — only the boolean intent.
    # Mirrors the legacy `ConfigAccumulator.declare_azure_needed()` flag
    # on main; restored here so the deploy step knows whether to surface
    # Azure inputs.
    uses_azure_blob: bool = False
    # Tracks whether the LLM has explicitly captured the user's answer to
    # the Azure-Blob question (via `update_intake(field="uses_azure_blob",
    # value=...)`). Distinct from `uses_azure_blob` because False is also a
    # valid answer — the value alone can't tell us whether the user
    # answered or whether we are still looking at the default. The router
    # uses this to gate knowledge-phase advancement: if `has_kb=True`
    # and this flag is False, the phase stays open until the LLM asks
    # the question and writes the answer.
    azure_blob_decided: bool = False

    def __post_init__(self) -> None:
        # Validate Channel literal manually since dataclass doesn't enforce it.
        if not self.selected_channels:
            raise ValueError(
                "selected_channels must be non-empty; pick at least one of 'web' or 'voice'"
            )
        for ch in self.selected_channels:
            if ch not in ("web", "voice"):
                raise ValueError(
                    f"Invalid channel {ch!r}; only 'web' and 'voice' allowed"
                )

    def touch(self) -> None:
        """Update `updated_at` to the current UTC ISO timestamp.

        Callers must invoke this before `save_intake_state` to record the
        modification time on disk. Returns None; mutates in-place.
        """
        self.updated_at = datetime.now(timezone.utc).isoformat()


def save_intake_state(path: Path, state: IntakeState) -> None:
    """Persist intake state to disk as JSON.

    Args:
        path: Target file path (typically `<slug>/_meta/intake_state.json`).
        state: The IntakeState to save.
    """
    from dev_kit.agent._atomic_io import write_atomic_text
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    write_atomic_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def load_intake_state(path: Path) -> IntakeState:
    """Load intake state from disk.

    Args:
        path: Source file path.

    Returns:
        The deserialised IntakeState.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file contains corrupt JSON or the JSON payload does
            not match the IntakeState schema (missing or extra fields).
    """
    if not path.exists():
        raise FileNotFoundError(f"intake state not found at {path}")

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "load_intake_state",
            extra={"operation": "load_intake_state", "status": "failure", "error": str(exc)},
        )
        raise ValueError(
            f"Corrupt JSON in intake state file {path}: {exc}"
        ) from exc

    # Drop any unknown keys so a forward-compat read of an older payload
    # (or a payload from a project created with a slightly different
    # IntakeState version) still loads. Dataclass field defaults cover
    # missing keys automatically.
    import dataclasses
    known_fields = {f.name for f in dataclasses.fields(IntakeState)}
    filtered_payload = {k: v for k, v in payload.items() if k in known_fields}

    try:
        return IntakeState(**filtered_payload)
    except TypeError as exc:
        logger.error(
            "load_intake_state",
            extra={"operation": "load_intake_state", "status": "failure", "error": str(exc)},
        )
        raise ValueError(
            f"Schema mismatch loading intake state from {path}: {exc}"
        ) from exc


__all__ = [
    "BINARY_INTAKE_FIELDS",
    "Channel",
    "IntakeState",
    "load_intake_state",
    "save_intake_state",
]
