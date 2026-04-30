# Reach Layer Web — Routing-Only Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `routing_only` mode to `reach_layer_web` so voice-only deployments don't run the full web UI, auth, chat, and sessions stack — just the ingest proxy and health check.

**Architecture:** A `REACH_LAYER_WEB_MODE` env var (set to `routing_only` or `full`) controls which FastAPI factory function boots at startup. The deploy wizard injects the correct value based on channel selection. The schema gains a `mode` field for config validation and the accumulator writes it into the reach_layer YAML so it flows through to the exported domain config.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, Vitest (frontend tests already passing — no frontend changes needed), pytest + respx (server tests).

---

## File Map

| File | Change |
|---|---|
| `dev-kit/dev_kit/schema.py` | Add `mode` field to `WebChannelConfig` |
| `dev-kit/dev_kit/agent/accumulator.py` | `set_reach_channel_selection()` writes `mode` into config |
| `dev-kit/dev_kit/agent/app.py` | Inject `REACH_LAYER_WEB_MODE` in `_run_docker_deploy` and `get_deploy_preview` |
| `automation/docker/docker-compose.dev.yml` | Add `REACH_LAYER_WEB_MODE` to `reach_layer_web` environment |
| `reach_layer/web/server.py` | Extract ingest routes; add `create_routing_only_app`; dispatch at module level |
| `dev-kit/tests/test_schema.py` | Tests for `WebChannelConfig.mode` |
| `dev-kit/tests/test_accumulator_web_mode.py` | Tests for mode written by `set_reach_channel_selection` |
| `dev-kit/tests/test_app_deploy_routes.py` | Tests for `REACH_LAYER_WEB_MODE` injection in preview and deploy |
| `reach_layer/web/tests/test_server.py` | Tests for `create_routing_only_app` and module-level dispatch |

---

## Task 1: Schema — add `mode` to `WebChannelConfig`

**Files:**
- Modify: `dev-kit/dev_kit/schema.py` (around line 1057)
- Test: `dev-kit/tests/test_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `dev-kit/tests/test_schema.py`:

```python
from dev_kit.schema import WebChannelConfig


class TestWebChannelConfigMode:
    def test_default_mode_is_full(self):
        cfg = WebChannelConfig()
        assert cfg.mode == "full"

    def test_routing_only_mode_accepted(self):
        cfg = WebChannelConfig(mode="routing_only")
        assert cfg.mode == "routing_only"

    def test_full_mode_accepted(self):
        cfg = WebChannelConfig(mode="full")
        assert cfg.mode == "full"

    def test_invalid_mode_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WebChannelConfig(mode="partial")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_schema.py::TestWebChannelConfigMode -v
```

Expected: FAIL — `WebChannelConfig` has no `mode` field.

- [ ] **Step 3: Add `mode` field to `WebChannelConfig`**

In `dev-kit/dev_kit/schema.py`, find `class WebChannelConfig` (line ~1057) and replace:

```python
class WebChannelConfig(BaseModel):
    """Configuration for the web channel adapter (React frontend)."""

    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Web UI branding and copy. Common keys: app_name, app_tagline, app_icon, "
            "agent_avatar, user_avatar, setup_heading, setup_subtitle, user_id_placeholder, "
            "user_id_hint, start_btn_label, new_session_msg, returning_user_msg, "
            "storage_key, theme_storage_key, sign_out_confirm, switch_user_confirm, "
            "delete_conversation_confirm"
        ),
    )
```

with:

```python
class WebChannelConfig(BaseModel):
    """Configuration for the web channel adapter (React frontend)."""

    mode: Literal["routing_only", "full"] = Field(
        default="full",
        description=(
            "Boot mode for the web service. 'full' enables the React SPA, auth, chat, "
            "and session endpoints. 'routing_only' exposes only /health and /ingest/* — "
            "used when the web channel is not selected but the ingest proxy is still needed."
        ),
    )
    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Web UI branding and copy. Common keys: app_name, app_tagline, app_icon, "
            "agent_avatar, user_avatar, setup_heading, setup_subtitle, user_id_placeholder, "
            "user_id_hint, start_btn_label, new_session_msg, returning_user_msg, "
            "storage_key, theme_storage_key, sign_out_confirm, switch_user_confirm, "
            "delete_conversation_confirm"
        ),
    )
