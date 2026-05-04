"""REST API adapter for the Action Gateway block.

This module provides RestApiAdapter, which wraps a single REST connector
configured via the domain YAML. It handles auth injection, parameter merging,
HTTP execution, error normalisation, and response truncation so that Agent Core
always receives a uniform ToolResult regardless of the upstream API behaviour.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import httpx
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


def _get_tracer() -> otel_trace.Tracer:
    """Return the OTel tracer for RestApiAdapter.

    Resolved lazily so tests can install a TracerProvider before the first call.

    Returns:
        opentelemetry.trace.Tracer for this instrumentation scope.
    """
    return otel_trace.get_tracer(__name__)


def _get_meter() -> otel_metrics.Meter:
    """Return the OTel meter for RestApiAdapter.

    Resolved lazily so tests can install a MeterProvider before the first call.

    Returns:
        opentelemetry.metrics.Meter for this instrumentation scope.
    """
    return otel_metrics.get_meter(__name__)

_DEFAULT_TIMEOUT_MS = 5000
_DEFAULT_MAX_SIZE_CHARS = 4000


def _get_nested(d, path: str):
    """Resolve a dot-notation path (e.g. 'a.b.c') against a nested dict."""
    current = d
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _apply_projection(result_dict: dict, projection: Optional[dict]):
    """Project a raw response dict into a slim LLM-visible shape.

    When ``projection`` is None or empty, returns None so the caller falls
    back to the raw dict. When ``list_key`` is set, projects each list element
    into a flat dict of {target_name: scalar}; otherwise projects the root.

    Args:
        result_dict: Raw parsed JSON response body.
        projection: Optional config with keys ``list_key`` (str) and
            ``fields`` (dict of target_name → dot-path into each item).

    Returns:
        A list (if list_key set), a dict (if not), or None if no projection.
    """
    if not projection or not isinstance(projection, dict):
        return None
    fields: dict = projection.get("fields") or {}
    if not fields:
        return None
    list_key: str = projection.get("list_key", "") or ""

    def _project_item(item):
        if not isinstance(item, dict):
            return None
        return {target: _get_nested(item, src) for target, src in fields.items()}

    if list_key:
        items = _get_nested(result_dict, list_key)
        if not isinstance(items, list):
            return []
        return [p for p in (_project_item(it) for it in items) if p is not None]
    return _project_item(result_dict)


class RestApiAdapter(ToolAdapter):
    """Adapter that executes tool calls against a single REST API endpoint.

    One instance is created per REST connector config block loaded from the
    domain YAML at startup. The adapter resolves auth secrets from environment
    variables, builds ToolDefinitions from the endpoint config, merges agent-
    supplied params with static params, and returns normalised ToolResults.

    Attributes:
        config: Raw adapter config dict.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the adapter, resolve auth secret, and build HTTP client.

        Args:
            config: Adapter-level config dict containing base_url, auth,
                endpoints, and optional response settings.

        Raises:
            ValueError: If the auth secret env var is configured but not set
                in the environment.
        """
        super().__init__(config)

        self._base_url: str = config.get("base_url", "").rstrip("/")
        self._timeout_ms: int = config.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
        self._max_size_chars: int = (
            config.get("response", {}).get("max_size_chars", _DEFAULT_MAX_SIZE_CHARS)
        )

        auth = config.get("auth", {})
        self._auth_type: str = auth.get("type", "none")
        self._auth_header: Optional[str] = auth.get("header")
        self._auth_secret: Optional[str] = None

        secret_env = auth.get("secret_env")
        if secret_env:
            secret_val = os.environ.get(secret_env)
            if secret_val is None:
                raise ValueError(
                    f"Required auth env var '{secret_env}' is not set for adapter '{config.get('id')}'"
                )
            self._auth_secret = secret_val

        # Lazily-created HTTP client; replaced by patch.object in tests.
        self._http_client: httpx.AsyncClient = httpx.AsyncClient()

        _m = _get_meter()
        self._response_size_hist = _m.create_histogram(
            "action.response.size_bytes", unit="By", description="Response payload size in bytes before truncation."
        )
        self._truncated_counter = _m.create_counter(
            "action.response.truncated_total", description="Count of responses truncated to max_size_chars."
        )

        logger.debug(
            "rest_api_adapter_init",
            extra={
                "operation": "RestApiAdapter.__init__",
                "status": "success",
                "adapter_id": config.get("id"),
                "auth_type": self._auth_type,
            },
        )

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return a single ToolDefinition built from the endpoint config.

        Only parameters with source='agent' are included in the input_schema;
        source='static' parameters are excluded because they are injected
        automatically at execution time and must not be exposed to the LLM.

        Returns:
            A list containing exactly one ToolDefinition for the first
            configured endpoint.
        """
        endpoint = self.config.get("endpoints", [{}])[0]
        params = endpoint.get("params", [])

        agent_params = [p for p in params if p.get("source") == "agent"]

        properties: dict = {}
        required: list[str] = []
        for p in agent_params:
            param_schema: dict = {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            }
            # OpenAI's function-calling validation rejects array params
            # without an `items` schema; Anthropic accepts the same shape.
            # When the domain config declares `items` use it verbatim;
            # otherwise default to `{"type": "string"}` so the most common
            # case (string arrays) works without per-domain configuration.
            if param_schema["type"] == "array":
                param_schema["items"] = p.get("items") or {"type": "string"}
            properties[p["name"]] = param_schema
            if p.get("required", False):
                required.append(p["name"])

        input_schema: dict = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        tool = ToolDefinition(
            name=self.config.get("id", ""),
            description=self.config.get("description", ""),
            input_schema=input_schema,
            category=self.config.get("category", "read"),
        )
        return [tool]

    async def execute(
        self,
        tool_name: str,
        params: dict,
        session_id: str,
        user_id: str = "",
    ) -> ToolResult:
        """Execute the configured REST endpoint and return a normalised result.

        Merges agent-supplied params with static params, substitutes
        ``{session_id}`` / ``{user_id}`` placeholders in the endpoint path,
        injects auth headers, dispatches the HTTP request, and normalises
        the response into a ToolResult. Never raises — all exceptions are
        caught and surfaced as failed ToolResults.

        Path templating lets a tool like ``get_profile`` point at
        ``/profile/{user_id}`` without asking the LLM to pass the caller's
        ID — the framework substitutes it from session context.

        Args:
            tool_name: Name of the tool to execute (used in error messages).
            params: Agent-supplied parameters from the LLM tool_use block.
            session_id: Session identifier for log correlation and path
                templating; may be empty.
            user_id: Stable user identifier for path templating; may be empty.

        Returns:
            ToolResult with success=True and populated result/result_text on
            success, or success=False with a structured error string on failure.
        """
        start = time.time()
        endpoint = self.config.get("endpoints", [{}])[0]
        method: str = endpoint.get("method", "GET").upper()
        path: str = endpoint.get("path", "")
        # Substitute context variables into the path. Empty strings are passed
        # through unchanged so a path like /profile/{user_id} with no user_id
        # produces /profile/ — which the backing endpoint can 404 or return an
        # empty profile for, matching "no profile" semantics.
        path = path.format(session_id=session_id or "", user_id=user_id or "")
        url = f"{self._base_url}{path}"

        # Merge agent params with static params
        all_params: dict = dict(params)
        for p in endpoint.get("params", []):
            if p.get("source") == "static":
                all_params[p["name"]] = p.get("value")

        # Build auth headers
        headers: dict = {}
        if self._auth_type == "api_key" and self._auth_header and self._auth_secret:
            headers[self._auth_header] = self._auth_secret
        elif self._auth_type == "bearer" and self._auth_secret:
            headers["Authorization"] = f"Bearer {self._auth_secret}"

        timeout_s = self._timeout_ms / 1000.0

        logger.debug(
            "rest_api_request tool=%s method=%s url=%s params=%s",
            tool_name, method, url, all_params,
            extra={
                "operation": "RestApiAdapter.execute",
                "status": "pending",
                "tool_name": tool_name,
                "session_id": session_id,
                "method": method,
                "url": url,
                "params": all_params,
            },
        )

        http_start = time.time()
        try:
            with _get_tracer().start_as_current_span("action.rest_api.http_call") as http_span:
                http_span.set_attribute("http.method", method)
                http_span.set_attribute("http.url", url)
                if method == "GET":
                    response = await self._http_client.request(
                        method=method,
                        url=url,
                        params=all_params,
                        headers=headers,
                        timeout=timeout_s,
                    )
                else:
                    response = await self._http_client.request(
                        method=method,
                        url=url,
                        json=all_params,
                        headers=headers,
                        timeout=timeout_s,
                    )
                http_span.set_attribute("http.status_code", response.status_code)
                http_span.set_attribute("latency_ms", int((time.time() - http_start) * 1000))

            logger.debug(
                "rest_api_response tool=%s status=%s body=%s",
                tool_name, response.status_code, response.text[:2000],
                extra={
                    "operation": "RestApiAdapter.execute",
                    "status": "received",
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "status_code": response.status_code,
                    "body": response.text[:2000],
                    "latency_ms": int((time.time() - http_start) * 1000),
                },
            )
        except httpx.TimeoutException as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.warning(
                "rest_api_timeout",
                extra={
                    "operation": "RestApiAdapter.execute",
                    "status": "failure",
                    "error": "adapter_timeout",
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result={},
                success=False,
                error=f"adapter_timeout: {tool_name}",
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.time() - start) * 1000)
            error_msg = f"adapter_error: {type(exc).__name__}: {exc}"
            logger.error(
                "rest_api_error",
                extra={
                    "operation": "RestApiAdapter.execute",
                    "status": "failure",
                    "error": error_msg,
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result={},
                success=False,
                error=error_msg,
            )

        latency_ms = int((time.time() - start) * 1000)

        if response.is_error:
            error_msg = f"http_error: {response.status_code}"
            logger.warning(
                "rest_api_http_error",
                extra={
                    "operation": "RestApiAdapter.execute",
                    "status": "failure",
                    "error": error_msg,
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result={},
                success=False,
                error=error_msg,
            )

        try:
            result_dict: dict = response.json()
        except Exception:
            result_dict = {}

        projected = _apply_projection(result_dict, self.config.get("response", {}).get("projection"))
        payload = projected if projected is not None else result_dict
        full_text = json.dumps(payload)

        # When the payload is a list (projected with list_key), drop items from
        # the end until the serialised form fits under max_size_chars. Keeps the
        # LLM input as valid JSON instead of a mid-object character chop.
        if isinstance(payload, list) and len(full_text) > self._max_size_chars:
            trimmed = list(payload)
            while trimmed and len(json.dumps(trimmed)) > self._max_size_chars:
                trimmed.pop()
            result_text = json.dumps(trimmed)
        else:
            result_text = full_text[: self._max_size_chars]

        self._response_size_hist.record(len(full_text.encode()), {"tool_name": tool_name})
        if len(full_text) > self._max_size_chars:
            self._truncated_counter.add(1, {"tool_name": tool_name})

        logger.info(
            "rest_api_execute",
            extra={
                "operation": "RestApiAdapter.execute",
                "status": "success",
                "tool_name": tool_name,
                "session_id": session_id,
                "latency_ms": latency_ms,
            },
        )
        return ToolResult(
            tool_use_id="",
            tool_name=tool_name,
            result=result_dict,
            success=True,
            result_text=result_text,
        )

    def health_check(self) -> bool:
        """Check whether the backing REST service is reachable.

        Issues a synchronous HEAD request to the base URL and returns True if
        the response status is below 500. Returns False on any error or
        server-side failure.

        Tools whose config sets ``health_check.enabled: false`` skip the HTTP
        probe entirely and always return True. This is important for
        self-referential mock connectors — an adapter configured with
        ``base_url: http://action_gateway:9999`` would otherwise deadlock
        the single uvicorn event loop thread (the synchronous ``httpx.head``
        blocks while the running ``/health`` handler holds the loop), making
        the docker healthcheck time out.

        Returns:
            True if the service responds with a status < 500; False otherwise.
        """
        hc_cfg = self.config.get("health_check", {}) or {}
        if hc_cfg.get("enabled", True) is False:
            return True
        try:
            resp = httpx.head(self._base_url, timeout=5.0)
            return resp.status_code < 500
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "rest_api_health_check_failed",
                extra={
                    "operation": "RestApiAdapter.health_check",
                    "status": "failure",
                    "error": str(exc),
                    "adapter_id": self.config.get("id"),
                },
            )
            return False
