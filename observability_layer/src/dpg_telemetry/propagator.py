"""
observability_layer/src/dpg_telemetry/propagator.py

Configures the global W3C TraceContext + Baggage propagator.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def configure_propagator() -> None:
    """Set the global OTel propagator to W3C TraceContext + Baggage.

    Must be called once at service startup. Safe to call multiple times.
    """
    set_global_textmap(
        CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ])
    )