```

Make sure `Literal` is imported — check the top of `schema.py` for `from typing import`. If `Literal` is already imported, no change needed. If not, add it to the import line.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_schema.py::TestWebChannelConfigMode -v
```

Expected: 4 PASS

- [ ] **Step 5: Run full backend suite to check for regressions**

```bash
cd dev-kit && uv run pytest -q
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/schema.py dev-kit/tests/test_schema.py
git commit -m "feat(schema): add mode field to WebChannelConfig (routing_only | full)"
```

---

## Task 2: Accumulator — write mode when channel selection is stored

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py` (line ~409)
- Create: `dev-kit/tests/test_accumulator_web_mode.py`

- [ ] **Step 1: Write the failing tests**

Create `dev-kit/tests/test_accumulator_web_mode.py`:

```python
"""Tests for web mode written by set_reach_channel_selection."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator


def _get_web_mode(acc: ConfigAccumulator) -> str:
    """Read the web mode from the accumulator's internal reach_layer config."""
    return (
        acc._data["reach_layer"]
        .get("reach_layer", {})
        .get("channels", {})
        .get("web", {})
        .get("mode", "NOT_SET")
    )


def test_voice_only_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["voice"])
    assert _get_web_mode(acc) == "routing_only"


def test_web_selected_sets_full_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web"])
    assert _get_web_mode(acc) == "full"


def test_web_and_voice_sets_full_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web", "voice"])
    assert _get_web_mode(acc) == "full"


def test_cli_only_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["cli"])
    assert _get_web_mode(acc) == "routing_only"


def test_empty_channels_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection([])
    assert _get_web_mode(acc) == "routing_only"


def test_channel_list_still_stored_correctly():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["voice", "cli"])
    assert acc.get_reach_channel_selection() == ["voice", "cli"]


def test_overwriting_selection_updates_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web"])
    assert _get_web_mode(acc) == "full"
    acc.set_reach_channel_selection(["voice"])
    assert _get_web_mode(acc) == "routing_only"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_accumulator_web_mode.py -v
```

Expected: FAIL — `set_reach_channel_selection` doesn't write `mode` yet.

- [ ] **Step 3: Update `set_reach_channel_selection` in accumulator**

In `dev-kit/dev_kit/agent/accumulator.py`, find `set_reach_channel_selection` (line ~409) and replace the entire method:

```python
def set_reach_channel_selection(self, channels: list[str]) -> None:
    """Store the selected deployment channels and write the web service mode.

    Args:
        channels: List of selected channel names (e.g. ['web', 'cli']).
            When 'web' is not in the list, sets reach_layer.channels.web.mode
            to 'routing_only' so the web service boots without the full UI stack.
    """
    self._data["reach_layer"]["_selected_channels"] = list(channels)
    web_mode = "full" if "web" in channels else "routing_only"
    reach_cfg = self._data["reach_layer"].setdefault("reach_layer", {})
    channels_cfg = reach_cfg.setdefault("channels", {})
    web_cfg = channels_cfg.setdefault("web", {})
    web_cfg["mode"] = web_mode
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_accumulator_web_mode.py -v
```

Expected: 7 PASS

- [ ] **Step 5: Run full backend suite**

```bash
cd dev-kit && uv run pytest -q
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/accumulator.py dev-kit/tests/test_accumulator_web_mode.py
git commit -m "feat(accumulator): set_reach_channel_selection writes web mode into config"
```

---

## Task 3: `app.py` — inject `REACH_LAYER_WEB_MODE` in deploy and preview

**Context:** `app.py` has two places that build a compose manifest — `get_deploy_preview` (read-only preview for the wizard) and `_run_docker_deploy` (actual deploy). Both iterate over compose services and patch them. We need to inject `REACH_LAYER_WEB_MODE` into the `reach_layer_web` service in both.

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py` (~lines 1424 and ~1755)
- Test: `dev-kit/tests/test_app_deploy_routes.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `dev-kit/tests/test_app_deploy_routes.py`:

```python
# ---------------------------------------------------------------------------
# REACH_LAYER_WEB_MODE injection in deploy preview and execute
# ---------------------------------------------------------------------------

import yaml as _yaml


