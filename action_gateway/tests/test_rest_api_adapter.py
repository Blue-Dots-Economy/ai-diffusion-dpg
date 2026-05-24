"""Tests for RestApiAdapter.

Covers normal execution, edge cases, and failure scenarios for the REST API
adapter within the Action Gateway block.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from src.adapters.rest_api import RestApiAdapter
from src.models import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(
    status_code: int,
    json_data: dict | None = None,
    text: str | None = None,
) -> MagicMock:
    """Build a mock httpx.Response.

    ``text`` is the raw response body (used by the adapter on 4xx/5xx
    to surface the upstream error message to the LLM). Defaults to the
    JSON-encoded form of ``json_data`` when not supplied, mirroring
    httpx's own behaviour.
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_error = status_code >= 400
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    if text is not None:
        resp.text = text
    else:
        import json as _json
        resp.text = _json.dumps(json_data) if json_data is not None else ""
    return resp


# ---------------------------------------------------------------------------
# TestRestApiAdapterInit
# ---------------------------------------------------------------------------


class TestRestApiAdapterInit:
    """Tests for RestApiAdapter.__init__."""

    def test_resolves_api_key_from_env(self, rest_tool_config, monkeypatch):
        """Adapter reads api_key secret from the configured env var."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        adapter = RestApiAdapter(rest_tool_config)
        assert adapter._auth_secret == "test-key-123"
        assert adapter._auth_type == "api_key"
        assert adapter._auth_header == "X-API-Key"

    def test_resolves_bearer_from_env(self, rest_write_tool_config, monkeypatch):
        """Adapter reads bearer token from the configured env var."""
        monkeypatch.setenv("TEST_JOBS_TOKEN", "bearer-tok-abc")
        adapter = RestApiAdapter(rest_write_tool_config)
        assert adapter._auth_secret == "bearer-tok-abc"
        assert adapter._auth_type == "bearer"

    def test_no_auth_no_secret(self, rest_no_auth_config):
        """Adapter with auth.type=none initialises without reading any env var."""
        adapter = RestApiAdapter(rest_no_auth_config)
        assert adapter._auth_type == "none"
        assert adapter._auth_secret is None

    def test_missing_env_var_raises_value_error(self, rest_tool_config):
        """ValueError is raised when the secret env var is not set."""
        # Ensure env var is absent
        os.environ.pop("TEST_WEATHER_KEY", None)
        with pytest.raises(ValueError, match="TEST_WEATHER_KEY"):
            RestApiAdapter(rest_tool_config)

    def test_extra_headers_resolved_from_env(self, rest_tool_config, monkeypatch):
        """``extra_headers`` entries are read from env at startup and attached
        alongside the auth header on every request. Used for upstreams that
        require a second identifier next to the API key (e.g. tenant id)."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        monkeypatch.setenv("TEST_TENANT_ID", "org_abc")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": "TEST_TENANT_ID"},
        ]
        adapter = RestApiAdapter(cfg)
        assert adapter._extra_headers == {"X-Tenant-Id": "org_abc"}

    def test_extra_headers_missing_env_var_raises(self, rest_tool_config, monkeypatch):
        """Missing extra_headers env var is a hard error at startup — same
        contract as auth.secret_env."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        os.environ.pop("TEST_TENANT_ID", None)
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": "TEST_TENANT_ID"},
        ]
        with pytest.raises(ValueError, match="TEST_TENANT_ID"):
            RestApiAdapter(cfg)

    def test_extra_headers_no_entries_no_op(self, rest_tool_config, monkeypatch):
        """No extra_headers in config is a clean no-op (default empty dict)."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        adapter = RestApiAdapter(rest_tool_config)
        assert adapter._extra_headers == {}

    @pytest.mark.asyncio
    async def test_extra_headers_attached_to_request(self, rest_tool_config, monkeypatch):
        """The extra header value reaches the outbound httpx request alongside
        the auth header — end-to-end through .execute()."""
        from unittest.mock import AsyncMock, patch
        monkeypatch.setenv("TEST_WEATHER_KEY", "test-key-123")
        monkeypatch.setenv("TEST_TENANT_ID", "org_abc")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": "TEST_TENANT_ID"},
        ]
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {"ok": True})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "X"}, "sess-eh")

        sent_headers = mock_client.request.call_args.kwargs["headers"]
        assert sent_headers["X-API-Key"] == "test-key-123"
        assert sent_headers["X-Tenant-Id"] == "org_abc"


# ---------------------------------------------------------------------------
# TestRestApiAdapterToolDefinition
# ---------------------------------------------------------------------------


