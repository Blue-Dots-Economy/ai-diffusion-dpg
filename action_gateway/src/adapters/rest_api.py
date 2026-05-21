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
import re
import string as _string
import time
from typing import Optional
from urllib.parse import quote as _url_quote

import httpx
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


# Sentinel returned by _render_body_template when a value resolves to nothing.
# Surfaced upward so the enclosing dict/list can drop the field entirely
# rather than emit an empty string or null.
_DROP = object()

# Whole-value placeholder: the string is exactly "{name}" with nothing else.
# Matches Python-identifier-shaped keys (letters/digits/underscore, no leading digit).
_WHOLE_PLACEHOLDER_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Embedded placeholder: a "{name}" occurrence somewhere inside a larger string.
_EMBEDDED_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _PathDefaultEmpty(dict):
    """``str.format_map`` helper — unbound ``{placeholder}`` → empty string.

    Preserves the historical behaviour where ``{user_id}`` / ``{session_id}``
    silently substituted to empty when the caller didn't supply them. Now
    applies to any LLM input_param placeholder too.
    """

    def __missing__(self, key: str) -> str:
        return ""


def _path_placeholders(path: str) -> set[str]:
    """Return the placeholder names referenced by ``{name}`` in a path template.

    Used to identify which LLM input_params have been consumed by path
    substitution so they don't get re-sent via httpx's ``params=`` kwarg —
    which would otherwise strip the URL's existing query string.
    """
    return {
        field_name
        for _, field_name, _, _ in _string.Formatter().parse(path)
        if field_name
    }


def _quote_for_path(value) -> str:
    """URL-encode a value for safe interpolation into a path/query string.

    Leaves alphanumerics and the standard unreserved set alone; escapes
    spaces, ``+``, ``&``, ``=``, and so on so a value like ``"New Delhi"``
    becomes ``"New%20Delhi"`` rather than corrupting the URL.
    """
    return _url_quote(str(value), safe="")


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


def _render_body_template(template, values: dict):
    """Walk a body template tree, substituting ``{placeholder}`` strings.

    Substitution rules:
      - A string equal to exactly ``"{key}"`` is replaced by the raw value of
        ``values[key]``, preserving type (lists, dicts, ints, bools stay
        intact).
      - A string containing ``"{key}"`` embedded among other characters is
        rendered via standard string substitution; non-string values are
        ``str()``-cast.
      - If a placeholder's key is missing from ``values``, or its value is
        ``None`` or an empty string, the enclosing field is dropped from the
        parent dict/list. After substitution, dicts that became empty are
        also dropped from their parent — so optional nested branches vanish
        cleanly when the user did not supply any of their fields.
      - Strings with no placeholders, and non-string scalars, copy through
        unchanged.
      - Nested dicts and lists are walked recursively.

    Args:
        template: The template tree. Top-level may be a dict, list, str, or
            scalar — but the adapter only invokes this helper when the
            top-level template is a dict or list.
        values: Merged dict of static + agent params, used as the
            substitution source.

    Returns:
        The rendered structure. May return :data:`_DROP` when the entire
        template resolved to nothing — the caller is expected to substitute
        an empty dict in that case.
    """
    if isinstance(template, dict):
        out: dict = {}
        for k, v in template.items():
            rendered = _render_body_template(v, values)
            if rendered is _DROP:
                continue
            if isinstance(rendered, dict) and not rendered:
                # Nested dict pruned to empty → drop from parent too.
                continue
            out[k] = rendered
        return out
    if isinstance(template, list):
        rendered_items: list = []
        for item in template:
            r = _render_body_template(item, values)
            if r is _DROP:
                continue
            if isinstance(r, dict) and not r:
                continue
            rendered_items.append(r)
        return rendered_items
    if isinstance(template, str):
        whole = _WHOLE_PLACEHOLDER_RE.match(template)
        if whole:
            key = whole.group(1)
            val = values.get(key)
            if val is None or val == "":
                return _DROP
            return val
        keys = _EMBEDDED_PLACEHOLDER_RE.findall(template)
        if not keys:
            return template
        for k in keys:
            val = values.get(k)
            if val is None or val == "":
                return _DROP
        return _EMBEDDED_PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), template)
    # int, bool, float, None — copy through.
    return template


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

    Request body shape (non-GET methods)
    ------------------------------------
    By default the adapter sends ``json=all_params`` — a flat dict of merged
    static + agent params — as the JSON body. When the endpoint declares an
    optional ``body_template`` (dict or list), :func:`_render_body_template`
    walks the template at call time, substituting ``{placeholder}`` strings
    from ``all_params``. This lets a YAML author declare arbitrary nested
    body shapes (e.g. ``source_item.item_id``) without asking the LLM to
    reconstruct protocol-static strings on every call. GET requests ignore
    ``body_template`` — their params continue to flow into the query string.

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
        ID — the framework substitutes it from session context. The same
        mechanism also substitutes any LLM-supplied ``input_param`` into
        the path (e.g. ``…?item_state[jobProviderLocation]={location}``),
        so domain configs can declare bracketed query keys with dynamic
        values without bracketed JSON-Schema property names (which
        Anthropic rejects). Values are URL-encoded on substitution; LLM
        input_params consumed by path placeholders are excluded from the
        GET ``params=`` kwarg so httpx doesn't double-send them or strip
        the path's existing query string.

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
        raw_path: str = endpoint.get("path", "")
        input_params: dict = dict(params or {})
        # Path substitution context: caller identity + LLM input_params.
        # Static params are intentionally NOT included — they always go via
        # the query string / body, never into path placeholders. Values are
        # URL-encoded so a value like "New Delhi" becomes "New%20Delhi".
        path_context = _PathDefaultEmpty(
            session_id=_quote_for_path(session_id or ""),
            user_id=_quote_for_path(user_id or ""),
            **{k: _quote_for_path(v) for k, v in input_params.items()},
        )
        path = raw_path.format_map(path_context)
        url = f"{self._base_url}{path}"
        # LLM input_params consumed by path placeholders — exclude these
        # from the GET ``params=`` kwarg below so we don't send them twice
        # and so httpx doesn't strip the path's existing query string.
        path_consumed = _path_placeholders(raw_path) & set(input_params.keys())

        # Merge agent params with static params (full dict; body_template
        # still sees everything, including path-consumed names).
        all_params: dict = dict(input_params)
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
                    # Exclude path-consumed param names from the GET query —
                    # they've already been baked into the URL via path
                    # substitution. Sending them again via ``params=`` would
                    # (a) duplicate them and (b) make httpx replace the
                    # path's existing query string with this dict, dropping
                    # any literal ``?key=value`` segments. Pass ``None`` if
                    # nothing remains so httpx leaves the URL untouched.
                    query_params = {
                        k: v for k, v in all_params.items()
                        if k not in path_consumed
                    }
                    response = await self._http_client.request(
                        method=method,
                        url=url,
                        params=query_params or None,
                        headers=headers,
                        timeout=timeout_s,
                    )
                else:
                    body_template = endpoint.get("body_template")
                    if body_template is not None:
                        rendered = _render_body_template(body_template, all_params)
                        body = {} if rendered is _DROP else rendered
                    else:
                        body = all_params
                    response = await self._http_client.request(
                        method=method,
                        url=url,
                        json=body,
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
