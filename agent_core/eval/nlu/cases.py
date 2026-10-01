# agent_core/eval/nlu/cases.py
"""NLU replay eval cases (NLU dialogue-acts spec §11.2). Structured inputs, so one case runs in both modes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REQUIRED = ("id", "step", "caller_now", "expect")


@dataclass(frozen=True)
class EvalCase:
    """One labelled turn.

    Attributes:
        id: Unique case id.
        tags: Tags for per-tag reporting (e.g. acknowledge, consent, age, termination).
        step: Subagent the caller is in.
        state: Merged routing state (drives pending resolution and the gate).
        session: Raw session (defaults to ``state``).
        caller_now: This turn's utterances (all but last interrupted).
        recent: Prior exchanges ``{caller, bot, interrupted}``.
        offered_tool: Tool whose stored result lists ``offered`` rows.
        offered: Rows on offer this turn.
        expect: Expected ``intent`` (required), ``terminate``, ``slots``, ``option_id``,
            ``acts``, ``relation``, ``topic``, ``pending``.
    """

    id: str
    step: str
    caller_now: list[str]
    expect: dict
    tags: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    session: dict | None = None
    recent: list[dict] = field(default_factory=list)
    offered_tool: str | None = None
    offered: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        """Build from a JSON object; raises ValueError naming the case on missing keys."""
        missing = [k for k in _REQUIRED if k not in d]
        if missing or "intent" not in (d.get("expect") or {}):
            raise ValueError(f"case {d.get('id', '?')}: missing {missing or ['expect.intent']}")
        return cls(id=str(d["id"]), step=d["step"], caller_now=list(d["caller_now"]), expect=dict(d["expect"]),
                   tags=list(d.get("tags") or []), state=dict(d.get("state") or {}), session=d.get("session"),
                   recent=list(d.get("recent") or []), offered_tool=d.get("offered_tool"),
                   offered=list(d.get("offered") or []))


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load a JSONL file of cases (blank lines ignored).

    Args:
        path: JSONL path.

    Returns:
        Cases in file order.

    Raises:
        ValueError: On a malformed case.
    """
    out: list[EvalCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(EvalCase.from_dict(json.loads(line)))
    return out
