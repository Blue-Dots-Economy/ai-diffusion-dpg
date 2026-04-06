"""
OTel SDK bootstrap — configures TracerProvider and MeterProvider.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

import logging
import threading

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from dpg_telemetry.propagator import configure_propagator
from dpg_telemetry.resource import build_resource

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialised = False


def init_otel(service_name: str, config: dict) -> None:
    """Configure OTel SDK for a DPG block. Idempotent. Never raises.

    Configures TracerProvider with OTLP gRPC export and ratio-based sampling,
    MeterProvider with periodic OTLP export, and W3C propagator. Failure never
    raises — a misconfigured Collector must not prevent service startup.
    If initialisation fails, the provider is left in a no-op state and the next call will retry.

    Args:
        service_name: Service name for Resource attributes (e.g. "trust_layer").
        config: Full merged config dict. Reads observability.otel section.
    """
    global _initialised
    with _lock:
        if _initialised:
            return
        try:
            obs_cfg = (config or {}).get("observability", {})
            otel_cfg = obs_cfg.get("otel", {})
            endpoint = otel_cfg.get("collector_endpoint", "http://localhost:4317")
            sample_rate = float(otel_cfg.get("sample_rate", 1.0))
            export_interval_ms = int(otel_cfg.get("export_interval_ms", 5000))

            resource = build_resource(service_name, config or {})

            # TracerProvider
            tracer_provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(TraceIdRatioBased(sample_rate)),
            )
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, insecure=True)
                )
            )
            trace.set_tracer_provider(tracer_provider)

            # MeterProvider
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True),
                export_interval_millis=export_interval_ms,
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader],
            )
            metrics.set_meter_provider(meter_provider)

            configure_propagator()
            _initialised = True

            logger.info(
                "dpg_telemetry.init",
                extra={
                    "operation": "dpg_telemetry.init_otel",
                    "status": "success",
                    "service_name": service_name,
                    "endpoint": endpoint,
                    "sample_rate": sample_rate,
                },
            )

        except Exception as e:
            import sys
            print(
                f"[dpg_telemetry] OTel init failed for '{service_name}': "
                f"{type(e).__name__}: {e}. Observability disabled.",
                file=sys.stderr,
            )
            logger.error(
                "dpg_telemetry.init_error",
                extra={
                    "operation": "dpg_telemetry.init_otel",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            # Never raise — observability must not prevent service startup


def reset_for_testing() -> None:
    """Reset global OTel state. For use in tests only — do not call in production.

    Clears the "set-once" guards in the OTel API so that tests can install
    their own TracerProvider and MeterProvider via the standard API calls.
    After calling this function, the next ``trace.set_tracer_provider()`` and
    ``metrics.set_meter_provider()`` calls will succeed without warnings.
    """
    global _initialised
    import opentelemetry.trace as _trace_module
    import opentelemetry.metrics._internal as _metrics_module
    with _lock:
        _initialised = False
        # Clear the "set once" guards so test code can install custom providers.
        if hasattr(_trace_module, "_TRACER_PROVIDER_SET_ONCE"):
            _trace_module._TRACER_PROVIDER_SET_ONCE._done = False
        if hasattr(_trace_module, "_TRACER_PROVIDER"):
            _trace_module._TRACER_PROVIDER = None
        if hasattr(_metrics_module, "_METER_PROVIDER_SET_ONCE"):
            _metrics_module._METER_PROVIDER_SET_ONCE._done = False
        if hasattr(_metrics_module, "_METER_PROVIDER"):
            _metrics_module._METER_PROVIDER = None
