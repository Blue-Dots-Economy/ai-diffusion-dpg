"""reach_layer/bridge/src/translate.py

Translation between the OpenAI chat-completions contract and Agent Core's turn
API. Pure functions only — no I/O, no network, no globals — so every mapping
rule is testable without a server.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from src.identity import IdentityError, extract_caller_phone


class RequestError(ValueError):
    """The inbound request cannot be translated.

    Attributes:
        param: Dotted path of the offending field, for the error envelope.
    """

    def __init__(self, message: str, param: Optional[str] = None) -> None:
        super().__init__(message)
        self.param = param


def _content_to_text(content: Any) -> str:
    """Flatten a message's content to plain text.

    The contract allows content to be a string or an array of typed parts.
    Non-text parts (image, audio, file) are skipped: this channel is text in,
    text out.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "".join(parts)
    return ""


def to_turn_request(body: dict, *, channel: str) -> dict[str, Any]:
    """Translate an OpenAI request body into an Agent Core turn request.

    The client sends only the caller's newest utterance (spec 11.1), but the
    last ``user``-role message is taken rather than ``messages[0]`` so a client
    that sends more than agreed still works.

    ``session_id`` is the phone number (spec 11.3): it identifies the person,
    and a caller who rings back inside the session TTL resumes where they left
    off rather than starting again.

    Args:
        body: Parsed chat-completions request body.
        channel: Channel name to declare to Agent Core. Must exist in the
            domain's ``channels`` config or Agent Core rejects the turn.

    Returns:
        A ``ProcessTurnRequest`` payload.

    Raises:
        RequestError: On any malformed or unsupported field.
    """
    if not body.get("model"):
        raise RequestError("'model' is a required field.", param="model")

    n = body.get("n")
    if n is not None and n != 1:
        raise RequestError(
            "'n' must be 1: this endpoint produces a single response.",
            param="n",
        )

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RequestError(
            "'messages' is required and must contain at least one message.",
            param="messages",
        )

    user_text = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            user_text = _content_to_text(message.get("content")).strip()
            break

    if not user_text:
        raise RequestError(
            "'messages' must contain a user message with non-empty content.",
            param="messages",
        )

    try:
        phone = extract_caller_phone(body)
    except IdentityError as exc:
        raise RequestError(str(exc), param=exc.param) from exc

    return {
        "session_id": phone,
        "user_id": phone,
        "user_message": user_text,
        "channel": channel,
        "timestamp_ms": int(time.time() * 1000),
    }
