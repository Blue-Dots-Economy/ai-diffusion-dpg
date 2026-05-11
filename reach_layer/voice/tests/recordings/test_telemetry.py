"""Tests for the recording telemetry helpers (OTel spans + observability signals)."""
from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


_SHARED_EXPORTER: InMemorySpanExporter | None = None


def _install_provider() -> InMemorySpanExporter:
    """Install a single TracerProvider with an InMemorySpanExporter.

    OTel's SDK forbids replacing the global provider once set, so we install
    it exactly once per process and reuse the exporter across tests.
    """
    global _SHARED_EXPORTER
    if _SHARED_EXPORTER is None:
        exp = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exp))
        otel_trace.set_tracer_provider(provider)
        _SHARED_EXPORTER = exp
    return _SHARED_EXPORTER


@pytest.fixture
def exporter():
    """Provide a clean InMemorySpanExporter backed by the shared TracerProvider."""
    import sys
    # Ensure telemetry module is imported with the correct provider.
    if "src.recordings.telemetry" in sys.modules:
        del sys.modules["src.recordings.telemetry"]
    exp = _install_provider()
    exp.clear()
    import src.recordings.telemetry  # noqa: F401 — re-imports with the active tracer
    yield exp
    exp.clear()


def test_recording_lifecycle_span_sets_required_attrs(exporter):
    from src.recordings.telemetry import recording_lifecycle_span
    with recording_lifecycle_span(call_sid="CA1", session_id="s",
                                  caller_id_hash="h", source="pipeline"):
        pass
    spans = exporter.get_finished_spans()
    span = next(s for s in spans if s.name == "recording.lifecycle")
    assert span.attributes["dpg.block"] == "reach_layer"
    assert span.attributes["dpg.channel"] == "voice"
    assert span.attributes["dpg.subsystem"] == "recording"
    assert span.attributes["recording.source"] == "pipeline"
    assert span.attributes["call_sid"] == "CA1"
    assert span.attributes["session_id"] == "s"
    assert span.attributes["caller_id_hash"] == "h"


def test_stage_span_records_status_failed(exporter):
    from src.recordings.telemetry import recording_stage_span
    with pytest.raises(RuntimeError):
        with recording_stage_span("recording.start", call_sid="CA1", source="vobiz"):
            raise RuntimeError("boom")
    spans = exporter.get_finished_spans()
    span = next(s for s in spans if s.name == "recording.start")
    assert span.status.status_code.name == "ERROR"


class _FakeObs:
    def __init__(self):
        self.calls = []

    def emit_signal(self, signal_type, data):
        self.calls.append((signal_type, data))


def test_signal_emitter_emits_started():
    from src.recordings.telemetry import SignalEmitter
    obs = _FakeObs()
    emitter = SignalEmitter(obs)
    emitter.started(call_sid="CA1", session_id="s", caller_id_hash="h",
                    source="pipeline", consent_granted_ts=1.0, start_ts=2.0)
    assert obs.calls[0][0] == "recording.started"
    assert obs.calls[0][1]["call_sid"] == "CA1"


def test_signal_emitter_emits_stored_empty_failed():
    from src.recordings.telemetry import SignalEmitter
    obs = _FakeObs()
    e = SignalEmitter(obs)
    e.stored(call_sid="CA1", session_id="s", source="vobiz", format="mp3",
             duration_ms=1000, bytes=100, sha256="abc",
             recording_uri="s3://b/k.mp3")
    e.empty(call_sid="CA1", session_id="s", source="vobiz", duration_ms=0, reason="empty")
    e.failed(call_sid="CA1", session_id="s", source="vobiz", stage="finalize",
             error_type="X", error_message="x")
    types = [t for t, _ in obs.calls]
    assert types == ["recording.stored", "recording.empty", "recording.failed"]


def test_signal_emitter_swallows_backend_failure():
    from src.recordings.telemetry import SignalEmitter

    class _Bad:
        def emit_signal(self, *_):
            raise RuntimeError("x")

    emitter = SignalEmitter(_Bad())
    # Must not raise.
    emitter.failed(call_sid="CA1", session_id="s", source="vobiz",
                   stage="finalize", error_type="RuntimeError", error_message="x")
