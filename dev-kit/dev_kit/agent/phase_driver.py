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
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, get_args, get_origin

from pydantic import BaseModel, ValidationError as PydanticValidationError

from dev_kit.agent._atomic_io import write_atomic_text
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
from dev_kit.agent.history import HistoryEntry, append_turn, load_history, utc_now_iso
from dev_kit.agent.tools import (
    add_routing_rule,
    add_subagent,
    add_tool,
    discover_mcp_tools,
    fetch_openapi_spec_from_url,
    parse_openapi_spec,
    update_config as tool_update_config,
    update_intake as tool_update_intake,
    update_subagent,
)
from dev_kit.agent.derived_fields import apply_derived_fields
from dev_kit.agent.renderer import render_all

logger = logging.getLogger(__name__)

_DEFAULT_PHASE = "tier"
_META_DIR = "_meta"
_ACCUMULATOR_FILENAME = "accumulator.json"
_CURRENT_PHASE_FILENAME = "current_phase.txt"
_INTAKE_STATE_FILENAME = "intake_state.json"
_FIELD_STATUS_FILENAME = "field_status.json"
_PHASE_PROGRESS_FILENAME = "phase_progress.json"

# After this many consecutive turns of zero progress in the same phase, the
# driver intervenes: pending fields with a FieldRule.default get force-written
# and marked answered, and any remaining no-default fields are also marked
# answered so the router can advance. Without this guard the LLM can gives up
# (says "I can't write that field", stops asking) and the wizard deadlocks —
# verified in the Akashvani Concierge E2E where the reach phase stalled with
# 2 pending fields the user couldn't even understand to set manually.
_PHASE_STALL_FORCE_THRESHOLD = int(
    os.environ.get("DEVKIT_PHASE_STALL_FORCE_THRESHOLD", "3")
)

# How many prior text turns from history.jsonl get echoed back to the LLM
# per turn. Tightened from 20 → 10 — at 20 the system prompt + 20 alternating
# user/assistant turns regularly pushed the request size to where a single
# turn cost as much as the entire rest of the wizard combined, and the
# extra context didn't help: each phase prompt already injects the
# cross-phase reference block (current model, NLU intents, knowledge
# filters, etc.) so the LLM has the canonical state without needing to
# re-read it from history. 10 turns is enough for the bot to remember
# the last 2-3 proposal+confirm cycles (which is all the conversational
# anchor the LLM needs at each phase). Override with DEVKIT_HISTORY_WINDOW
# if a deployment ever needs more.
_HISTORY_WINDOW = int(os.environ.get("DEVKIT_HISTORY_WINDOW", "10"))

# Safety cap on consecutive tool_use rounds within a single user turn.
# Each round = one full LLM call. Tightened 10 → 4 to slash the worst-
# case cost of a single turn (1 initial + N retries = N+1 LLM calls).
# In practice the LLM batches many tool_use blocks into one response —
# the round count is the number of times the model re-thinks AFTER
# seeing tool_results. 4 covers: initial proposal, retry on validation
# rejection, second retry with the corrected shape, and a final pass to
# emit user-facing text. If a turn legitimately needs more rounds (e.g.
# heavy workflow-graph creation) the LLM can chain tool calls within a
# single round; only error-recovery loops need multiple rounds. Tighter
# cap also bounds the credit burn when the LLM gets stuck retrying a
# broken write — earlier 10-round budget could spend ~10× a normal turn
# on a single hopeless tool-use loop.
_MAX_TOOL_ROUNDS = int(os.environ.get("DEVKIT_MAX_TOOL_ROUNDS", "4"))

# Maximum number of phase transitions to chain inside a SINGLE call to
# ``run_turn``. When a phase completes (e.g. tier intake captures the last
# binary flag), the router advances the wizard to the next relevant phase.
# Without inline continuation the user would have to send a do-nothing
# "ok continue" message just to see the new phase's first question — a UX
# wart we explicitly reject. Cap at 1 by default: the just-finished phase's
# closing text plus the new phase's first question(s) come back in a single
# assistant reply; further phases wait for the user's next real answer.
_MAX_PHASE_TRANSITIONS_PER_TURN = int(
    os.environ.get("DEVKIT_MAX_PHASE_TRANSITIONS_PER_TURN", "1")
)

# Synthetic user message that drives the inline continuation after a phase
# transition. The Anthropic API requires the message list to end on a user
# turn before producing an assistant reply; we cannot just swap the system
# prompt and re-run. This message is NEVER persisted to ``history.jsonl`` —
# only the resulting assistant text is — so it stays invisible to future
# turns. The instruction itself tells the model to skip any "ok, moving
# on" preamble and jump straight into the new phase's first question(s).
_CONTINUATION_PROMPT = (
    "[System note: the previous configuration step is complete and the "
    "conversation has now moved to a NEW phase. The updated system prompt "
    "above lists the pending fields for the new phase under "
    "'CURRENT PHASE — focus only on this'. Read those fields carefully — "
    "produce the FIRST question (or grouped questions per the phase's "
    "pacing plan) that moves at least one of those pending fields toward "
    "'answered'. Do NOT continue the topic of the prior user turn; that "
    "topic belonged to the finished phase. Do NOT acknowledge this system "
    "note. Do NOT ask about channels, languages, voice, or any value the "
    "project-creation form already captured. Go straight to the new phase's "
    "numbered question list as if this were a fresh turn.]"
)


# ---------------------------------------------------------------------------
# Driver-local types for the LLM response shape
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM.

    Attributes:
        name: Tool name (e.g., ``"update_intake"``, ``"update_config"``).
        args: Tool arguments as a plain dict.
        id: Provider-assigned ``tool_use_id`` used to pair this call with its
            ``tool_result`` block on the follow-up turn. ``None`` for fakes
            that don't model provider IDs — the driver synthesizes one.
    """

    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class LLMResponse:
    """Driver-local response shape returned by the injected ``llm_call``.

    Attributes:
        text: The assistant's text reply for this turn.
        tool_calls: Ordered list of tool calls the LLM emitted in this turn.
        model: Model identifier used for this call, if the provider exposes it.
        input_tokens: Number of input tokens consumed, if exposed by the provider.
        output_tokens: Number of output tokens generated, if exposed by the provider.
        stop_reason: Provider-reported reason the model stopped (e.g.
            ``"end_turn"``, ``"tool_use"``). The driver loops while this is
            ``"tool_use"`` so the model can react to ``tool_result`` blocks
            in the same turn.
        raw_content: The model's content blocks for this response, serialized
            into the Anthropic message-format dicts (``{"type": "text", ...}``,
            ``{"type": "tool_use", ...}``). Echoed back as the assistant turn
            in the next ``llm_call`` so the model sees what it just emitted.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    raw_content: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Banned-phrase lint for assistant replies
