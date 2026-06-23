"""
reach_layer/cli/src/cli_reach.py

CLIReachLayer — CLI channel adapter for the Reach Layer.

Extends TextChannelBase. Runs as a REPL: reads user lines from stdin,
submits them to Agent Core, subscribes to SSE events, and prints sentence
output to stdout as tokens arrive.

Default assembly_mode for this channel is ``session`` (see
dev-kit/dpg/reach_layer.yaml): each stdin line is a complete utterance
that Agent Core's TurnAssembler packages into a turn. The stream of
SentenceEvents back to stdout gives low-latency, sentence-by-sentence
delivery — the same path a voice channel would use, which makes the CLI
a realistic surrogate for voice flows during development.

Design decisions not in the spec:

1. Separate tasks for receive / subscribe: Input reading and SSE reception
   run as concurrent asyncio tasks. The receive task reads one line at a
   time from stdin, calls submit_input(), then waits for the matching
   DoneEvent on the subscribe task before prompting again. This preserves
   a conversational turn-taking feel on the CLI without requiring the user
   to wait for a full response before typing (they can still Ctrl-C to
   barge in and cancel the active turn).

2. Escalation notice: was_escalated / was_tool_used flags on DoneEvent are
   surfaced as short tags in the prompt line rather than full-line
   notices. This keeps the REPL compact.

3. Verbose mode: SignalEvents are normally suppressed; when --verbose is
   set they are printed as dim status lines so developers can see pipeline
   stage transitions.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from typing import Optional

from reach_layer_base import (
    DoneEvent,
    SentenceEvent,
    SignalEvent,
    TextChannelBase,
)

logger = logging.getLogger(__name__)


class CLIReachLayer(TextChannelBase):
    """CLI channel adapter backed by stdin/stdout.

    Args:
        config: Full merged reach_layer config dict.
        session_id: Optional override; a UUID4 is generated if absent.
        user_id: Optional persistent user identifier.
        verbose: If True, SignalEvents are printed as status lines.
    """

    def __init__(
        self,
        config: dict,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        super().__init__(config, channel_name="cli")

        cli_cfg = (
            config.get("reach_layer", {}).get("channels", {}).get("cli", {})
            if config
            else {}
        )
        # UI strings — safe defaults. Kept in config so domains can localise.
        self._prompt: str = cli_cfg.get("prompt", "You: ")
        self._agent_prefix: str = cli_cfg.get("agent_prefix", "Agent: ")

        self._session_id: str = session_id or str(uuid.uuid4())
        self._user_id: Optional[str] = user_id
        self._verbose: bool = verbose

        # Event synchronisation between the receive loop and the subscribe
        # loop. The receive task must wait for DoneEvent before re-prompting.
        self._turn_complete = asyncio.Event()
        self._turn_complete.set()  # idle at startup
        self._active = False

        logger.info(
            "reach_layer.cli.init",
            extra={
                "operation": "cli_reach.init",
                "status": "success",
                "session_id": self._session_id,
                "assembly_mode": self.assembly_mode,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks required by ReachLayerBase
    # ------------------------------------------------------------------

    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """Log session start. CLI has no per-session resources to allocate."""
        logger.info(
            "reach_layer.cli.session_start",
            extra={
                "operation": "cli_reach.on_session_start",
                "status": "success",
                "session_id": session_id,
                "user_id": user_id or "anonymous",
            },
        )

    async def on_session_end(self, session_id: str) -> None:
        """Tear down the HTTP client and print a session-end banner."""
        await self.close()
        sys.stdout.write("\nSession ended.\n")
        sys.stdout.flush()
        logger.info(
            "reach_layer.cli.session_end",
            extra={
                "operation": "cli_reach.on_session_end",
                "status": "success",
                "session_id": session_id,
            },
        )

    # ------------------------------------------------------------------
    # TextChannelBase.run_loop
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Run the CLI REPL until stdin is closed or the user aborts.

        Submits each stdin line, subscribes to SSE events, and prints sentence
        output as it arrives. The subscribe task is started lazily on the
        first successful submit.
        """
        await self.on_session_start(self._session_id, self._user_id or "")

        # GH-149: in session mode, open the SSE subscription eagerly when we
        # know the user_id so Agent Core can push the entry subagent's
        # opening_phrase before the user types anything. Without user_id we
        # fall back to the legacy lazy-subscribe-on-first-input flow, since
        # opening_phrase emission on the server side is gated on user_id.
        subscribe_task: Optional[asyncio.Task] = None
        if self.assembly_mode == "session" and self._user_id:
            subscribe_task = asyncio.create_task(self._consume_events())

        try:
            while True:
                try:
                    line = await asyncio.to_thread(self._read_line)
                except (EOFError, KeyboardInterrupt):
                    break

                if line is None:
                    break
                if not line:
                    continue

                # Lazy fallback: no user_id → subscribe on first input like before.
                if self.assembly_mode == "session" and subscribe_task is None:
                    subscribe_task = asyncio.create_task(self._consume_events())

                self._turn_complete.clear()
                self._active = True
                start = time.time()

                try:
                    result = await self.submit_input(
                        self._session_id, line, self._user_id
                    )
                except Exception as e:
                    self._turn_complete.set()
                    self._active = False
                    sys.stdout.write(f"\n[Error: {type(e).__name__}: {e}]\n")
                    sys.stdout.flush()
                    continue

                if self.assembly_mode == "direct":
                    # Synchronous path: submit_input already returned TurnResult.
                    self._render_direct_result(result, start)
                    self._turn_complete.set()
                    self._active = False
                else:
                    # Session path: wait for DoneEvent on the subscribe task.
                    await self._turn_complete.wait()

        finally:
            if subscribe_task is not None:
                subscribe_task.cancel()
                try:
                    await subscribe_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.on_session_end(self._session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_line(self) -> Optional[str]:
        """Blocking stdin read. Returns stripped line, or None on EOF."""
        sys.stdout.write(self._prompt)
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            return None
        return line.rstrip("\n").strip()

    async def _consume_events(self) -> None:
        """Consume SSE events for the session and render them to stdout."""
        try:
            async for event in self.subscribe_events(self._session_id, user_id=self._user_id):
                self._render_event(event)
                if isinstance(event, DoneEvent):
                    # Signal the receive loop that it can re-prompt.
                    self._turn_complete.set()
                    self._active = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "reach_layer.cli.subscribe_error",
                extra={
                    "operation": "cli_reach.consume_events",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "session_id": self._session_id,
                },
            )
            self._turn_complete.set()
            self._active = False

    def _render_event(self, event) -> None:
        """Write a single stream event to stdout."""
        if isinstance(event, SentenceEvent):
            # First sentence of a turn gets the agent prefix; subsequent
            # sentences are appended inline so they read as continuous text.
            if event.sentence_index == 0:
                sys.stdout.write(f"\n{self._agent_prefix}")
            sys.stdout.write(event.text)
            if not event.text.endswith((" ", "\n")):
                sys.stdout.write(" ")
            sys.stdout.flush()
        elif isinstance(event, SignalEvent):
            if self._verbose:
                sys.stdout.write(
                    f"\n  [{event.stage}:{event.status}]\n"
                )
                sys.stdout.flush()
        elif isinstance(event, DoneEvent):
            error_type = getattr(event, "error_type", None)
            error_message = getattr(event, "error_message", None)
            if error_type:
                sys.stdout.write(f"\n[System Error: {error_message}]\n")
                sys.stdout.flush()
                return

            suffix = []
            if event.was_escalated:
                suffix.append("escalated")
            if event.was_tool_used:
                suffix.append("tool")
            if event.turn_status and event.turn_status != "completed":
                suffix.append(event.turn_status)
            tag = f" ({', '.join(suffix)})" if suffix else ""
            sys.stdout.write(f"\n{tag}".rstrip() + "\n")
            sys.stdout.flush()

    def _render_direct_result(self, result, start: float) -> None:
        """Print a synchronous TurnResult returned by direct-mode submit_input."""
        if not result:
            sys.stdout.write("\n[Error: no response from Agent Core]\n")
            sys.stdout.flush()
            return
        error_type = result.get("error_type")
        error_message = result.get("error_message")
        if error_type:
            sys.stdout.write(f"\n[System Error: {error_message}]\n")
            sys.stdout.flush()
            return

        text = result.get("response_text", "(no response)")
        was_escalated = result.get("was_escalated", False)
        sys.stdout.write(f"\n{self._agent_prefix}{text}\n")
        if was_escalated:
            sys.stdout.write("[ESCALATED TO HUMAN AGENT]\n")
        sys.stdout.flush()
        logger.info(
            "reach_layer.cli.direct_turn",
            extra={
                "operation": "cli_reach.render_direct_result",
                "status": "success",
                "session_id": self._session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        """Return the session ID for this CLI session."""
        return self._session_id

    @property
    def user_id(self) -> Optional[str]:
        """Return the user ID for this CLI session, or None if anonymous."""
        return self._user_id
