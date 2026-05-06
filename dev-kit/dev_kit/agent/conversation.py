"""
dev-kit/dev_kit/agent/conversation.py

ConversationEngine — manages the chat loop with Claude, dispatches tool calls,
maintains conversation history, and persists state after each turn.
"""
from __future__ import annotations

import json
import logging
import os as _os
import time
from pathlib import Path

import anthropic
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from dev_kit.agent.accumulator import PHASES, ConfigAccumulator
from dev_kit.agent.checkpoints import build_summary, list_checkpoints, restore_checkpoint, save_checkpoint
from dev_kit.agent.errors import ConversationError
from dev_kit.agent.prompts.base import build_system_prompt
from dev_kit.agent.renderer import render_all
from dev_kit.agent.tools import TOOL_DEFINITIONS, ToolHandler

_MODEL = _os.environ.get("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS = int(_os.environ.get("DEVKIT_MAX_TOKENS", "4096"))
_HISTORY_WINDOW = int(_os.environ.get("DEVKIT_HISTORY_WINDOW", "20"))  # Max recent messages to send per turn
# Circuit breaker for the LLM tool-call loop. Normal turns use ~5-20 tool
# calls (multiple update_config + set_phase + checkpoints). 50 is generous
# enough to never trip in normal use, low enough to halt a runaway loop
# (e.g. LLM ignores VALIDATION_SECTION_STALE and keeps retrying).
_MAX_TOOL_ROUNDS = int(_os.environ.get("DEVKIT_MAX_TOOL_ROUNDS", "50"))

# Stalled-progress early-exit. Once the per-section retry counter caps and
# both update_config (returns SECTION_STALE) and set_phase (returns
# PHASE_ADVANCE_BLOCKED) are rejecting, the LLM has only one valid move —
# produce a text response asking the user. If it keeps calling tools
# instead, every round returns a rejection. Detect that pattern and break
# out so the user isn't waiting for the absolute 50-round ceiling.
_STALLED_ROUNDS_THRESHOLD = int(_os.environ.get("DEVKIT_STALLED_ROUNDS_THRESHOLD", "4"))

_REJECTION_PREFIXES = (
    "VALIDATION_ERROR",
    "VALIDATION_FAILED_AFTER",
    "VALIDATION_SECTION_STALE",
    "PHASE_ADVANCE_BLOCKED",
    "ERROR ",
    "ERROR:",
    "ERROR—",  # em dash
    "ERROR —",
)


def _is_rejection(result: str) -> bool:
    """Return True if a tool_result string is a structured rejection.

    Used by the conversation loop to detect "no-progress" rounds where
    every tool call comes back as a validation error, a STALE rejection,
    a blocked phase advance, or a generic ERROR. Successful writes
    ("ok: updated …") and free-form tool successes return False.
    """
    if not isinstance(result, str):
        return False
    head = result.lstrip()
    return any(head.startswith(p) for p in _REJECTION_PREFIXES)

logger = logging.getLogger(__name__)

_llm_retry = retry(
    retry=retry_if_exception_type(
        (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError)
    ),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


class ConversationEngine:
    """Manages one project's conversation with Claude.

    Holds message history, the config accumulator, and mutable engine
    state (current phase, pending phase transitions). All tool calls are
    dispatched synchronously; only the Claude API call is async.

    Args:
        project_path: Root directory of the project (configs/<slug>/).
        client: Anthropic AsyncAnthropic client.
    """

    def __init__(self, project_path: Path, client: "anthropic.AsyncAnthropic") -> None:
        self._project_path = project_path
        self._client = client
        self._history: list[dict] = []
        self._state: dict = {
            "phase": "tier",
            "phase_changed": None,
            "rollback_to": None,
            "project_meta": {},
        }
        self.accumulator = ConfigAccumulator()
        self._tool_handler = ToolHandler(self.accumulator, self._state)
        self._load()

    def _load(self) -> None:
        """Load persisted accumulator and project meta from disk if they exist.

        Logs a warning and falls back to defaults if either file is corrupt.
        """
        acc_path = self._project_path / "_meta" / "accumulator.json"
        if acc_path.exists():
            try:
                self.accumulator = ConfigAccumulator.from_dict(json.loads(acc_path.read_text()))
                self._tool_handler = ToolHandler(self.accumulator, self._state)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning(
                    "accumulator_load_failed",
                    extra={
                        "operation": "conversation._load",
                        "status": "failure",
                        "error": str(exc),
                        "path": str(acc_path),
                    },
                    exc_info=True,
                )

        meta_path = self._project_path / "_meta" / "project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                self._state["project_meta"] = meta
                self._state["phase"] = meta.get("current_phase", "tier")
            except json.JSONDecodeError as exc:
                logger.warning(
                    "project_meta_load_failed",
                    extra={
                        "operation": "conversation._load",
                        "status": "failure",
                        "error": str(exc),
                        "path": str(meta_path),
                    },
                    exc_info=True,
                )

        # Restore conversation history — prefer the persisted history file over
        # checkpoint reconstruction, since checkpoints only capture phase boundaries.
        history_path = self._project_path / "_meta" / "history.json"
        if history_path.exists():
            try:
                self._history = json.loads(history_path.read_text())
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "history_load_failed",
                    extra={
                        "operation": "conversation._load",
                        "status": "failure",
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                self._history = self._load_history_from_checkpoints()
        else:
            self._history = self._load_history_from_checkpoints()

    def _load_history_from_checkpoints(self) -> list[dict]:
        """Load and concatenate conversation history from all checkpoint history.json files.

        Only loads messages with string content (user text and assistant text).
        Tool_use and tool_result messages are excluded because they can cause
        invalid_request_error when the history window slices mid-exchange.
        The LLM gets prior context via checkpoint summaries in the system prompt.

        Returns:
            Combined text-only message history from all checkpoints in phase order.
        """
        checkpoints_dir = self._project_path / "_meta" / "checkpoints"
        if not checkpoints_dir.exists():
            return []
        history: list[dict] = []
        for phase_dir in sorted(checkpoints_dir.iterdir()):
            if not phase_dir.is_dir():
                continue
            history_file = phase_dir / "history.json"
            if history_file.exists():
                try:
                    phase_history = json.loads(history_file.read_text())
                    if isinstance(phase_history, list):
                        for msg in phase_history:
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                history.append(msg)
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "checkpoint_history_load_failed",
                        extra={
                            "operation": "conversation._load_history_from_checkpoints",
                            "status": "failure",
                            "error": str(exc),
                            "path": str(history_file),
                        },
                        exc_info=True,
                    )
        if history:
            logger.info(
                "history_restored_from_checkpoints",
                extra={
                    "operation": "conversation._load",
                    "status": "success",
                    "message_count": len(history),
                },
            )
        return history

    def _save_history(self) -> None:
        """Persist the full conversation history to disk.

        Saves every turn (user + assistant + tool exchanges) so the UI
        can restore the complete conversation after a devkit restart.
        Non-serializable entries are silently skipped.
        """
        history_path = self._project_path / "_meta" / "history.json"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            history_path.write_text(json.dumps(self._history, ensure_ascii=False, indent=2, default=str))
        except (TypeError, ValueError) as exc:
            logger.warning(
                "history_save_failed",
                extra={"operation": "conversation._save_history", "status": "failure", "error": str(exc)},
                exc_info=True,
            )

    def _save_accumulator(self) -> None:
        """Persist the current accumulator state to disk."""
        acc_path = self._project_path / "_meta" / "accumulator.json"
        acc_path.parent.mkdir(parents=True, exist_ok=True)
        acc_path.write_text(json.dumps(self.accumulator.to_dict(), ensure_ascii=False, indent=2))

    def _save_project_meta(self) -> None:
        """Persist current phase to project.json."""
        meta_path = self._project_path / "_meta" / "project.json"
        meta = self._state.get("project_meta", {})
        meta["current_phase"] = self._state["phase"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    def _get_checkpoint_summaries(self) -> list[str]:
        """Load summary text from all completed phase checkpoints."""
        return [cp["summary"] for cp in list_checkpoints(self._project_path) if cp["summary"]]

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the current phase and accumulator state."""
        meta = self._state.get("project_meta", {})
        available_tools = [t["id"] for t in self.accumulator.get_action_gateway_tools()]
        return build_system_prompt(
            project_name=meta.get("name", ""),
            project_slug=meta.get("slug", ""),
            project_description=meta.get("description", ""),
            accumulator=self.accumulator,
            phase=self._state["phase"],
            checkpoint_summaries=self._get_checkpoint_summaries(),
            available_tools=available_tools or None,
        )

    async def chat(self, user_message: str) -> dict:
        """Process a user message and return the agent's response.

        Calls Claude, dispatches any tool calls, saves state, and re-renders
        YAML config files.

        Args:
            user_message: The user's input text.

        Returns:
            Dict with keys: reply (str), phase (str), config_updates (list),
            checkpoint_created (str | None), graph (dict).

        Raises:
            ConversationError: If the Anthropic API call fails after retries.
        """
        async def _call_llm(system: str, messages: list) -> object:
            start = time.time()
            try:
                resp = await _llm_retry(self._client.messages.create)(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    timeout=30.0,
                )
                logger.info(
                    "llm_call",
                    extra={
                        "operation": "conversation.chat.llm_call",
                        "status": "success",
                        "latency_ms": int((time.time() - start) * 1000),
                        "phase": self._state["phase"],
                        "model": resp.model,
                        "input_tokens": resp.usage.input_tokens,
                        "output_tokens": resp.usage.output_tokens,
                    },
                )
                return resp
            except Exception as exc:
                logger.error(
                    "llm_call_failed",
                    extra={
                        "operation": "conversation.chat.llm_call",
                        "status": "failure",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                    exc_info=True,
                )
                raise ConversationError(f"LLM call failed: {exc}") from exc

        self._history.append({"role": "user", "content": user_message})
        self._state["phase_changed"] = None
        self._state["rollback_to"] = None
        # Reset per-section validation retry counters so the budget is fresh
        # for this turn — within-turn tool retries keep climbing until the cap.
        self.accumulator.reset_validation_attempts()

        system = self._build_system_prompt()
        messages = self._history[-_HISTORY_WINDOW:]
        config_updates: list[dict] = []
        checkpoint_created: str | None = None

        try:
            response = await _call_llm(system, messages)
        except ConversationError:
            self._history.pop()  # roll back the appended user message
            raise

        tool_rounds = 0
        stalled_rounds = 0   # consecutive rounds with all-rejection tool_results
        while response.stop_reason == "tool_use":
            tool_rounds += 1
            if tool_rounds > _MAX_TOOL_ROUNDS:
                # Circuit breaker: stop the loop even if the LLM keeps
                # requesting tools. Append a final user-side note so the
                # next LLM call must produce a text response, then break.
                logger.warning(
                    "devkit.conversation.tool_loop_capped",
                    extra={
                        "operation": "conversation.chat",
                        "status": "tool_loop_capped",
                        "rounds": tool_rounds - 1,
                        "max_rounds": _MAX_TOOL_ROUNDS,
                        "phase": self._state.get("phase"),
                    },
                )
                self._history.append({
                    "role": "user",
                    "content": (
                        f"SYSTEM: tool-call loop cap reached ({_MAX_TOOL_ROUNDS} "
                        f"rounds). Stop calling tools and produce a text response "
                        f"summarising progress and any unresolved issues. The user "
                        f"will guide the next step."
                    ),
                })
                response = await _call_llm(system, self._history[-_HISTORY_WINDOW:])
                break

            tool_results = []
            round_all_rejected = True
            round_had_tool_use = False
            for block in response.content:
                if block.type == "tool_use":
                    round_had_tool_use = True
                    result_text = self._tool_handler.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
                    config_updates.append({"tool": block.name, "input": block.input})
                    if not _is_rejection(result_text):
                        round_all_rejected = False

            # Stalled-progress detector: when the per-section caps are hit
            # AND set_phase is also blocked, every tool call comes back as
            # a rejection and the LLM's only valid move is a text response.
            # If it keeps calling tools anyway, break out early instead of
            # letting the loop drift to _MAX_TOOL_ROUNDS.
            if round_had_tool_use and round_all_rejected:
                stalled_rounds += 1
            else:
                stalled_rounds = 0
            if stalled_rounds >= _STALLED_ROUNDS_THRESHOLD:
                logger.warning(
                    "devkit.conversation.stalled_loop_break",
                    extra={
                        "operation": "conversation.chat",
                        "status": "stalled_loop_break",
                        "rounds": tool_rounds,
                        "stalled_rounds": stalled_rounds,
                        "phase": self._state.get("phase"),
                    },
                )
                # Persist the assistant + tool_results from THIS round so
                # the next LLM call sees the same context, then inject the
                # system instruction and force a text response.
                serialized_content = [
                    blk.model_dump() if hasattr(blk, "model_dump") else blk
                    for blk in response.content
                ]
                self._history.append({"role": "assistant", "content": serialized_content})
                self._history.append({"role": "user", "content": tool_results})
                self._history.append({
                    "role": "user",
                    "content": (
                        f"SYSTEM: detected {stalled_rounds} consecutive tool-call "
                        f"rounds with only validation/STALE/BLOCKED rejections — no "
                        f"successful writes. Stop calling tools and reply to the user "
                        f"as text. Explain which fields couldn't be auto-configured "
                        f"and ask the user to either change values, choose a different "
                        f"option, or skip the affected section. The user must guide "
                        f"the next step."
                    ),
                })
                response = await _call_llm(system, self._history[-_HISTORY_WINDOW:])
                break

            serialized_content = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in response.content
            ]
            self._history.append({"role": "assistant", "content": serialized_content})
            self._history.append({"role": "user", "content": tool_results})

            # Handle phase transition
            if self._state["phase_changed"]:
                old_phase = self._state["phase"]
                new_phase = self._state["phase_changed"]
                slug = self._state.get("project_meta", {}).get("slug", "")
                phase_list = PHASES
                phase_number = phase_list.index(old_phase) + 1 if old_phase in phase_list else 0
                phase_label = f"{phase_number:02d}_{old_phase}"
                save_checkpoint(self._project_path, phase_label, self.accumulator, self._history[:-2])
                logger.info(
                    "devkit.conversation.checkpoint_saved",
                    extra={
                        "operation": "conversation.checkpoint_save",
                        "status": "success",
                        "slug": slug,
                        "phase": phase_label,
                    },
                )
                checkpoint_created = phase_label
                self._state["phase"] = new_phase
                self._state["phase_changed"] = None
                logger.info(
                    "devkit.conversation.phase_transition",
                    extra={
                        "operation": "conversation.phase_transition",
                        "status": "success",
                        "slug": slug,
                        "to_phase": new_phase,
                    },
                )
                system = self._build_system_prompt()

            # Handle rollback requested by tool
            if self._state["rollback_to"]:
                requested_phase = self._state["rollback_to"]
                self._state["rollback_to"] = None
                try:
                    restored_acc, _ = restore_checkpoint(self._project_path, requested_phase)
                    self.accumulator = restored_acc
                    self._tool_handler._acc = restored_acc
                    self._history = []
                    self._state["phase"] = requested_phase.split("_", 1)[-1] if "_" in requested_phase else requested_phase
                    logger.info(
                        "checkpoint_restored_via_tool",
                        extra={
                            "operation": "conversation.chat.rollback",
                            "status": "success",
                            "phase": requested_phase,
                        },
                    )
                except FileNotFoundError:
                    logger.warning(
                        "checkpoint_not_found",
                        extra={
                            "operation": "conversation.chat.rollback",
                            "status": "failure",
                            "error": f"checkpoint '{requested_phase}' not found",
                        },
                    )

            try:
                response = await _call_llm(system, self._history[-_HISTORY_WINDOW:])
            except ConversationError:
                # Roll back the two entries appended this iteration (assistant + tool_results)
                if len(self._history) >= 2:
                    self._history.pop()  # tool_results user message
                    self._history.pop()  # assistant content block
                raise

        # Extract final text reply
        reply = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        self._history.append({"role": "assistant", "content": reply})

        # Persist state and re-render configs
        self._save_accumulator()
        self._save_project_meta()
        self._save_history()
        _render_slug = self._state.get("project_meta", {}).get("slug", "")
        logger.info(
            "devkit.conversation.render_all",
            extra={
                "operation": "conversation.render_all",
                "status": "start",
                "slug": _render_slug,
            },
        )
        _render_start = time.time()
        render_all(self._project_path, self.accumulator)
        logger.info(
            "devkit.conversation.render_all",
            extra={
                "operation": "conversation.render_all",
                "status": "success",
                "slug": _render_slug,
                "elapsed_ms": int((time.time() - _render_start) * 1000),
            },
        )

        return {
            "reply": reply,
            "phase": self._state["phase"],
            "config_updates": config_updates,
            "checkpoint_created": checkpoint_created,
            "graph": self.accumulator.get_workflow_graph(),
        }