# ---------------------------------------------------------------------------

# Sentence-level patterns that expose internal machinery (schema paths,
# retry intents, debug fragments) to the end user. The Akashvani E2E
# surfaced each of these as real LLM output that confused the user. We
# strip the matching sentence — from the pattern through the next
# terminator — and leave the rest of the paragraph in place.
#
# Each pattern is case-insensitive and matches greedily up to the next
# `.`, `!`, `?`, or newline. Adding patterns here is cheaper than asking
# every phase prompt to forbid them individually; the lint is the last
# line of defence before history.jsonl persists the reply.
_BANNED_SENTENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bpath mismatch\b[^.!?\n]*[.!?\n]?"),
    re.compile(r"(?i)\b(?:let me|i'll|i will) (?:try again|retry|fix that)\b[^.!?\n]*[.!?\n]?"),
    re.compile(r"(?i)\bissue found:[^.!?\n]*[.!?\n]?"),
    re.compile(r"(?i)\bversion of the schema\b[^.!?\n]*[.!?\n]?"),
    re.compile(r"(?i)\bschema (?:rejects|doesn't allow|validation failed)\b[^.!?\n]*[.!?\n]?"),
    re.compile(r"(?i)\b(?:my apologies|i apologize|apologies)[,. ][^.!?\n]*[.!?\n]?"),
)


def _strip_banned_sentences(text: str) -> str:
    """Strip sentences matching internal-state-leak patterns.

    Args:
        text: Final assistant reply about to be persisted to history.

    Returns:
        The reply with any banned sentences removed and consecutive blank
        lines collapsed. Returns the input unchanged if no patterns match.
    """
    if not text:
        return text
    cleaned = text
    for pat in _BANNED_SENTENCE_PATTERNS:
        cleaned = pat.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


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
    write_atomic_text(path, json.dumps(accumulator, indent=2, ensure_ascii=False, sort_keys=True))


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
    write_atomic_text(path, phase)


# ---------------------------------------------------------------------------
# Phase-progress tracking (deadlock recovery)
# ---------------------------------------------------------------------------


def load_phase_progress(slug_root: Path) -> dict[str, Any]:
    """Read the per-project zero-progress counter for the current phase.

    The progress file persists between turns and is used by the driver to
    detect when the LLM has stalled — i.e. produced two or more consecutive
    turns in the same phase without flipping any pending chat field to
    ``"answered"``. After ``_PHASE_STALL_FORCE_THRESHOLD`` such turns the
    driver intervenes (see ``_apply_stall_recovery``).

    Args:
        slug_root: The project directory.

    Returns:
        A dict shaped ``{"phase": <str>, "consecutive_no_progress_turns": <int>}``.
        Returns a fresh ``{"phase": "", "consecutive_no_progress_turns": 0}``
        when the file is absent or corrupt — a corrupt counter file should
        never block the wizard, so the failure mode is "treat it as fresh".
    """
    path = slug_root / _META_DIR / _PHASE_PROGRESS_FILENAME
    if not path.exists():
        return {"phase": "", "consecutive_no_progress_turns": 0}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning(
            "load_phase_progress corrupt — resetting",
            extra={
                "operation": "phase_driver.load_phase_progress",
                "status": "failure",
                "path": str(path),
            },
        )
        return {"phase": "", "consecutive_no_progress_turns": 0}
    if not isinstance(data, dict):
        return {"phase": "", "consecutive_no_progress_turns": 0}
    return {
        "phase": str(data.get("phase", "")),
        "consecutive_no_progress_turns": int(data.get("consecutive_no_progress_turns", 0) or 0),
    }


def save_phase_progress(slug_root: Path, progress: dict[str, Any]) -> None:
    """Persist the per-project zero-progress counter.

    Args:
        slug_root: The project directory.
        progress: Dict with keys ``phase`` and ``consecutive_no_progress_turns``.
    """
    path = slug_root / _META_DIR / _PHASE_PROGRESS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic_text(path, json.dumps(progress, indent=2, ensure_ascii=False))


