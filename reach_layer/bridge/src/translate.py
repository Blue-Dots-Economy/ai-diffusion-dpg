"""reach_layer/bridge/src/translate.py

Translation between the OpenAI chat-completions contract and Agent Core's turn
API. Pure functions only — no I/O, no network, no globals — so every mapping
rule is testable without a server.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from src.identity import IdentityError, extract_caller_phone
from src.openai_models import (
    ZERO_USAGE,
    build_chunk,
    build_completion,
    new_completion_id,
)


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
    text out. A part whose ``text`` is present but not a string is also
    skipped rather than coerced — ``str(123)`` would silently turn the number
    123 into the user "saying" the word "123", which is a worse failure than
    dropping the part.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict)
            and p.get("type") == "text"
            and isinstance(p.get("text"), str)
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
    if not isinstance(body, dict):
        raise RequestError(
            "The request body must be a JSON object.", param=None
        )

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


# ---------------------------------------------------------------------------
# Response translation (spec sections 8 and 9)
# ---------------------------------------------------------------------------

# Agent Core's /stream_turn ends after its terminal DoneEvent and sends no
# sentinel. OpenAI clients read "data: [DONE]" to know the stream is finished,
# so the shim appends it.
SSE_DONE = "data: [DONE]\n\n"


def sse(payload: dict) -> str:
    """Frame one payload as a Server-Sent Event.

    Args:
        payload: JSON-serialisable chunk or usage object.

    Returns:
        The ``data: <json>\\n\\n`` SSE frame.
    """
    return f"data: {json.dumps(payload)}\n\n"


class StreamTranslator:
    """Turns one Agent Core event stream into one OpenAI chunk stream.

    Agent Core's ``/stream_turn`` emits a ``SignalEvent`` (pipeline progress,
    dropped here), a ``SentenceEvent`` per trust-checked sentence, then a
    terminal ``DoneEvent``. This class turns those into
    ``chat.completion.chunk`` objects.

    Holds the per-response identity — ``id`` and ``created`` are minted once
    and repeated on every chunk, as OpenAI does — so one instance serves
    exactly one request. Never share an instance across requests.
    """

    def __init__(self, model: str, terminal_word: str = "") -> None:
        """Initialise a translator for a single response.

        Args:
            model: The client's requested model, echoed back. Agent Core
                returns ``model_used`` as an empty string, so the request's
                value is the only meaningful one to report.
            terminal_word: Closing word spoken when the turn ends the session.
                Empty disables it.
        """
        self._model = model
        self._terminal_word = terminal_word
        self._id = new_completion_id()
        self._created = int(time.time())

    def _chunk(self, **kwargs) -> dict:
        return build_chunk(self._id, self._created, self._model, **kwargs)

    def opening(self) -> dict:
        """Build the first chunk of the stream, declaring the assistant role.

        Returns:
            A ``chat.completion.chunk`` with an empty-content role delta.
        """
        return self._chunk(delta={"role": "assistant", "content": ""})

    def sentence(self, text: str) -> dict:
        """Translate one ``SentenceEvent`` into a content delta chunk.

        Args:
            text: The trust-checked sentence text.

        Returns:
            A ``chat.completion.chunk`` carrying ``text`` as content.
        """
        return self._chunk(delta={"content": text})

    def finish(self, done: dict, *, include_usage: bool) -> list[dict]:
        """Build the chunks that close the stream from Agent Core's DoneEvent.

        Args:
            done: Agent Core's terminal ``DoneEvent``.
            include_usage: True when the client sent
                ``stream_options.include_usage``.

        Returns:
            The closing chunks in emission order. The caller appends
            ``SSE_DONE`` after these. ``finish_reason`` is always the literal
            ``"stop"`` — the only value this method ever emits, out of the
            five the contract permits, since the DoneEvent carries no field
            that maps to ``length``, ``tool_calls``, ``content_filter`` or
            ``function_call``.
        """
        out: list[dict] = []
        if done.get("session_ended") and self._terminal_word:
            out.append(self.sentence(" " + self._terminal_word))
        out.append(self._chunk(delta={}, finish_reason="stop"))
        if include_usage:
            out.append(self._chunk(usage=ZERO_USAGE, empty_choices=True))
        return out


def to_completion(turn_response: dict, model: str) -> dict:
    """Translate a blocking ``/process_turn`` response into a completion.

    Args:
        turn_response: Agent Core's ``ProcessTurnResponse``.
        model: The client's requested model, echoed back since Agent Core's
            ``model_used`` comes back as an empty string.

    Returns:
        A ``chat.completion`` object. ``session_id`` is deliberately not
        exposed: it is the caller's phone number, PII, and not part of the
        contract. ``finish_reason`` defaults to ``"stop"`` in
        :func:`src.openai_models.build_completion`, the only value this
        blocking path ever produces.
    """
    return build_completion(
        new_completion_id(),
        int(time.time()),
        model,
        turn_response.get("response_text", ""),
    )
