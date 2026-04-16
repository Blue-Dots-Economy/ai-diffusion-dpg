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

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS = 5000
_DEFAULT_MAX_SIZE_CHARS = 4000


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
            properties[p["name"]] = {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            }
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
        self, tool_name: str, params: dict, session_id: str
    ) -> ToolResult:
        """Execute the configured REST endpoint and return a normalised result.

        Merges agent-supplied params with static params, injects auth headers,
        dispatches the HTTP request, and normalises the response into a
        ToolResult. Never raises — all exceptions are caught and surfaced as
        failed ToolResults.

        Args:
            tool_name: Name of the tool to execute (used in error messages).
            params: Agent-supplied parameters from the LLM tool_use block.
            session_id: Session identifier for log correlation; may be empty.

        Returns:
            ToolResult with success=True and populated result/result_text on
            success, or success=False with a structured error string on failure.
        """
        start = time.time()
        endpoint = self.config.get("endpoints", [{}])[0]
        method: str = endpoint.get("method", "GET").upper()
        path: str = endpoint.get("path", "")
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

        try:
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
        except httpx.TimeoutException:
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

        result_text = json.dumps(result_dict)[: self._max_size_chars]

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

        Returns:
            True if the service responds with a status < 500; False otherwise.
        """
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
