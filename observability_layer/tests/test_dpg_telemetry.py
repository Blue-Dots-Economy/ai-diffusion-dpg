"""Tests for the dpg_telemetry bootstrap package."""
import pytest


def test_build_resource_sets_service_name():
    from dpg_telemetry.resource import build_resource
    resource = build_resource("trust_layer", {"observability": {"domain": "kkb"}})
    attrs = resource.attributes
    assert attrs["service.name"] == "trust_layer"
    assert attrs["dpg.block"] == "trust_layer"
    assert attrs["dpg.domain"] == "kkb"


def test_build_resource_defaults_domain_to_unknown():
    from dpg_telemetry.resource import build_resource
    resource = build_resource("agent_core", {})
    assert resource.attributes["dpg.domain"] == "unknown"


def test_configure_propagator_sets_w3c():
    from dpg_telemetry.propagator import configure_propagator
    from opentelemetry.propagate import get_global_textmap
    configure_propagator()
    propagator = get_global_textmap()
    assert propagator is not None


def test_init_otel_does_not_raise_on_missing_collector():
    """init_otel must not raise even when the collector is unreachable."""
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    config = {"observability": {"otel": {"collector_endpoint": "http://localhost:19999"}, "domain": "test"}}
    init_otel("test_service", config)


def test_init_otel_is_idempotent():
    """Calling init_otel twice must not raise or reconfigure."""
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    config = {"observability": {"otel": {"collector_endpoint": "http://localhost:4317"}, "domain": "test"}}
    init_otel("svc", config)
    init_otel("svc", config)  # second call — must be a no-op


def test_get_tracer_returns_tracer():
    from dpg_telemetry import get_tracer
    tracer = get_tracer("my.module")
    assert tracer is not None


def test_get_meter_returns_meter():
    from dpg_telemetry import get_meter
    meter = get_meter("my.module")
    assert meter is not None


def test_init_otel_empty_config_does_not_raise():
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    init_otel("svc", {})  # empty config should use defaults, not raise


def test_build_resource_none_config_does_not_raise():
    from dpg_telemetry.resource import build_resource
    resource = build_resource("agent_core", None)
    assert resource.attributes["dpg.domain"] == "unknown"


def test_init_otel_none_config_does_not_raise():
    from dpg_telemetry import init_otel, _reset_for_testing
    _reset_for_testing()
    init_otel("svc", None)  # must not raise
