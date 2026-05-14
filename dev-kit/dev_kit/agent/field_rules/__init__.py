"""FieldRule dataclass and the aggregated rules registry.

Each runtime block has its own module under this package
(e.g. `field_rules.agent_core`) exporting a `FIELD_RULES` dict keyed by
dotted field path (relative to the block root). This module re-exports
the union as `AGGREGATED_FIELD_RULES` with block-prefixed paths.

See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, Optional

Category = Literal[
    "predetermined", "chat", "deploy", "derived", "framework_default_only"
]
_VALID_CATEGORIES = set(Category.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class FieldRule:
    """Per-field rule. See catalogue §2.2 for category semantics."""

    category: Category

    # For predetermined: Python-expression string referencing intake state.
    #   e.g. "set: is_companion_style", "set: needs_consent"
    rule: Optional[str] = None

    # For chat
    phase: Optional[str] = None
    default: Optional[Any] = None
    must_include: Optional[list[Any]] = None
    description: Optional[str] = None
    applies_if: Optional[str] = None
    invalidated_by: list[str] = dc_field(default_factory=list)

    # For deploy and deploy-overridable chat
    advanced: bool = False
    deploy_overridable: bool = False

    # For derived
    compute: Optional[str] = None

    # For schema injection in prompts
    pydantic_class: Optional[str] = None

    def __post_init__(self) -> None:
        if self.category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category {self.category!r}; "
                f"must be one of {sorted(_VALID_CATEGORIES)}"
            )


# Valid phase names — referenced by per-block FIELD_RULES tests to assert
# every chat field's `phase` is one of these.
FIELD_RULES_PHASES_VALID = {
    "tier", "language", "knowledge", "memory", "user_state", "trust",
    "tools", "workflow", "observability", "reach", "review",
}


# AGGREGATED_FIELD_RULES is built lazily by the loader below; populated
# after every per-block module has registered its FIELD_RULES dict.
# At plan-time it's empty — each block module fills it in Phase 3.
AGGREGATED_FIELD_RULES: dict[str, FieldRule] = {}


def register_block_rules(block_name: str, rules: dict[str, FieldRule]) -> None:
    """Register a block's FIELD_RULES into the aggregate registry.

    Args:
        block_name: e.g. "agent_core", "trust_layer".
        rules: The block's FIELD_RULES dict with paths relative to block root.

    Mutation: prefixes each path with `<block_name>.` and inserts into
    AGGREGATED_FIELD_RULES. Re-registering the same block replaces its entries.
    """
    # Drop previous entries for this block (idempotent re-registration).
    prefix = f"{block_name}."
    for path in list(AGGREGATED_FIELD_RULES.keys()):
        if path.startswith(prefix):
            del AGGREGATED_FIELD_RULES[path]
    for relative_path, rule in rules.items():
        AGGREGATED_FIELD_RULES[f"{prefix}{relative_path}"] = rule


__all__ = [
    "FieldRule", "Category", "FIELD_RULES_PHASES_VALID",
    "AGGREGATED_FIELD_RULES", "register_block_rules",
]
