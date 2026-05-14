"""phase_driver — single shared turn-runner for the deterministic wizard.

Orchestrates each wizard turn end-to-end: load persisted state, filter pending
fields for the current phase, build the phase prompt, call the LLM, route any
tool calls returned by the LLM through the router handlers
(``on_intake_update``, ``on_config_update``), then call
``router.decide_next_phase`` to compute the next phase and persist the new
state to disk. Appends user and assistant entries to ``_meta/history.jsonl``
around the LLM call so chat history survives across processes.

Belongs to the dev-kit deterministic wizard. See design §6:
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

import importlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule
from dev_kit.agent.field_status import load_field_status, save_field_status
from dev_kit.agent.intake_state import (
    IntakeState,
    load_intake_state,
    save_intake_state,
)
from dev_kit.agent.phases_config import PHASES
from dev_kit.agent.router import (
    PHASE_ORDER,
    decide_next_phase,
)
from dev_kit.agent.skeleton import BLOCKS, eval_expr
from dev_kit.agent.history import HistoryEntry, append_turn, utc_now_iso
from dev_kit.agent.tools import (
    add_routing_rule,
    add_subagent,
    add_tool,
    discover_mcp_tools,
    parse_openapi_spec,
    update_config as tool_update_config,
    update_intake as tool_update_intake,
    update_subagent,
)

logger = logging.getLogger(__name__)

_DEFAULT_PHASE = "tier"
_META_DIR = "_meta"
_ACCUMULATOR_FILENAME = "accumulator.json"
_CURRENT_PHASE_FILENAME = "current_phase.txt"
_INTAKE_STATE_FILENAME = "intake_state.json"
_FIELD_STATUS_FILENAME = "field_status.json"


# ---------------------------------------------------------------------------
# Driver-local types for the LLM response shape
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM.

    Attributes:
        name: Tool name (e.g., ``"update_intake"``, ``"update_config"``).
        args: Tool arguments as a plain dict.
    """

    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    """Driver-local response shape returned by the injected ``llm_call``.

    Attributes:
        text: The assistant's text reply for this turn.
        tool_calls: Ordered list of tool calls the LLM emitted in this turn.
        model: Model identifier used for this call, if the provider exposes it.
        input_tokens: Number of input tokens consumed, if exposed by the provider.
        output_tokens: Number of output tokens generated, if exposed by the provider.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


# ---------------------------------------------------------------------------
# Accumulator persistence
# ---------------------------------------------------------------------------


def load_accumulator(slug_root: Path) -> dict[str, dict]:
    """Read the accumulator JSON for a project, or return an empty skeleton.

    The accumulator is a flat dict keyed by runtime block name; each value is
    the block's domain-half YAML payload as a nested dict. Missing blocks are
    backfilled with empty dicts so callers can always index by block.

    Args:
        slug_root: The project directory (e.g.
            ``<projects_root>/<project_slug>``). The accumulator lives at
            ``<slug_root>/_meta/accumulator.json``.

    Returns:
        Dict of ``{block_name: domain_yaml_dict, ...}`` for every block in
        ``BLOCKS``. Returns an empty skeleton if the file is missing or its
        contents cannot be parsed as a JSON object.
    """
    path = slug_root / _META_DIR / _ACCUMULATOR_FILENAME
    empty = {block: {} for block in BLOCKS}
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.warning(
            "load_accumulator corrupt",
            extra={
                "operation": "phase_driver.load_accumulator",
                "status": "failure",
                "error": str(exc),
                "path": str(path),
            },
        )
        return empty
    if not isinstance(payload, dict):
        logger.warning(
            "load_accumulator non-dict",
            extra={
                "operation": "phase_driver.load_accumulator",
                "status": "failure",
                "error": f"expected dict, got {type(payload).__name__}",
                "path": str(path),
            },
        )
        return empty
    # Always ensure all blocks are present so downstream callers can index by
    # block name unconditionally.
    for block in BLOCKS:
        payload.setdefault(block, {})
    return payload


def save_accumulator(slug_root: Path, accumulator: dict[str, dict]) -> None:
    """Persist the accumulator to ``<slug_root>/_meta/accumulator.json``.

    Args:
        slug_root: The project directory.
        accumulator: Dict of ``{block_name: domain_yaml_dict, ...}``.
    """
    path = slug_root / _META_DIR / _ACCUMULATOR_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(accumulator, indent=2, ensure_ascii=False, sort_keys=True))


# ---------------------------------------------------------------------------
# Current phase persistence
# ---------------------------------------------------------------------------


def load_current_phase(slug_root: Path) -> str:
    """Return the current wizard phase for a project.

    Args:
        slug_root: The project directory. The phase file lives at
            ``<slug_root>/_meta/current_phase.txt``.

    Returns:
        The phase identifier on disk, or ``"tier"`` (the wizard's entry phase)
        if the file is absent, empty, or contains an unknown phase.
    """
    path = slug_root / _META_DIR / _CURRENT_PHASE_FILENAME
    if not path.exists():
        return _DEFAULT_PHASE
    raw = path.read_text().strip()
    if not raw:
        return _DEFAULT_PHASE
    if raw not in PHASE_ORDER:
        logger.warning(
            "load_current_phase unknown phase",
            extra={
                "operation": "phase_driver.load_current_phase",
                "status": "failure",
                "error": f"unknown phase {raw!r}",
                "path": str(path),
            },
        )
        return _DEFAULT_PHASE
    return raw


def save_current_phase(slug_root: Path, phase: str) -> None:
    """Persist the current wizard phase.

    Args:
        slug_root: The project directory.
        phase: Phase identifier; must be one of ``PHASE_ORDER``.

    Raises:
        ValueError: If ``phase`` is not a valid phase name.
    """
    if phase not in PHASE_ORDER:
        raise ValueError(
            f"Unknown phase {phase!r}; must be one of {PHASE_ORDER}"
        )
    path = slug_root / _META_DIR / _CURRENT_PHASE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(phase)


# ---------------------------------------------------------------------------
# Pending-field collection and prompt assembly helpers
# ---------------------------------------------------------------------------


def collect_pending_fields(
    phase_id: str,
    intake_state: IntakeState,
    field_status: dict[str, str],
) -> list[tuple[str, FieldRule]]:
    """Return ``(path, rule)`` pairs the LLM still needs to ask about.

    A field is "pending for this phase" when all of these hold:

    - ``rule.category == "chat"``
    - ``rule.phase == phase_id``
    - ``eval_expr(rule.applies_if, intake_state) is True``
    - ``field_status.get(full_path) in {"pending", "needs_re_asking"}``

    Args:
        phase_id: The current wizard phase.
        intake_state: Current IntakeState (used for applies_if evaluation).
        field_status: Per-field status registry.

    Returns:
        A list of ``(full_path, rule)`` tuples — the shape phase-prompt
        builders accept directly.
    """
    pending: list[tuple[str, FieldRule]] = []
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat":
            continue
        if rule.phase != phase_id:
            continue
        if not eval_expr(rule.applies_if, intake_state):
            continue
        status = field_status.get(full_path, "pending")
        if status in ("pending", "needs_re_asking"):
            pending.append((full_path, rule))
    return pending


def render_pydantic_classes(pending_fields: list[tuple[str, FieldRule]]) -> str:
    """Render the Pydantic class closure for ``pending_fields``.

    For Task 6.3 this is a stub: the real source-code injection lands in a
    later phase. Returns either an empty string or a short placeholder block
    listing the pending paths so phase prompts have something to display.

    Args:
        pending_fields: List of ``(path, rule)`` tuples for the current phase.

    Returns:
        Empty string when ``pending_fields`` is empty; a placeholder comment
        block listing the pending paths otherwise.
    """
    if not pending_fields:
        return ""
    paths = ", ".join(path for path, _ in pending_fields)
    return (
        "# Pydantic schemas for pending fields are injected by Phase 9 work.\n"
        f"# Pending fields: {paths}"
    )


def cross_phase_references(accumulator: dict[str, dict]) -> str:
    """Render a multi-line string of already-set cross-phase reference values.

    Surfaces the values downstream phase prompts tell the LLM to read directly
    (provider/model, language settings, NLU intents/entities, knowledge intent
    filters). Returns an empty string when nothing has been set yet.

    Patterned after ``ConfigAccumulator._render_cross_phase_references``.

    Args:
        accumulator: ``{block_name: domain_yaml_dict, ...}``.

    Returns:
        Newline-joined lines describing each populated reference, or ``""`` if
        none of the tracked paths is populated.
    """
    refs: list[str] = []
    ac = accumulator.get("agent_core") or {}
    ke = accumulator.get("knowledge_engine") or {}

    agent = ac.get("agent") or {}
    for fld in ("provider", "primary_model", "fallback_model"):
        val = agent.get(fld)
        if val:
            refs.append(f"  agent_core.agent.{fld}: {val}")

    preprocessing = ac.get("preprocessing") or {}
    lang_norm = preprocessing.get("language_normalisation") or {}
    if lang_norm.get("default_language"):
        refs.append(
            "  agent_core.preprocessing.language_normalisation.default_language: "
            f"{lang_norm['default_language']}"
        )
    supported = lang_norm.get("supported_languages")
    if supported:
        refs.append(
            "  agent_core.preprocessing.language_normalisation.supported_languages: "
            f"{supported}"
        )

    nlu = preprocessing.get("nlu_processor") or {}
    intents = nlu.get("intents")
    if intents:
        refs.append(f"  agent_core.preprocessing.nlu_processor.intents: {intents}")
    entities = nlu.get("entities")
    if entities:
        refs.append(f"  agent_core.preprocessing.nlu_processor.entities: {entities}")

    kb = ((ke.get("knowledge") or {}).get("blocks") or {}).get("static_knowledge_base") or {}
    intent_filters = kb.get("intent_filters")
    if intent_filters and isinstance(intent_filters, dict):
        refs.append(
            "  knowledge_engine.intent_filters keys: "
            f"{sorted(intent_filters.keys())}"
        )

    return "\n".join(refs)


def _load_phase_prompt(phase_id: str) -> Callable[..., str]:
    """Return the ``build`` callable for a phase's prompt module.

    Args:
        phase_id: Phase identifier; must be a key in ``PHASES``.

    Returns:
        The phase-prompt module's ``build`` function.

    Raises:
        ValueError: If ``phase_id`` is not a known phase.
        AttributeError: If the resolved module is missing a ``build`` attribute.
    """
    if phase_id not in PHASES:
        raise ValueError(f"Unknown phase {phase_id!r}; must be one of {tuple(PHASES)}")
    phase_def = PHASES[phase_id]
    module = importlib.import_module(
        f"dev_kit.agent.phase_prompts.{phase_def.prompt_module}"
    )
    build = getattr(module, "build", None)
    if build is None:
        raise AttributeError(
            f"Phase prompt module {phase_def.prompt_module!r} has no 'build' function"
        )
    return build


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------

# Dispatch table — 8 canonical tools per design §6 "Slimmed tool surface".
# All handlers share the signature:
#   (args, intake_state, accumulator, field_status) -> dict[str, Any]
TOOL_HANDLERS: dict[
    str,
    Callable[[dict[str, Any], IntakeState, dict[str, dict], dict[str, str]], dict[str, Any]],
] = {
    "update_intake": tool_update_intake,
    "update_config": tool_update_config,
    "add_subagent": add_subagent,
    "update_subagent": update_subagent,
    "add_routing_rule": add_routing_rule,
    "add_tool": add_tool,
    "parse_openapi_spec": parse_openapi_spec,
    "discover_mcp_tools": discover_mcp_tools,
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_turn(
    user_message: str,
    project_slug: str,
    *,
    projects_root: Path,
    llm_call: Callable[[str, str], LLMResponse],
) -> str:
    """Run a single wizard turn end-to-end.

    Sequence (per design §6):

    1. Load intake_state, accumulator, field_status, current_phase from disk.
    2. Filter pending/needs_re_asking chat fields for the current phase.
    3. Build the phase prompt via the phase's ``build()`` function.
    4. Call the injected ``llm_call`` with ``(system_prompt, user_message)``.
    5. Route each ``tool_call`` returned by the LLM through ``TOOL_HANDLERS``.
       Unknown tool names are logged and skipped (no crash).
    6. Compute the next phase via ``router.decide_next_phase`` and persist all
       state files back to disk.

    Args:
        user_message: The user's text message for this turn. Passed to
            ``llm_call`` only — never logged as it may contain PII.
        project_slug: The project's directory name under ``projects_root``.
        projects_root: The root path containing all project directories.
        llm_call: A callable accepting ``(system_prompt, user_message)`` and
            returning an ``LLMResponse``. Tests inject a fake; production wires
            in a real provider adapter.

    Returns:
        The assistant's response string for this turn.

    Raises:
        FileNotFoundError: If
            ``<projects_root>/<project_slug>/_meta/intake_state.json`` does
            not exist.
        ValueError: If ``field_status.json`` contains corrupt JSON, or if
            the current phase cannot be resolved to a known phase (via
            ``_load_phase_prompt``) or persisted via ``save_current_phase``.
        AttributeError: If the resolved phase-prompt module has no
            ``build`` function.
    """
    turn_start = time.time()
    slug_root = projects_root / project_slug

    # ----- Step 1: load all state -----
    # NOTE: phase_driver.load_accumulator is lenient (logs + empty on corrupt
    # JSON) while load_field_status raises ValueError. The asymmetry is
    # acceptable: a corrupt accumulator can be recovered by /configs/reload,
    # but a corrupt field_status invalidates the entire turn — fail fast.
    intake_state = load_intake_state(slug_root / _META_DIR / _INTAKE_STATE_FILENAME)
    accumulator = load_accumulator(slug_root)
    try:
        field_status = load_field_status(slug_root / _META_DIR / _FIELD_STATUS_FILENAME)
    except ValueError as exc:
        logger.error(
            "phase_driver.field_status_corrupt",
            extra={
                "operation": "phase_driver.run_turn",
                "status": "failure",
                "error": str(exc),
                "project_slug": project_slug,
            },
            exc_info=True,
        )
        raise
    current_phase = load_current_phase(slug_root)

    # Record the user turn immediately so it is persisted even if the LLM call
    # fails.  Phase label is the phase that received this message.
    append_turn(
        slug_root,
        HistoryEntry(
            role="user",
            content=user_message,
            phase=current_phase,
            timestamp=utc_now_iso(),
        ),
    )

    logger.info(
        "phase_driver.run_turn started",
        extra={
            "operation": "phase_driver.run_turn",
            "status": "started",
            "project_slug": project_slug,
            "current_phase": current_phase,
        },
    )

    # ----- Step 2: filter pending fields and resolve phase prompt -----
    pending_fields = collect_pending_fields(current_phase, intake_state, field_status)
    pydantic_schemas = render_pydantic_classes(pending_fields)
    refs = cross_phase_references(accumulator)
    build = _load_phase_prompt(current_phase)
    system_prompt = build(pending_fields, pydantic_schemas, refs, intake_state)

    # ----- Step 3: LLM call -----
    llm_start = time.time()
    response = llm_call(system_prompt, user_message)
    llm_latency_ms = int((time.time() - llm_start) * 1000)
    # Point 12: log LLM call with phase, model, tokens, and tool call names
    logger.info(
        "phase_driver.llm_call",
        extra={
            "operation": "phase_driver.llm_call",
            "status": "success",
            "phase": current_phase,
            "latency_ms": llm_latency_ms,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "tool_calls": {
                "count": len(response.tool_calls),
                "names": [tc.name for tc in response.tool_calls],
            },
        },
    )

    # ----- Step 4: route tool calls -----
    for call in response.tool_calls:
        handler = TOOL_HANDLERS.get(call.name)
        if handler is None:
            # Point 13: log unsupported (rejected) tool call
            logger.warning(
                "phase_driver.tool_call_rejected",
                extra={
                    "operation": "phase_driver.tool_call_rejected",
                    "status": "failure",
                    "tool": call.name,
                    "tool_args": call.args,
                    "error": f"no handler registered for tool {call.name!r}",
                    "error_type": "KeyError",
                },
            )
            continue
        try:
            handler(call.args, intake_state, accumulator, field_status)
        except (KeyError, ValueError, AttributeError) as exc:
            # Handler-internal errors (missing args, unknown intake field,
            # unknown chat path) should not abort the turn — log and continue.
            # Point 13: log tool call that failed during execution
            logger.warning(
                "phase_driver.tool_call_rejected",
                extra={
                    "operation": "phase_driver.tool_call_rejected",
                    "status": "failure",
                    "tool": call.name,
                    "tool_args": call.args,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            continue

    # ----- Step 5: end-of-turn router -----
    next_phase = decide_next_phase(current_phase, intake_state, accumulator, field_status)

    # Record the assistant turn; phase label is the phase that produced the
    # response (current_phase, not next_phase).
    append_turn(
        slug_root,
        HistoryEntry(
            role="assistant",
            content=response.text,
            phase=current_phase,
            timestamp=utc_now_iso(),
        ),
    )

    # ----- Step 6: persist all state -----
    save_intake_state(
        slug_root / _META_DIR / _INTAKE_STATE_FILENAME, intake_state
    )
    save_accumulator(slug_root, accumulator)
    save_field_status(
        slug_root / _META_DIR / _FIELD_STATUS_FILENAME, field_status
    )
    if next_phase != current_phase:
        save_current_phase(slug_root, next_phase)
        logger.info(
            "phase_driver.transition",
            extra={
                "operation": "phase_driver.transition",
                "status": "success",
                "from_phase": current_phase,
                "to_phase": next_phase,
            },
        )

    total_latency_ms = int((time.time() - turn_start) * 1000)
    logger.info(
        "phase_driver.run_turn success",
        extra={
            "operation": "phase_driver.run_turn",
            "status": "success",
            "latency_ms": total_latency_ms,
            "project_slug": project_slug,
            "current_phase": current_phase,
            "next_phase": next_phase,
        },
    )

    return response.text


__all__ = [
    "ToolCall",
    "LLMResponse",
    "TOOL_HANDLERS",
    "load_accumulator",
    "save_accumulator",
    "load_current_phase",
    "save_current_phase",
    "collect_pending_fields",
    "render_pydantic_classes",
    "cross_phase_references",
    "run_turn",
]
