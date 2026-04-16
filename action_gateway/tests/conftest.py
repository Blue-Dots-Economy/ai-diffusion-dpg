"""Shared pytest fixtures for Action Gateway tests."""
from __future__ import annotations

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dpg_telemetry import _reset_for_testing


@pytest.fixture
def otel_setup():
    """Install in-memory OTel providers for span and metric assertions.

    Yields a tuple of (InMemorySpanExporter, InMemoryMetricReader). Resets
    global OTel state before and after so tests do not leak into each other.

    Yields:
        Tuple of (exporter, reader) for span and metric assertions.
    """
    _reset_for_testing()

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(tracer_provider)

    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    yield exporter, reader

    _reset_for_testing()


@pytest.fixture
def rest_tool_config():
    """REST API adapter config for a read-only weather endpoint."""
    return {
        "id": "test_weather",
        "type": "rest_api",
        "category": "read",
        "description": "Get weather for a location",
        "base_url": "https://api.weather.test/v1",
        "auth": {"type": "api_key", "header": "X-API-Key", "secret_env": "TEST_WEATHER_KEY"},
        "endpoints": [
            {
                "name": "get_forecast",
                "method": "GET",
                "path": "/forecast",
                "params": [
                    {
                        "name": "location",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "City name",
                    },
                    {
                        "name": "units",
                        "source": "static",
                        "type": "string",
                        "value": "metric",
                    },
                ],
            }
        ],
        "response": {"max_size_chars": 4000},
    }


@pytest.fixture
def rest_write_tool_config():
    """REST API adapter config for a write endpoint (job application submit)."""
    return {
        "id": "test_apply",
        "type": "rest_api",
        "category": "write",
        "description": "Submit an application",
        "base_url": "https://api.jobs.test/v1",
        "auth": {"type": "bearer", "secret_env": "TEST_JOBS_TOKEN"},
        "endpoints": [
            {
                "name": "submit",
                "method": "POST",
                "path": "/applications",
                "params": [
                    {
                        "name": "job_id",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "Job listing ID",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def rest_no_auth_config():
    """REST API adapter config for a public (no-auth) endpoint."""
    return {
        "id": "test_public",
        "type": "rest_api",
        "category": "read",
        "description": "Public data endpoint",
        "base_url": "https://api.public.test",
        "auth": {"type": "none"},
        "endpoints": [
            {
                "name": "get_data",
                "method": "GET",
                "path": "/data",
                "params": [
                    {
                        "name": "query",
                        "source": "agent",
                        "type": "string",
                        "required": True,
                        "description": "Search query",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def mcp_tool_config():
    """MCP adapter config for a test SSE server."""
    return {
        "id": "test_mcp",
        "type": "mcp",
        "category": "read",
        "description": "Test MCP server",
        "server_url": "https://mcp.test.example/sse",
        "transport": "sse",
        "namespace": "test_mcp",
    }
