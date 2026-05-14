"""
dev-kit/dev_kit/agent/conversation.py

ConversationEngine — manages the chat loop with Claude, dispatches tool calls,
maintains conversation history, and persists state after each turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os as _os
from pathlib import Path

import anthropic

from dev_kit.agent import phase_driver
from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.errors import ConversationError
from dev_kit.agent.phase_driver import LLMResponse, ToolCall, load_current_phase
from dev_kit.agent.tools import DEVKIT_TOOL_SCHEMAS

_MODEL = _os.environ.get("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS = int(_os.environ.get("DEVKIT_MAX_TOKENS", "4096"))
_HISTORY_WINDOW = int(_os.environ.get("DEVKIT_HISTORY_WINDOW", "20"))  # Max recent messages to send per turn

logger = logging.getLogger(__name__)


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
        self._load()

    def _load(self) -> None:
        """Load persisted accumulator and project meta from disk if they exist.

        Logs a warning and falls back to defaults if either file is corrupt.
        """
        acc_path = self._project_path / "_meta" / "accumulator.json"
        if acc_path.exists():
            try:
                self.accumulator = ConfigAccumulator.from_dict(json.loads(acc_path.read_text()))
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

    async def chat(self, user_message: str) -> dict:
        """Process a user message and return the agent's response.

        Delegates to ``phase_driver.run_turn`` (the deterministic wizard path).
        All projects created via the new project form will have
        ``_meta/intake_state.json``; projects that pre-date the new wizard are
        no longer supported.

        Args:
            user_message: The user's input text.

        Returns:
            Dict with keys ``reply``, ``phase``, ``config_updates`` (always []),
            ``checkpoint_created`` (None), and ``graph`` ({}).

        Raises:
            ConversationError: If ``_meta/intake_state.json`` is missing (legacy
                project), or if ``phase_driver.run_turn`` fails.
        """
        intake_path = self._project_path / "_meta" / "intake_state.json"
        if not intake_path.exists():
            raise ConversationError(
                "This project was created with an older version of the dev-kit and "
                "is no longer supported. Please create a new project using the project "
                "creation form to continue."
            )
        return await self._chat_new_wizard(user_message)

    def _build_phase_driver_llm_call(self):
        """Return a synchronous llm_call wrapper for phase_driver.run_turn.

        Builds a fresh ``anthropic.Anthropic`` (sync) client per call. Cheap to
        construct, and lets us bridge ``phase_driver.run_turn``'s synchronous
        ``llm_call`` callable from the async ``chat()`` method via
        ``asyncio.to_thread``.

        Returns:
            A callable ``(system_prompt, user_message) -> LLMResponse``.
        """
        def _llm_call(system_prompt: str, user_message: str) -> LLMResponse:
            sync_client = anthropic.Anthropic()
            response = sync_client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                tools=DEVKIT_TOOL_SCHEMAS,
                timeout=30.0,
            )
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
                elif getattr(block, "type", None) == "tool_use":
                    tool_calls.append(
                        ToolCall(name=block.name, args=dict(block.input))
                    )
            usage = getattr(response, "usage", None)
            return LLMResponse(
                text="\n".join(text_parts),
                tool_calls=tool_calls,
                model=getattr(response, "model", None),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )

        return _llm_call

    async def _chat_new_wizard(self, user_message: str) -> dict:
        """Delegate the turn to phase_driver.run_turn (deterministic wizard).

        Wraps the sync ``llm_call`` in ``asyncio.to_thread`` so this method
        stays awaitable from FastAPI handlers. Appends both the user and
        assistant messages to ``self._history`` so the existing
        ``GET /history`` endpoint keeps working.

        Args:
            user_message: The user's input text.

        Returns:
            Dict with ``reply``, ``phase``, ``config_updates`` (empty),
            ``checkpoint_created`` (None), and ``graph`` (empty).

        Raises:
            ConversationError: If phase_driver.run_turn fails for any reason.
        """
        slug = self._project_path.name
        projects_root = self._project_path.parent
        logger.info(
            "conversation.chat.new_wizard",
            extra={
                "operation": "conversation.chat.new_wizard",
                "status": "started",
                "slug": slug,
            },
        )
        try:
            response_text = await asyncio.to_thread(
                phase_driver.run_turn,
                user_message,
                slug,
                projects_root=projects_root,
                llm_call=self._build_phase_driver_llm_call(),
            )
        except Exception as exc:
            logger.error(
                "conversation.chat.new_wizard_failed",
                extra={
                    "operation": "conversation.chat.new_wizard",
                    "status": "failure",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "slug": slug,
                },
                exc_info=True,
            )
            raise ConversationError(f"phase_driver.run_turn failed: {exc}") from exc

        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": response_text})
        self._save_history()

        return {
            "reply": response_text,
            "phase": load_current_phase(self._project_path),
            "config_updates": [],
            "checkpoint_created": None,
            "graph": {},
        }

