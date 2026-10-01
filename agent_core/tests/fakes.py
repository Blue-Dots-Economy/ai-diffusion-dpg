"""Shared test doubles for the single (dialogue-act) NLU path."""
from __future__ import annotations

from typing import Sequence
from unittest.mock import MagicMock

from src.models import NLUResult
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding


def fake_understander(nlu_result: NLUResult | None = None, *, writes: Sequence[StateWrite] = (),
                      signals: Sequence[str] = ()) -> MagicMock:
    """A TurnUnderstander stand-in whose understand() returns a fixed TurnUnderstanding.

    Args:
        nlu_result: The routing result to return; defaults to ``any_input``.
        writes: State writes the orchestrator should apply.
        signals: Signal names the orchestrator should emit.

    Returns:
        A MagicMock with a configured ``understand`` method.
    """
    u = MagicMock()
    u.understand.return_value = TurnUnderstanding(
        nlu_result=nlu_result or NLUResult(intent="any_input", entities={}, confidence=1.0, sentiment="neutral"),
        dialogue=DialogueActResult(acts=("other",), relation="unclear"),
        writes=list(writes), signals=list(signals))
    return u
