"""
action_gateway/tests/test_mock_server.py

Tests for MockONESTServer FastAPI app.

Uses httpx AsyncClient in ASGI transport mode — no real network call.
"""

import pytest
from fastapi.testclient import TestClient

from src.mock_server import create_mock_server


@pytest.fixture()
def client() -> TestClient:
    app = create_mock_server()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Known fixtures
# ---------------------------------------------------------------------------

class TestKnownTrades:
    def test_electrician_salary_range(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "electrician"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["salary_range"] == "₹15k–₹28k"

    def test_electrician_market_signal(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "electrician"})
        assert "12% QoQ" in resp.json()["market_signal"]

    def test_electrician_top_employers(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "electrician"})
        employers = resp.json()["top_employers"]
        assert len(employers) > 0
        assert any("Hubli" in e or "Karnataka" in e for e in employers)

    def test_welder_salary_range(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "welder"})
        assert resp.json()["salary_range"] == "₹13k–₹22k"

    def test_fitter_salary_range(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "fitter"})
        assert resp.json()["salary_range"] == "₹14k–₹24k"

    def test_source_always_onest(self, client: TestClient) -> None:
        for trade in ("electrician", "welder", "fitter"):
            resp = client.post("/onest/market_lookup", json={"trade": trade})
            assert resp.json()["source"] == "ONEST"


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_uppercase_trade(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "ELECTRICIAN"})
        assert resp.status_code == 200
        assert resp.json()["salary_range"] == "₹15k–₹28k"

    def test_mixed_case_trade(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "Welder"})
        assert resp.status_code == 200
        assert resp.json()["salary_range"] == "₹13k–₹22k"


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------

class TestDefaultFallback:
    def test_unknown_trade_returns_default(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "unknown_trade_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["salary_range"] == "₹12k–₹20k"
        assert data["market_signal"] == "stable"

    def test_empty_trade_returns_default(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": ""})
        assert resp.status_code == 200
        assert resp.json()["salary_range"] == "₹12k–₹20k"


# ---------------------------------------------------------------------------
# Location echoed back
# ---------------------------------------------------------------------------

class TestLocation:
    def test_location_reflected_in_response(self, client: TestClient) -> None:
        resp = client.post(
            "/onest/market_lookup",
            json={"trade": "electrician", "location": "Hubli"},
        )
        assert resp.json()["location_queried"] == "Hubli"

    def test_missing_location_defaults_to_not_specified(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={"trade": "electrician"})
        assert resp.json()["location_queried"] == "not specified"

    def test_empty_location_defaults_to_not_specified(self, client: TestClient) -> None:
        resp = client.post(
            "/onest/market_lookup",
            json={"trade": "electrician", "location": ""},
        )
        assert resp.json()["location_queried"] == "not specified"


# ---------------------------------------------------------------------------
# Bad requests
# ---------------------------------------------------------------------------

class TestBadRequests:
    def test_missing_trade_field_returns_422(self, client: TestClient) -> None:
        resp = client.post("/onest/market_lookup", json={})
        assert resp.status_code == 422

    def test_invalid_json_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/onest/market_lookup",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# OTel span instrumentation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def otel_setup():
    """Set up a shared in-memory OTel provider for the test module."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Re-bind the module-level tracer to pick up the new provider.
    import src.mock_server as ms
    ms._tracer = provider.get_tracer(ms.__name__)

    return exporter


class TestOtelSpans:
    def test_execute_tool_emits_action_span(self, client: TestClient, otel_setup) -> None:
        """The /execute endpoint must emit an action.execute span with required attributes."""
        exporter = otel_setup
        exporter.clear()

        resp = client.post(
            "/execute",
            json={
                "tool_name": "onest_market_lookup",
                "tool_use_id": "test-id-001",
                "input_params": {"trade": "electrician", "location": "Hubli"},
                "session_id": "test-session",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        assert "action.execute" in span_names, f"Expected 'action.execute' span, got: {span_names}"

        action_span = next(s for s in spans if s.name == "action.execute")
        attrs = dict(action_span.attributes or {})
        assert attrs.get("dpg.tool_name") == "onest_market_lookup"
        assert attrs.get("dpg.tool_status") == "success"

    def test_execute_unknown_tool_emits_failure_span(self, client: TestClient, otel_setup) -> None:
        """Unknown tool calls must emit an action.execute span with dpg.tool_status=failure."""
        exporter = otel_setup
        exporter.clear()

        resp = client.post(
            "/execute",
            json={
                "tool_name": "unknown_tool",
                "tool_use_id": "test-id-002",
                "input_params": {},
            },
        )
        assert resp.status_code == 200

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        assert "action.execute" in span_names, f"Expected 'action.execute' span, got: {span_names}"

        action_span = next(s for s in spans if s.name == "action.execute")
        attrs = dict(action_span.attributes or {})
        assert attrs.get("dpg.tool_name") == "unknown_tool"
        assert attrs.get("dpg.tool_status") == "failure"