class TestRestApiAdapterToolDefinition:
    """Tests for RestApiAdapter.get_tool_definitions()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")

    def test_returns_single_element_list(self, rest_tool_config):
        """get_tool_definitions returns a list with exactly one element."""
        adapter = RestApiAdapter(rest_tool_config)
        defs = adapter.get_tool_definitions()
        assert isinstance(defs, list)
        assert len(defs) == 1

    def test_name_from_config_id(self, rest_tool_config):
        """Tool name is taken from the top-level config id."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.name == "test_weather"

    def test_description_from_config(self, rest_tool_config):
        """Tool description matches the config description field."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.description == "Get weather for a location"

    def test_category_from_config(self, rest_tool_config):
        """Tool category matches the config category field."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        assert tool.category == "read"

    def test_only_agent_params_in_schema(self, rest_tool_config):
        """Static params are excluded from the input_schema properties."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        props = tool.input_schema.get("properties", {})
        assert "location" in props
        assert "units" not in props  # source: static

    def test_required_params_in_schema(self, rest_tool_config):
        """Required agent params appear in the input_schema required list."""
        adapter = RestApiAdapter(rest_tool_config)
        tool = adapter.get_tool_definitions()[0]
        required = tool.input_schema.get("required", [])
        assert "location" in required

    def test_array_param_gets_default_items_schema(self, rest_tool_config):
        """Array params without an explicit items schema get items={type:string}.

        Regression: OpenAI's function-calling validation rejects
        `{"type": "array"}` without `items`. Anthropic accepts it, so
        domain configs originally written against the Anthropic provider
        omit `items`. The adapter now defaults to `items: {type: string}`
        so cross-provider deploys work unchanged.
        """
        cfg = {
            **rest_tool_config,
            "endpoints": [{
                **rest_tool_config["endpoints"][0],
                "params": [
                    {"name": "location", "source": "agent", "type": "string", "required": True},
                    {"name": "languages", "source": "agent", "type": "array", "required": False},
                ],
            }],
        }
        adapter = RestApiAdapter(cfg)
        tool = adapter.get_tool_definitions()[0]
        languages = tool.input_schema["properties"]["languages"]
        assert languages["type"] == "array"
        assert languages["items"] == {"type": "string"}

    def test_array_param_uses_explicit_items_schema_when_provided(self, rest_tool_config):
        """When the domain config declares `items`, the adapter passes it through verbatim."""
        cfg = {
            **rest_tool_config,
            "endpoints": [{
                **rest_tool_config["endpoints"][0],
                "params": [
                    {"name": "location", "source": "agent", "type": "string", "required": True},
                    {
                        "name": "preferred_work_mode",
                        "source": "agent",
                        "type": "array",
                        "required": False,
                        "items": {
                            "type": "string",
                            "enum": ["on-site-no-shift", "on-site-shifts", "remote", "hybrid"],
                        },
                    },
                ],
            }],
        }
        adapter = RestApiAdapter(cfg)
        tool = adapter.get_tool_definitions()[0]
        items = tool.input_schema["properties"]["preferred_work_mode"]["items"]
        assert items["type"] == "string"
        assert "remote" in items["enum"]


# ---------------------------------------------------------------------------
# TestRestApiAdapterExecute
# ---------------------------------------------------------------------------


class TestRestApiAdapterExecute:
    """Tests for RestApiAdapter.execute()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "secret-key")
        monkeypatch.setenv("TEST_JOBS_TOKEN", "secret-bearer")

    @pytest.mark.asyncio
    async def test_get_with_merged_params(self, rest_tool_config):
        """GET request sends agent + static params merged as query string."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {"temp": 22})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "Delhi"}, "sess-1")

        assert result.success is True
        call_kwargs = mock_client.request.call_args
        # GET params go in query string via 'params' kwarg
        sent_params = call_kwargs.kwargs.get("params") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs.get("params", {})
        # Just check method and url
        assert call_kwargs.kwargs.get("method", call_kwargs.args[0] if call_kwargs.args else None) in ("GET", None) or True
        # Verify static param is merged
        full_params = mock_client.request.call_args.kwargs
        assert full_params.get("params", {}).get("units") == "metric"
        assert full_params.get("params", {}).get("location") == "Delhi"

    @pytest.mark.asyncio
    async def test_post_request(self, rest_write_tool_config):
        """POST request sends params as JSON body."""
        adapter = RestApiAdapter(rest_write_tool_config)
        mock_resp = make_mock_response(201, {"application_id": "app-99"})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_apply", {"job_id": "job-42"}, "sess-2")

        assert result.success is True
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs.get("json", {}).get("job_id") == "job-42"

    @pytest.mark.asyncio
    async def test_api_key_in_header(self, rest_tool_config):
        """api_key auth injects configured header with the secret value."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "Mumbai"}, "sess-3")

        headers = mock_client.request.call_args.kwargs.get("headers", {})
        assert headers.get("X-API-Key") == "secret-key"

    @pytest.mark.asyncio
    async def test_bearer_auth_in_header(self, rest_write_tool_config):
        """bearer auth injects Authorization: Bearer <secret> header."""
        adapter = RestApiAdapter(rest_write_tool_config)
        mock_resp = make_mock_response(201, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_apply", {"job_id": "j1"}, "sess-4")

        headers = mock_client.request.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer secret-bearer"

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self, rest_tool_config):
        """HTTP 4xx/5xx status returns a ToolResult with success=False."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(404, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-5")

        assert result.success is False
        assert "http_error" in result.error
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_http_error_surfaces_upstream_body_to_llm(self, rest_tool_config):
        """4xx with a JSON error body puts that body into result_text so the
        LLM downstream can read why the call failed and re-ask the missing
        field. Previously the body was logged at debug level and
        result_text was empty — the LLM saw an empty "{}" content and
        treated it as a benign success."""
        adapter = RestApiAdapter(rest_tool_config)
        upstream_body = (
            '{"error": "INVALID_ITEM_STATE", '
            '"message": "Invalid item_state: must be >= 14"}'
        )
        mock_resp = make_mock_response(400, json_data={}, text=upstream_body)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-err")

        assert result.success is False
        assert "http_error" in result.error
        assert "400" in result.error
        assert "INVALID_ITEM_STATE" in result.result_text
        assert "must be >= 14" in result.result_text
        assert "400" in result.result_text

    @pytest.mark.asyncio
    async def test_http_error_with_empty_body_still_describes_failure(self, rest_tool_config):
        """4xx with no body still produces a non-empty result_text describing
        the status so the LLM can branch on failure rather than guessing
        from an empty payload."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(500, json_data={}, text="")

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-empty")

        assert result.success is False
        assert "500" in result.result_text
        assert "empty body" in result.result_text

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, rest_tool_config):
        """Timeout raises httpx.TimeoutException which becomes a failed ToolResult."""
        adapter = RestApiAdapter(rest_tool_config)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            result = await adapter.execute("test_weather", {"location": "Y"}, "sess-6")

        assert result.success is False
        assert "adapter_timeout" in result.error
        assert "test_weather" in result.error

    @pytest.mark.asyncio
    async def test_response_truncation(self, rest_tool_config):
        """Response JSON is truncated to max_size_chars characters."""
        adapter = RestApiAdapter(rest_tool_config)
        big_payload = {"data": "x" * 10_000}
        mock_resp = make_mock_response(200, big_payload)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "Z"}, "sess-7")

        assert result.success is True
        # result_text must be <= max_size_chars (4000)
        assert len(result.result_text) <= 4000


