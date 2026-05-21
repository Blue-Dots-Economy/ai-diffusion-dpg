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
import random
import time

from fastapi import FastAPI, Request
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
        """Return a canned profile or an empty "new user" response at random.

        Backs the ``get_profile`` tool. For demos, ~33% of calls return the
        full ``_MOCK_PROFILE`` and ~67% return ``{"user_id": ...}`` — the
        empty-object shape the connector documents for new users — so the
        bot exercises both the returning-user and onboarding paths during a
        single session instead of always hitting the same static record.
        """
        if random.random() > 0.67:
            logger.info(
                "mock.get_profile",
                extra={
                    "operation": "mock.get_profile",
                    "status": "success",
                    "user_id": user_id or "",
                    "outcome": "found",
                },
            )
            return {**_MOCK_PROFILE, "user_id": user_id or ""}
        logger.info(
            "mock.get_profile",
            extra={
                "operation": "mock.get_profile",
                "status": "success",
                "user_id": user_id or "",
                "outcome": "not_found",
            },
        )
        return {"user_id": user_id or ""}

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

    # ------------------------------------------------------------------
    # Blue Dots Economy mocks (#TODO: replace with real endpoints).
    #
    # Four endpoints back the four tools declared in
    # ``dev-kit/configs/blue-dots-economy/action_gateway.yaml``:
    #
    #   GET  /mock/blue_dots/seeker_fetch   — fetch_profile by phone
    #   GET  /mock/blue_dots/job_fetch      — fetch_jobs by city/role
    #   POST /mock/blue_dots/onboard        — create/update user+profile
    #   POST /mock/blue_dots/apply          — submit application
    #
    # Behaviour is deterministic from the input phone so the three
    # disambiguation scenarios (zero / single / multiple profiles) are
    # testable without real data:
    #   - phone ending in 0000 → zero profiles  (new-user path)
    #   - phone ending in 9999 → five profiles  (multi-profile path)
    #   - any other phone      → one profile    (returning-user path)
    # ------------------------------------------------------------------

    _BLUE_DOTS_CANNED_PROFILE_FIELDS = {
        "name": "Ashwin Seeker",
        "gender": "male",
        "location": "Bengaluru",
        "workExperience": "Worked before",
        "natureOfJobsInterestedIn": "Full-time",
        "nameOfJobRolesInterestedIn": "Plumber",
        "highestQualificationOrSkill": "ITI",
        "workExperienceYearsConditional": "2 Years",
        "age": 28,
    }

    _BLUE_DOTS_MULTI_PROFILES = [
        {"name": "Ashwin Seeker", "nameOfJobRolesInterestedIn": "Plumber",     "location": "Bengaluru"},
        {"name": "Ashwin Test",   "nameOfJobRolesInterestedIn": "Fitter",      "location": "Kannur"},
        {"name": "Ashwin Raj",    "nameOfJobRolesInterestedIn": "Electrician", "location": "Hubballi"},
        {"name": "Agent-1",       "nameOfJobRolesInterestedIn": "Welder",      "location": "Bengaluru"},
        {"name": "Demo Seeker",   "nameOfJobRolesInterestedIn": "Plumber",     "location": "Mysuru"},
    ]

    _BLUE_DOTS_CANNED_JOBS = [
        {
            "item_id": "job_plumber_bengaluru_acme",
            "city": "Bengaluru", "role": "Plumber",
            "company_name": "Acme Retail Pvt Ltd", "salary_range": "20000-30000",
            "employment_type": "full_time",
        },
        {
            "item_id": "job_plumber_bengaluru_zenith",
            "city": "Bengaluru", "role": "Plumber",
            "company_name": "Zenith Services", "salary_range": "18000-25000",
            "employment_type": "full_time",
        },
        {
            "item_id": "job_electrician_hubballi_qwerty",
            "city": "Hubballi", "role": "Electrician",
            "company_name": "QWERTY Electricals", "salary_range": "12000-18000",
            "employment_type": "full_time",
        },
        {
            "item_id": "job_fitter_kannur_metro",
            "city": "Kannur", "role": "Fitter",
            "company_name": "Metro Industries", "salary_range": "15000-22000",
            "employment_type": "full_time",
        },
        {
            "item_id": "job_welder_bengaluru_arc",
            "city": "Bengaluru", "role": "Welder",
            "company_name": "Arc Welding Co", "salary_range": "20000-28000",
            "employment_type": "full_time",
        },
    ]

    def _bd_profile_item(item_id: str, phone: str, overrides: dict) -> dict:
        """Wrap a profile's item_state in the full fetch response shape."""
        item_state = dict(_BLUE_DOTS_CANNED_PROFILE_FIELDS)
        item_state.update(overrides)
        item_state["phone"] = phone
        return {
            "item_network": "blue_dot",
            "item_domain": "seeker",
            "item_type": "profile_1.0",
            "item_id": item_id,
            "item_instance_url": "http://65.2.66.144:2742",
            "item_schema_url": "http://schemas/blue_dot/network.json#/seeker/profile_1.0",
            "item_state": item_state,
            "item_latitude": 12.9767936,
            "item_longitude": 77.590082,
            "created_by": "mock-user-uuid-from-phone",
            "created_at": "2026-05-15T12:00:00.000Z",
            "updated_at": "2026-05-15T12:00:00.000Z",
        }

    @app.get("/mock/blue_dots/seeker_fetch")
    async def mock_blue_dots_seeker_fetch(request: Request) -> dict:
        """Mock for blue-dots fetch_profile (seeker lookup by phone).

        Reads ``item_state[phone]`` from the query string. Disambiguation
        scenarios are keyed off the trailing digits of the phone (see the
        block comment above) so a single test session can exercise all
        three branches without needing real backend data.
        """
        phone = (
            request.query_params.get("item_state[phone]")
            or request.query_params.get("phone")
            or ""
        ).strip()

        if phone.endswith("0000") or not phone:
            items: list = []
        elif phone.endswith("9999"):
            items = [
                _bd_profile_item(
                    f"mock-profile-multi-{i}",
                    phone,
                    p,
                )
                for i, p in enumerate(_BLUE_DOTS_MULTI_PROFILES)
            ]
        else:
            items = [_bd_profile_item("mock-profile-single", phone, {})]

        logger.info(
            "mock.blue_dots.seeker_fetch",
            extra={
                "operation": "mock.blue_dots.seeker_fetch",
                "status": "success",
                "phone": phone,
                "profile_count": len(items),
            },
        )
        return {"meta": {"total": len(items), "limit": 100, "offset": 0}, "items": items}

    @app.get("/mock/blue_dots/job_fetch")
    async def mock_blue_dots_job_fetch(request: Request) -> dict:
        """Mock for blue-dots fetch_jobs (provider listings by city + role).

        Reads ``item_state[city]`` and ``item_state[role]`` from the query
        string; either may be absent. Returns the subset of the canned job
        list whose fields match the filter (case-insensitive).
        """
        city = (
            request.query_params.get("item_state[city]")
            or request.query_params.get("city")
            or ""
        ).strip().lower()
        role = (
            request.query_params.get("item_state[role]")
            or request.query_params.get("role")
            or ""
        ).strip().lower()

        def _matches(job: dict) -> bool:
            if city and job["city"].lower() != city:
                return False
            if role and job["role"].lower() != role:
                return False
            return True

        items = []
        for j in _BLUE_DOTS_CANNED_JOBS:
            if not _matches(j):
                continue
            items.append({
                "item_network": "blue_dot",
                "item_domain": "provider",
                "item_type": "job_posting_1.0",
                "item_id": j["item_id"],
                "item_instance_url": "http://65.2.66.144:2742",
                "item_schema_url": "http://schemas/blue_dot/network.json#/provider/job_posting_1.0",
                "item_state": {
                    "city": j["city"],
                    "role": j["role"],
                    "company_name": j["company_name"],
                    "salary_range": j["salary_range"],
                    "employment_type": j["employment_type"],
                },
                "item_latitude": 12.9767936,
                "item_longitude": 77.590082,
                "created_by": "mock-employer-uuid",
                "created_at": "2026-05-14T10:00:00.000Z",
                "updated_at": "2026-05-14T10:00:00.000Z",
            })

        logger.info(
            "mock.blue_dots.job_fetch",
            extra={
                "operation": "mock.blue_dots.job_fetch",
                "status": "success",
                "city": city,
                "role": role,
                "job_count": len(items),
            },
        )
        return {"meta": {"total": len(items), "limit": 100, "offset": 0}, "items": items}

    @app.post("/mock/blue_dots/onboard")
    async def mock_blue_dots_onboard(body: dict) -> dict:
        """Mock for the multi-purpose onboard API (create user / create or update profile).

        Body shape:
          ``{"user": {"name": str, "phoneNumber": str}, "profile": {...} | None}``

        Behaviour:
          * ``user.id`` is derived deterministically from the phone so
            repeated calls within the same session return a stable UUID
            that ``apply`` can reuse as ``source_item_owner``.
          * If ``profile`` is omitted, ``profileCreated`` and
            ``profileUpdated`` are both false and the response carries the
            existing profile(s) for the phone (from the seeker_fetch mock).
          * If ``profile.item_id`` is set, the profile is treated as an
            update; otherwise a new ``item_id`` is generated.
        """
        body = body or {}
        user = body.get("user") or {}
        profile_in = body.get("profile")

        name = (user.get("name") or "").strip()
        phone = (user.get("phoneNumber") or "").strip()
        if not name or not phone:
            from fastapi import HTTPException

            logger.warning(
                "mock.blue_dots.onboard_bad_request",
                extra={
                    "operation": "mock.blue_dots.onboard",
                    "status": "failure",
                    "missing": [k for k, v in (("user.name", name), ("user.phoneNumber", phone)) if not v],
                },
            )
            raise HTTPException(status_code=400, detail="user.name and user.phoneNumber are required")

        user_id = f"mock-owner-{phone.lstrip('+').replace(' ', '')}"

        status = {
            "userCreated": False,
            "userExisted": True,
            "profileCreated": False,
            "profileUpdated": False,
            "profileExisted": False,
        }

        if profile_in is not None:
            if profile_in.get("item_id"):
                status["profileUpdated"] = True
                profile_item_id = profile_in["item_id"]
            else:
                status["profileCreated"] = True
                profile_item_id = f"mock-profile-onboard-{phone.lstrip('+')[-4:]}"
            item_state = dict(_BLUE_DOTS_CANNED_PROFILE_FIELDS)
            item_state.update(profile_in.get("item_state") or {})
            item_state["name"] = name
            item_state["phone"] = phone
            profiles = [{
                "item_id": profile_item_id,
                "item_network": "blue_dot",
                "item_domain": "seeker",
                "item_type": "profile_1.0",
                "item_state": item_state,
                "item_latitude": None,
                "item_longitude": None,
                "created_at": "2026-05-20T10:00:00.000Z",
                "updated_at": "2026-05-20T10:00:00.000Z",
            }]
        else:
            # No profile sent — return any pre-existing profiles for this phone.
            # Use the same disambiguation key as seeker_fetch so the apply-time
            # call and the start-of-session call agree.
            if phone.endswith("0000"):
                profiles = []
            elif phone.endswith("9999"):
                profiles = [
                    {
                        "item_id": f"mock-profile-multi-{i}",
                        "item_network": "blue_dot",
                        "item_domain": "seeker",
                        "item_type": "profile_1.0",
                        "item_state": {**_BLUE_DOTS_CANNED_PROFILE_FIELDS, **p, "phone": phone},
                        "item_latitude": None,
                        "item_longitude": None,
                        "created_at": "2026-05-20T10:00:00.000Z",
                        "updated_at": "2026-05-20T10:00:00.000Z",
                    }
                    for i, p in enumerate(_BLUE_DOTS_MULTI_PROFILES)
                ]
            else:
                profiles = [{
                    "item_id": "mock-profile-single",
                    "item_network": "blue_dot",
                    "item_domain": "seeker",
                    "item_type": "profile_1.0",
                    "item_state": {**_BLUE_DOTS_CANNED_PROFILE_FIELDS, "phone": phone, "name": name},
                    "item_latitude": None,
                    "item_longitude": None,
                    "created_at": "2026-05-20T10:00:00.000Z",
                    "updated_at": "2026-05-20T10:00:00.000Z",
                }]
            if not profiles:
                status["profileExisted"] = False
            else:
                status["profileExisted"] = True

        logger.info(
            "mock.blue_dots.onboard",
            extra={
                "operation": "mock.blue_dots.onboard",
                "status": "success",
                "user_id": user_id,
                "profile_count": len(profiles),
                "status_block": status,
            },
        )
        return {
            "user": {
                "id": user_id,
                "name": name,
                "email": None,
                "phoneNumber": phone,
                "role": "user",
            },
            "profiles": profiles,
            "status": status,
        }

    @app.post("/mock/blue_dots/apply")
    async def mock_blue_dots_apply(body: dict) -> dict:
        """Mock for the blue-dots apply (action.perform) endpoint.

        Validates the nested body shape — ``source_item.item_id``,
        ``target_item.item_id``, and ``source_item_owner`` must all be
        present — and returns a synthesised ``application_id`` so the
        bot can confirm the submission to the caller.
        """
        body = body or {}
        source_item = body.get("source_item") or {}
        target_item = body.get("target_item") or {}
        source_owner = (body.get("source_item_owner") or "").strip()
        profile_item_id = (source_item.get("item_id") or "").strip()
        job_item_id = (target_item.get("item_id") or "").strip()

        if not profile_item_id or not job_item_id or not source_owner:
            from fastapi import HTTPException

            logger.warning(
                "mock.blue_dots.apply_bad_request",
                extra={
                    "operation": "mock.blue_dots.apply",
                    "status": "failure",
                    "missing": [
                        k for k, v in (
                            ("source_item.item_id", profile_item_id),
                            ("target_item.item_id", job_item_id),
                            ("source_item_owner", source_owner),
                        ) if not v
                    ],
                },
            )
            raise HTTPException(
                status_code=400,
                detail="source_item.item_id, target_item.item_id, and source_item_owner are required",
            )

        logger.info(
            "mock.blue_dots.apply",
            extra={
                "operation": "mock.blue_dots.apply",
                "status": "success",
                "profile_item_id": profile_item_id,
                "job_item_id": job_item_id,
                "source_item_owner": source_owner,
            },
        )
        return {
            "status": "submitted",
            "application_id": f"bd_app_{job_item_id[-6:]}_{profile_item_id[-6:]}",
            "source_item_id": profile_item_id,
            "target_item_id": job_item_id,
            "source_item_owner": source_owner,
            "expected_callback_within_hours": 24,
        }

    return app
