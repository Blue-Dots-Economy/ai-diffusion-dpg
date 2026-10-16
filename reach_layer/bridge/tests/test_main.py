"""Bridge startup must survive a broken telemetry setup."""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI

import main


def test_telemetry_failure_is_logged_and_does_not_stop_startup(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "dpg_telemetry", None)
    with caplog.at_level(logging.WARNING):
        main._init_telemetry({})
    assert any(r.getMessage() == "bridge.telemetry_disabled" for r in caplog.records)


def test_app_instrumentation_failure_is_logged_and_does_not_stop_startup(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.fastapi", None)
    with caplog.at_level(logging.WARNING):
        main._instrument_app(FastAPI())
    assert any(r.getMessage() == "bridge.telemetry_disabled" for r in caplog.records)


def test_app_is_traced_when_telemetry_is_available():
    app = FastAPI()
    main._instrument_app(app)
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


def test_telemetry_setup_starts_the_exporters_and_outgoing_tracing(monkeypatch):
    calls = []
    monkeypatch.setattr("dpg_telemetry.init_otel", lambda name, cfg: calls.append(("init", name)))
    monkeypatch.setattr("opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor.instrument",
                        lambda self, **kw: calls.append(("httpx",)))
    main._init_telemetry({})
    assert calls == [("init", "reach_layer.bridge"), ("httpx",)]
