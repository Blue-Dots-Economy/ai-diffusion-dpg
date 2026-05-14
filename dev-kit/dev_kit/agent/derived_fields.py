"""Derived-field pass for the dev-kit configuration wizard.

Evaluates every ``category="derived"`` entry in ``AGGREGATED_FIELD_RULES``
and writes the computed value into the per-block accumulator dict.

Design reference: docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §5
and the field-rules catalogue §2.2 (``derived`` category semantics).

This module belongs to the dev-kit deterministic wizard (Tier 1 Configuration
Agent).  It is intentionally separate from ``renderer.py`` to avoid bloating
that module and to make the derived-field pass unit-testable in isolation.

Typical call sequence::

    accumulator, field_status = build_skeleton(intake_state)
    # ... LLM fills in chat fields ...
    apply_derived_fields(accumulator, intake_state)
    render_all(project_path, accumulator)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.path_ops import set_path
from dev_kit.agent.skeleton import BLOCKS, eval_expr

logger = logging.getLogger(__name__)


def slug(text: str) -> str:
    """Convert a project name to a docker-compose-safe lowercase slug.

    Replaces any sequence of characters that is not alphanumeric or underscore
    with a single underscore, then strips leading and trailing underscores.

    Args:
        text: Input string, typically a project name.

    Returns:
        A lowercase, underscore-delimited slug safe for use as an identifier
        in docker-compose service names, storage keys, and OTel domain labels.
        Returns an empty string when ``text`` is empty or contains only
        special characters.

    Examples:
        >>> slug("My Project Name")
        'my_project_name'
        >>> slug("My Test! Project")
        'my_test_project'
        >>> slug("hello-world")
        'hello_world'
    """
    if not text:
        return ""
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def _build_eval_namespace(intake_state: IntakeState) -> dict[str, Any]:
    """Build the restricted namespace for evaluating ``compute`` expressions.

    Exposes:
    - ``project_name``  — the raw project name string from intake_state.
    - ``slug``          — the ``slug()`` function (so ``slug(project_name)`` works).
    - ``project_slug``  — pre-computed ``slug(project_name)`` value (so
      ``f"{project_slug}_workflow"`` works without calling the function).

    No ``__builtins__`` are exposed; f-string interpolation and function calls
    are the only operations needed by existing compute expressions.

    Args:
        intake_state: Source of ``project_name`` and other intake fields.

    Returns:
        Dict suitable for ``eval(expr, {"__builtins__": {}}, namespace)``.
    """
    project_name = getattr(intake_state, "project_name", "") or ""
    project_slug_value = slug(project_name)
    return {
        "project_name": project_name,
        "slug": slug,
        "project_slug": project_slug_value,
    }


def apply_derived_fields(
    accumulator: dict[str, dict],
    intake_state: IntakeState,
) -> None:
    """Walk AGGREGATED_FIELD_RULES; evaluate every derived entry's ``compute`` string.

    For each rule with ``category="derived"``, evaluates the ``compute`` expression
    in a controlled namespace containing ``project_name``, ``project_slug`` (the
    pre-computed slug value), and a ``slug()`` function.  Writes the result into
    ``accumulator[block]`` at the relative path using ``path_ops.set_path``.

    Skips rules whose ``applies_if`` evaluates to False against ``intake_state``.
    Skips rules with no ``compute`` string.  Skips unknown blocks (blocks not
    present as keys in ``accumulator``).  Logs a warning and skips any rule
    whose ``compute`` expression raises an exception.

    Mutates ``accumulator`` in place.  Calling this function multiple times with
    the same arguments is idempotent — the same deterministic value is written on
    each call.

    Args:
        accumulator: Per-block dict keyed by block name (e.g. ``"agent_core"``).
            Must have an entry for every block referenced by a derived rule, or
            those rules will be silently skipped.
        intake_state: Source of ``project_name`` and other derived inputs.
    """
    namespace = _build_eval_namespace(intake_state)

    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "derived":
            continue
        if not rule.compute:
            continue

        # Split "block_name.relative.path" — first segment is the block.
        parts = full_path.split(".", 1)
        if len(parts) != 2:
            logger.warning(
                "apply_derived_fields: unexpected path format",
                extra={
                    "operation": "apply_derived_fields",
                    "status": "skipped",
                    "path": full_path,
                },
            )
            continue

        block, relative_path = parts

        if block not in accumulator:
            logger.debug(
                "apply_derived_fields: block not in accumulator, skipping",
                extra={
                    "operation": "apply_derived_fields",
                    "status": "skipped",
                    "block": block,
                    "path": full_path,
                },
            )
            continue

        # Honour applies_if gating (e.g. a derived field that only exists for
        # voice-enabled projects).
        if not eval_expr(rule.applies_if, intake_state):
            continue

        try:
            value = eval(rule.compute, {"__builtins__": {}}, namespace)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "apply_derived_fields: compute eval failed",
                extra={
                    "operation": "apply_derived_fields",
                    "status": "failure",
                    "path": full_path,
                    "expr": rule.compute,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            continue

        set_path(accumulator[block], relative_path, value)
        # Point 10: promote DEBUG → INFO, rename operation, add computed_value
        logger.info(
            "derived_fields.computed",
            extra={
                "operation": "derived_fields.computed",
                "status": "success",
                "path": full_path,
                "computed_value": value,
            },
        )


__all__ = ["slug", "apply_derived_fields"]
