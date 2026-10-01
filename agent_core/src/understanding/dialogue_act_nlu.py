"""
agent_core/src/understanding/dialogue_act_nlu.py

The dialogue-act NLU LLM call: a static, prompt-cached system prompt, a
strict JSON-schema output, and a bounded fallback (NLU dialogue-acts spec
§5.1, §5.3, §9). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatRequest, Message, OutputFormat, SystemPrompt, TextBlock
from src.understanding.config import DialogueActConfig, SlotSpec
from src.understanding.models import ACTS, RELATIONS, DialogueActResult

logger = logging.getLogger(__name__)

_ACT_TEXT = {
    "affirm": "says yes / agrees to what was asked (हाँ, जी हाँ, ठीक है — only as an answer to a yes/no question)",
    "deny": "says no / refuses what was asked (नहीं, मत करो)",
    "acknowledge": "only acknowledges or thanks, without answering anything (ठीक है, अच्छा, धन्यवाद, जी)",
    "provide_info": "gives a fact about themselves (age, trade, city, name, …)",
    "correct": "corrects something said earlier (\"X नहीं, Y\")",
    "select": "chooses one of the offered options (by number, name or description)",
    "ask": "asks a question",
    "request_change": "asks for something different (another search, change details)",
    "repeat": "asks the bot to say it again",
    "hold": "asks the bot to wait (एक मिनट, रुको)",
    "close": "clearly wants to end the call (बस, रखता हूँ, बाद में बात करते हैं) — a thank-you alone is NOT close",
    "other": "none of the above",
}
_RELATION_TEXT = {
    "answers_pending": "the turn answers the pending question",
    "answers_other": "the turn answers something else the bot needs, not the pending question",
    "new_topic": "the caller raises a different in-domain topic (a question, a change)",
    "unrelated": "the turn is unrelated to the call (background talk, other people, off-topic)",
    "unclear": "the turn cannot be understood or is only an acknowledgement with nothing pending to answer",
}


def _slot_schema(spec: SlotSpec) -> dict:
    if spec.type == "int":
        return {"type": ["integer", "null"]}
    if spec.type == "enum":
        return {"type": ["string", "null"], "enum": [*spec.values, None]}
    return {"type": ["string", "null"]}


def _obj(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def build_output_schema(cfg: DialogueActConfig) -> dict:
    """Strict-mode JSON schema for the NLU output (every object closed, every key required).

    Args:
        cfg: Parsed dialogue-act config.

    Returns:
        JSON schema dict.
    """
    signal_items = {"type": "string", "enum": list(cfg.signals)} if cfg.signals else {"type": "string"}
    props = {
        "acts": {"type": "array", "items": {"type": "string", "enum": list(ACTS)}},
        "relation": {"type": "string", "enum": list(RELATIONS)},
        "topic": {"type": ["string", "null"], "enum": [*cfg.topics, None]} if cfg.topics else {"type": "null"},
        "slots": _obj({name: _slot_schema(s) for name, s in cfg.slots.items()}),
        "reference": _obj({"option": {"type": ["integer", "null"]}, "spoken": {"type": ["string", "null"]}}),
        "signals": {"type": "array", "items": signal_items},
        "extras": {"type": "array", "items": _obj({"key": {"type": "string"}, "value": {"type": "string"}})},
    }
    if cfg.user_states:
        props["user_state"] = _obj({"id": {"type": "string", "enum": [s["id"] for s in cfg.user_states]},
                                    "confidence": {"type": "number"}})
    return _obj(props)


def _slot_line(spec: SlotSpec) -> str:
    kind = {"int": "integer", "enum": "one of " + ", ".join(spec.values)}.get(spec.type, "text")
    bounds = f" ({spec.min}–{spec.max})" if spec.type == "int" and spec.min is not None and spec.max is not None else ""
    ex = f" e.g. {'; '.join(spec.examples)}" if spec.examples else ""
    desc = f" — {spec.description}" if spec.description else ""
    return f"- {spec.name}: {kind}{bounds}{desc}{ex}"


def build_system_prompt_text(cfg: DialogueActConfig) -> str:
    """Render the static NLU system prompt. Depends only on config, never on the turn.

    Args:
        cfg: Parsed dialogue-act config.

    Returns:
        Prompt text (identical on every call — prompt-cacheable).
    """
    parts = [
        "You understand one caller turn in a phone conversation. You do NOT reply to the caller.",
        "Read <frame> (what the bot is waiting for, what was offered, what is already known), "
        "<recent> (the last exchanges) and <caller_now> (what the caller just said). "
        "Return only the JSON object described by the schema.",
        "",
        "Acts — what the caller did (1 to 3, in order):",
        *[f"- {a}: {_ACT_TEXT[a]}" for a in ACTS],
        "",
        "Relation — how the turn relates to the pending question:",
        *[f"- {r}: {_RELATION_TEXT[r]}" for r in RELATIONS],
        "",
        "Topics (only for ask / request_change; otherwise null): " + (", ".join(cfg.topics) or "none"),
        "",
        "Slots — values the caller SAID in <caller_now>; null when not said:",
        *[_slot_line(s) for s in cfg.slots.values()],
        "",
        "Rules:",
        "- Extract only what the caller said this turn. Never copy values from <frame> known: or <recent>.",
        "- A yes to a question other than the pending one is not an answer to the pending one.",
        "- Never infer consent from a yes to anything except the consent question.",
        "- Hindi number words become digits (बाईस → 22).",
        "- For select, set reference.option to the number of the offered option the caller means "
        "(by position, company or role) and reference.spoken to their words; otherwise both null.",
        "- extras: other personal details worth keeping, as key/value text pairs; usually empty.",
        "- signals: only from this list, when clearly present: " + (", ".join(cfg.signals) or "none"),
    ]
    if cfg.user_states:
        parts += ["", "Caller state — classify their mental state; keep previous_state (in <frame>) "
                      "when the turn does not clearly shift it:"]
        for s in cfg.user_states:
            first = (str(s.get("guidance") or "").strip().splitlines() or [""])[0]
            sigs = " | ".join(s.get("signals") or []) or "(none)"
            parts.append(f"- {s['id']}: signals {sigs} — {first}")
    if cfg.examples:
        parts += ["", "Examples:"]
        for ex in cfg.examples:
            parts.append(f"- pending: {ex.get('pending') or 'none'} | caller: {ex.get('caller', '')} → "
                         f"{json.dumps(ex.get('out', {}), ensure_ascii=False)}")
    return "\n".join(parts)


class DialogueActNLUBase(ABC):
    """Interface for the dialogue-act NLU call."""

    @abstractmethod
    def classify(self, user_message: str) -> tuple[DialogueActResult, str | None, int]:
        """Return (result, fallback_reason, latency_ms). Never raises."""


class DialogueActNLU(DialogueActNLUBase):
    """Calls the dedicated NLU provider with a strict output schema.

    Args:
        cfg: Parsed dialogue-act config (read once at startup).
        chat_provider: Dedicated provider (timeout/retry per spec §9.1).
    """

    def __init__(self, cfg: DialogueActConfig, chat_provider: ChatProviderBase) -> None:
        self._cfg = cfg
        self._provider = chat_provider
        self._system_text = build_system_prompt_text(cfg)
        self._schema = build_output_schema(cfg)

    def classify(self, user_message: str) -> tuple[DialogueActResult, str | None, int]:
        """Classify one rendered frame. Never raises.

        Args:
            user_message: Output of ``FrameBuilder.build``.

        Returns:
            (result, fallback_reason, latency_ms). ``fallback_reason`` is None
            on success; otherwise the result is ``DialogueActResult.fallback()``.
        """
        start = time.time()

        def _done(result: DialogueActResult, reason: str | None) -> tuple[DialogueActResult, str | None, int]:
            return result, reason, int((time.time() - start) * 1000)

        if not (user_message or "").strip():
            return _done(DialogueActResult.fallback(), "empty_input")
        try:
            hint = "session" if getattr(self._provider.capabilities, "supports_prompt_cache", False) else None
            response = self._provider.call(ChatRequest(
                messages=[Message(role="user", content=[TextBlock(text=user_message)])],
                system=SystemPrompt(blocks=[TextBlock(text=self._system_text, cache_hint=hint)]),
                output_format=OutputFormat(schema=self._schema, strict=True),
                max_tokens=400,
            ))
            if response.stop_reason == "error" and response.parsed_output is None:
                return _done(DialogueActResult.fallback(), f"provider_error:{response.error_type or 'unknown'}")
            try:
                return _done(DialogueActResult.from_parsed(
                    response.parsed_output, slot_names=tuple(self._cfg.slots),
                    topics=self._cfg.topics, signals=self._cfg.signals,
                    user_state_ids=[s["id"] for s in self._cfg.user_states]), None)
            except ValueError:
                return _done(DialogueActResult.fallback(), "schema_violation")
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.error("dialogue_act_nlu.error", extra={
                "operation": "dialogue_act_nlu.classify", "status": "failure",
                "error": type(e).__name__, "latency_ms": int((time.time() - start) * 1000)})
            return _done(DialogueActResult.fallback(), "exception")
