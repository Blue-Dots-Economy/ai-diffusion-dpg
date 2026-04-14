"""
agent_core/base.py

Public contract for the Agent Core.
process_turn() is the sync entry point exposed to the Reach Layer.
stream_turn() is the async streaming entry point for SSE delivery.
"""

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
    async def stream_turn(self, turn_input: TurnInput) -> AsyncGenerator[StreamEvent, None]:
        """Execute one conversation turn with streaming SSE output.

        Runs the same 13-step pipeline as process_turn() but uses async
        HTTP clients and yields StreamEvents as the pipeline progresses.

        Yields:
            SignalEvent for pipeline stage notifications.
            SentenceEvent for each trust-checked LLM output sentence.
            DoneEvent as the terminal event (always last).

        The caller must consume the generator to completion. Post-turn
        memory writes and observability emits fire via asyncio.create_task()
        after the DoneEvent is yielded.
        """
        yield  # pragma: no cover
