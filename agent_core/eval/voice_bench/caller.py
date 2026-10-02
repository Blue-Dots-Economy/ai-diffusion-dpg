# agent_core/eval/voice_bench/caller.py
"""Simulated caller (spec §6.3)."""
from __future__ import annotations

import json
import re

from eval.voice_bench.llm import JsonLLM
from eval.voice_bench.suite import Persona

_SYSTEM = (
    "You are role-playing a phone caller to a Hindi voice job-assistant for blue-collar workers in India.\n"
    "Persona: {title}. Facts you know (reveal only when asked or when natural): {facts}.\n"
    "Your goal for this call: {goal}. Behaviour: {quirks}. End the call {ends_when}.\n"
    "Rules: Speak like a real caller on the phone: short, spoken Hindi in Devanagari{hinglish}. One utterance per turn.\n"
    "Never describe actions or emotions in brackets or asterisks. To stay silent, say exactly \"...\".\n"
    "When the conversation is over (the assistant said goodbye, or you have finished), output <END> instead of a line.\n"
    'Return JSON: {{"line": "<what you say>"}}.'
)
_AUDIT = (
    "You audit a simulated caller. Persona: {title}; goal: {goal}; behaviour: {quirks}.\n"
    "Given the caller's lines, answer whether the caller broke persona: spoke as an AI, described actions,\n"
    'revealed it was simulated, or went off-script for 2 or more lines. Return JSON {{"broken": true|false}}.'
)
_STAGE = re.compile(r"[\(\[\*]|\b(pause|silence|laughs?)\b", re.I)


def stage_direction(line: str) -> bool:
    """True when a caller line contains a voiced stage direction."""
    return _STAGE.search(line) is not None


class Caller:
    """LLM caller persona."""

    def __init__(self, llm: JsonLLM) -> None:
        self._llm = llm

    def next_line(self, persona: Persona, leg_idx: int, history: list[tuple[str, str]], seed: int) -> str:
        """Next caller line given (caller, bot) history; '<END>' to hang up."""
        leg = persona.legs[leg_idx]
        system = _SYSTEM.format(title=persona.title, facts=json.dumps(persona.facts, ensure_ascii=False),
                                goal=leg.goal, quirks="; ".join(persona.quirks), ends_when=persona.ends_when,
                                hinglish=", mixing English words as Hinglish" if persona.id == "T03" else "")
        transcript = "\n".join(f"आप: {c}\nबॉट: {b}" for c, b in history)
        line = str(self._llm.complete_json(system, transcript, seed).get("line", "<END>")).strip()
        return "<END>" if "<END>" in line else (line or "...")


def persona_broken(llm: JsonLLM, persona: Persona, caller_lines: list[str], seed: int) -> bool:
    """Cheap LLM audit of the caller's lines (plan ruling 7)."""
    system = _AUDIT.format(title=persona.title, goal=persona.goal, quirks="; ".join(persona.quirks))
    return bool(llm.complete_json(system, "\n".join(caller_lines), seed).get("broken", False))
