"""build_skeleton — pure function producing a domain-only accumulator + field_status.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8
("build_skeleton()") and the field rules catalogue.
"""
from __future__ import annotations

import logging
from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule
from dev_kit.agent.field_rules.trust_layer import _CANONICAL_DIGNITY_QUESTIONS
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.path_ops import set_path

logger = logging.getLogger(__name__)

# The 7 runtime blocks. Each gets a (possibly empty) accumulator dict.
BLOCKS = (
    "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
    "action_gateway", "reach_layer", "observability_layer",
)

# Sentinel returned by eval_rule when evaluation fails (e.g. placeholder rule).
_SKIP = object()

# Extra constants available to predetermined rule expressions. These are
# module-level values defined in per-block field_rules modules that rules
# may reference by name (e.g. `_CANONICAL_DIGNITY_QUESTIONS`).
_RULE_EXTRAS: dict[str, Any] = {
    "_CANONICAL_DIGNITY_QUESTIONS": _CANONICAL_DIGNITY_QUESTIONS,
}


# Maps a `languages` enum value (from dev_kit/schemas/enums_config.yaml)
# to a Raya language tag — the codes Raya itself uses in
# `raya_voices[].language`. Used by FIELD_RULES predetermined rules to
# convert the project's `default_language` into the voice-channel STT/TTS
# language. Keep in sync with `raya_voices` in enums_config.yaml when new
# Raya languages are added.
#
# Hinglish has no dedicated Raya voice; en-in is the closest match.
_LANG_CODE_MAP: dict[str, str] = {
    "english":  "en-in",
    "hindi":    "hi",
    "hinglish": "en-in",
    "marathi":  "mr",
    "telugu":   "te",
    "kannada":  "kn",
    "bengali":  "bn",
    "assamese": "as",
    "gujarati": "gu",
    "malayalam": "ml",
    "nepali":   "ne",
    "tamil":    "ta",
}


def lang_code(language: str | None) -> str:
    """Convert an enum language name (e.g. ``"english"``) to a Raya tag (``"en-in"``).

    Args:
        language: A value from the ``languages`` enum (case-insensitive).
            ``None`` or unknown values fall back to ``"en-in"`` so the
            wizard never writes an empty STT/TTS language string — the
            voice service rejects empty strings at boot.

    Returns:
        The Raya language tag for the given language.
    """
    if not language:
        return "en-in"
    return _LANG_CODE_MAP.get(language.lower(), "en-in")


def eval_expr(expr: str | None, state: IntakeState) -> Any:
    """Evaluate an applies_if/invalidated_by expression against IntakeState.

    Expressions are Python expressions referencing IntakeState attributes by name
    (e.g., `has_kb`, `is_multi_turn and is_companion_style`, `"voice" in selected_channels`).
    For safety, we evaluate in a restricted namespace containing only the
    intake fields plus Python builtins for boolean operators.

    Args:
        expr: A Python expression string, or None (treated as True).
        state: The intake state providing variable bindings.

    Returns:
        The boolean result of the expression, or True if expr is None.
    """
    if expr is None:
        return True
    namespace = {f: getattr(state, f) for f in IntakeState.__dataclass_fields__}
    # No builtins — boolean operators don't need them.
    return eval(expr, {"__builtins__": {}}, namespace)


def eval_rule(rule_str: str, state: IntakeState) -> Any:
    """Evaluate a `predetermined` rule's `rule` expression.

    Rule format: `set: <python_expression>`. The expression is evaluated in the
    same restricted namespace as applies_if, augmented with known rule-level
    constants (e.g. `_CANONICAL_DIGNITY_QUESTIONS`) and the derived-field
    helpers ``slug`` (callable) and ``project_slug`` (precomputed) so rules
    can reference the project slug without each one re-implementing it.

    If the expression cannot be evaluated (e.g. it references an undefined
    name like a placeholder class), returns the ``_SKIP`` sentinel so the
    caller can silently skip writing that path.

    Args:
        rule_str: A rule string starting with ``"set: "``.
        state: The intake state providing variable bindings.

    Returns:
        The evaluated value, or ``_SKIP`` if evaluation fails.

    Raises:
        ValueError: If ``rule_str`` does not start with ``"set:"``.
    """
    if not rule_str.startswith("set:"):
        raise ValueError(f"predetermined rule must start with 'set:': {rule_str!r}")
    expr = rule_str.removeprefix("set:").strip()
    namespace = {f: getattr(state, f) for f in IntakeState.__dataclass_fields__}
    namespace.update(_RULE_EXTRAS)
    # Expose `slug(...)` and `project_slug` so predetermined rules can
    # reference the project's hyphen-separated slug — mirrors what
    # `derived_fields.apply_derived_fields` provides. Without this,
    # rules like `set: f"{slug}_knowledge"` (for the KB collection
    # name) silently return _SKIP and the field is never written.
    from dev_kit.agent.derived_fields import slug as _slug_fn  # noqa: PLC0415
    project_name = getattr(state, "project_name", "") or ""
    namespace["slug"] = _slug_fn
    namespace["project_slug"] = _slug_fn(project_name)
    # `lang_code(language)` converts the project's language enum value
    # to the Raya tag the runtime expects. Used by the voice STT/TTS
    # language predetermined rules in reach_layer FIELD_RULES; without
    # this in the namespace those rules silently return _SKIP and the
    # voice service starts without a language config.
    namespace["lang_code"] = lang_code
    try:
        return eval(expr, {"__builtins__": {}}, namespace)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "skeleton.eval_rule skip",
            extra={
                "operation": "skeleton.eval_rule",
                "status": "skipped",
                "error": f"{type(exc).__name__}: {exc}",
                "expr": expr,
            },
        )
        return _SKIP


