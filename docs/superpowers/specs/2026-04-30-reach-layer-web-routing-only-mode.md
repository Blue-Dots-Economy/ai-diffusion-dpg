# Reach Layer Web — Routing-Only Mode

## Goal

`reach_layer_web` is always deployed (even for voice-only setups) because it is the mandatory ingest proxy between dev-kit and the Knowledge Engine. Today it always boots in full mode — React SPA, Google auth, chat, sessions — even when those features will never be used. This spec adds a `routing_only` mode that runs only `/health` and `/ingest/*`, eliminating the unnecessary weight for voice-only deployments.

## Background

The deploy wizard lets users select channels (web, voice, cli). The current deployment logic in `app.py` always forces `reach_layer_web` into the manifest regardless of selection (`effective_channels = set(selected_channels) | {"web"}`), because without it the KB ingest path breaks. `reach_layer_voice` and `ngrok` are only deployed when voice is selected.

This means a voice-only deploy today runs:
- `reach_layer_web` — full mode (SPA, auth, chat, sessions — all idle and wasted)
- `reach_layer_voice` ✓
- `ngrok` ✓

The fix: keep `reach_layer_web` always in the manifest, but boot it in `routing_only` mode when web channel is not selected.

## What runs in each mode

### `routing_only` (web channel not selected)

Registered routes:
- `GET /health`
- `POST /ingest/upload`
- `GET /ingest/job/{job_id}`
- `GET /ingest/jobs`

Not created or started:
- `WebReachLayer` — not instantiated
- `ac_client` (httpx to Agent Core) — not created
- `ml_client` (httpx to Memory Layer) — not created
- Auth config validation — skipped entirely (`GOOGLE_CLIENT_ID` and `REACH_SESSION_SECRET` not required)
- React SPA static files — not mounted

All web/auth/chat/session routes are simply never registered. Any request to `GET /`, `POST /chat`, `/auth/*`, `/sessions/*`, `GET /user-history/*` receives 404.

Startup log: `"Starting reach_layer_web in routing_only mode — web UI disabled"`

### `full` (web channel selected)

Exactly current behaviour — nothing changes.

## Architecture

### Mode signal flow

```
deploy wizard channel selector
  → accumulator.set_reach_channel_selection(channels)
      → writes reach_layer.channels.web.mode into config YAML
  → _run_docker_deploy in app.py
      → if "web" not in selected_channels:
            inject REACH_LAYER_WEB_MODE=routing_only into reach_layer_web service env
        else:
            inject REACH_LAYER_WEB_MODE=full
  → docker compose up
      → reach_layer_web container starts
          → server.py reads REACH_LAYER_WEB_MODE at module level
          → dispatches to create_routing_only_app() or create_app()
```

### Two factory functions sharing ingest routes

```
_register_ingest_routes(app, config)    ← shared helper, called by both factories
create_routing_only_app(config)         ← health + ingest only, no WebReachLayer
create_app(web_reach, config)           ← current full behaviour, unchanged
```

Module-level startup dispatch (bottom of `server.py`):

```python
WEB_MODE = os.getenv("REACH_LAYER_WEB_MODE", "full")
if WEB_MODE == "routing_only":
    logger.info("reach_server.mode", extra={"operation": "server.startup", "status": "success", "mode": "routing_only"})
    app = create_routing_only_app(_config)
else:
    _web_reach = WebReachLayer(_config)
    app = create_app(_web_reach, _config)
```

`WebReachLayer` is never instantiated in `routing_only` mode — clean separation.

## Files changed

| File | Change |
|---|---|
| `dev-kit/dev_kit/schema.py` | Add `mode: Literal["routing_only", "full"] = "full"` to `WebChannelConfig` |
| `dev-kit/dev_kit/agent/accumulator.py` | `set_reach_channel_selection()` writes `mode` into `reach_layer.channels.web` config |
| `dev-kit/dev_kit/agent/app.py` | `_run_docker_deploy()` injects `REACH_LAYER_WEB_MODE` env var into `reach_layer_web` service. Same in `get_deploy_preview()` so preview matches runtime |
| `automation/docker/docker-compose.dev.yml` | Add `REACH_LAYER_WEB_MODE=${REACH_LAYER_WEB_MODE:-full}` to `reach_layer_web` environment |
| `reach_layer/web/server.py` | Read `REACH_LAYER_WEB_MODE` at module level; extract `_register_ingest_routes()`; add `create_routing_only_app()`; dispatch at bottom |
| `reach_layer/web/tests/test_server.py` | New tests for `routing_only` mode |
| `dev-kit/tests/test_app_deploy_routes.py` | Tests that voice-only deploy injects `REACH_LAYER_WEB_MODE=routing_only` |
| `dev-kit/tests/test_schema.py` | Test `mode` field on `WebChannelConfig` |
| `dev-kit/tests/test_accumulator_*.py` | Test `set_reach_channel_selection` writes mode correctly |

**No changes needed:**
- `get_required_channel_secrets()` — already only returns `GOOGLE_CLIENT_ID` when `web` is selected
- Deploy wizard step 5 UI — already only shows Google Client ID when `project.channel_secrets` includes it
- `reach_layer_voice`, `ngrok` deployment logic — unchanged

## Detailed component changes

### `schema.py`

```python
class WebChannelConfig(BaseModel):
    mode: Literal["routing_only", "full"] = "full"
    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: dict[str, Any] = Field(default_factory=dict, description="...")
```

### `accumulator.py` — `set_reach_channel_selection()`

After storing the channel list, write the web mode into the reach_layer config so it flows into the domain YAML:

