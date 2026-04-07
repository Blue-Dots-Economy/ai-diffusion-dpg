"""
Public API for the dpg_telemetry bootstrap package.

Every DPG block imports this package to initialise OTel SDK and obtain
framework-standard tracers and meters.

Usage:
    from dpg_telemetry import init_otel, get_tracer, get_meter

    init_otel(service_name="trust_layer", config=config)
    tracer = get_tracer(__name__)
    meter  = get_meter(__name__)
"""
from __future__ import annotations

from opentelemetry import metrics, trace

from dpg_telemetry.bootstrap import init_otel as _bootstrap_init
from dpg_telemetry.bootstrap import reset_for_testing as _bootstrap_reset


def init_otel(service_name: str, config: dict) -> None:
    """Initialise OTel SDK for a DPG block. Idempotent. Never raises.

    Args:
        service_name: Block service name (e.g. "agent_core").
        config: Full merged config dict.
    """
    _bootstrap_init(service_name, config)


def get_tracer(name: str) -> "trace.Tracer":
    """Return an OTel Tracer for the given instrumentation scope.

    Args:
        name: Instrumentation scope name, typically ``__name__``.

    Returns:
        opentelemetry.trace.Tracer instance.
    """
    return trace.get_tracer(name)


def get_meter(name: str) -> "metrics.Meter":
    """Return an OTel Meter for the given instrumentation scope.

    Args:
        name: Instrumentation scope name, typically ``__name__``.

    Returns:
        opentelemetry.metrics.Meter instance.
    """
    return metrics.get_meter(name)


def _reset_for_testing() -> None:
    """Reset OTel global state between tests. Do not call in production."""
    _bootstrap_reset()