def _apply_stall_recovery(
    phase: str,
    intake_state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> list[str]:
    """Force-resolve every pending chat field in ``phase`` so the router can advance.

    Used as a last-resort escape when the LLM has spent
    ``_PHASE_STALL_FORCE_THRESHOLD`` consecutive turns in the same phase
    without flipping any pending field to ``"answered"``. The Akashvani
    Concierge E2E hit exactly this state: the LLM tried to write a
    consent-purpose field whose mirror schema didn't accept it, eventually
    gave up, and the reach phase deadlocked with the user typing "hello"
    in confusion. Recovery rules per pending field:

    - If the FieldRule has a non-None ``default``: write that default into
      the accumulator and mark ``answered``.
    - If the FieldRule has ``auto_answer=True`` or ``default=None`` with no
      writable value: just mark ``answered`` (the runtime will use its
      own default).
    - Either way the field exits ``pending`` so ``decide_next_phase`` can
      walk forward to the next relevant phase.

    Args:
        phase: The stalled phase name (e.g. ``"reach"``).
        intake_state: For applies_if evaluation — only applicable fields
            are considered "pending" for this phase.
        accumulator: Per-block YAML dicts; mutated in place when a default
            is written.
        field_status: Status registry; mutated in place.

    Returns:
        A list of ``full_path`` strings the recovery flipped to ``answered``,
        for the structured stall log.
    """
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES  # noqa: PLC0415
    from dev_kit.agent.path_ops import set_path  # noqa: PLC0415
    from dev_kit.agent.skeleton import eval_expr  # noqa: PLC0415

    forced: list[str] = []
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat" or rule.phase != phase:
            continue
        if not eval_expr(rule.applies_if, intake_state):
            continue
        status = field_status.get(full_path, "pending")
        if status == "answered":
            continue
        block, relative_path = full_path.split(".", 1)
        if rule.default is not None:
            accumulator.setdefault(block, {})
            set_path(accumulator[block], relative_path, rule.default)
        else:
            # No FieldRule default. Write a type-shaped placeholder so the
            # downstream deploy-validate doesn't fail with
            # ``Field required`` for chat fields the LLM never wrote. The
            # placeholder is intentionally trivial — a domain author can
            # edit it in the YAML directly if they care. Without this
            # fallback the Akashvani Concierge E2E ended with
            # ``voice.raya.voice_id: Field required`` because stall
            # recovery marked it answered without writing.
            placeholder = _stall_recovery_placeholder(full_path, rule)
            if placeholder is not None:
                accumulator.setdefault(block, {})
                set_path(accumulator[block], relative_path, placeholder)
        field_status[full_path] = "answered"
        forced.append(full_path)
    return forced


# Field-specific placeholders the wizard writes during stall recovery
# when the LLM never wrote a value AND the FieldRule has no default.
# Keep this map narrow and intentional — only fields known to be
# required at runtime + commonly stalled in E2E should land here.
# The exact values are intentionally obviously-test data so a human
# review of the rendered YAML catches them before deploy.
_STALL_PLACEHOLDERS: dict[str, Any] = {
    # First English Raya voice; the user-edited E2E hit a stall when
    # the LLM proposed Priyanka (Hindi) but never wrote voice_id.
    "reach_layer.channels.voice.raya.voice_id": "0f24fb66-e495-4781-9e84-1224aa7dacde",
    # Match the voice_id placeholder above (Nayra is en-in).
    "reach_layer.channels.voice.raya.stt_language": "en-in",
    "reach_layer.channels.voice.raya.tts_language": "en-in",
}


def _stall_recovery_placeholder(full_path: str, rule: "FieldRule") -> Any:
    """Return a type-shaped placeholder for a stalled required-no-default field.

    Looks the path up in ``_STALL_PLACEHOLDERS`` first (exact match).
    Falls back to a value derived from the rule's pydantic_class when
    available, and finally to ``None`` (caller skips the write).

    Args:
        full_path: Block-prefixed path of the stalled field.
        rule: The FieldRule for that path.

    Returns:
        The placeholder to write, or ``None`` to skip the write.
    """
    return _STALL_PLACEHOLDERS.get(full_path)


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


# Map block name → dotted import path for its dev-kit mirror schema module.
# The mirror modules host the Pydantic classes referenced by each
# ``FieldRule.pydantic_class`` string; we resolve those names through this
# table so the LLM sees the actual source for every class touched by a
# pending field.
_BLOCK_TO_DOMAIN_MODULE: dict[str, str] = {
    "agent_core": "dev_kit.schemas.domain.agent_core",
    "trust_layer": "dev_kit.schemas.domain.trust_layer",
    "knowledge_engine": "dev_kit.schemas.domain.knowledge_engine",
    "memory_layer": "dev_kit.schemas.domain.memory_layer",
    "action_gateway": "dev_kit.schemas.domain.action_gateway",
    "reach_layer": "dev_kit.schemas.domain.reach_layer",
    "observability_layer": "dev_kit.schemas.domain.observability_layer",
}


def _extract_basemodel_types(annotation: Any) -> Iterator[type[BaseModel]]:
    """Yield every Pydantic ``BaseModel`` subclass referenced by an annotation.

    Walks the type expression (handling ``Optional[X]``, ``list[X]``,
    ``dict[K, V]``, ``Union[A, B]``, …) and returns each ``BaseModel``
    found at any depth. Plain types (``str``, ``int``, etc.) are skipped.

    Args:
        annotation: A type annotation captured from a Pydantic
            ``FieldInfo.annotation``.

    Yields:
        Each ``BaseModel`` subclass embedded in the annotation. Order is
        not guaranteed; deduplication is the caller's responsibility.
    """
    if annotation is None:
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    origin = get_origin(annotation)
    if origin is None:
        return
    for arg in get_args(annotation):
        yield from _extract_basemodel_types(arg)


def _collect_model_closure(
    root: type[BaseModel], collected: dict[str, type[BaseModel]]
) -> None:
    """Walk ``root`` and add every transitively-referenced Pydantic model to ``collected``.

    Args:
        root: Starting Pydantic model class.
        collected: Mutated in place. Keyed by class name so duplicates from
            different fields are dropped naturally.
    """
    if root.__name__ in collected:
        return
    collected[root.__name__] = root
    for _name, field_info in root.model_fields.items():
        for nested in _extract_basemodel_types(field_info.annotation):
            _collect_model_closure(nested, collected)


def render_pydantic_classes(pending_fields: list[tuple[str, FieldRule]]) -> str:
    """Render the Pydantic class closure for ``pending_fields`` as raw source.

    For every pending field, resolves ``FieldRule.pydantic_class`` to the
    actual class in the matching ``dev_kit.schemas.domain.<block>`` module,
    walks every transitively-referenced ``BaseModel`` (e.g.
    ``Optional[UserStateModel]`` on ``ConversationSection``), and renders
    each class's source via ``inspect.getsource``.

    The output is what the LLM reads to learn the exact field names,
    types, defaults, and validators it must respect when calling
    ``update_config``. Without this, the model hallucinates field names
    (the GoGuide regression: ``consent_declined_message`` /
    ``blocked_output_message`` instead of the real ``consent_decline_ack``
    / ``output_blocked_message``), each write is rejected by the mirror
    validator, and the wizard stalls.

    Args:
        pending_fields: List of ``(path, rule)`` tuples for the current phase.
            ``path`` must start with the block name (e.g.
            ``"agent_core.conversation.consent_message"``) so we can pick
            the right schema module.

    Returns:
        Concatenated class source (``\\n\\n`` between classes) for every
        Pydantic model touched by the pending fields and their referenced
        submodels. Empty string when ``pending_fields`` is empty or when
        no rules carry a usable ``pydantic_class``.
    """
    if not pending_fields:
        return ""

    # Closure of classes to render, keyed by class name so siblings that
    # share submodels (e.g. multiple conversation fields all pulling in
    # UserStateModel) appear only once in the output.
    collected: dict[str, type[BaseModel]] = {}

    for path, rule in pending_fields:
        cls_name = getattr(rule, "pydantic_class", None)
        if not cls_name:
            continue
        block = path.split(".", 1)[0]
        module_path = _BLOCK_TO_DOMAIN_MODULE.get(block)
        if module_path is None:
            logger.warning(
                "render_pydantic_classes.unknown_block",
                extra={
                    "operation": "phase_driver.render_pydantic_classes",
                    "status": "skipped",
                    "block": block,
                    "path": path,
                    "pydantic_class": cls_name,
                },
            )
            continue
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            logger.warning(
                "render_pydantic_classes.import_failed",
                extra={
                    "operation": "phase_driver.render_pydantic_classes",
                    "status": "failure",
                    "module": module_path,
                    "error": str(exc),
                },
            )
            continue
        cls = getattr(module, cls_name, None)
        if cls is None or not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            logger.warning(
                "render_pydantic_classes.class_not_found",
                extra={
                    "operation": "phase_driver.render_pydantic_classes",
                    "status": "skipped",
                    "module": module_path,
                    "pydantic_class": cls_name,
                },
            )
            continue
        _collect_model_closure(cls, collected)

    if not collected:
        return ""

    snippets: list[str] = []
    for cls_name, cls in collected.items():
        try:
            snippets.append(inspect.getsource(cls))
        except (OSError, TypeError) as exc:
            # OSError: source file unavailable (e.g. dynamic class).
            # TypeError: built-in or otherwise un-inspectable.
            logger.warning(
                "render_pydantic_classes.getsource_failed",
                extra={
                    "operation": "phase_driver.render_pydantic_classes",
                    "status": "failure",
                    "class": cls_name,
                    "error": str(exc),
                },
            )

    return "\n\n".join(snippets)


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

# Dispatch table — 9 canonical tools (design §6's 8-tool baseline plus
# fetch_openapi_spec_from_url, restored so users with a real-world API doc
# do not have to paste a multi-thousand-line spec into chat).
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
    "fetch_openapi_spec_from_url": fetch_openapi_spec_from_url,
    "discover_mcp_tools": discover_mcp_tools,
}