```python
def set_reach_channel_selection(self, channels: list[str]) -> None:
    self._data["reach_layer"]["_selected_channels"] = list(channels)
    web_mode = "full" if "web" in channels else "routing_only"
    # Write into the reach_layer block so it appears in the exported config YAML
    reach_cfg = self._data["reach_layer"].setdefault("reach_layer", {})
    channels_cfg = reach_cfg.setdefault("channels", {})
    web_cfg = channels_cfg.setdefault("web", {})
    web_cfg["mode"] = web_mode
```

### `app.py` — `_run_docker_deploy()` and `get_deploy_preview()`

In the service-patching loop, after handling `reach_layer_web`:

```python
if svc_name == "reach_layer_web":
    web_mode = "full" if "web" in set(selected_channels) else "routing_only"
    env_list = svc.setdefault("environment", [])
    env_list.append(f"REACH_LAYER_WEB_MODE={web_mode}")
```

Apply the same logic in `get_deploy_preview()` so the preview YAML shows the correct mode.

### `docker-compose.dev.yml` — `reach_layer_web` environment

```yaml
reach_layer_web:
  environment:
    - CONFIG_FOLDER=/app/config
    - REACH_LAYER_WEB_MODE=${REACH_LAYER_WEB_MODE:-full}
    - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
    - REACH_SESSION_SECRET=${REACH_SESSION_SECRET:-}
    - DEVKIT_TO_REACH_API_KEY=${DEVKIT_TO_REACH_API_KEY:-}
    - REACH_TO_KE_API_KEY=${REACH_TO_KE_API_KEY:-}
    - KE_INTERNAL_URL=${KE_INTERNAL_URL:-http://knowledge_engine:8001}
```

### `server.py` — factory refactor

```python
def _register_ingest_routes(app: FastAPI, config: dict) -> None:
    """Register /health and /ingest/* routes onto app. Called by both factories."""
    _DEVKIT_TO_REACH_API_KEY = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
    _REACH_TO_KE_API_KEY = os.environ.get("REACH_TO_KE_API_KEY", "")
    _KE_INTERNAL_URL = os.environ.get("KE_INTERNAL_URL") or config.get("ke_internal_url", "")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/ingest/upload")
    async def ingest_upload(request: Request): ...   # exact copy of current implementation

    @app.get("/ingest/job/{job_id}")
    async def ingest_job_status(job_id: str, request: Request): ...

    @app.get("/ingest/jobs")
    async def list_ingest_jobs(request: Request, limit: int = 100): ...


def create_routing_only_app(config: dict) -> FastAPI:
    """Create a minimal FastAPI app with only /health and /ingest/* routes.

    No WebReachLayer, no HTTP clients, no auth, no SPA. Used when
    REACH_LAYER_WEB_MODE=routing_only (voice-only deployments).
    """
    app = FastAPI(title="Reach Layer — Web Channel Adapter (routing-only)")
    FastAPIInstrumentor.instrument_app(app)

    @app.on_event("shutdown")
    def _noop_shutdown() -> None:
        pass

    _register_ingest_routes(app, config)
    return app


def create_app(web_reach: WebReachLayer, config: dict) -> FastAPI:
    """Create the full FastAPI app — current behaviour, unchanged."""
    ...
    # calls _register_ingest_routes(app, config) instead of inline route definitions
    ...
```

## Test coverage

### `reach_layer/web/tests/test_server.py` — new routing_only tests

```python
# routing_only mode — ingest routes work
def test_routing_only_health():         # GET /health → 200
def test_routing_only_ingest_upload():  # POST /ingest/upload → proxies to KE
def test_routing_only_ingest_job():     # GET /ingest/job/{id} → proxies to KE
def test_routing_only_ingest_jobs():    # GET /ingest/jobs → proxies to KE

# routing_only mode — web routes absent
def test_routing_only_chat_404():       # POST /chat → 404
def test_routing_only_root_404():       # GET / → 404
def test_routing_only_auth_404():       # POST /auth/google → 404
def test_routing_only_sessions_404():   # GET /sessions → 404
def test_routing_only_user_history_404():  # GET /user-history/x → 404

# routing_only mode — no auth env vars required
def test_routing_only_no_google_client_id_required():
def test_routing_only_no_session_secret_required():
```

### `dev-kit/tests/test_app_deploy_routes.py`

```python
def test_voice_only_deploy_sets_routing_only_mode():
    # selected_channels = ["voice"], reach_layer_web env should contain REACH_LAYER_WEB_MODE=routing_only

def test_web_deploy_sets_full_mode():
    # selected_channels = ["web"], reach_layer_web env should contain REACH_LAYER_WEB_MODE=full

def test_web_and_voice_deploy_sets_full_mode():
    # selected_channels = ["web", "voice"], reach_layer_web env should contain REACH_LAYER_WEB_MODE=full
```

## Acceptance criteria

- [ ] Voice-only deploy generates a compose manifest where `reach_layer_web` has `REACH_LAYER_WEB_MODE=routing_only`
- [ ] Web deploy generates `REACH_LAYER_WEB_MODE=full`
- [ ] In `routing_only` mode: `GET /health` → 200, `/ingest/*` works exactly as today
- [ ] In `routing_only` mode: `GET /`, `POST /chat`, `/auth/*`, `/sessions/*`, `/user-history/*` → 404
- [ ] In `routing_only` mode: startup succeeds without `GOOGLE_CLIENT_ID` or `REACH_SESSION_SECRET`
- [ ] In `full` mode: all current behaviour preserved — no regression
- [ ] Deploy wizard step 5 does not show `GOOGLE_CLIENT_ID` field for voice-only deploy (already working — no change)
- [ ] `docker-compose.dev.yml` allows `REACH_LAYER_WEB_MODE` to be overridden locally via env var
