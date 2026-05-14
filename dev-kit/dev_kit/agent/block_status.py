"""block_status — derive per-block completion from field_status.

Replaces the old wizard's `ConfigStatus` enum (PENDING / DRAFT / STALE /
COMPLETE) with on-demand derivation from `field_status.json` values
(pending / answered / needs_re_asking / not_applicable).

A block is "complete" when every tracked field for that block has status
"answered" or "not_applicable". Otherwise "incomplete".
"""
from __future__ import annotations

from typing import Literal

from dev_kit.agent.project_state import BLOCKS

BlockStatus = Literal["complete", "incomplete"]
_COMPLETE_FIELD_STATUSES = {"answered", "not_applicable"}


def block_completion_status(block: str, field_status: dict[str, str]) -> BlockStatus:
    """Return 'complete' iff every field of `block` in field_status is answered/not_applicable.

    Args:
        block: Block name (one of `project_state.BLOCKS`).
        field_status: The full field_status dict (paths are `<block>.<rest>`).

    Returns:
        "complete" or "incomplete". A block with no fields tracked is
        "incomplete" (nothing has been answered yet).
    """
    prefix = f"{block}."
    block_field_statuses = [s for path, s in field_status.items() if path.startswith(prefix)]
    if not block_field_statuses:
        return "incomplete"
    if all(s in _COMPLETE_FIELD_STATUSES for s in block_field_statuses):
        return "complete"
    return "incomplete"


def all_block_statuses(field_status: dict[str, str]) -> dict[str, BlockStatus]:
    """Return {block_name: status} for every block.

    Args:
        field_status: The full field_status dict (paths are `<block>.<rest>`).

    Returns:
        A dict mapping each known block name to its "complete" or "incomplete"
        status, derived from the provided field_status.
    """
    return {block: block_completion_status(block, field_status) for block in BLOCKS}


__all__ = ["BlockStatus", "block_completion_status", "all_block_statuses"]