# ---------------------------------------------------------------------------
# LLM call + tool dispatch helpers
# ---------------------------------------------------------------------------


def _call_llm_logged(
    llm_call: Callable[[str, list[dict[str, Any]]], LLMResponse],
    system_prompt: str,
    messages: list[dict[str, Any]],
    phase: str,
    *,
    round_index: int,
    slug_root: Optional[Path] = None,
) -> LLMResponse:
    """Invoke ``llm_call`` and emit the structured ``phase_driver.llm_call`` log.

    Args:
        llm_call: The injected provider adapter.
        system_prompt: System prompt for this phase.
        messages: Current Anthropic-format messages list to send.
        phase: The active wizard phase, recorded on the log entry.
        round_index: 0 for the initial LLM call; 1+ for each tool-use loop
            iteration. Surfaced on the log so the tool-use loop's depth is
            visible in production logs.
        slug_root: When provided, the per-call token-usage record is also
            appended to ``<slug_root>/_meta/token_usage.jsonl`` so an
            operator can sum total credits used per E2E without
            scraping logs. None disables the file write (e.g. for unit
            tests).

    Returns:
        The ``LLMResponse`` returned by the adapter.
    """
    llm_start = time.time()
    response = llm_call(system_prompt, messages)
    latency_ms = int((time.time() - llm_start) * 1000)
    logger.info(
        "phase_driver.llm_call",
        extra={
            "operation": "phase_driver.llm_call",
            "status": "success",
            "phase": phase,
            "round": round_index,
            "latency_ms": latency_ms,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "stop_reason": response.stop_reason,
            "tool_calls": {
                "count": len(response.tool_calls),
                "names": [tc.name for tc in response.tool_calls],
            },
        },
    )
    if slug_root is not None:
        _append_token_usage(
            slug_root,
            phase=phase,
            round_index=round_index,
            latency_ms=latency_ms,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            stop_reason=response.stop_reason,
            tool_call_count=len(response.tool_calls),
        )
    return response


def _append_token_usage(
    slug_root: Path,
    *,
    phase: str,
    round_index: int,
    latency_ms: int,
    model: Optional[str],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    stop_reason: Optional[str],
    tool_call_count: int,
) -> None:
    """Append a single LLM-call record to ``<slug_root>/_meta/token_usage.jsonl``.

    One line per LLM round (initial call + every tool-use retry counts
    as its own row). Format is deliberately a JSON-lines file so an
    operator can sum totals trivially:

        jq -s 'map(.input_tokens) | add' _meta/token_usage.jsonl
        jq -s 'map(.output_tokens) | add' _meta/token_usage.jsonl

    Or with the helper that ships in ``scripts/total_tokens.py``.

    File writes are best-effort: any IO error is logged at WARNING and
    swallowed so a transient disk problem can't crash an LLM turn.

    Args:
        slug_root: Project directory; the file lives at
            ``<slug_root>/_meta/token_usage.jsonl``.
        phase: Active wizard phase when this call happened.
        round_index: 0 for initial, 1+ for tool-use retries.
        latency_ms: LLM-call latency in milliseconds.
        model: Model identifier reported by the provider, or None.
        input_tokens / output_tokens: Per-call token usage, or None
            when the provider doesn't expose it.
        stop_reason: Provider's stop_reason for the response.
        tool_call_count: Number of tool_use blocks the model emitted.
    """
    path = slug_root / _META_DIR / "token_usage.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": utc_now_iso(),
            "phase": phase,
            "round": round_index,
            "latency_ms": latency_ms,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "tool_call_count": tool_call_count,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(
            "phase_driver.token_usage_write_failed",
            extra={
                "operation": "phase_driver._append_token_usage",
                "status": "failure",
                "error": str(exc),
                "path": str(path),
            },
        )


def _deep_merge_defaults(target: dict, source: dict) -> None:
    """Recursively merge ``source`` into ``target`` without overwriting.

    Used to fold skeleton-rendered defaults into the live accumulator at
    tier completion. Cascade-set values (already in ``target``) win;
    skeleton fills the gaps. The merge is depth-first so a nested
    skeleton value like ``agent.provider="anthropic"`` lands even when
    ``target['agent']`` already exists with another sub-key like
    ``ask_for_consent=true`` set by the predetermined cascade.

    Lists and scalars in ``target`` are NEVER replaced — only dict-typed
    branches recurse; everything else is left alone if it already has
    a value.

    Mutates ``target`` in place.

    Args:
        target: The dict to fill in. Must be a real dict (not None).
        source: The dict of defaults to merge under existing keys.
    """
    for key, src_val in source.items():
        if key not in target:
            target[key] = src_val
            continue
        tgt_val = target[key]
        if isinstance(tgt_val, dict) and isinstance(src_val, dict):
            _deep_merge_defaults(tgt_val, src_val)
        # else: target already has a non-dict value; leave it alone
        # (cascade wins over skeleton baseline).