def get_framework_default(path: str) -> Any:
    """Return the framework default for a path (from dpg.yaml or Pydantic).

    For Phase 4 we use a minimal stub that knows the canonical dpg defaults
    for predetermined fields whose dpg value is well-known (e.g.,
    `dignity_check.enabled: false`, `dignity_check.questions: []`).
    The full lookup against parsed dpg.yaml is Phase 5 work.

    Args:
        path: Full dotted path including block prefix (e.g.
            ``"trust_layer.dignity_check.questions"``).

    Returns:
        The known framework default value, or ``None`` if not in the known
        defaults table.
    """
    KNOWN_DPG_DEFAULTS: dict[str, Any] = {
        "trust_layer.dignity_check.enabled": False,
        "trust_layer.dignity_check.questions": [],
        "agent_core.agent.ask_for_consent": False,
        "agent_core.conversation.user_state_model.enabled": False,
        "agent_core.conversation.session_end_eval.enabled": False,
        "knowledge_engine.knowledge.blocks.static_knowledge_base.enabled": False,
        "memory_layer.user_data_persistence.default_mode": "saved",
    }
    return KNOWN_DPG_DEFAULTS.get(path)


def build_skeleton(
    intake_state: IntakeState,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Walk FIELD_RULES → (accumulator, field_status).

    Iterates every entry in ``AGGREGATED_FIELD_RULES`` and:

    - ``chat`` fields: marks status ``"pending"`` (or ``"not_applicable"`` when
      ``applies_if`` evaluates to false). Writes the ``default`` if one is set.
    - ``predetermined`` fields: evaluates the ``rule`` expression and writes
      the result unless it equals the framework default (no-redundancy principle
      from design §3), equals ``None``, or the expression cannot be evaluated.
    - ``deploy``, ``derived``, ``framework_default_only``: skipped; handled at
      render/deploy time.

    Args:
        intake_state: The complete IntakeState (all 12 fields).

    Returns:
        A 2-tuple ``(accumulator, field_status)`` where:

        - ``accumulator``: ``{block_name: domain_yaml_dict, ...}`` for every block.
          Predetermined rules whose value equals the framework default are
          NOT written (no-redundancy principle from design §3).
        - ``field_status``: ``{full_path: status, ...}`` for every chat field.
          Statuses: ``"pending"``, ``"not_applicable"`` (when ``applies_if`` is false).
    """
    accumulator: dict[str, dict] = {block: {} for block in BLOCKS}
    field_status: dict[str, str] = {}

    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        block, relative_path = full_path.split(".", 1)
        applies = eval_expr(rule.applies_if, intake_state)

        if rule.category == "chat":
            if not applies:
                field_status[full_path] = "not_applicable"
                continue
            if rule.default is not None:
                # The FIELD_RULES default IS the answer for this field — the
                # framework has a safe default, the user is not required to
                # pick anything, so mark it `answered` immediately. Without
                # this, the router would block phase advancement on chat
                # fields the LLM and user have nothing useful to say about
                # (e.g. `language_normalisation.enabled=True` or any
                # inheritance-via-empty-string field). The GoGuide
                # regression: seven such fields in language phase kept the
                # phase stuck at `pending` forever.
                #
                # Invalidation still moves these to `needs_re_asking` via
                # `router.on_intake_update`, so user-driven re-confirmation
                # remains intact when intake flags flip.
                set_path(accumulator[block], relative_path, rule.default)
                field_status[full_path] = "answered"
                # Point 8: log chat field written with its default
                logger.info(
                    "skeleton.field_written",
                    extra={
                        "operation": "skeleton.field_written",
                        "status": "success",
                        "path": full_path,
                        "category": rule.category,
                        "value_kind": "chat_default",
                        "field_status": "answered",
                    },
                )
            elif rule.auto_answer:
                # `default=None` is a *meaningful* default for this field
                # (typically "inherit from parent" — see the
                # `nlu_processor.provider` / `language_normalisation.provider`
                # FieldRule entries, whose Pydantic field is
                # `Optional[ProviderField] = None`). Nothing to write, but
                # the field IS configured — mark answered so the router
                # does not block on it.
                field_status[full_path] = "answered"
                logger.info(
                    "skeleton.field_written",
                    extra={
                        "operation": "skeleton.field_written",
                        "status": "success",
                        "path": full_path,
                        "category": rule.category,
                        "value_kind": "auto_answer",
                        "field_status": "answered",
                    },
                )
            else:
                field_status[full_path] = "pending"

        elif rule.category == "predetermined":
            if not applies:
                continue
            if not rule.rule:
                continue
            value = eval_rule(rule.rule, intake_state)
            if value is _SKIP or value is None:
                continue
            framework_default = get_framework_default(full_path)
            if value != framework_default:
                set_path(accumulator[block], relative_path, value)
                # Point 8: log predetermined field written
                logger.info(
                    "skeleton.field_written",
                    extra={
                        "operation": "skeleton.field_written",
                        "status": "success",
                        "path": full_path,
                        "category": rule.category,
                        "value_kind": "predetermined_value",
                    },
                )
            else:
                # Point 9: log field skipped because it equals framework default
                logger.info(
                    "skeleton.field_skipped",
                    extra={
                        "operation": "skeleton.field_skipped",
                        "status": "skipped",
                        "path": full_path,
                        "reason": "equals_dpg_default",
                    },
                )

        elif rule.category in ("deploy", "derived", "framework_default_only"):
            # deploy: nothing in domain YAML; deploy overlay applies at render time.
            # derived: renderer computes at write time (Phase 5).
            # framework_default_only: lives in dpg.yaml.
            continue

    return accumulator, field_status


__all__ = [
    "build_skeleton", "BLOCKS",
    "eval_expr", "eval_rule", "get_framework_default", "_SKIP",
]
