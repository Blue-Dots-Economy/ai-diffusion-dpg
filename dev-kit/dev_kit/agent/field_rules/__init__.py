"""FieldRule dataclass and the aggregated rules registry.

Each runtime block has its own module under this package
(e.g. `field_rules.agent_core`) exporting a `FIELD_RULES` dict keyed by
dotted field path (relative to the block root). This module re-exports
the union as `AGGREGATED_FIELD_RULES` with block-prefixed paths.

See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, Optional, get_args

Category = Literal[
    "predetermined", "chat", "deploy", "derived", "framework_default_only"
]
_VALID_CATEGORIES = set(get_args(Category))


@dataclass(frozen=True)
class FieldRule:
    """Per-field rule definition for the wizard's FIELD_RULES registry.

    A rule's `category` determines which other attributes are meaningful:

    - `predetermined`: requires `rule` (set: <python_expression>). Recomputed
      whenever any field in `invalidated_by` changes.
    - `chat`: requires `phase`. May carry `default`, `description`,
      `applies_if`, `invalidated_by`, `must_include`. Set `deploy_overridable=True`
      to additionally surface in the deploy form.
    - `deploy`: captured by the deploy form (not chat). May set `advanced=True`
      for collapsible groups.
    - `derived`: requires `compute` (a python expression). No user input.
    - `framework_default_only`: lives in dpg/<block>.yaml. Skeleton never
      writes it; allowlisted from the Coverage CI guard.

    Categories are mutually exclusive. `__post_init__` rejects any value
    not in `Category`.

    See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.2
    for the canonical semantics.
    """

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
    # When True, the skeleton marks this chat field as ``answered`` even
    # though no concrete default value is written. Use for fields whose
    # Pydantic default (``None`` / absent) is a meaningful "inherit from
    # parent" or "use framework default" signal — the user has nothing
    # to add, but ``default=None`` would otherwise leave the field at
    # ``pending`` forever and stall phase advancement.
    auto_answer: bool = False

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


# AGGREGATED_FIELD_RULES is module-level mutable state. Per-block modules
# call register_block_rules() at import time, so any test that imports a
# per-block module pre-populates this dict. Tests that touch the registry
# directly should snapshot and restore (see tests/agent/test_field_rules_dataclass.py).
AGGREGATED_FIELD_RULES: dict[str, FieldRule] = {}


def _intake_state_field_names() -> set[str]:
    """Return the set of declared IntakeState dataclass field names.

    Imported lazily inside the helper to avoid a circular import at
    module load (``intake_state`` imports nothing from
    ``field_rules`` but the inverse path is fragile during package
    initialisation).
    """
    from dataclasses import fields as _dc_fields
    from dev_kit.agent.intake_state import IntakeState
    return {f.name for f in _dc_fields(IntakeState)}


def _validate_invalidated_by(block_name: str, rules: dict[str, FieldRule]) -> None:
    """Reject typo'd ``invalidated_by`` entries at registration time.

    Each entry must be either:

    * A name of a declared ``IntakeState`` field (cascades through
      ``router.on_intake_update``), or
    * A dotted path (contains ``.``) — treated as a cross-field
      reference. Cross-field references are not validated here because
      not every block is registered at the time this runs.

    A typo like ``"has_external_tool"`` (missing ``s``) silently fails
    to cascade — the dependent chat field never invalidates when the
    user flips the real ``has_external_tools`` flag — and the stale
    answer flows through ``apply_derived_fields`` and ``render_all``
    into the deployed YAML. Catching at import surfaces the typo
    immediately rather than at deploy time.

    Args:
        block_name: For error message context.
        rules: The block's FIELD_RULES dict.

    Raises:
        ValueError: If any ``invalidated_by`` entry is not a dotted
            path and is not a known IntakeState field.
    """
    intake_names = _intake_state_field_names()
    typos: list[tuple[str, str]] = []
    for relative_path, rule in rules.items():
        for ref in rule.invalidated_by:
            if "." in ref:
                continue
            if ref not in intake_names:
                typos.append((relative_path, ref))
    if typos:
        formatted = "\n  ".join(
            f"{block_name}.{p}: invalidated_by={ref!r} is not an IntakeState field "
            f"(known names: {sorted(intake_names)})"
            for p, ref in typos
        )
        raise ValueError(
            f"FIELD_RULES typo in block {block_name!r} — invalidated_by entries "
            f"must reference declared IntakeState fields or use a dotted path:\n  "
            f"{formatted}"
        )


def register_block_rules(block_name: str, rules: dict[str, FieldRule]) -> None:
    """Register a block's FIELD_RULES into the aggregate registry.

    Args:
        block_name: Simple identifier (no dots), e.g. "agent_core", "trust_layer".
        rules: The block's FIELD_RULES dict with paths relative to block root.
            Every value must be a FieldRule instance.

    Raises:
        ValueError: If block_name is empty or contains dots, or if any
            ``invalidated_by`` entry references a non-existent
            ``IntakeState`` field (see ``_validate_invalidated_by``).
        TypeError: If any value in rules is not a FieldRule instance.

    Mutation: prefixes each path with `<block_name>.` and inserts into
    AGGREGATED_FIELD_RULES. Re-registering the same block replaces its entries.
    """
    if not block_name or "." in block_name:
        raise ValueError(
            f"block_name must be a simple identifier with no dots, got {block_name!r}"
        )
    for path, rule in rules.items():
        if not isinstance(rule, FieldRule):
            raise TypeError(
                f"Expected FieldRule for {block_name}.{path!r}, got {type(rule).__name__}"
            )
    _validate_invalidated_by(block_name, rules)
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

# Eagerly import every block module so register_block_rules() runs and
# AGGREGATED_FIELD_RULES is fully populated.
from dev_kit.agent.field_rules import (  # noqa: E402, F401
    agent_core,
    trust_layer,
    knowledge_engine,
    memory_layer,
    action_gateway,
    reach_layer,
    observability_layer,
)