def _dispatch_tool_calls(
    tool_calls: list[ToolCall],
    intake_state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
    round_index: int,
    *,
    turn_tool_history: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Route each ``ToolCall`` through ``TOOL_HANDLERS`` and build tool_results.

    Unknown tool names are logged and skipped. Handler-internal errors
    (``KeyError``/``ValueError``/``AttributeError``) are logged and surfaced
    as an ``{"ok": False, "error": ...}`` tool_result so the model sees the
    rejection on its next turn.

    Args:
        tool_calls: The model's tool_use blocks for this round.
        intake_state: Mutated in place by handlers that touch intake fields.
        accumulator: Mutated in place by handlers that touch domain config.
        field_status: Mutated in place by handlers that flip field statuses.
        round_index: The tool-use loop iteration; used to synthesize stable
            ``tool_use_id`` values when the adapter (or a test fake) leaves
            ``ToolCall.id`` unset.
        turn_tool_history: List of tool names called earlier in the same
            user turn (across prior rounds). Used to enforce the
            ``parse_openapi_spec → user confirmation → add_tool`` pacing
            rule. When the LLM calls ``add_tool`` for a REST tool in the
            same turn it called ``parse_openapi_spec``, the wizard
            rejects with a humanised error — the user MUST get to see
            the extracted operations + response-field projection before
            tools are registered. None disables the check (e.g. for
            unit tests that drive the dispatch directly).

    Returns:
        A list of Anthropic ``tool_result`` content blocks paired one-to-one
        with the input ``tool_calls`` — ready to be wrapped in a user-role
        message on the next LLM round.
    """
    results: list[dict[str, Any]] = []
    for idx, call in enumerate(tool_calls):
        tool_use_id = call.id or f"toolu_dev_{round_index}_{idx}"

        # Enforce the parse → confirm → register pacing for REST tools.
        # The tools-phase prompt already instructs the LLM to wait, but
        # the model routinely skips that step (verified in 3 consecutive
        # Akashvani Concierge E2E runs). Rejecting at the dispatch
        # layer forces the LLM to surface the extracted operations
        # to the user, who can then edit the projection before tools
        # are registered.
        if (
            turn_tool_history is not None
            and call.name == "add_tool"
            and any(t in turn_tool_history for t in ("parse_openapi_spec", "fetch_openapi_spec_from_url"))
        ):
            spec = call.args.get("spec") if isinstance(call.args, dict) else None
            spec_is_rest = isinstance(spec, dict) and spec.get("type") != "mcp"
            if spec_is_rest:
                logger.warning(
                    "phase_driver.tool_call_rejected",
                    extra={
                        "operation": "phase_driver.tool_call_rejected",
                        "status": "failure",
                        "tool": call.name,
                        "error": "add_tool called in same turn as parse_openapi_spec",
                        "error_type": "ProjectionConfirmationRequired",
                    },
                )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps({
                        "ok": False,
                        "error": (
                            "STOP. You called add_tool in the same turn you "
                            "parsed the OpenAPI spec. Do NOT register tools "
                            "yet. Do NOT call add_tool again this turn. "
                            "Do NOT claim the tools are registered (they "
                            "are not).\n\n"
                            "Your reply to the user this turn MUST contain "
                            "a Markdown table with one row per parsed "
                            "operation and these columns: **Operation**, "
                            "**Method + Path**, **Params**, **Response "
                            "fields the bot will read** (the projection "
                            "list you propose to put in "
                            "`response.projection.fields`).\n\n"
                            "End your reply with a SINGLE confirmation "
                            "question: 'Here's what I parsed. Does the "
                            "projection list look right, or would you like "
                            "to add, drop, or rename any fields?'\n\n"
                            "On the NEXT turn, after the user replies, "
                            "call add_tool once per operation with the "
                            "confirmed projection in "
                            "response.projection.fields. Not before."
                        ),
                    }),
                    "is_error": True,
                })
                if turn_tool_history is not None:
                    turn_tool_history.append(call.name)
                continue

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
            results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(
                    {"ok": False, "error": f"unknown tool {call.name!r}"}
                ),
                "is_error": True,
            })
            continue
        try:
            handler_result = handler(call.args, intake_state, accumulator, field_status)
        except (KeyError, ValueError, AttributeError, PydanticValidationError) as exc:
            # Handler-internal errors (missing args, unknown intake field,
            # unknown chat path, pydantic shape rejection) should not abort
            # the turn — log, surface the error to the model as a
            # tool_result, and continue. ``PydanticValidationError`` is
            # included explicitly because in Pydantic v2 it does NOT
            # inherit from ``ValueError`` (unlike v1); without it the
            # FastAPI handler would 500 mid-turn and the LLM would never
            # see a paired ``tool_result``, which Anthropic then rejects
            # with "tool_use ids found without tool_result blocks".
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
            results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps({"ok": False, "error": str(exc)}),
                "is_error": True,
            })
            continue
        # Tool ran. Pass its dict payload through to the model as JSON text.
        results.append({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(handler_result if handler_result is not None else {"ok": True}),
        })
        # Record the tool name so subsequent rounds in the same turn can
        # enforce same-turn pacing rules (e.g. add_tool after parse).
        if turn_tool_history is not None:
            turn_tool_history.append(call.name)
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_turn(
    user_message: str,
    project_slug: str,
    *,
    projects_root: Path,
    llm_call: Callable[[str, list[dict[str, Any]]], LLMResponse],
) -> str:
    """Run a single wizard turn end-to-end.

    Sequence (per design §6):

    1. Load intake_state, accumulator, field_status, current_phase from disk.
    2. Filter pending/needs_re_asking chat fields for the current phase.
    3. Build the phase prompt via the phase's ``build()`` function.
    4. Build the messages list: sliding-window prior text history from
       ``history.jsonl`` plus the new user message.
    5. Call the injected ``llm_call`` with ``(system_prompt, messages)``. If
       the response's ``stop_reason`` is ``"tool_use"``, dispatch each
       ``tool_call`` through ``TOOL_HANDLERS``, append the assistant's raw
       content blocks and the synthesized ``tool_result`` blocks to the
       messages list, then call the LLM again — looping until the model
       produces a text response or ``_MAX_TOOL_ROUNDS`` is hit. When the
       response is a final text response that still carries tool calls (the
       legacy fake-LLM pattern used by tests), those tools are still routed
       once so behavior is identical to a single-turn run.
    6. Compute the next phase via ``router.decide_next_phase`` and persist all
       state files back to disk.

    Args:
        user_message: The user's text message for this turn. Persisted to
            ``history.jsonl`` and appended to the messages list — never
            logged as it may contain PII.
        project_slug: The project's directory name under ``projects_root``.
        projects_root: The root path containing all project directories.
        llm_call: A callable accepting ``(system_prompt, messages)`` and
            returning an ``LLMResponse``. ``messages`` follows the Anthropic
            ``messages.create`` format: a list of ``{"role", "content"}``
            dicts where ``content`` is either a string (text turn) or a list
            of content blocks (``tool_use``/``tool_result`` rounds). Tests
            inject a fake; production wires in the Anthropic adapter.

    Returns:
        The assistant's final response string for this turn (the text from
        the LLM call after the tool-use loop terminates).

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

    # Snapshot field_status before the turn so we can detect zero-progress
    # turns (LLM ran but didn't flip any pending field to "answered"). The
    # snapshot is a shallow dict copy — values are strings, so this is
    # cheap.
    field_status_before = dict(field_status)
    phase_progress = load_phase_progress(slug_root)

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

    # ----- Step 2: build initial messages list -----
    # Echo prior text turns from history.jsonl as a sliding window so the model
    # has cross-turn memory. The user turn just appended above is included.
    prior_history = load_history(slug_root)
    windowed = prior_history[-_HISTORY_WINDOW:]
    messages: list[dict[str, Any]] = [
        {"role": entry.role, "content": entry.content} for entry in windowed
    ]

    # ----- Step 3: phase processing loop (with inline continuation) -----
    # The loop body processes ONE phase: build its system prompt, run the
    # LLM + tool-use loop, dispatch tool calls, populate the skeleton on
    # tier completion, and ask the router whether the phase advanced. If
    # it did, we re-enter the loop for the new phase WITHOUT returning to
    # the user — the new phase's opening question(s) get appended to the
    # same assistant reply. ``_MAX_PHASE_TRANSITIONS_PER_TURN`` caps the
    # chain so a misconfigured prompt cannot run all 11 phases in one go.
    assistant_text_parts: list[str] = []
    transitions = 0
    initial_phase = current_phase
    final_phase = current_phase

    # Per-turn telemetry. Aggregated across every LLM round and every
    # inline-continuation phase so the end-of-turn summary log lets a
    # developer answer "what did this turn actually do?" without
    # trawling 200 lines of per-call output.
    tool_call_counts: dict[str, int] = {}
    tool_reject_counts: dict[str, int] = {}
    total_llm_calls = 0
    # Names of every tool the LLM has called this turn, across all
    # phases and all tool_use rounds. Enables same-turn pacing rules
    # — currently used to reject ``add_tool`` calls that follow
    # ``parse_openapi_spec`` without a user-confirmation turn in
    # between (forces the projection-step UX).
    turn_tool_history: list[str] = []

    while True:
        # Build system prompt for the phase currently active.
        pending_fields = collect_pending_fields(current_phase, intake_state, field_status)
        pydantic_schemas = render_pydantic_classes(pending_fields)
        refs = cross_phase_references(accumulator)
        build = _load_phase_prompt(current_phase)
        system_prompt = build(pending_fields, pydantic_schemas, refs, intake_state)

        # LLM call + tool-use loop. Mirrors the legacy ConversationEngine
        # pattern: keep calling while the model requests more tools,
        # echoing the assistant's raw blocks and synthesized tool_results
        # back as the conversation grows.
        response = _call_llm_logged(
            llm_call, system_prompt, messages, current_phase, round_index=0,
            slug_root=slug_root,
        )
        total_llm_calls += 1
        tool_rounds = 0
        while True:
            # Always dispatch this response's tool calls — keeps
            # single-shot behavior identical for callers (and test fakes)
            # that emit tool calls without setting stop_reason="tool_use".
            tool_results = _dispatch_tool_calls(
                response.tool_calls, intake_state, accumulator, field_status, tool_rounds,
                turn_tool_history=turn_tool_history,
            )
            # Telemetry: tally what the LLM tried this round and which
            # dispatches were rejected (results with is_error=True).
            for call in response.tool_calls:
                tool_call_counts[call.name] = tool_call_counts.get(call.name, 0) + 1
            for call, result in zip(response.tool_calls, tool_results):
                if isinstance(result, dict) and result.get("is_error"):
                    tool_reject_counts[call.name] = tool_reject_counts.get(call.name, 0) + 1
            if response.stop_reason != "tool_use":
                break
            if tool_rounds >= _MAX_TOOL_ROUNDS:
                logger.warning(
                    "phase_driver.tool_loop_capped",
                    extra={
                        "operation": "phase_driver.tool_loop_capped",
                        "status": "skipped",
                        "rounds": tool_rounds,
                        "max_rounds": _MAX_TOOL_ROUNDS,
                        "phase": current_phase,
                    },
                )
                break
            # Echo the assistant's raw blocks + synthesized tool_results so the
            # next LLM call sees the same context the model itself produced.
            if response.raw_content:
                messages.append({"role": "assistant", "content": response.raw_content})
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            tool_rounds += 1
            response = _call_llm_logged(
                llm_call, system_prompt, messages, current_phase, round_index=tool_rounds,
                slug_root=slug_root,
            )
            total_llm_calls += 1

        # Capture this phase's user-facing text.
        if response.text:
            assistant_text_parts.append(response.text)

        # Populate skeleton when tier completes (must run BEFORE
        # decide_next_phase so language has populated chat fields to
        # gate on).
        #
        # Trigger marker: `language_normalisation.enabled` is a defaulted
        # chat field with no `invalidated_by` entries, so the router
        # cascade never touches it during tier — its presence in
        # field_status reliably signals that skeleton has already run.
        # The earlier `not field_status` gate was buggy because the
        # cascade DOES populate field_status during tier (it marks fields
        # `needs_re_asking` when binary flags flip), so by tier-complete
        # field_status was non-empty and skeleton was skipped — the
        # defaulted fields stayed `pending` and the language phase
        # deadlocked.
        skeleton_marker = (
            "agent_core.preprocessing.language_normalisation.enabled"
        )
        if intake_state.completed and skeleton_marker not in field_status:
            from dev_kit.agent.skeleton import build_skeleton  # noqa: PLC0415
            skeleton_acc, skeleton_fs = build_skeleton(intake_state)
            # Deep-merge skeleton values into the live accumulator —
            # shallow setdefault at the section level (e.g.
            # `accumulator['agent_core'].setdefault('agent', ...)`) would
            # drop sub-field defaults whenever the cascade had already
            # added one key under that section (e.g. cascade writes
            # `agent.ask_for_consent`, then skeleton's
            # `agent.provider = 'anthropic'` is silently lost). The
            # cascade's keys win; the skeleton fills gaps.
            for block, block_data in skeleton_acc.items():
                if block not in accumulator:
                    accumulator[block] = {}
                _deep_merge_defaults(accumulator[block], block_data)
            # Use setdefault for field_status — cascade-set
            # `needs_re_asking` entries from the tier turn must win over
            # skeleton's `pending`/`answered` baseline.
            for path, status in skeleton_fs.items():
                field_status.setdefault(path, status)
            logger.info(
                "phase_driver.skeleton_populated",
                extra={
                    "operation": "phase_driver.skeleton_populated",
                    "status": "success",
                    "field_count": len(field_status),
                },
            )

        # Ask the router whether this phase is done and which phase comes next.
        next_phase = decide_next_phase(current_phase, intake_state, accumulator, field_status)
        if next_phase == current_phase:
            final_phase = current_phase  # phase still in progress; turn complete.
            break

        # Phase transitioned. Decide whether to continue inline.
        if transitions >= _MAX_PHASE_TRANSITIONS_PER_TURN:
            # Cap hit. Do NOT commit the pending transition and do NOT run
            # another LLM call — stay at the current phase. The next user
            # message will drive whatever further advancement is needed.
            # Committing without an LLM call would leave the user in a phase
            # they have never seen any text from.
            logger.info(
                "phase_driver.transition_cap_reached",
                extra={
                    "operation": "phase_driver.transition_cap_reached",
                    "status": "skipped",
                    "phase": current_phase,
                    "pending_next_phase": next_phase,
                    "transitions": transitions,
                    "cap": _MAX_PHASE_TRANSITIONS_PER_TURN,
                },
            )
            final_phase = current_phase
            break

        logger.info(
            "phase_driver.transition",
            extra={
                "operation": "phase_driver.transition",
                "status": "success",
                "from_phase": current_phase,
                "to_phase": next_phase,
            },
        )

        # Within the cap — set up the in-memory messages for the new phase's
        # LLM call and loop again. Neither of these messages gets persisted
        # to history.jsonl: the user [continue] trigger is a transport
        # detail, and the per-phase assistant text is concatenated into a
        # single history entry below.
        messages.append({"role": "assistant", "content": response.text})
        messages.append({"role": "user", "content": _CONTINUATION_PROMPT})
        transitions += 1
        current_phase = next_phase
        final_phase = next_phase

    # ----- Step 4: persist assistant reply + state -----
    # When an inline continuation ran (transitions > 0), drop the closing
    # text from the just-completed phase(s) and only surface the FINAL
    # phase's reply to the user. The earlier phase produced a wrap-up
    # message ("All knowledge-retrieval rules are set"), then the
    # continuation phase produced its own opening proposal — concatenating
    # both creates a wall of text that buries decisions and forces the
    # user to scroll. The next phase's proposal IS the implicit
    # confirmation that the prior phase succeeded.
    if transitions > 0 and assistant_text_parts:
        final_text = assistant_text_parts[-1]
    else:
        final_text = "\n\n".join(assistant_text_parts)
    final_text = _strip_banned_sentences(final_text)
    append_turn(
        slug_root,
        HistoryEntry(
            role="assistant",
            content=final_text,
            phase=final_phase,
            timestamp=utc_now_iso(),
        ),
    )

    # ----- Step 4b: deadlock recovery -----
    # Compute how many fields this turn flipped from a non-answered status
    # to "answered". If zero AND the final phase is still incomplete AND we
    # already stalled in this phase for the previous turn(s), force-write
    # FieldRule defaults so the router can advance on the next call to
    # decide_next_phase.
    answered_delta = sum(
        1 for path, status in field_status.items()
        if status == "answered" and field_status_before.get(path) != "answered"
    )
    if answered_delta > 0 or final_phase != phase_progress.get("phase"):
        # Progress made OR we landed in a different phase than the prior
        # turn — reset the stall counter and pin it to the final phase.
        phase_progress = {
            "phase": final_phase,
            "consecutive_no_progress_turns": 0,
        }
    else:
        phase_progress["consecutive_no_progress_turns"] += 1
        logger.warning(
            "phase_driver.zero_progress_turn",
            extra={
                "operation": "phase_driver.zero_progress_turn",
                "status": "skipped",
                "phase": final_phase,
                "consecutive_no_progress_turns": phase_progress["consecutive_no_progress_turns"],
                "project_slug": project_slug,
            },
        )
        if phase_progress["consecutive_no_progress_turns"] >= _PHASE_STALL_FORCE_THRESHOLD:
            # The LLM has stalled — apply recovery.  This either writes a
            # FieldRule.default into the accumulator (the common case) or
            # marks the field answered without writing (when no default
            # exists; the runtime falls back to its own default at boot).
            forced_paths = _apply_stall_recovery(
                final_phase, intake_state, accumulator, field_status
            )
            logger.error(
                "phase_driver.stall_recovery",
                extra={
                    "operation": "phase_driver.stall_recovery",
                    "status": "success",
                    "phase": final_phase,
                    "forced_paths": forced_paths[:25],
                    "forced_count": len(forced_paths),
                    "stall_turns": phase_progress["consecutive_no_progress_turns"],
                    "project_slug": project_slug,
                },
            )
            phase_progress = {
                "phase": final_phase,
                "consecutive_no_progress_turns": 0,
            }
            # Now ask the router again — the freshly-forced answers should
            # let it walk forward to the next relevant phase. `decide_next_phase`
            # is imported at module level; do NOT re-import locally (the
            # earlier reference inside the while-loop above would then
            # shadow the global and crash with UnboundLocalError).
            next_after_recovery = decide_next_phase(
                final_phase, intake_state, accumulator, field_status
            )
            if next_after_recovery != final_phase:
                final_phase = next_after_recovery
                # Run the LLM once more, this turn, in the new phase so
                # the user immediately sees the new phase's opening
                # questions. Without this the user gets the prior
                # stalled assistant reply, has to send a dummy "next" or
                # "ok" message, and only THEN sees the new phase's first
                # question. End-user testing in the Akashvani Concierge
                # run confirmed that's confusing — users assume the
                # wizard is stuck.
                pending_fields = collect_pending_fields(
                    final_phase, intake_state, field_status
                )
                pydantic_schemas = render_pydantic_classes(pending_fields)
                refs = cross_phase_references(accumulator)
                recovery_build = _load_phase_prompt(final_phase)
                recovery_system_prompt = recovery_build(
                    pending_fields, pydantic_schemas, refs, intake_state
                )
                # Same continuation-prompt trick as inline phase advancement
                # uses: tell the LLM the conversation moved on and to
                # produce the FIRST question of the new phase without
                # acknowledging this system note.
                messages.append(
                    {"role": "assistant", "content": response.text or ""}
                )
                messages.append(
                    {"role": "user", "content": _CONTINUATION_PROMPT}
                )
                recovery_response = _call_llm_logged(
                    llm_call,
                    recovery_system_prompt,
                    messages,
                    final_phase,
                    round_index=0,
                    slug_root=slug_root,
                )
                total_llm_calls += 1
                # Run the new phase's tool-use loop too — the LLM might
                # call update_config / add_subagent etc. as part of its
                # opening turn.
                recovery_rounds = 0
                while True:
                    # CAPTURE the tool_results — previously the recovery
                    # loop discarded them, which meant the next LLM call
                    # sent an assistant message with tool_use blocks but
                    # NO paired user message carrying tool_result blocks.
                    # Anthropic rejects that with HTTP 400
                    # "tool_use ids found without tool_result blocks
                    # immediately after". Mirror the main loop pattern
                    # at the top of run_turn so the recovery branch
                    # produces well-formed message sequences.
                    recovery_tool_results = _dispatch_tool_calls(
                        recovery_response.tool_calls,
                        intake_state,
                        accumulator,
                        field_status,
                        recovery_rounds,
                        turn_tool_history=turn_tool_history,
                    )
                    for call in recovery_response.tool_calls:
                        tool_call_counts[call.name] = tool_call_counts.get(call.name, 0) + 1
                    for call, result in zip(
                        recovery_response.tool_calls, recovery_tool_results
                    ):
                        if isinstance(result, dict) and result.get("is_error"):
                            tool_reject_counts[call.name] = (
                                tool_reject_counts.get(call.name, 0) + 1
                            )
                    if recovery_response.stop_reason != "tool_use":
                        break
                    if recovery_rounds >= _MAX_TOOL_ROUNDS:
                        break
                    # Echo the assistant's raw content blocks AND the
                    # synthesised tool_results before the next call —
                    # same pattern as the main loop.
                    if recovery_response.raw_content:
                        messages.append(
                            {"role": "assistant", "content": recovery_response.raw_content}
                        )
                    if recovery_tool_results:
                        messages.append(
                            {"role": "user", "content": recovery_tool_results}
                        )
                    recovery_rounds += 1
                    recovery_response = _call_llm_logged(
                        llm_call,
                        recovery_system_prompt,
                        messages,
                        final_phase,
                        round_index=recovery_rounds,
                        slug_root=slug_root,
                    )
                    total_llm_calls += 1
                # Replace the stalled text with the recovered phase's
                # opening — the user should only see the latter, not
                # the "wait for the system" prose the LLM produced
                # before recovery fired.
                if recovery_response.text:
                    assistant_text_parts = [recovery_response.text]

    save_intake_state(slug_root / _META_DIR / _INTAKE_STATE_FILENAME, intake_state)
    save_accumulator(slug_root, accumulator)
    save_field_status(slug_root / _META_DIR / _FIELD_STATUS_FILENAME, field_status)
    save_phase_progress(slug_root, phase_progress)
    if final_phase != initial_phase:
        save_current_phase(slug_root, final_phase)

    # ----- Step 5: render YAML files from the up-to-date accumulator -----
    # Without this, the wizard's chat tool calls update accumulator.json
    # but the on-disk per-block YAML files stay frozen at the placeholder
    # state written at project creation. Re-render on every turn so the
    # YAML files always reflect the latest answers — the user can open
    # them in their IDE and see live progress, and the deploy step picks
    # them up without any extra refresh.
    _render_yaml_after_turn(slug_root, accumulator, intake_state, project_slug)

    total_latency_ms = int((time.time() - turn_start) * 1000)
    logger.info(
        "phase_driver.run_turn success",
        extra={
            "operation": "phase_driver.run_turn",
            "status": "success",
            "latency_ms": total_latency_ms,
            "project_slug": project_slug,
            "initial_phase": initial_phase,
            "final_phase": final_phase,
            "transitions": transitions,
            "llm_calls": total_llm_calls,
            "tool_calls": dict(tool_call_counts),
            "tool_rejects": dict(tool_reject_counts),
            "tool_call_total": sum(tool_call_counts.values()),
            "tool_reject_total": sum(tool_reject_counts.values()),
        },
    )

    return final_text


