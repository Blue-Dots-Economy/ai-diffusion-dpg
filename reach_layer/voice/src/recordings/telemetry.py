"""OTel span helpers + Observability Layer signal emitter for recording.

Belongs to the Reach Layer / Voice channel in the DPG framework.
Provides context managers for wrapping recording lifecycle and stage spans
with standard DPG attributes, and a SignalEmitter that forwards events to
ObservabilityLayerBase.emit_signal without ever raising into the call path.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

_BASE_ATTRS: dict[str, str] = {
    "dpg.block": "reach_layer",
    "dpg.channel": "voice",
    "dpg.subsystem": "recording",
}


def _tracer() -> otel_trace.Tracer:
    """Return a tracer scoped to the recording subsystem."""
    return otel_trace.get_tracer("reach_layer.voice.recording")


@contextlib.contextmanager
def recording_lifecycle_span(
    *,
    call_sid: str,
    session_id: str,
    caller_id_hash: str,
    source: str,
    link: Any = None,
) -> Iterator[otel_trace.Span]:
    """Context manager that wraps the full recording lifecycle in a single OTel span.

    Stamps standard DPG block/channel/subsystem attributes plus call-level
    identifiers.  Records any exception that escapes the block and re-raises it.

    Args:
        call_sid: Telephony call identifier (e.g. Twilio CallSid).
        session_id: Agent Core session identifier for this call.
        caller_id_hash: One-way hash of the caller's phone number (no PII).
        source: Recording source label (e.g. ``"pipeline"``, ``"vobiz"``).
        link: Optional OTel ``Link`` to a parent span from an upstream service.

    Yields:
        The active ``opentelemetry.trace.Span`` for the lifecycle.
    """
    attrs: dict[str, str] = {
        **_BASE_ATTRS,
        "recording.source": source,
        "call_sid": call_sid,
        "session_id": session_id,
        "caller_id_hash": caller_id_hash,
    }
    links = [link] if link is not None else []
    with _tracer().start_as_current_span(
        "recording.lifecycle", attributes=attrs, links=links
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


@contextlib.contextmanager
def recording_stage_span(
    name: str,
    *,
    call_sid: str,
    source: str,
    **extra: Any,
) -> Iterator[otel_trace.Span]:
    """Context manager for a single recording stage span.

    Suitable for wrapping individual stages such as ``recording.start``,
    ``recording.stop``, ``recording.finalize``, and ``recording.store``.
    Sets ERROR status on any exception and re-raises.

    Args:
        name: OTel span name (e.g. ``"recording.start"``).
        call_sid: Telephony call identifier.
        source: Recording source label.
        **extra: Additional span attributes to attach (e.g. ``store_backend``).

    Yields:
        The active ``opentelemetry.trace.Span`` for this stage.
    """
    attrs: dict[str, Any] = {
        **_BASE_ATTRS,
        "recording.source": source,
        "call_sid": call_sid,
        **extra,
    }
    with _tracer().start_as_current_span(name, attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


class SignalEmitter:
    """Adapter that forwards recording events to ``ObservabilityLayerBase.emit_signal``.

    All emit methods swallow backend failures so that an observability outage
    can never block call teardown.  Failures are logged as structured warnings.
    """

    def __init__(self, observability: Any) -> None:
        """Initialise with an observability backend.

        Args:
            observability: Any object that exposes ``emit_signal(signal_type, data)``.
        """
        self._obs = observability

    def _emit(self, signal_type: str, data: dict) -> None:
        """Emit a signal, swallowing any backend exception.

        Args:
            signal_type: Signal type string (e.g. ``"recording.started"``).
            data: Payload dict forwarded verbatim to the backend.
        """
        try:
            self._obs.emit_signal(signal_type, data)
        except Exception as exc:
            logger.warning(
                "recording.signal_emit_failed",
                extra={
                    "operation": "recording.signal_emit",
                    "status": "failure",
                    "signal_type": signal_type,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    def started(self, **fields: Any) -> None:
        """Emit a ``recording.started`` signal.

        Args:
            **fields: Keyword arguments included verbatim in the signal payload
                (e.g. ``call_sid``, ``session_id``, ``caller_id_hash``,
                ``source``, ``consent_granted_ts``, ``start_ts``).
        """
        self._emit("recording.started", fields)

    def stored(self, **fields: Any) -> None:
        """Emit a ``recording.stored`` signal after successful persistence.

        Args:
            **fields: Payload fields such as ``call_sid``, ``session_id``,
                ``source``, ``format``, ``duration_ms``, ``bytes``,
                ``sha256``, ``recording_uri``.
        """
        self._emit("recording.stored", fields)

    def empty(self, **fields: Any) -> None:
        """Emit a ``recording.empty`` signal when no audio was captured.

        Args:
            **fields: Payload fields such as ``call_sid``, ``session_id``,
                ``source``, ``duration_ms``, ``reason``.
        """
        self._emit("recording.empty", fields)

    def failed(self, **fields: Any) -> None:
        """Emit a ``recording.failed`` signal on unrecoverable errors.

        Args:
            **fields: Payload fields such as ``call_sid``, ``session_id``,
                ``source``, ``stage``, ``error_type``, ``error_message``.
        """
        self._emit("recording.failed", fields)
