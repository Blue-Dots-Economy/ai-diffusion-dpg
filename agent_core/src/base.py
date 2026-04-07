"""
agent_core/base.py

Public contract for the Agent Core.
process_turn() is the single entry point exposed to the Reach Layer.
No other method on Agent Core is callable from outside the module.
"""

from abc import ABC, abstractmethod

from src.models import TurnInput, TurnResult


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