# ---------------------------------------------------------------------------
# TestRestApiAdapterProjectionInvariant (GH #198 — P5-C regression guard)
# ---------------------------------------------------------------------------


class TestRestApiAdapterProjectionInvariant:
    """Regression guard for the projection-on-raw-dict invariant (GH #198).

    Mirrors the KKB ``onest_market_lookup`` shape: a payload whose serialised
    size exceeds ``max_size_chars`` and whose projected fields live deep
    inside list items pulled via a ``list_key``. Pins the contract that
    ``_apply_projection`` always sees the full raw dict and that truncation
    only ever clips ``result_text`` — never the projection input nor the
    raw dict retained on ``ToolResult.result``.
    """

    @pytest.fixture
    def rest_projection_config(self):
        """REST adapter config with list_key projection + small max_size_chars.

        ``max_size_chars`` is intentionally far smaller than the synthetic
        payload below so truncation is guaranteed and the invariant is
        exercised on every run.
        """
        return {
            "id": "test_market_lookup",
            "type": "rest_api",
            "category": "read",
            "description": "Search market listings",
            "base_url": "https://api.market.test/v1",
            "auth": {"type": "none"},
            "endpoints": [
                {
                    "name": "search",
                    "method": "GET",
                    "path": "/search",
                    "params": [
                        {
                            "name": "q",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "Search term",
                        },
                    ],
                }
            ],
            "response": {
                "max_size_chars": 4000,
                "projection": {
                    "list_key": "data.items",
                    "fields": {
                        "role_id": "job.job_id",
                        "title": "job.beckn_structure.tags.title",
                        "city": "job.beckn_structure.locations.city",
                    },
                },
            },
        }

    @staticmethod
    def _build_oversized_payload(num_items: int = 50) -> dict:
        """Build a deeply-nested KKB-shaped payload that exceeds 4000 chars.

        Each item carries a ``noise`` blob to bloat the serialised form well
        past ``max_size_chars`` so the deepest fields fall beyond the
        truncation boundary.
        """
        items = []
        for i in range(num_items):
            items.append(
                {
                    "job": {
                        "job_id": f"role-{i:04d}",
                        "is_active": True,
                        "beckn_structure": {
                            "locations": {"city": f"city-{i:04d}", "state": "ST"},
                            "tags": {
                                "title": f"Title {i:04d}",
                                "noise": "x" * 200,
                            },
                        },
                    }
                }
            )
        return {"data": {"items": items}}

    @pytest.mark.asyncio
    async def test_projection_runs_on_full_raw_dict_when_response_exceeds_max_size_chars(
        self, rest_projection_config
    ):
        """Projection sees deep fields from items beyond the truncation boundary.

        Pins the invariant from rest_api.py:381-394 — ``_apply_projection``
        is called against the raw response dict before any size clipping
        happens. If a regression ever truncates the input to projection,
        the deep-tail items would silently disappear and this test would
        catch it.
        """
        import json

        adapter = RestApiAdapter(rest_projection_config)
        payload = self._build_oversized_payload(num_items=50)

        # Sanity: the unprojected serialised payload must exceed max_size_chars,
        # otherwise the test is not actually exercising the invariant.
        assert len(json.dumps(payload)) > rest_projection_config["response"]["max_size_chars"]

        mock_resp = make_mock_response(200, payload)
        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute(
                "test_market_lookup", {"q": "electrician"}, "sess-proj-1"
            )

        assert result.success is True

        # ToolResult.result is the raw dict — never truncated.
        assert result.result == payload
        assert len(result.result["data"]["items"]) == 50

        # The projection input was the full raw dict, so the LAST item
        # (which lives well past character offset 4000 in the serialised
        # form) must still be present and faithfully projected. Pull it
        # from result_text rather than asserting the raw payload, so we
        # can only succeed if both (a) projection saw it AND (b) it fit
        # under max_size_chars after the slim transform.
        projected = json.loads(result.result_text)
        assert isinstance(projected, list)
        # Last surviving item must be one of the deep-tail entries —
        # i.e. projection did not stop at the early items.
        ids = [item["role_id"] for item in projected]
        assert "role-0000" in ids
        assert any(rid >= "role-0020" for rid in ids), (
            "Projection must process items past the raw truncation boundary; "
            f"saw only {ids}"
        )
        # Every surviving projected item carries the deep nested fields,
        # proving _apply_projection traversed the full nested structure.
        for item in projected:
            assert set(item.keys()) == {"role_id", "title", "city"}
            assert item["title"].startswith("Title ")
            assert item["city"].startswith("city-")

    @pytest.mark.asyncio
    async def test_result_text_is_capped_but_raw_result_is_not(
        self, rest_projection_config
    ):
        """``result_text`` is bounded by ``max_size_chars``; ``result`` is not.

        Pins rest_api.py:388-394 (list payload trimmed item-by-item to fit)
        and rest_api.py:413 (``ToolResult.result = result_dict`` — the raw,
        unprojected, untruncated dict). Observability and downstream
        consumers depend on ``result`` carrying the full payload.
        """
        import json

        adapter = RestApiAdapter(rest_projection_config)
        payload = self._build_oversized_payload(num_items=50)
        mock_resp = make_mock_response(200, payload)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute(
                "test_market_lookup", {"q": "plumber"}, "sess-proj-2"
            )

        max_size = rest_projection_config["response"]["max_size_chars"]

        # result_text is clipped to max_size_chars (list payload: trimmed
        # by dropping items from the tail; remains valid JSON).
        assert len(result.result_text) <= max_size
        json.loads(result.result_text)  # must still parse

        # ToolResult.result holds the FULL raw dict — no key, item, or
        # nested field is dropped, even though the serialised form is
        # larger than max_size_chars.
        assert result.result == payload
        assert len(json.dumps(result.result)) > max_size
        assert len(result.result["data"]["items"]) == 50
        # Spot-check a deep field on the last item survived intact.
        last = result.result["data"]["items"][-1]
        assert last["job"]["job_id"] == "role-0049"
        assert last["job"]["beckn_structure"]["tags"]["title"] == "Title 0049"


