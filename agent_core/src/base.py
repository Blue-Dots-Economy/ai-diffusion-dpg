"""
agent_core/base.py

Public contract for the Agent Core.
process_turn() is the sync entry point exposed to the Reach Layer.
stream_turn() is the async streaming entry point for SSE delivery.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from src.models import StreamEvent, TurnInput, TurnResult


class AgentCoreBase(ABC):

    @abstractmethod
    def process_turn(self, turn_input: TurnInput) -> TurnResult:
        """
        Execute one complete conversation turn.

        Sequence (enforced by concrete implementation):
            1. Read session state from Memory Layer
            2. Check input via Trust Layer  — block/escalate exits here
            3. Assemble prompt via Knowledge Engine
            4. LLM call #1
            5. Tool-use loop via ManagerAgent (if LLM requested a tool)
            6. Check output via Trust Layer — blocked output replaced with fallback
            7. Return TurnResult to caller

        After return (async, non-blocking):
            8. Write updated session state to Memory Layer
            9. Emit TurnEvent to Observability Layer

        Guarantees:
        - Always returns TurnResult. Never raises to the caller.
        - Trust Layer is called exactly twice per turn (input + output).
        - Steps 8 and 9 never block or delay the return.
        """

    @abstractmethod
    async def stream_turn(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute one conversation turn with streaming SSE output.

        Runs the same 13-step pipeline as process_turn() but uses async
        HTTP clients and yields StreamEvents as the pipeline progresses.

        Args:
            turn_input: Normalised inbound message from the Reach Layer.
            abort_event: Optional asyncio.Event. When set, stream_turn exits
                cleanly at the next stage boundary without yielding further
                events. Tool calls and trust checks that are already in-flight
                run to completion to preserve external-side-effect safety.
            turn_id: Optional caller-supplied identifier for this turn. When
                non-empty, it is stamped on every emitted StreamEvent. When
                empty (the default), an internal uuid4 is generated and used.

        Yields:
            SignalEvent, SentenceEvent, or DoneEvent. DoneEvent is the
            terminal event when stream_turn runs to completion. When
            abort_event fires, the generator exits without emitting DoneEvent
            — the caller is responsible for emitting the terminal
            Done(interrupted).

        The caller must consume the generator to completion. Post-turn
        memory writes and observability emits fire via asyncio.create_task()
        after the DoneEvent is yielded.
        """
        yield  # pragma: no cover
