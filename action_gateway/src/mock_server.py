"""
action_gateway/src/mock_server.py

POC DEMO STUB — Domain-specific fixture data. Not production framework code.
Replace with real connector implementations for production deployments.

Mock ONEST FastAPI server — PoC stub for external connector API.

Runs on port 9999 (configurable). Endpoints:
    POST /onest/market_lookup  — job market data lookup
    POST /onest/apply          — mock job application (always succeeds)

Returns hardcoded fixture JSON for sample trades (electrician, welder, fitter).
Falls back to a default fixture for any other trade.

Run standalone:
    python -m action_gateway.src.mock_server      (from repo root)
    python main.py                                 (from action_gateway/ directory)
"""

from __future__ import annotations

import json
import logging
import random
import string
import time
from typing import Optional

from fastapi import FastAPI
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

_tracer = otel_trace.get_tracer(__name__)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded fixture data — PoC demo stub
# ---------------------------------------------------------------------------

_FIXTURES: dict[str, dict] = {
    "electrician": {
        "trade": "electrician",
        "salary_range": "₹15k–₹28k",
        "market_signal": "steady signal 12% QoQ",
        "top_employers": ["Hubli Distribution Co", "Karnataka Power"],
        "source": "ONEST",
    },
    "welder": {
        "trade": "welder",
        "salary_range": "₹13k–₹22k",
        "market_signal": "8% QoQ",
        "top_employers": ["Hubli Iron Works", "Dharwad Fabrication"],
        "source": "ONEST",
    },
    "fitter": {
        "trade": "fitter",
        "salary_range": "₹14k–₹24k",
        "market_signal": "10% QoQ",
        "top_employers": ["BEML Hubli", "KA Manufacturing"],
        "source": "ONEST",
    },
    "plumber": {
        "trade": "plumber",
        "salary_range": "₹12k–₹22k",
        "market_signal": "growing 9% QoQ",
        "top_employers": ["Hubli Municipal Corp", "KA Infrastructure Projects"],
        "source": "ONEST",
    },
    "plumbing": {
        "trade": "plumbing",
        "salary_range": "₹12k–₹22k",
        "market_signal": "growing 9% QoQ",
        "top_employers": ["Hubli Municipal Corp", "KA Infrastructure Projects"],
        "source": "ONEST",
    },
    "carpenter": {
        "trade": "carpenter",
        "salary_range": "₹13k–₹24k",
        "market_signal": "stable 6% QoQ",
        "top_employers": ["Dharwad Furniture Hub", "Urban Interiors Hubli"],
        "source": "ONEST",
    },
    "mason": {
        "trade": "mason",
        "salary_range": "₹14k–₹25k",
        "market_signal": "growing 11% QoQ",
        "top_employers": ["KA Construction Co", "Hubli Builders Association"],
        "source": "ONEST",
    },
    "driver": {
        "trade": "driver",
        "salary_range": "₹14k–₹26k",
        "market_signal": "high demand 15% QoQ",
        "top_employers": ["Ola Fleet Hubli", "Karnataka Road Transport"],
        "source": "ONEST",
    },
    "tailor": {
        "trade": "tailor",
        "salary_range": "₹10k–₹18k",
        "market_signal": "stable 5% QoQ",
        "top_employers": ["Dharwad Garments", "KA Textile Mills"],
        "source": "ONEST",
    },
}

_DEFAULT_FIXTURE: dict = {
    "trade": "unknown",
    "salary_range": "₹12k–₹20k",
    "market_signal": "stable",
    "top_employers": ["Local Contractor Network", "District Employment Exchange"],
    "source": "ONEST",
}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class MarketLookupRequest(BaseModel):
    trade: str
    location: str = ""
    distance_km: int = 50


class MarketLookupResponse(BaseModel):
    trade: str
    salary_range: str
    market_signal: str
    top_employers: list[str]
    source: str
    location_queried: str


class ApplyRequest(BaseModel):
    trade: str
    employer: str
    location: str = ""
    applicant_name: str = ""


class ApplyResponse(BaseModel):
    status: str
    reference_number: str
    message: str
    employer: str
    trade: str


class HealthResponse(BaseModel):
    status: str


class ExecuteRequest(BaseModel):
    tool_name: str
    tool_use_id: str
    input_params: dict
    session_id: Optional[str] = None


