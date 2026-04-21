"""FastAPI server for the Action Gateway block.

This module exposes the HTTP interface used by Agent Core to discover available
tools and execute tool calls. It is a thin routing layer over AdapterRegistry
and delegates all business logic to the registered ToolAdapter instances.

Endpoints:
  GET  /tools    — return all ToolDefinitions in the registry.
  POST /execute  — execute a single tool call by name.
  GET  /health   — return per-adapter health status.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from src.models import ExecuteRequest, ExecuteResponse, HealthResponse, ToolsResponse
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)


def _get_tracer() -> otel_trace.Tracer:
    """Return the OTel tracer for the Action Gateway server.

    Resolved lazily so tests can install a TracerProvider before the first call.

    Returns:
        opentelemetry.trace.Tracer for this instrumentation scope.
    """
    return otel_trace.get_tracer(__name__)


def _get_meter() -> otel_metrics.Meter:
    """Return the OTel meter for the Action Gateway server.

    Resolved lazily so tests can install a MeterProvider before the first call.

    Returns:
        opentelemetry.metrics.Meter for this instrumentation scope.
    """
    return otel_metrics.get_meter(__name__)


def create_app(registry: AdapterRegistry) -> FastAPI:
    """Create and return the FastAPI application with the given registry.

    The registry is captured in the closure and is used for all request
    handling. This factory pattern allows tests to inject a mock registry
    without modifying module-level state.

    Args:
        registry: Pre-built AdapterRegistry containing all registered adapters.

    Returns:
        A configured FastAPI application instance.
    """
    app = FastAPI(title="Action Gateway", description="DPG Action Gateway service")
    FastAPIInstrumentor.instrument_app(app)

    _m = _get_meter()
    _duration_hist = _m.create_histogram("action.execute.duration_ms", unit="ms", description="Duration of adapter execute calls in milliseconds.")
    _success_counter = _m.create_counter("action.execute.success_total", description="Count of successful adapter execute calls.")
    _failure_counter = _m.create_counter("action.execute.failure_total", description="Count of failed adapter execute calls.")

    @app.get("/tools", response_model=ToolsResponse)
    async def get_tools() -> ToolsResponse:
        """Return all tool definitions available in the registry.

        Returns:
            ToolsResponse containing a list of all ToolDefinitions.
        """
        start = time.time()
        definitions = registry.get_all_tool_definitions()
        logger.info(
            "get_tools",
            extra={
                "operation": "server.get_tools",
                "status": "success",
                "tool_count": len(definitions),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return ToolsResponse(tools=definitions)

    @app.post("/execute", response_model=ExecuteResponse)
    async def execute_tool(request: ExecuteRequest) -> ExecuteResponse:
        """Execute a single tool call and return the normalised result.

        Resolves the adapter for the requested tool, delegates execution, and
        maps the ToolResult back to an ExecuteResponse. Unknown tool names are
        returned as a structured error rather than an HTTP error code so that
        Agent Core can handle them in the tool loop.

        Args:
            request: ExecuteRequest carrying tool_name, tool_use_id,
                input_params, and optional session_id.

        Returns:
            ExecuteResponse with success=True on success, or success=False
            with an error string for unknown tools or adapter failures.
        """
        start = time.time()

        try:
            adapter = registry.resolve(request.tool_name)
        except KeyError:
            logger.warning(
                "execute_tool_unknown",
                extra={
                    "operation": "server.execute_tool",
                    "status": "failure",
                    "error": f"unknown_tool: {request.tool_name}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ExecuteResponse(
                tool_use_id=request.tool_use_id,
                tool_name=request.tool_name,
                success=False,
                result={},
                error=f"unknown_tool: {request.tool_name}",
            )

        adapter_type: str = adapter.config.get("type", "unknown")
        category: str = adapter.config.get("category", "read")

        with _get_tracer().start_as_current_span("action.execute") as span:
            span.set_attribute("tool_name", request.tool_name)
            span.set_attribute("adapter_type", adapter_type)
            span.set_attribute("category", category)
            span.set_attribute("session_id", request.session_id or "")

            result = await adapter.execute(
                request.tool_name,
                request.input_params,
                request.session_id,
                request.user_id,
            )

            if not result.success:
                span.record_exception(Exception(result.error or "adapter_failure"))

        latency_ms = int((time.time() - start) * 1000)
        _duration_hist.record(latency_ms, {"tool_name": request.tool_name, "adapter_type": adapter_type})
        if result.success:
            _success_counter.add(1, {"tool_name": request.tool_name})
        else:
            _failure_counter.add(1, {"tool_name": request.tool_name})

        logger.info(
            "execute_tool",
            extra={
                "operation": "server.execute_tool",
                "status": "success" if result.success else "failure",
                "tool_name": request.tool_name,
                "latency_ms": latency_ms,
            },
        )
        return ExecuteResponse(
            tool_use_id=request.tool_use_id,
            tool_name=result.tool_name,
            success=result.success,
            result=result.result,
            result_text=result.result_text,
            error=result.error,
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return the health status of each unique registered adapter.

        Calls health_check() on each unique adapter instance and assembles an
        adapter_status dict keyed by adapter type and id. The overall status is
        "healthy" if all adapters are healthy, "degraded" otherwise.

        Returns:
            HealthResponse with overall status string and per-adapter booleans.
        """
        start = time.time()
        adapter_status: dict[str, bool] = {}
        seen_adapter_ids: set[int] = set()

        for tool_name, adapter in registry._adapters.items():
            adapter_id = id(adapter)
            if adapter_id in seen_adapter_ids:
                continue
            seen_adapter_ids.add(adapter_id)
            adapter_key = tool_name
            adapter_status[adapter_key] = adapter.health_check()

        overall = "healthy" if all(adapter_status.values()) else "degraded"
        if not adapter_status:
            overall = "healthy"

        logger.info(
            "health_check",
            extra={
                "operation": "server.health",
                "status": overall,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return HealthResponse(status=overall, adapters=adapter_status)

    # ------------------------------------------------------------------
    # Mock upstream endpoints (GH-151 follow-up)
    # ------------------------------------------------------------------
    # Deterministic canned responses backing the ``get_profile``,
    # ``update_profile``, and ``apply_job`` tools in the KKB config. They
    # live on the Action Gateway itself so the existing RestApiAdapter
    # can call them via http://action_gateway:9999/mock/... without any
    # extra service, while still exercising the full tool → HTTP →
    # response-shaping path the real connectors will follow later.
    #
    # These endpoints are demo-grade fixtures; switch each tool's
    # base_url to a real service once one exists and drop these routes.

    _MOCK_PROFILE: dict = {
        "profile_id": "prof_mock_0001",
        "trade": "electrician",
        "location": "Hubli",
        "age": 28,
        "languages": ["Hindi", "Kannada"],
        "years_experience": 5,
        "certifications": ["ITI"],
        "preferred_work_mode": ["on-site-no-shift"],
        "monthly_in_hand_expected": 15000,
        "language_preference": "hindi",
        "actions_taken": [],
    }

    @app.get("/mock/profile/{user_id}")
    async def mock_get_profile(user_id: str) -> dict:
        """Return a deterministic canned profile.

        Backs the ``get_profile`` tool. ``user_id`` is echoed back in the
        response so call logs can correlate, but the payload is the same
        ``_MOCK_PROFILE`` dict for every caller — this is a demo fixture,
        not real storage.
        """
        logger.info(
            "mock.get_profile",
            extra={
                "operation": "mock.get_profile",
                "status": "success",
                "user_id": user_id or "",
            },
        )
        return {**_MOCK_PROFILE, "user_id": user_id or ""}

    @app.post("/mock/profile/{user_id}")
    async def mock_update_profile(user_id: str, body: dict) -> dict:
        """Acknowledge a profile update without persisting anything.

        Backs the ``update_profile`` tool. Returns which fields the LLM
        attempted to update so the bot can confirm back to the caller
        ("updated your location to Pune") without us needing a real
        write path.
        """
        updated = [k for k in (body or {}).keys() if k not in ("user_id",)]
        logger.info(
            "mock.update_profile",
            extra={
                "operation": "mock.update_profile",
                "status": "success",
                "user_id": user_id or "",
                "updated_fields": updated,
            },
        )
        return {
            "status": "ok",
            "profile_id": _MOCK_PROFILE["profile_id"],
            "user_id": user_id or "",
            "updated_fields": updated,
        }

    @app.post("/mock/apply")
    async def mock_apply_job(body: dict) -> dict:
        """Acknowledge a job application submission.

        Backs the ``apply_job`` tool. Requires both ``job_id`` and
        ``profile_id`` in the body; returns 400 if either is missing so
        the LLM gets a structured error it can react to.
        """
        body = body or {}
        job_id = (body.get("job_id") or "").strip()
        profile_id = (body.get("profile_id") or "").strip()
        if not job_id or not profile_id:
            logger.warning(
                "mock.apply_job_bad_request",
                extra={
                    "operation": "mock.apply_job",
                    "status": "failure",
                    "missing": [
                        k for k, v in (("job_id", job_id), ("profile_id", profile_id))
                        if not v
                    ],
                },
            )
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail="job_id and profile_id are both required",
            )
        logger.info(
            "mock.apply_job",
            extra={
                "operation": "mock.apply_job",
                "status": "success",
                "job_id": job_id,
                "profile_id": profile_id,
            },
        )
        return {
            "status": "submitted",
            "application_id": f"app_{job_id}_{profile_id[-4:]}",
            "job_id": job_id,
            "profile_id": profile_id,
            "expected_callback_within_hours": 24,
            "employer_name": "Sundaram Electricals",
        }

    return app
