"""
action_gateway/src/mock_server.py

Mock ONEST FastAPI server — PoC stub for external connector API.

Runs on port 9999 (configurable). Endpoints:
    POST /onest/market_lookup  — job market data lookup
    POST /onest/apply          — mock job application (always succeeds)

Returns hardcoded fixture JSON for KKB demo trades (electrician, welder, fitter).
Falls back to a default fixture for any other trade.

Fixture data sourced from KKB PoC plan (Task 2.6):
    electrician → ₹15k–₹28k, steady signal 12% QoQ
    welder      → ₹13k–₹22k, 8% QoQ
    fitter      → ₹14k–₹24k, 10% QoQ
    default     → ₹12k–₹20k, stable

Run standalone:
    python -m action_gateway.src.mock_server      (from repo root)
    python main.py                                 (from action_gateway/ directory)
"""

from __future__ import annotations

import logging
import random
import string
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded fixture data — from KKB PoC plan Task 2.6
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


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_mock_server() -> FastAPI:
    """Create and return the FastAPI mock ONEST server."""

    app = FastAPI(
        title="ONEST Mock Server",
        description="Synthetic ONEST job market data for KKB PoC Action Gateway stub.",
        version="0.1.0",
        docs_url="/docs",
    )

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
        ref = "KKB-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
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

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Readiness probe."""
        return HealthResponse(status="ok")

    return app


# Module-level app instance for uvicorn
app = create_mock_server()
