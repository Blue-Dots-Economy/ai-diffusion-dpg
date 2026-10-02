"""Warn when config-authored spoken copy contains digits or markdown (Spec D §10).

The runtime output guard covers model text only; copy written in config is
spoken verbatim, so the dev-kit flags it at authoring/deploy time.
"""
from __future__ import annotations

import re

_DIGIT = re.compile(r"[0-9०-९]")
_MARKDOWN = re.compile(r"\*\*|`|^\s*#|^\s*[-*•]\s", re.M)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_MESSAGE_KEYS = ("blocked_message", "escalation_message", "unsupported_language_message",
                 "unknown_intent_message", "termination_message")


def _check(where: str, text: object, out: list[str]) -> None:
    if not isinstance(text, str) or not text:
        return
    bare = _PLACEHOLDER.sub("", text)
    if _DIGIT.search(bare):
        out.append(f"{where}: contains a digit; spoken copy is read verbatim, write numbers in words")
    if _MARKDOWN.search(bare):
        out.append(f"{where}: contains markdown; spoken copy is read verbatim")


def lint_spoken_copy(agent_core_cfg: dict) -> list[str]:
    """Warnings for digits/markdown in config-authored spoken copy.

    Args:
        agent_core_cfg: Merged agent_core config dict.

    Returns:
        Human-readable warnings (empty when clean). Never raises.
    """
    out: list[str] = []
    conv = agent_core_cfg.get("conversation") or {}
    for key in _MESSAGE_KEYS:
        _check(f"conversation.{key}", conv.get(key), out)
    for sa in (agent_core_cfg.get("agent_workflow") or {}).get("subagents") or []:
        if isinstance(sa, dict):
            for key in ("opening_phrase", "fixed_opening"):
                _check(f"subagents[{sa.get('id')}].{key}", sa.get(key), out)
    return out