def _render_yaml_after_turn(
    slug_root: Path,
    accumulator: dict[str, dict],
    intake_state: IntakeState,
    project_slug: str,
) -> None:
    """Apply derived fields and write per-block YAML files for ``slug_root``.

    Called at the end of every chat turn so the on-disk YAML files
    reflect the latest accumulator state. Errors are caught and logged
    rather than propagated — a half-configured project mid-flow may not
    pass strict runtime dry-run, but that should not crash the chat turn.

    Safe behavior:

    - ``apply_derived_fields`` is idempotent — repeated calls write the
      same values.
    - On the host (``RUNTIME_SCHEMAS is None`` in ``renderer.py``),
      ``render_all`` skips the strict dry-run entirely and just writes
      YAML with advisory ``# WARNINGS:`` headers for blocks that fail
      the mirror schema.
    - Inside the Docker image, the dry-run can raise; we log and
      continue so the user's chat turn is not lost.

    Args:
        slug_root: Project directory (e.g. ``<projects_root>/<slug>``).
        accumulator: Per-block domain dicts.
        intake_state: Source for derived-field evaluation.
        project_slug: For log correlation only.
    """
    render_start = time.time()
    try:
        apply_derived_fields(accumulator, intake_state)
    except Exception as exc:  # noqa: BLE001 — derived eval is best-effort
        logger.warning(
            "phase_driver.render_yaml.derived_failed",
            extra={
                "operation": "phase_driver.render_yaml",
                "status": "failure",
                "project_slug": project_slug,
                "stage": "apply_derived_fields",
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        # Continue — derived field failures should not block YAML rendering.

    try:
        statuses = render_all(slug_root, accumulator, intake_state)
    except Exception as exc:  # noqa: BLE001 — partial drafts may fail dry-run
        logger.warning(
            "phase_driver.render_yaml.failed",
            extra={
                "operation": "phase_driver.render_yaml",
                "status": "failure",
                "project_slug": project_slug,
                "stage": "render_all",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "latency_ms": int((time.time() - render_start) * 1000),
            },
            exc_info=True,
        )
        return

    logger.info(
        "phase_driver.render_yaml",
        extra={
            "operation": "phase_driver.render_yaml",
            "status": "success",
            "project_slug": project_slug,
            "block_statuses": statuses,
            "latency_ms": int((time.time() - render_start) * 1000),
        },
    )


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
