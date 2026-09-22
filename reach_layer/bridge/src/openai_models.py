"""reach_layer/bridge/src/openai_models.py

Builders for the OpenAI chat-completions objects this channel emits.

This module belongs to the Reach Layer's bridge channel: it exposes an
OpenAI-compatible ``/v1/chat/completions`` surface backed by Agent Core.
Shapes are taken from OpenAI's canonical machine-readable specification
(openai/openai-openapi, OpenAPI 3.1.0). Two details are easy to get wrong and
are handled here once:

- ``choices[]`` in a non-streaming response requires all four of ``index``,
  ``message``, ``finish_reason`` and ``logprobs``. ``logprobs`` is null, not
  absent.
- The error envelope requires all four of ``message``, ``type``, ``param`` and
  ``code``; the inapplicable ones are null rather than omitted.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

# Agent Core's turn responses do not expose token counts, so usage is reported
# as zeros rather than omitted or invented (spec section 9). Clients that read
# usage get a well-formed object; none depend on the values being non-zero.
ZERO_USAGE: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}


def new_completion_id() -> str:
    """Return a fresh completion id, stable for the life of one response."""
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def build_chunk(
    cid: str,
    created: int,
    model: str,
    *,
    delta: Optional[dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    usage: Optional[dict[str, int]] = None,
    empty_choices: bool = False,
) -> dict[str, Any]:
    """Build one ``chat.completion.chunk``.

    Args:
        cid: Completion id, identical on every chunk of one response.
        created: Unix seconds, identical on every chunk of one response.
        model: Echoed from the client's request.
        delta: Contents of ``choices[0].delta``.
        finish_reason: One of the five permitted values, or None mid-stream.
        usage: Token counts, for the final usage chunk only.
        empty_choices: When True, emit ``choices: []`` — the shape a usage
            chunk uses.

    Returns:
        A dict matching the ``chat.completion.chunk`` schema.
    """
    chunk: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }
    if empty_choices:
        chunk["choices"] = []
    else:
        chunk["choices"] = [
            {
                "index": 0,
                "delta": delta if delta is not None else {},
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ]
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def build_completion(
    cid: str,
    created: int,
    model: str,
    content: str,
    *,
    finish_reason: str = "stop",
    usage: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Build one non-streaming ``chat.completion`` object.

    Args:
        cid: Completion id for this response.
        created: Unix seconds when the response was generated.
        model: Echoed from the client's request.
        content: Full assistant message text.
        finish_reason: One of the permitted finish-reason values.
        usage: Token counts; defaults to :data:`ZERO_USAGE` when omitted.

    Returns:
        A dict matching the ``chat.completion`` schema, with ``logprobs``
        present and null on the single choice.
    """
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage if usage is not None else ZERO_USAGE,
    }


def build_error(
    message: str,
    err_type: str,
    param: Optional[str] = None,
    code: Optional[str] = None,
) -> dict[str, Any]:
    """Build an OpenAI error envelope.

    Args:
        message: Human-readable error description.
        err_type: OpenAI error type, e.g. ``"invalid_request_error"``.
        param: Name of the offending request parameter, if applicable.
        code: Machine-readable error code, if applicable.

    Returns:
        A dict with an ``error`` object. All four members — ``message``,
        ``type``, ``param``, ``code`` — are always present; inapplicable ones
        are null rather than omitted.
    """
    return {
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": code,
        }
    }
