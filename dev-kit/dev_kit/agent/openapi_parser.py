"""
dev-kit/dev_kit/agent/openapi_parser.py

Parses OpenAPI 3.0/3.1 specs to extract tool-building data for the Action Gateway.

Given an OpenAPI spec dict (already parsed from JSON or YAML), produces a list of
ParsedTool entries that the agent can present to the user for naming and filtering
before writing to action_gateway config.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _camel_to_snake(name: str) -> str:
    """Convert ``camelCase`` / ``PascalCase`` to ``snake_case``.

    ``bookTour`` → ``book_tour``; ``getWeatherForecast`` →
    ``get_weather_forecast``; already-snake names pass through unchanged.
    """
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


@dataclass
class ParsedParam:
    """A single parameter extracted from an OpenAPI endpoint.

    Attributes:
        name: Parameter name.
        source: Always 'agent' — static values are assigned interactively later.
        type: JSON Schema type string.
        required: Whether this parameter is required.
        description: Human-readable description.
        default: Default value if specified in the spec, or None.
    """

    name: str
    source: str
    type: str
    required: bool
    description: str
    default: Any = None


@dataclass
class ParsedTool:
    """An API endpoint extracted from an OpenAPI spec, ready for tool naming.

    Attributes:
        path: The URL path, e.g. '/search'.
        method: HTTP method in uppercase, e.g. 'POST'.
        description: Human-readable description from summary or operationId.
        base_url: Server base URL from the spec.
        operation_id: Original ``operationId`` from the OpenAPI spec when
            present, else ``None``. Used by ``suggested_id`` to produce a
            human-readable tool name; falls back to the path when absent
            (which is why UUID-style paths produce ugly auto-generated ids
            unless the spec author supplies an ``operationId``).
        params: List of extracted parameters.
        auth_type: Detected auth scheme: 'none', 'api_key', 'bearer', or 'oauth2'.
        auth_header: Header name for api_key auth, or None.
        auth_secret_env_hint: Suggested env var name based on header, or None.
    """

    path: str
    method: str
    description: str
    base_url: str
    operation_id: str | None = None
    params: list[ParsedParam] = field(default_factory=list)
    auth_type: str = "none"
    auth_header: str | None = None
    auth_secret_env_hint: str | None = None

    @property
    def suggested_id(self) -> str:
        """Generate a snake_case suggested tool ID.

        Prefers the spec's ``operationId`` (snake-cased) when present, so a
        spec with ``operationId: bookTour`` yields ``book_tour`` — matching
        what humans intuitively name the tool. Falls back to
        ``{method}_{path_sanitized}`` when no ``operationId`` is supplied,
        which is when UUID-style paths produce auto-generated ids like
        ``post_d394c4e8_...``.

        The returned id is what subsequent phases (workflow, observability,
        review) must use to reference this tool in ``subagent.tools`` and
        ``subagent.system_prompt`` — the tools-phase prompt forbids the LLM
        from renaming it at ``add_tool`` time so chat history and registered
        connector names stay in lockstep.

        Returns:
            Snake_case identifier like 'book_tour', 'get_v1_forecast', or
            'post_apply_job_id'.
        """
        if self.operation_id:
            return _camel_to_snake(self.operation_id)
        path_part = (
            self.path.strip("/")
            .replace("/", "_")
            .replace("{", "")
            .replace("}", "")
            .replace("-", "_")
        )
        if not path_part:
            path_part = "root"
        return f"{self.method.lower()}_{path_part}"


def parse_openapi_spec(spec: dict[str, Any]) -> list[ParsedTool]:
    """Extract tool definitions from an OpenAPI 3.0/3.1 specification dict.

    Parses paths, methods, parameters (path, query, body), auth schemes,
    and produces one ParsedTool per path+method combination.

    Args:
        spec: Parsed OpenAPI spec as a dict. Must contain a 'paths' key.

    Returns:
        List of ParsedTool entries, one per path+method combination.

    Raises:
        ValueError: If 'paths' key is absent from the spec.
    """
    if "paths" not in spec:
        raise ValueError("OpenAPI spec must contain a 'paths' key")

    base_url = _extract_base_url(spec)
    auth_type, auth_header = _extract_global_auth(spec)
    auth_hint = _make_env_hint(auth_header) if auth_header else None
    paths: dict[str, Any] = spec.get("paths", {})

    tools: list[ParsedTool] = []
    _http_methods = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _http_methods:
                continue
            if not isinstance(operation, dict):
                continue

            description = (
                operation.get("summary")
                or operation.get("description")
                or f"{method.upper()} {path}"
            )

            params = _extract_params(operation, path_item)

            tool = ParsedTool(
                path=path,
                method=method.upper(),
                description=description,
                base_url=base_url,
                operation_id=operation.get("operationId"),
                params=params,
                auth_type=auth_type,
                auth_header=auth_header,
                auth_secret_env_hint=auth_hint,
            )
            tools.append(tool)
            logger.debug(
                "openapi_parse_endpoint",
                extra={
                    "operation": "openapi_parser.parse_openapi_spec",
                    "status": "success",
                    "path": path,
                    "method": method.upper(),
                    "param_count": len(params),
                },
            )

    return tools


def _extract_base_url(spec: dict[str, Any]) -> str:
    """Extract the first server URL from the spec.

    Args:
        spec: OpenAPI spec dict.

    Returns:
        First server URL with trailing slash stripped, or empty string if none.
    """
    servers = spec.get("servers", [])
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url", "").rstrip("/")
    return ""


def _extract_global_auth(spec: dict[str, Any]) -> tuple[str, str | None]:
    """Extract global auth scheme from components/securitySchemes.

    Examines the first security requirement at spec level to find
    the active scheme. Falls back to the first defined scheme.

    Args:
        spec: OpenAPI spec dict.

    Returns:
        Tuple of (auth_type, auth_header). auth_type is one of:
        'none', 'api_key', 'bearer', 'oauth2'. auth_header is the header
        name for api_key, or None otherwise.
    """
    components = spec.get("components", {})
    schemes: dict[str, Any] = components.get("securitySchemes", {})

    global_security = spec.get("security", [])
    active_scheme_name: str | None = None
    if global_security and isinstance(global_security[0], dict):
        active_scheme_name = next(iter(global_security[0]), None)

    if active_scheme_name and active_scheme_name in schemes:
        scheme = schemes[active_scheme_name]
    elif schemes:
        scheme = next(iter(schemes.values()))
    else:
        return "none", None

    scheme_type = scheme.get("type", "")
    if scheme_type == "apiKey":
        location = scheme.get("in", "header")
        header_name = scheme.get("name", "") if location == "header" else None
        return "api_key", header_name or None
    if scheme_type == "http" and scheme.get("scheme", "").lower() == "bearer":
        return "bearer", None
    if scheme_type == "oauth2":
        return "oauth2", None

    return "none", None


def _make_env_hint(header_name: str | None) -> str | None:
    """Convert a header name to a suggested env var name.

    Args:
        header_name: Header name like 'X-API-KEY'.

    Returns:
        Suggested env var like 'API_KEY', or None if header_name is None.
    """
    if not header_name:
        return None
    return header_name.upper().replace("-", "_").lstrip("X_")


def _extract_params(operation: dict[str, Any], path_item: dict[str, Any]) -> list[ParsedParam]:
    """Extract all parameters from an operation (path, query, and request body).

    Combines path-level parameters with operation-level parameters.
    Request body properties are extracted as agent-sourced params.

    Args:
        operation: The operation dict from the OpenAPI paths section.
        path_item: The parent path-item dict (may contain shared parameters).

    Returns:
        List of ParsedParam entries with no duplicates.
    """
    params: list[ParsedParam] = []
    seen: set[str] = set()

    # Collect path-level + operation-level explicit parameters
    all_param_defs = list(path_item.get("parameters", [])) + list(
        operation.get("parameters", [])
    )
    for param_def in all_param_defs:
        if not isinstance(param_def, dict):
            continue
        name = param_def.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)

        schema = param_def.get("schema", {})
        params.append(
            ParsedParam(
                name=name,
                source="agent",
                type=schema.get("type", "string"),
                required=bool(param_def.get("required", False)),
                description=param_def.get("description", schema.get("description", "")),
                default=schema.get("default"),
            )
        )

    # Extract request body properties
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    body_schema: dict[str, Any] = {}

    for media_type in ("application/json", "application/x-www-form-urlencoded"):
        if media_type in content:
            body_schema = content[media_type].get("schema", {})
            break

    if body_schema.get("type") == "object":
        required_fields: list[str] = body_schema.get("required", [])
        for prop_name, prop_schema in body_schema.get("properties", {}).items():
            if prop_name in seen:
                continue
            seen.add(prop_name)
            params.append(
                ParsedParam(
                    name=prop_name,
                    source="agent",
                    type=prop_schema.get("type", "string"),
                    required=prop_name in required_fields,
                    description=prop_schema.get("description", ""),
                    default=prop_schema.get("default"),
                )
            )

    return params
