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
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from dev_kit.agent.accumulator import PHASES, ConfigAccumulator
from dev_kit.agent.checkpoints import build_summary, list_checkpoints, restore_checkpoint, save_checkpoint
from dev_kit.agent.errors import ConversationError
from dev_kit.agent.prompts.base import build_system_prompt
from dev_kit.agent.renderer import render_all
from dev_kit.agent.tools import TOOL_DEFINITIONS, ToolHandler

_MODEL = _os.environ.get("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS = int(_os.environ.get("DEVKIT_MAX_TOKENS", "4096"))
_HISTORY_WINDOW = int(_os.environ.get("DEVKIT_HISTORY_WINDOW", "20"))  # Max recent messages to send per turn

logger = logging.getLogger(__name__)

_llm_retry = retry(
    retry=retry_if_exception_type(
        (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError)
    ),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
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
            "phase": "overview",
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
                )

        meta_path = self._project_path / "_meta" / "project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                self._state["project_meta"] = meta
                self._state["phase"] = meta.get("current_phase", "overview")
            except json.JSONDecodeError as exc:
                logger.warning(
                    "project_meta_load_failed",
                    extra={
                        "operation": "conversation._load",
                        "status": "failure",
                        "error": str(exc),
                        "path": str(meta_path),
                    },
                )

        # Restore conversation history from checkpoints
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
                )
                raise ConversationError(f"LLM call failed: {exc}") from exc

        self._history.append({"role": "user", "content": user_message})
        self._state["phase_changed"] = None
        self._state["rollback_to"] = None

        system = self._build_system_prompt()
        messages = self._history[-_HISTORY_WINDOW:]
        config_updates: list[dict] = []
        checkpoint_created: str | None = None

        try:
            response = await _call_llm(system, messages)
        except ConversationError:
            self._history.pop()  # roll back the appended user message
            raise

        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self._tool_handler.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
                    config_updates.append({"tool": block.name, "input": block.input})

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
                phase_list = PHASES
                phase_number = phase_list.index(old_phase) + 1 if old_phase in phase_list else 0
                phase_label = f"{phase_number:02d}_{old_phase}"
                save_checkpoint(self._project_path, phase_label, self.accumulator, self._history[:-2])
                checkpoint_created = phase_label
                self._state["phase"] = new_phase
                self._state["phase_changed"] = None
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
        render_all(self._project_path, self.accumulator)

        return {
            "reply": reply,
            "phase": self._state["phase"],
            "config_updates": config_updates,
            "checkpoint_created": checkpoint_created,
            "graph": self.accumulator.get_workflow_graph(),
        }