class TestWebModeInjection:
    """REACH_LAYER_WEB_MODE is injected into reach_layer_web based on channel selection."""

    def _get_web_env(self, client, slug: str, selected_channels: list[str]) -> list[str]:
        """Return the environment list for reach_layer_web from the preview compose output."""
        # Prime the engine and set channel selection on the accumulator
        client.get(f"/api/projects/{slug}")
        engine = app_module._engines[slug]
        engine.accumulator.set_reach_channel_selection(selected_channels)

        res = client.post(
            f"/api/projects/{slug}/deploy/preview",
            json={"target": "docker"},
        )
        assert res.status_code == 200
        compose_str = res.json()["preview"]["docker-compose.yml"]
        parsed = _yaml.safe_load(compose_str)
        svc = parsed.get("services", {}).get("reach_layer_web", {})
        return svc.get("environment", [])

    def test_voice_only_preview_sets_routing_only(self, client_with_project):
        client, slug = client_with_project
        env = self._get_web_env(client, slug, ["voice"])
        assert any("REACH_LAYER_WEB_MODE=routing_only" in str(e) for e in env)

    def test_web_selected_preview_sets_full(self, client_with_project):
        client, slug = client_with_project
        env = self._get_web_env(client, slug, ["web"])
        assert any("REACH_LAYER_WEB_MODE=full" in str(e) for e in env)

    def test_web_and_voice_preview_sets_full(self, client_with_project):
        client, slug = client_with_project
        env = self._get_web_env(client, slug, ["web", "voice"])
        assert any("REACH_LAYER_WEB_MODE=full" in str(e) for e in env)

    def test_cli_only_preview_sets_routing_only(self, client_with_project):
        client, slug = client_with_project
        env = self._get_web_env(client, slug, ["cli"])
        assert any("REACH_LAYER_WEB_MODE=routing_only" in str(e) for e in env)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_app_deploy_routes.py::TestWebModeInjection -v
```

Expected: FAIL — `REACH_LAYER_WEB_MODE` not in env list yet.

- [ ] **Step 3: Add mode injection to `get_deploy_preview`**

In `dev-kit/dev_kit/agent/app.py`, find the service-patching loop inside `get_deploy_preview` (around line 1424). The loop looks like:

```python
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                ag_env = svc.setdefault("environment", [])
                for env_var in tool_secrets:
                    if tool_secrets[env_var]:
                        ag_env.append(f"{env_var}=<set at deploy time>")
```

Replace it with:

```python
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                ag_env = svc.setdefault("environment", [])
                for env_var in tool_secrets:
                    if tool_secrets[env_var]:
                        ag_env.append(f"{env_var}=<set at deploy time>")
            if svc_name == "reach_layer_web":
                web_mode = "full" if "web" in set(selected_channels) else "routing_only"
                svc.setdefault("environment", []).append(f"REACH_LAYER_WEB_MODE={web_mode}")
```

- [ ] **Step 4: Add mode injection to `_run_docker_deploy`**

In `dev-kit/dev_kit/agent/app.py`, find the service-patching loop inside `_run_docker_deploy` (around line 1755). The loop looks like:

```python
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                env_list = svc.setdefault("environment", [])
                for env_var, value in tool_secrets.items():
                    if value:
                        env_list.append(f"{env_var}={value}")
```

Replace it with:

```python
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                env_list = svc.setdefault("environment", [])
                for env_var, value in tool_secrets.items():
                    if value:
                        env_list.append(f"{env_var}={value}")
            if svc_name == "reach_layer_web":
                web_mode = "full" if "web" in set(selected_channels) else "routing_only"
                svc.setdefault("environment", []).append(f"REACH_LAYER_WEB_MODE={web_mode}")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_app_deploy_routes.py::TestWebModeInjection -v