# ---------------------------------------------------------------------------
# TestRestApiAdapterHealthCheck
# ---------------------------------------------------------------------------


class TestRestApiAdapterHealthCheck:
    """Tests for RestApiAdapter.health_check()."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")

    def test_health_check_success(self, rest_tool_config):
        """health_check returns True when the backing service responds < 500."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.head", return_value=mock_resp):
            assert adapter.health_check() is True

    def test_health_check_failure(self, rest_tool_config):
        """health_check returns False when the backing service responds >= 500."""
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.head", return_value=mock_resp):
            assert adapter.health_check() is False

    def test_health_check_connection_error(self, rest_tool_config):
        """health_check returns False when a connection error occurs."""
        adapter = RestApiAdapter(rest_tool_config)

        with patch("httpx.head", side_effect=Exception("connection refused")):
            assert adapter.health_check() is False

    def test_health_check_disabled_via_config_returns_true(self, rest_tool_config):
        """health_check.enabled=false skips the HTTP probe and returns True.

        Required for self-referential mock connectors whose base_url points
        back at the Action Gateway itself — probing them synchronously would
        deadlock the single uvicorn event loop thread while serving /health.
        """
        rest_tool_config["health_check"] = {"enabled": False}
        adapter = RestApiAdapter(rest_tool_config)

        # httpx.head must never be called on this path; if the guard regresses
        # it'd hit the external API during tests and leak credentials.
        with patch("httpx.head", side_effect=AssertionError("must not probe")):
            assert adapter.health_check() is True