class ExecuteResponse(BaseModel):
    tool_use_id: str
    success: bool
    result: dict = {}
    result_text: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_mock_server() -> FastAPI:
    """Create and return the FastAPI mock ONEST server."""

    app = FastAPI(
        title="ONEST Mock Server",
        description="Synthetic ONEST job market data — PoC Action Gateway stub.",
        version="0.1.0",
        docs_url="/docs",
    )

    FastAPIInstrumentor.instrument_app(app)

    @app.post("/onest/market_lookup", response_model=MarketLookupResponse)
    def market_lookup(request: MarketLookupRequest) -> MarketLookupResponse:
        """
        Return hardcoded market data for the given trade.

        Looks up trade (case-insensitive) in fixture dict.
        Falls back to default fixture for unknown trades.
        """
        trade_key = request.trade.lower().strip()
        fixture = _FIXTURES.get(trade_key, _DEFAULT_FIXTURE)

        logger.info(
            "mock_server.market_lookup",
            extra={
                "operation": "mock_server.market_lookup",
                "status": "success",
                "trade": trade_key,
                "location": request.location,
                "fixture_matched": trade_key in _FIXTURES,
            },
        )

        return MarketLookupResponse(
            trade=fixture["trade"],
            salary_range=fixture["salary_range"],
            market_signal=fixture["market_signal"],
            top_employers=fixture["top_employers"],
            source=fixture["source"],
            location_queried=request.location or "not specified",
        )

    @app.post("/onest/apply", response_model=ApplyResponse)
    def apply(request: ApplyRequest) -> ApplyResponse:
        """
        Mock job application endpoint.

        Always succeeds with a generated reference number.
        In production this will call ONEST's actual apply API.
        """
        ref = "APP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        name_part = f" for {request.applicant_name}" if request.applicant_name else ""
        message = (
            f"Application submitted{name_part} to {request.employer} "
            f"for {request.trade} role. Reference: {ref}."
        )

        logger.info(
            "mock_server.apply",
            extra={
                "operation": "mock_server.apply",
                "status": "success",
                "trade": request.trade,
                "employer": request.employer,
                "reference": ref,
            },
        )

        return ApplyResponse(
            status="success",
            reference_number=ref,
            message=message,
            employer=request.employer,
            trade=request.trade,
        )

    @app.post("/execute", response_model=ExecuteResponse)
    def execute(request: ExecuteRequest) -> ExecuteResponse:
        """
        Generic tool execution router.
        Bridges the generic Agent Core call to specific domain connectors.
        Emits an ``action.execute`` OpenTelemetry span with ``dpg.tool_name``
        and ``dpg.tool_status`` attributes on every invocation.
        """
        logger.info(
            "mock_server.execute",
            extra={
                "operation": "mock_server.execute",
                "tool_name": request.tool_name,
                "session_id": request.session_id,
            },
        )

        with _tracer.start_as_current_span("action.execute") as span:
            span.set_attribute("dpg.tool_name", request.tool_name)
            start = time.time()
            try:
                if request.tool_name == "onest_market_lookup":
                    lookup_req = MarketLookupRequest(**request.input_params)
                    res = market_lookup(lookup_req)

                    logger.info(
                        "mock_server.tool_result",
                        extra={
                            "tool": "onest_market_lookup",
                            "trade_requested": lookup_req.trade,
                            "match_found": lookup_req.trade.lower() in _FIXTURES,
                        },
                    )

                    span.set_attribute("dpg.tool_status", "success")
                    logger.info(
                        "mock_server.execute",
                        extra={
                            "operation": "server.execute_tool",
                            "status": "success",
                            "tool_name": request.tool_name,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return ExecuteResponse(
                        tool_use_id=request.tool_use_id,
                        success=True,
                        result=res.model_dump(),
                        result_text=json.dumps(res.model_dump()),
                    )

                if request.tool_name == "onest_apply":
                    apply_req = ApplyRequest(**request.input_params)
                    res = apply(apply_req)
                    span.set_attribute("dpg.tool_status", "success")
                    logger.info(
                        "mock_server.execute",
                        extra={
                            "operation": "server.execute_tool",
                            "status": "success",
                            "tool_name": request.tool_name,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return ExecuteResponse(
                        tool_use_id=request.tool_use_id,
                        success=True,
                        result=res.model_dump(),
                        result_text=json.dumps(res.model_dump()),
                    )

                # Fallback for unknown tools
                span.set_attribute("dpg.tool_status", "failure")
                logger.info(
                    "mock_server.execute",
                    extra={
                        "operation": "server.execute_tool",
                        "status": "failure",
                        "tool_name": request.tool_name,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ExecuteResponse(
                    tool_use_id=request.tool_use_id,
                    success=False,
                    result_text="",
                    error=f"Unknown tool: {request.tool_name}",
                )

            except Exception as e:
                span.set_attribute("dpg.tool_status", "failure")
                span.record_exception(e)
                logger.error(
                    "mock_server.execute_error",
                    extra={
                        "operation": "server.execute_tool",
                        "status": "failure",
                        "tool_name": request.tool_name,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ExecuteResponse(
                    tool_use_id=request.tool_use_id,
                    success=False,
                    result_text="",
                    error=f"Execution failed: {str(e)}",
                )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Readiness probe."""
        return HealthResponse(status="ok")

    return app


# Module-level app instance for uvicorn
app = create_mock_server()