```

Expected: 4 PASS

- [ ] **Step 6: Run full backend suite**

```bash
cd dev-kit && uv run pytest -q
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py dev-kit/tests/test_app_deploy_routes.py
git commit -m "feat(deploy): inject REACH_LAYER_WEB_MODE into reach_layer_web based on channel selection"
```

---

## Task 4: `docker-compose.dev.yml` — add `REACH_LAYER_WEB_MODE` to reach_layer_web

**Context:** The compose file is used for local dev (`docker compose up`) and as the template for deployments. Adding `REACH_LAYER_WEB_MODE=${REACH_LAYER_WEB_MODE:-full}` lets developers override the mode locally via env var, and ensures the variable is always defined in the container (defaulting to `full`).

**Files:**
- Modify: `automation/docker/docker-compose.dev.yml` (around line 276)

- [ ] **Step 1: Edit `docker-compose.dev.yml`**

Find the `reach_layer_web` service environment block (around line 276):

```yaml
    environment:
      - CONFIG_FOLDER=/app/config
      # Optional when auth.enabled: false (default). Required and validated by server.py
      # at startup when auth.enabled: true — export both before running docker compose.
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
      - REACH_SESSION_SECRET=${REACH_SESSION_SECRET:-}
      # Upload chain auth — dev-kit → reach-layer and reach-layer → KE keys.
      - DEVKIT_TO_REACH_API_KEY=${DEVKIT_TO_REACH_API_KEY:-}
      - REACH_TO_KE_API_KEY=${REACH_TO_KE_API_KEY:-}
      - KE_INTERNAL_URL=${KE_INTERNAL_URL:-http://knowledge_engine:8001}
```

Replace with:

```yaml
    environment:
      - CONFIG_FOLDER=/app/config
      # routing_only = health + ingest proxy only (no SPA, no auth, no chat).
      # full = complete web channel. Injected automatically by the deploy wizard.
      - REACH_LAYER_WEB_MODE=${REACH_LAYER_WEB_MODE:-full}
      # Optional when auth.enabled: false (default). Required and validated by server.py
      # at startup when auth.enabled: true — export both before running docker compose.
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
      - REACH_SESSION_SECRET=${REACH_SESSION_SECRET:-}
      # Upload chain auth — dev-kit → reach-layer and reach-layer → KE keys.
      - DEVKIT_TO_REACH_API_KEY=${DEVKIT_TO_REACH_API_KEY:-}
      - REACH_TO_KE_API_KEY=${REACH_TO_KE_API_KEY:-}
      - KE_INTERNAL_URL=${KE_INTERNAL_URL:-http://knowledge_engine:8001}
```

- [ ] **Step 2: Verify the YAML is still valid**

```bash
cd automation/docker && docker compose -f docker-compose.dev.yml config --quiet 2>&1 | head -5
```

Expected: no output (exit 0 = valid YAML). If docker isn't available: `python3 -c "import yaml; yaml.safe_load(open('docker-compose.dev.yml'))"` — no error.

- [ ] **Step 3: Commit**

```bash
git add automation/docker/docker-compose.dev.yml
git commit -m "feat(compose): add REACH_LAYER_WEB_MODE env var to reach_layer_web service"
```

---

## Task 5: `server.py` — routing_only factory

**Context:** `server.py` has one factory `create_app(web_reach, config)` that registers all routes. We need to:
1. Extract `/health` + all three `/ingest/*` routes into `_register_ingest_routes(app, config)`
2. Add `create_routing_only_app(config)` that only calls the helper
3. Update `create_app` to call the helper instead of re-defining those routes inline
4. Dispatch at module level based on `REACH_LAYER_WEB_MODE`

The ingest routes currently live at lines ~791–951 and `/health` at lines ~363–366. Both will move to the helper.

**Files:**
- Modify: `reach_layer/web/server.py`
- Test: `reach_layer/web/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `reach_layer/web/tests/test_server.py` (after the existing imports and fixtures):

```python
from server import create_routing_only_app


# ---------------------------------------------------------------------------
# Fixture for routing_only mode
# ---------------------------------------------------------------------------

@pytest.fixture
def client_routing_only(config):
    """TestClient for routing_only mode — no WebReachLayer needed."""
    app = create_routing_only_app(config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# routing_only mode — routes that MUST work
# ---------------------------------------------------------------------------

def test_routing_only_health(client_routing_only):
    """GET /health returns 200 in routing_only mode."""
    res = client_routing_only.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@respx.mock
def test_routing_only_ingest_upload_proxies_to_ke(client_routing_only):
    """POST /ingest/upload proxies to KE in routing_only mode."""
    import os
    os.environ["DEVKIT_TO_REACH_API_KEY"] = "test-devkit-key"
    os.environ["KE_INTERNAL_URL"] = "http://ke-test"
    respx.post("http://ke-test/upload").mock(
        return_value=httpx.Response(200, json={"job_id": "j1"})
    )
    res = client_routing_only.post(
        "/ingest/upload",
        headers={"X-API-Key": "test-devkit-key", "Content-Type": "multipart/form-data; boundary=x"},
        content=b"--x\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\ndata\r\n--x--",
    )
    assert res.status_code == 200
    os.environ.pop("DEVKIT_TO_REACH_API_KEY", None)
    os.environ.pop("KE_INTERNAL_URL", None)


@respx.mock
def test_routing_only_ingest_job_proxies_to_ke(client_routing_only):
    """GET /ingest/job/{id} proxies to KE in routing_only mode."""
    import os
    os.environ["DEVKIT_TO_REACH_API_KEY"] = "test-devkit-key"
    os.environ["KE_INTERNAL_URL"] = "http://ke-test"
    respx.get("http://ke-test/upload/job/job-123").mock(
        return_value=httpx.Response(200, json={"status": "complete"})
    )
    res = client_routing_only.get(
        "/ingest/job/job-123",
        headers={"X-API-Key": "test-devkit-key"},
    )
    assert res.status_code == 200
    os.environ.pop("DEVKIT_TO_REACH_API_KEY", None)
    os.environ.pop("KE_INTERNAL_URL", None)


@respx.mock
def test_routing_only_ingest_jobs_proxies_to_ke(client_routing_only):
    """GET /ingest/jobs proxies to KE in routing_only mode."""
    import os
    os.environ["DEVKIT_TO_REACH_API_KEY"] = "test-devkit-key"
    os.environ["KE_INTERNAL_URL"] = "http://ke-test"
    respx.get("http://ke-test/upload/jobs").mock(
        return_value=httpx.Response(200, json=[])
    )
    res = client_routing_only.get(
        "/ingest/jobs",
        headers={"X-API-Key": "test-devkit-key"},
    )
    assert res.status_code == 200
    os.environ.pop("DEVKIT_TO_REACH_API_KEY", None)
    os.environ.pop("KE_INTERNAL_URL", None)


# ---------------------------------------------------------------------------
# routing_only mode — web/auth/chat/session routes must NOT exist (404)
# ---------------------------------------------------------------------------

def test_routing_only_root_is_404(client_routing_only):
    assert client_routing_only.get("/").status_code == 404


def test_routing_only_chat_is_404(client_routing_only):
    assert client_routing_only.post("/chat", json={}).status_code == 404


def test_routing_only_app_config_is_404(client_routing_only):
    assert client_routing_only.get("/app-config").status_code == 404


def test_routing_only_auth_google_is_404(client_routing_only):
    assert client_routing_only.post("/auth/google", json={"credential": "x"}).status_code == 404


def test_routing_only_auth_me_is_404(client_routing_only):
    assert client_routing_only.get("/auth/me").status_code == 404


def test_routing_only_sessions_is_404(client_routing_only):
    assert client_routing_only.get("/sessions").status_code == 404


def test_routing_only_user_history_is_404(client_routing_only):
    assert client_routing_only.get("/user-history/user-1").status_code == 404


# ---------------------------------------------------------------------------
# routing_only mode — no auth env vars required at startup
# ---------------------------------------------------------------------------

def test_routing_only_boots_without_google_client_id(config):
    """create_routing_only_app must not raise even with no GOOGLE_CLIENT_ID."""
    import os
    os.environ.pop("GOOGLE_CLIENT_ID", None)
    os.environ.pop("REACH_SESSION_SECRET", None)
    app = create_routing_only_app(config)   # must not raise
    client = TestClient(app)
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Full mode — existing behaviour preserved (smoke test)
# ---------------------------------------------------------------------------

def test_full_mode_health_still_works(client):
    """GET /health returns 200 in full mode — regression check."""
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd reach_layer/web && uv run pytest tests/test_server.py -k "routing_only" -v 2>&1 | head -30
```

Expected: FAIL — `create_routing_only_app` doesn't exist yet.

- [ ] **Step 3: Extract `_register_ingest_routes` helper in `server.py`**

In `reach_layer/web/server.py`, add the following function **immediately before** `create_app` (i.e., before line 265). This function contains the `/health` route and all three `/ingest/*` routes — copied verbatim from their current inline positions inside `create_app`:

```python
def _register_ingest_routes(app: FastAPI, config: dict) -> None:
    """Register /health and /ingest/* routes onto app.

    Called by both create_routing_only_app and create_app so ingest
    behaviour is identical in both modes.
    """
    _DEVKIT_TO_REACH_API_KEY = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
    _REACH_TO_KE_API_KEY = os.environ.get("REACH_TO_KE_API_KEY", "")
    _KE_INTERNAL_URL = os.environ.get("KE_INTERNAL_URL") or config.get("ke_internal_url", "")

    @app.get("/health")
    def health() -> dict:
        """Return service health status."""
        return {"status": "ok"}

    @app.post("/ingest/upload")
    async def ingest_upload(request: Request):
        """Stream multipart upload from dev-kit to KE without buffering."""
        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, _DEVKIT_TO_REACH_API_KEY)
        if not _KE_INTERNAL_URL:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{_KE_INTERNAL_URL}/upload",
                    content=request.stream(),
                    headers={
                        "Content-Type": request.headers.get("Content-Type", ""),
                        "X-API-Key": _REACH_TO_KE_API_KEY,
                    },
                )
            logger.info(
                "reach.ingest_upload",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "success",
                    "ke_status": response.status_code,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.ingest_upload_ke_unreachable",
                extra={"operation": "reach.ingest_upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.ingest_upload_timeout",
                extra={"operation": "reach.ingest_upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e

    @app.get("/ingest/job/{job_id}")
    async def ingest_job_status(job_id: str, request: Request):
        """Proxy job status poll from dev-kit to KE."""
        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, _DEVKIT_TO_REACH_API_KEY)
        if not _KE_INTERNAL_URL:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{_KE_INTERNAL_URL}/upload/job/{job_id}",
                    headers={"X-API-Key": _REACH_TO_KE_API_KEY},
                )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.ingest_job_status_ke_unreachable",
                extra={"operation": "reach.ingest_job_status", "status": "failure", "error": str(e)},
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.ingest_job_status_timeout",
                extra={"operation": "reach.ingest_job_status", "status": "failure", "error": str(e)},
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e

    @app.get("/ingest/jobs")
    async def list_ingest_jobs(request: Request, limit: int = 100):
        """Proxy ingestion history list from dev-kit to KE."""
        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, _DEVKIT_TO_REACH_API_KEY)
        if not _KE_INTERNAL_URL:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{_KE_INTERNAL_URL}/upload/jobs",
                    params={"limit": limit},
                    headers={"X-API-Key": _REACH_TO_KE_API_KEY},
                )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.list_ingest_jobs_ke_unreachable",
                extra={"operation": "reach.list_ingest_jobs", "status": "failure", "error": str(e)},
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.list_ingest_jobs_timeout",
                extra={"operation": "reach.list_ingest_jobs", "status": "failure", "error": str(e)},
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e
```

- [ ] **Step 4: Add `create_routing_only_app` in `server.py`**

Immediately after `_register_ingest_routes` and before `create_app`, add:

```python
def create_routing_only_app(config: dict) -> FastAPI:
    """Create a minimal FastAPI app for voice-only deployments.

    Registers only GET /health and the three /ingest/* proxy routes.
    No WebReachLayer, no HTTP clients, no auth validation, no React SPA.

    Args:
        config: Full merged config dict (used by _register_ingest_routes).

    Returns:
        Configured minimal FastAPI application.
    """
    app = FastAPI(title="Reach Layer — Web Channel Adapter (routing-only)")
    FastAPIInstrumentor.instrument_app(app)
    _register_ingest_routes(app, config)
    return app
```

- [ ] **Step 5: Remove the inline `/health` and `/ingest/*` routes from `create_app`**

Inside `create_app`, delete the four inline route definitions that are now covered by `_register_ingest_routes`:

- Delete the `GET /health` block (currently around lines 359–366):
  ```python
  # ------------------------------------------------------------------
  # GET /health
  # ------------------------------------------------------------------

  @app.get("/health")
  def health() -> dict:
      """Return service health status."""
      return {"status": "ok"}
  ```

- Delete the entire ingest section — the comment block and all three ingest route functions (currently around lines 786–951):
  ```python
  # ------------------------------------------------------------------
  # Upload proxy — Reach Layer → KE (approved architecture exception)
  # ...
  # ------------------------------------------------------------------

  _DEVKIT_TO_REACH_API_KEY = os.environ.get(...)
  _REACH_TO_KE_API_KEY = os.environ.get(...)
  _KE_INTERNAL_URL = ...

  @app.post("/ingest/upload")
  ...

  @app.get("/ingest/job/{job_id}")
  ...

  @app.get("/ingest/jobs")
  ...
  ```

Then add a single call to the helper just before `return app`:

```python
    _register_ingest_routes(app, config)

    return app
```

- [ ] **Step 6: Update module-level startup dispatch at the bottom of `server.py`**

Find the bottom section of `server.py` (currently around lines 956–1029). Replace the final two lines:

```python
_web_reach = WebReachLayer(_config)
app = create_app(_web_reach, _config)
```

with:

```python
WEB_MODE = os.getenv("REACH_LAYER_WEB_MODE", "full")
if WEB_MODE == "routing_only":
    logger.info(
        "reach_server.startup_mode",
        extra={"operation": "server.startup", "status": "success", "mode": "routing_only"},
    )
    app = create_routing_only_app(_config)
else:
    _web_reach = WebReachLayer(_config)
    app = create_app(_web_reach, _config)
```

- [ ] **Step 7: Run the routing_only tests**

```bash
cd reach_layer/web && uv run pytest tests/test_server.py -k "routing_only" -v
```

Expected: all routing_only tests PASS.

- [ ] **Step 8: Run the full server test suite**

```bash
cd reach_layer/web && uv run pytest tests/test_server.py -v
```

Expected: all previously passing tests still pass (full mode behaviour unchanged).

- [ ] **Step 9: Commit**

```bash
git add reach_layer/web/server.py reach_layer/web/tests/test_server.py
git commit -m "feat(reach-layer-web): add routing_only mode — health + ingest only for voice deployments"
```

---

## Task 6: End-to-end verification

Verify the complete chain from channel selection → config → mode injection → server boot.

**Files:** No code changes — verification only.

- [ ] **Step 1: Run all backend devkit tests**

```bash
cd dev-kit && uv run pytest -q
```

Expected: all tests pass (514+ tests).

- [ ] **Step 2: Run reach_layer web tests**

```bash
cd reach_layer/web && uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run the full devkit test script**

```bash
cd dev-kit && bash run_tests.sh
```

Expected: `All devkit tests passed.`

- [ ] **Step 4: Verify the acceptance criteria manually**

Check each item from the spec:

1. **Voice-only compose has `routing_only`**: Run a preview with voice-only channel selection and grep the output.
   ```python
   # In a Python shell inside dev-kit/:
   from dev_kit.agent.accumulator import ConfigAccumulator
   acc = ConfigAccumulator()
   acc.set_reach_channel_selection(["voice"])
   mode = acc._data["reach_layer"].get("reach_layer", {}).get("channels", {}).get("web", {}).get("mode")
   assert mode == "routing_only", f"got {mode}"
   print("✓ accumulator writes routing_only for voice-only")
   ```

2. **`create_routing_only_app` boots without Google Client ID**:
   ```python
   import os; os.environ.pop("GOOGLE_CLIENT_ID", None); os.environ.pop("REACH_SESSION_SECRET", None)
   import sys; sys.path.insert(0, "reach_layer/web")
   from server import create_routing_only_app
   app = create_routing_only_app({})
   print("✓ routing_only boots without auth env vars")
   ```

3. **Web-only channel still gets `full` mode**:
   ```python
   acc2 = ConfigAccumulator()
   acc2.set_reach_channel_selection(["web"])
   mode2 = acc2._data["reach_layer"].get("reach_layer", {}).get("channels", {}).get("web", {}).get("mode")
   assert mode2 == "full", f"got {mode2}"
   print("✓ accumulator writes full for web channel")
   ```

- [ ] **Step 5: Final commit if any last fixes were needed**

```bash
git add -A
git commit -m "fix: end-to-end verification fixes for routing-only mode"
```

Only create this commit if something was actually fixed. Skip if all tests passed clean.