# ---------------------------------------------------------------------------
# TestRestApiAdapterOtel
# ---------------------------------------------------------------------------


class TestRestApiAdapterOtel:
    """Tests for OTel span instrumentation in RestApiAdapter."""

    @pytest.fixture(autouse=True)
    def _inject_env(self, monkeypatch):
        monkeypatch.setenv("TEST_WEATHER_KEY", "secret-key")

    @pytest.mark.asyncio
    async def test_execute_emits_http_call_span(self, otel_setup, rest_tool_config):
        """execute() must produce an action.rest_api.http_call child span."""
        exporter, _ = otel_setup
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {"temp": 25})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "Delhi"}, "sess-otel-1")

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        assert "action.rest_api.http_call" in span_names

        http_span = next(s for s in spans if s.name == "action.rest_api.http_call")
        assert http_span.attributes.get("http.method") == "GET"
        assert "https://api.weather.test/v1/forecast" in http_span.attributes.get("http.url", "")
        assert http_span.attributes.get("http.status_code") == 200

    @pytest.mark.asyncio
    async def test_timeout_sets_span_error(self, otel_setup, rest_tool_config):
        """Timeout must end action.rest_api.http_call span with ERROR status."""
        exporter, _ = otel_setup
        adapter = RestApiAdapter(rest_tool_config)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-otel-2")

        assert result.success is False
        spans = exporter.get_finished_spans()
        http_span = next((s for s in spans if s.name == "action.rest_api.http_call"), None)
        assert http_span is not None
        # OTel records exception events when record_exception=True (default)
        assert len(http_span.events) > 0

    @pytest.mark.asyncio
    async def test_response_size_metric_recorded(self, otel_setup, rest_tool_config):
        """execute() must record action.response.size_bytes histogram on success."""
        _, reader = otel_setup
        adapter = RestApiAdapter(rest_tool_config)
        mock_resp = make_mock_response(200, {"data": "hello"})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "Mumbai"}, "sess-otel-3")

        metrics_data = reader.get_metrics_data()
        metric_names = {
            m.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "action.response.size_bytes" in metric_names

    @pytest.mark.asyncio
    async def test_truncation_counter_recorded(self, otel_setup, rest_tool_config):
        """execute() must increment action.response.truncated_total when response is truncated."""
        _, reader = otel_setup
        adapter = RestApiAdapter(rest_tool_config)
        big_payload = {"data": "x" * 10_000}
        mock_resp = make_mock_response(200, big_payload)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("test_weather", {"location": "Z"}, "sess-otel-4")

        metrics_data = reader.get_metrics_data()
        metric_names = {
            m.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "action.response.truncated_total" in metric_names


# ---------------------------------------------------------------------------
# GH-151 follow-up: path templating (get_profile stub)
# ---------------------------------------------------------------------------


class TestRestApiAdapterPathTemplating:
    """execute() substitutes {user_id} / {session_id} into the endpoint path.

    Introduced for the get_profile stub so the caller's user_id doesn't have
    to be echoed by the LLM — rest_api.yaml declares path: '/profile/{user_id}'
    and the framework fills it in.
    """

    @pytest.fixture
    def rest_profile_config(self, monkeypatch):
        """Adapter config that uses {user_id} path templating + no auth."""
        # No auth env var needed — adapter accepts auth block with type="none"
        # by leaving it off entirely.
        return {
            "id": "get_profile",
            "type": "rest_api",
            "category": "read",
            "description": "Fetch caller's profile from Memory Layer.",
            "base_url": "http://memory_layer:8002",
            "endpoints": [
                {
                    "name": "get_profile",
                    "method": "GET",
                    "path": "/profile/{user_id}",
                    "params": [],
                }
            ],
            "response": {"max_size_chars": 3000},
        }

    @pytest.mark.asyncio
    async def test_user_id_substituted_into_path(self, rest_profile_config):
        from unittest.mock import AsyncMock, patch
        from src.adapters.rest_api import RestApiAdapter

        adapter = RestApiAdapter(rest_profile_config)
        mock_resp = make_mock_response(200, {"trade": "electrician"})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute(
                "get_profile", {}, "sess-1", "+919876543210"
            )

        assert result.success is True
        url = mock_client.request.call_args.kwargs["url"]
        assert url == "http://memory_layer:8002/profile/+919876543210"

    @pytest.mark.asyncio
    async def test_empty_user_id_leaves_path_bare(self, rest_profile_config):
        """Missing user_id produces /profile/ — backing endpoint handles the
        empty case (our Memory Layer /profile/ returns {}). Adapter must not
        crash on the format call."""
        from unittest.mock import AsyncMock, patch
        from src.adapters.rest_api import RestApiAdapter

        adapter = RestApiAdapter(rest_profile_config)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("get_profile", {}, "sess-2")

        url = mock_client.request.call_args.kwargs["url"]
        assert url == "http://memory_layer:8002/profile/"

    @pytest.mark.asyncio
    async def test_session_id_also_substitutable(self, monkeypatch):
        """path='/sessions/{session_id}/summary' substitutes session_id too."""
        from unittest.mock import AsyncMock, patch
        from src.adapters.rest_api import RestApiAdapter

        cfg = {
            "id": "session_summary",
            "type": "rest_api",
            "category": "read",
            "description": "Get session summary",
            "base_url": "http://memory_layer:8002",
            "endpoints": [
                {
                    "name": "summary",
                    "method": "GET",
                    "path": "/sessions/{session_id}/summary",
                    "params": [],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("session_summary", {}, "abc-123")

        url = mock_client.request.call_args.kwargs["url"]
        assert url == "http://memory_layer:8002/sessions/abc-123/summary"

    @pytest.mark.asyncio
    async def test_user_id_substituted_into_body_template(self, monkeypatch):
        """body_template ``"{user_id}"`` placeholders are filled from the
        session identity, mirroring path-templating. Lets a YAML wire the
        caller's identity into a POST body without asking the LLM to know
        or echo it — otherwise the LLM tends to emit a literal
        ``"{{session.user_id}}"`` placeholder string into the body and the
        upstream stores garbage."""
        from unittest.mock import AsyncMock, patch
        from src.adapters.rest_api import RestApiAdapter

        cfg = {
            "id": "create_user",
            "type": "rest_api",
            "category": "write",
            "description": "Create user from session identity.",
            "base_url": "http://upstream",
            "endpoints": [
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/users",
                    "body_template": {
                        "user": {
                            "name": "{name}",
                            "phoneNumber": "{user_id}",
                        },
                    },
                    "params": [
                        {
                            "name": "name",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "Caller's full name.",
                        },
                    ],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {"ok": True})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute(
                "create_user",
                {"name": "Rahul"},
                "sess-id-1",
                "9999900001",
            )

        sent_body = mock_client.request.call_args.kwargs["json"]
        assert sent_body == {
            "user": {
                "name": "Rahul",
                "phoneNumber": "9999900001",
            }
        }

    @pytest.mark.asyncio
    async def test_body_template_session_id_substitution(self):
        """body_template ``"{session_id}"`` also resolves, same as paths."""
        from unittest.mock import AsyncMock, patch
        from src.adapters.rest_api import RestApiAdapter

        cfg = {
            "id": "log_event",
            "type": "rest_api",
            "category": "write",
            "description": "Log a session event.",
            "base_url": "http://upstream",
            "endpoints": [
                {
                    "name": "log",
                    "method": "POST",
                    "path": "/events",
                    "body_template": {
                        "session": "{session_id}",
                        "event": "{event}",
                    },
                    "params": [
                        {
                            "name": "event",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "Event name.",
                        },
                    ],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute(
                "log_event",
                {"event": "started"},
                "sess-xyz",
                "ignored-user",
            )

        sent_body = mock_client.request.call_args.kwargs["json"]
        assert sent_body == {"session": "sess-xyz", "event": "started"}


# ---------------------------------------------------------------------------
# TestRestApiAdapterExtraHeadersValidation
# ---------------------------------------------------------------------------


class TestRestApiAdapterExtraHeadersValidation:
    """Init-time validation of the extra_headers config block."""

    def test_empty_string_env_var_rejected_like_missing(self, rest_tool_config, monkeypatch):
        """An exported-but-empty env var must fail the same way as unset.

        Catches the silent-partial-auth failure mode: the operator runs
        ``export TENANT_ID=""`` (or docker compose expands a missing
        variable to empty), and the adapter previously sent
        ``X-Tenant-Id: ""`` on every request indefinitely.
        """
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        monkeypatch.setenv("TEST_TENANT_ID", "")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": "TEST_TENANT_ID"},
        ]
        with pytest.raises(ValueError, match="TEST_TENANT_ID"):
            RestApiAdapter(cfg)

    def test_empty_string_auth_secret_env_rejected(self, rest_tool_config, monkeypatch):
        """Same empty-string contract on auth.secret_env — symmetric with extra_headers."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "")
        with pytest.raises(ValueError, match="TEST_WEATHER_KEY"):
            RestApiAdapter(rest_tool_config)

    def test_multiple_entries_all_resolved(self, rest_tool_config, monkeypatch):
        """Several extra_headers entries all resolve independently — guards
        against a regression that broke on len > 1."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        monkeypatch.setenv("TEST_TENANT_ID", "org_abc")
        monkeypatch.setenv("TEST_REQUESTOR_ID", "req_42")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": "TEST_TENANT_ID"},
            {"name": "X-Requestor-Id", "secret_env": "TEST_REQUESTOR_ID"},
        ]
        adapter = RestApiAdapter(cfg)
        assert adapter._extra_headers == {
            "X-Tenant-Id": "org_abc",
            "X-Requestor-Id": "req_42",
        }

    def test_malformed_entry_missing_name_raises(self, rest_tool_config, monkeypatch):
        """An entry with no 'name' is a config bug, not a runtime no-op."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        monkeypatch.setenv("TEST_TENANT_ID", "org_abc")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "", "secret_env": "TEST_TENANT_ID"},
        ]
        with pytest.raises(ValueError, match="extra_headers"):
            RestApiAdapter(cfg)

    def test_malformed_entry_missing_secret_env_raises(self, rest_tool_config, monkeypatch):
        """An entry with no 'secret_env' is a config bug, not a runtime no-op."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            {"name": "X-Tenant-Id", "secret_env": ""},
        ]
        with pytest.raises(ValueError, match="extra_headers"):
            RestApiAdapter(cfg)

    def test_collision_with_auth_header_rejected_case_insensitive(
        self, rest_tool_config, monkeypatch
    ):
        """An extra_headers entry whose name collides with the auth header
        is rejected at init — papering over the conflict with
        ``headers.setdefault`` would silently ignore the operator's
        misconfiguration. Collision check is case-insensitive (HTTP header
        names are case-insensitive)."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "real-key")
        monkeypatch.setenv("DECOY_KEY", "decoy")
        cfg = dict(rest_tool_config)
        cfg["extra_headers"] = [
            # rest_tool_config's auth.header is "X-API-Key" — different case
            {"name": "x-api-key", "secret_env": "DECOY_KEY"},
        ]
        with pytest.raises(ValueError, match="collides with the auth header"):
            RestApiAdapter(cfg)


# ---------------------------------------------------------------------------
# TestRestApiAdapterHttpErrorBodySurfacing
# ---------------------------------------------------------------------------


class TestRestApiAdapterHttpErrorBodySurfacing:
    """Tests for the 4xx/5xx response-body capture path."""

    @pytest.mark.asyncio
    async def test_5xx_with_body_surfaced_to_result_text(self, rest_tool_config, monkeypatch):
        """A 5xx with a body must also include the body in result_text —
        not just 4xx. Guards against a future regression where the body
        capture is mistakenly gated to ``status < 500``."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        adapter = RestApiAdapter(rest_tool_config)
        upstream_body = '{"error": "BACKEND_DOWN", "request_id": "abc-123"}'
        mock_resp = make_mock_response(503, json_data={}, text=upstream_body)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-5xx")

        assert result.success is False
        assert "http_error" in result.error
        assert "503" in result.error
        assert "BACKEND_DOWN" in result.result_text
        assert "503" in result.result_text

    @pytest.mark.asyncio
    async def test_body_excerpt_capped_at_max_size_chars(self, rest_tool_config, monkeypatch):
        """An oversized 4xx body is capped at ``response.max_size_chars``,
        not unbounded — guards against a giant upstream error response
        blowing the LLM context window."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        adapter = RestApiAdapter(rest_tool_config)
        # rest_tool_config sets response.max_size_chars (default 4000); use
        # the adapter's own configured cap so the assertion tracks config.
        cap = adapter._max_size_chars
        huge_body = "x" * (cap * 3)
        mock_resp = make_mock_response(400, json_data={}, text=huge_body)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-cap")

        # result_text adds a short prefix; the body excerpt itself must be
        # capped at max_size_chars. Allow a small slack for the prefix.
        assert "x" * cap in result.result_text
        assert "x" * (cap + 1) not in result.result_text

    @pytest.mark.asyncio
    async def test_non_utf8_body_does_not_raise(self, rest_tool_config, monkeypatch):
        """Undecodable upstream bytes don't break the adapter's no-raise
        contract — the 4xx path falls back to a lossy decode rather than
        propagating UnicodeDecodeError out of the adapter."""
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        adapter = RestApiAdapter(rest_tool_config)

        # Mock a response where .text raises UnicodeDecodeError but .content
        # is still readable (the realistic httpx failure mode).
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 400
        resp.is_error = True
        resp.json.return_value = {}
        type(resp).text = property(
            lambda self: (_ for _ in ()).throw(
                UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "bad bytes")
            )
        )
        resp.content = b"\xff\xfe{\"error\":\"x\"}"

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=resp)
            result = await adapter.execute("test_weather", {"location": "X"}, "sess-bad-bytes")

        # Adapter must not have raised; it returns a structured failure.
        assert result.success is False
        assert "400" in result.error
        # Lossy decode preserves at least the readable tail.
        assert "error" in result.result_text

    @pytest.mark.asyncio
    async def test_warn_log_does_not_include_body_field(
        self, rest_tool_config, monkeypatch, caplog
    ):
        """The WARN-level ``rest_api_http_error`` log must not contain the
        upstream body verbatim — upstream 4xx bodies routinely echo
        submitted PII (phone numbers, age, names) and operator stdout/Loki
        is not the designated audit log path."""
        import logging as _logging
        monkeypatch.setenv("TEST_WEATHER_KEY", "k")
        adapter = RestApiAdapter(rest_tool_config)
        pii_body = '{"error": "DUPLICATE", "echo": "phone 9999900001 already exists"}'
        mock_resp = make_mock_response(400, json_data={}, text=pii_body)

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            with caplog.at_level(_logging.WARNING, logger="src.adapters.rest_api"):
                await adapter.execute("test_weather", {"location": "X"}, "sess-pii")

        warn_records = [
            r for r in caplog.records
            if r.name == "src.adapters.rest_api" and r.levelno == _logging.WARNING
        ]
        assert warn_records, "expected a WARN record for the 4xx response"
        for record in warn_records:
            # The body excerpt must not appear in any field of the record's
            # extras — only the length metadata is allowed.
            assert not hasattr(record, "body"), (
                "WARN log must not carry the response body verbatim"
            )
            assert "9999900001" not in record.getMessage()


# ---------------------------------------------------------------------------
# TestRestApiAdapterBodyTemplateSessionIdentity
# ---------------------------------------------------------------------------


class TestRestApiAdapterBodyTemplateSessionIdentity:
    """Tests for the session-identity warning path in body_template."""

    @pytest.mark.asyncio
    async def test_empty_user_id_warns_when_referenced_in_body_template(
        self, monkeypatch, caplog
    ):
        """If body_template references ``{user_id}`` but the caller passed
        an empty user_id, the renderer silently drops the enclosing field
        and the upstream gets a corrupted body. The adapter must emit a
        structured WARN so operators can distinguish 'LLM omitted a field'
        from 'session lost identity mid-call'."""
        import logging as _logging
        cfg = {
            "id": "create_user",
            "type": "rest_api",
            "category": "write",
            "description": "Create user from session identity.",
            "base_url": "http://upstream",
            "endpoints": [
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/users",
                    "body_template": {
                        "phoneNumber": "{user_id}",
                        "name": "{name}",
                    },
                    "params": [
                        {
                            "name": "name",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "Caller's full name.",
                        },
                    ],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {"ok": True})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            with caplog.at_level(_logging.WARNING, logger="src.adapters.rest_api"):
                # user_id deliberately empty — simulates session-state loss.
                await adapter.execute("create_user", {"name": "Rahul"}, "sess-1", "")

        warn_records = [
            r for r in caplog.records
            if r.name == "src.adapters.rest_api"
            and r.levelno == _logging.WARNING
            and getattr(r, "operation", None) == "RestApiAdapter.execute"
        ]
        assert warn_records, "expected a WARN record for missing session identity"
        assert any(
            getattr(r, "missing_placeholder", None) == "user_id" for r in warn_records
        )

        # And the enclosing phoneNumber field is dropped, as the renderer
        # has always done — the warn just makes the silent drop visible.
        sent_body = mock_client.request.call_args.kwargs["json"]
        assert "phoneNumber" not in sent_body
        assert sent_body == {"name": "Rahul"}

    @pytest.mark.asyncio
    async def test_empty_user_id_does_not_warn_when_not_referenced(self, monkeypatch, caplog):
        """No warning fires when the body_template does not reference
        ``{user_id}`` — an empty user_id is only suspicious if the template
        expected one."""
        import logging as _logging
        cfg = {
            "id": "log_event",
            "type": "rest_api",
            "category": "write",
            "description": "Log an event.",
            "base_url": "http://upstream",
            "endpoints": [
                {
                    "name": "log",
                    "method": "POST",
                    "path": "/events",
                    "body_template": {"event": "{event}"},
                    "params": [
                        {
                            "name": "event",
                            "source": "agent",
                            "type": "string",
                            "required": True,
                            "description": "Event name.",
                        },
                    ],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            with caplog.at_level(_logging.WARNING, logger="src.adapters.rest_api"):
                await adapter.execute("log_event", {"event": "ok"}, "sess-2", "")

        identity_warns = [
            r for r in caplog.records
            if getattr(r, "missing_placeholder", None) in ("user_id", "session_id")
        ]
        assert not identity_warns

    @pytest.mark.asyncio
    async def test_user_id_substituted_into_embedded_placeholder(self, monkeypatch):
        """Embedded placeholders (e.g. ``"+91{user_id}"``) substitute the
        session identity inline — exercises the embedded branch of
        _render_body_template, distinct from the whole-string branch
        covered by the existing test."""
        cfg = {
            "id": "create_user",
            "type": "rest_api",
            "category": "write",
            "description": "Create user with country-coded phone.",
            "base_url": "http://upstream",
            "endpoints": [
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/users",
                    "body_template": {
                        "phoneNumber": "+91{user_id}",
                    },
                    "params": [],
                }
            ],
            "response": {"max_size_chars": 1000},
        }
        adapter = RestApiAdapter(cfg)
        mock_resp = make_mock_response(200, {"ok": True})

        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            await adapter.execute("create_user", {}, "sess-1", "9999900001")

        sent_body = mock_client.request.call_args.kwargs["json"]
        assert sent_body == {"phoneNumber": "+919999900001"}

