# Child A — Ring 0 + Inter-DPG Service Auth: Implementation Plan

**Design documents:**
- [Umbrella design](https://github.com/Blue-Dots-Economy/ai-diffusion-dpg/blob/feat/auth-iam-v2/docs/superpowers/specs/2026-06-29-auth-iam-v2-umbrella-design.md)
- [Child A design](https://github.com/Blue-Dots-Economy/ai-diffusion-dpg/blob/feat/auth-iam-v2/docs/superpowers/specs/2026-06-29-auth-iam-childA-inter-dpg-service-auth-design.md)

**In-scope:** Ring 0 (Keycloak foundation + `dpg_auth` shared library) + Child A (service-to-service token authentication).
**Out-of-scope:** Child B (end-user OIDC/OTP), Child C (dev-kit tenancy), Child D (MCP `private_key_jwt`).

> [!IMPORTANT]
> **Revision history:** This plan was updated on 2026-07-02 to incorporate all PR #362 review feedback (reviewer: AniketSaki, CHANGES_REQUESTED). See §18 for the complete change log and §19 for open questions.

---

## 1. Background & Problem Statement

Every DPG HTTP endpoint currently accepts requests from any caller with no mutual authentication. A pod on the same Kubernetes network can call `POST /execute` on Action Gateway directly, read Memory Layer session state, or forge Trust Layer results — bypassing Agent Core's consent gate. The only auth today is:

- Static `X-API-Key` header on the `reach-layer → knowledge-engine` ingest chain (`knowledge_engine/src/auth.py`, `reach_layer/web/src/auth.py`).
- Google SSO + HS256 session JWT in reach-web.

No JWT/OIDC/Keycloak/JWKS exists anywhere. This child closes the gap for **service-to-service traffic only** by adopting Keycloak `client_credentials` service-account tokens, verified by a new `dpg_auth` shared library installed as a FastAPI middleware on every service.

---

## 2. Architecture

### 2.1 Trust Model After Child A

```
┌──────────────────────────────────────────────────────────────────────────┐
│  network-common Keycloak                                                  │
│  Realm: ai-diffusion-platform                                             │
│  One service-account client per block (svc-agent-core, svc-trust-layer…) │
│  Protocol mapper: role claim = "service:<block_name>"                    │
└──────────────────────────────────────────────────────────────────────────┘
          │  issues short-lived tokens (client_credentials)
          ▼
┌────────────────────────┐           ┌──────────────────────────────────────┐
│  ServiceAuthClient     │           │  VerifyJwtMiddleware (callee side)    │
│  (caller side)         │           │  + AuthorizeMiddleware                │
│  dpg_auth/client.py    │─ Bearer ─▶│  dpg_auth/middleware/                 │
│  caches + refreshes    │           │  KeycloakAuthProvider (JWKS offline) │
│  own token             │           │  CompositeAuthProvider (routes iss)   │
└────────────────────────┘           └──────────────────────────────────────┘
```

### 2.2 Dual-Realm Architecture

| Realm | Purpose | Issuer |
|---|---|---|
| `ai-diffusion-platform` | Service accounts (always used) | `<KC_URL>/realms/ai-diffusion-platform` |
| `<host-realm>` (future) | End-user tokens (children B/D) | routing by `iss` claim |

The `CompositeAuthProvider` routes verification by the JWT `iss` claim, making children B/D a drop-in. Service auth is therefore independent of the end-user realm decision.

### 2.3 Key Flows

**Caller flow (every outgoing inter-service HTTP request):**
1. `ServiceAuthClient.get_token()` — checks in-memory cache, fetches from Keycloak `/token` if expired, returns `access_token`.
2. httpx wrapper injects `Authorization: Bearer <token>` header.

**Callee flow (every incoming inter-service HTTP request):**
1. `VerifyJwtMiddleware` extracts Bearer token.
2. Token forwarded to `CompositeAuthProvider.verify(token)` → routes by `iss` to `KeycloakAuthProvider`.
3. `KeycloakAuthProvider` fetches JWKS from Keycloak (cached, refresh-ahead), verifies signature + exp + iss + aud.
4. Returns frozen `AuthContext(caller_id, service_role, token_exp)` stored in `contextvars`.
5. `AuthorizeMiddleware` checks `AuthContext.service_role` against the callee's `allow_callers` config list.
6. On failure: 401/403 immediately; on bypass paths (e.g., `/health`): no auth.

**Shadow mode** (rollout gate): middleware logs auth decisions but never blocks. A config flag (`dpg_auth.enforce: false`) keeps all services functional while credentials are provisioned.

---

## 3. Current System Map

### 3.1 Services and Their Create-App Factories

| Block | Entry point | `create_app()` factory | Middleware today |
|---|---|---|---|
| Agent Core | `agent_core/main.py` | `create_orchestration_app()` in `src/servers/orchestration_server.py` | `FastAPIInstrumentor` only |
| Knowledge Engine | `knowledge_engine/main.py` | inline in `main.py` | `FastAPIInstrumentor` |
| Memory Layer | `memory_layer/main.py` | likely same pattern | `FastAPIInstrumentor` |
| Trust Layer | `trust_layer/src/server.py` → `create_app(trust)` | `trust_layer/src/server.py` | `FastAPIInstrumentor` |
| Action Gateway | `action_gateway/src/server.py` | `create_app()` or inline | `FastAPIInstrumentor` |
| Observability Layer | `observability_layer/src/server.py` | inline | `FastAPIInstrumentor` |
| Reach Web | `reach_layer/web/` | `web_reach.py` | Google SSO / HS256 |
| Reach MCP | `reach_layer/mcp/src/server.py` | inline | Static API key (`_authenticate_request`) — **child D scope, not touched here** |

### 3.2 HTTP Clients (Caller Side)

All live in `agent_core/src/http_clients/` (sync) and `agent_core/src/http_clients/async_/` (async). Each is initialised in `agent_core/main.py::_build_app()` and receives the full merged `config` dict. They read endpoint + timeout from `<service>_client` config sections. **No auth header injection today.**

### 3.3 Config System

- Each block: `config/dpg.yaml` + domain YAML deep-merged at startup → strict Pydantic `MergedConfig` (`extra="forbid"`).
- Dev-kit mirrors runtime schemas → runtime-devkit-sync discipline applies to every config field added.
- Shared dependency pattern: `dpg_telemetry` lives in `observability_layer/src/dpg_telemetry/`, consumed via `[tool.uv.sources] observability-layer = { path = "../observability_layer" }` in every module's `pyproject.toml`.
- **`dpg_auth` will follow exactly this pattern.**

### 3.4 Reusable vs Modified Components

| Component | Action | Why |
|---|---|---|
| `dpg_telemetry` package layout | **Model** `dpg_auth` on this | Established shared-package precedent |
| `agent_core/src/http_clients/*.py` | **Modify** — inject auth header from `ServiceAuthClient` | Auth must propagate on every outgoing call |
| `agent_core/src/http_clients/async_/*.py` | **Modify** — same for async path | stream_turn uses async clients |
| Every `create_app()` / server factory (excluding MCP) | **Modify** — add `VerifyJwtMiddleware` + `AuthorizeMiddleware` | Callee-side enforcement |
| `<block>/src/schema/config.py` | **Modify** — add `dpg_auth` config section | `extra="forbid"` rejects unknown keys |
| `dev-kit/dev_kit/schemas/domain/<block>.py` | **Modify** — mirror new auth section | runtime-devkit-sync discipline |
| `dev-kit/dev_kit/schema.py` | **Modify** — flat-file copy | host-mode deploy gate |
| `automation/docker/docker-compose.dev.yml` | **Modify** — add Keycloak service | Ring 0 foundation |
| `automation/docker/keycloak/` | **Create** — realm import JSON | Baked realm provisioning |
| `knowledge_engine/src/auth.py` | **Retire** X-API-Key — on enforce flip | Spec §4.5: static keys retire when Keycloak enforces |
| `reach_layer/web/src/auth.py` | **Retire** X-API-Key (ingest path only) — on enforce flip | Same; Google SSO for user routes preserved |

---

## 4. New Package: `dpg_auth/`

### 4.1 Location and Discovery

```
dpg_auth/
├── pyproject.toml
├── src/
│   └── dpg_auth/
│       ├── __init__.py          # public API re-exports; build_auth_provider() factory
│       ├── config.py            # DpgAuthConfig Pydantic model (self-contained)
│       ├── context.py           # AuthContext frozen dataclass + ContextVar
│       ├── logging.py           # StructuredLogFilter + install_filter()
│       ├── client.py            # ServiceAuthClient (token fetch + httpx wrapper)
│       ├── provider/
│       │   ├── __init__.py
│       │   ├── base.py          # AuthProviderBase (ABC): verify(token) -> AuthContext
│       │   ├── keycloak.py      # KeycloakAuthProvider (offline JWKS + cache)
│       │   ├── composite.py     # CompositeAuthProvider (routes by iss)
│       │   └── static.py        # StaticAuthProvider (tests/CI — explicit opt-in only)
│       └── middleware/
│           ├── __init__.py
│           ├── verify.py        # VerifyJwtMiddleware (Starlette ASGI)
│           └── authorize.py     # AuthorizeMiddleware (per-callee allow_callers check)
└── tests/
    ├── test_config.py
    ├── test_context.py
    ├── test_client.py
    ├── test_provider_keycloak.py
    ├── test_provider_composite.py
    ├── test_provider_static.py
    ├── test_middleware_verify.py
    └── test_middleware_authorize.py
```

All 8 module `pyproject.toml` files gain:
```toml
[tool.uv.sources]
dpg-auth = { path = "../dpg_auth" }
```
And in `[project] dependencies`: `"dpg-auth"`.

### 4.2 `config.py` — `DpgAuthConfig`

> **Critical rule:** `<block>/src/schema/config.py` may only import from `pydantic`, `enum`, `typing`, `__future__`. The `DpgAuthConfig` itself must be self-contained for the Dockerfile bake.

```
DpgAuthConfig
├── enabled: bool = True          # when False: middleware is no-op (future killswitch)
├── enforce: bool = False         # shadow mode default; set True after rollout
├── provider: str = "keycloak"    # "keycloak" | "static" — explicit, never inferred from URL
├── keycloak_url: str = ""        # e.g. "http://keycloak:8080" (container port)
├── realm: str = "ai-diffusion-platform"
├── client_id: str = ""           # set per-block in framework YAML (not secret)
├── client_secret: str = ""       # injected from env var only; never committed
├── token_ttl_margin_s: int = 30  # refresh this many seconds before exp
├── jwks_cache_ttl_s: int = 300   # re-fetch JWKS after this many seconds
├── allow_callers: list[str]      # per-block allowlist from spec §4.3 — NOT empty default
└── bypass_paths: list[str] = ["/health"]  # paths that skip JWT check
```

> [!IMPORTANT]
> **`provider` field added (reviewer Should-fix #6):** The original plan inferred `StaticAuthProvider` from `keycloak_url == ""`. This is fail-open: a prod misconfiguration (empty URL) silently disables real verification. Use `provider: "keycloak"` (default) which fails closed when `enforce: true` and `keycloak_url` is unset. `provider: "static"` must be set explicitly (tests/CI only).

> [!IMPORTANT]
> **`allow_callers` ships with real values (reviewer Must-fix #1):** Each block's framework YAML (`dev-kit/dpg/<block>.yaml`) ships with the real per-callee allowlist from spec §4.3. These only take effect when `enforce: true`; populating them during shadow mode carries zero lockout risk. See §6.4 for the full table.

**Schema isolation strategy:** In each block's `schema/config.py`, inline a mirror of `DpgAuthConfig` (same field names, same types, same defaults). The block schema only needs this to avoid `extra="forbid"` rejections. The `dpg_auth` library validates the dict again at startup with its own `DpgAuthConfig`.

### 4.3 `context.py` — `AuthContext`

```python
@dataclass(frozen=True)
class AuthContext:
    caller_id: str          # e.g. "svc-agent-core"
    service_role: str       # e.g. "service:agent_core"
    token_exp: int          # Unix epoch
    raw_claims: dict        # full decoded payload (for logging)
```

Stored in a `ContextVar[Optional[AuthContext]]` so it is request-scoped and async-safe. Accessors: `get_auth_context()`, `set_auth_context()`.

### 4.4 `provider/base.py` — `AuthProviderBase`

```python
class AuthProviderBase(ABC):
    @abstractmethod
    def verify(self, token: str) -> AuthContext:
        """Verify token. Raises AuthError on any failure."""
```

Concrete: `KeycloakAuthProvider`, `CompositeAuthProvider`, `StaticAuthProvider`.

### 4.5 `provider/keycloak.py` — `KeycloakAuthProvider`

- Fetches JWKS from `<keycloak_url>/realms/<realm>/protocol/openid-connect/certs`.
- Cache: `dict[kid, public_key]` + `fetched_at`. Refreshes when stale or `kid` is unknown.
- Verifies: RS256 signature, `exp`, `iss == <keycloak_url>/realms/<realm>`, role claim presence.
- **Refresh-ahead:** background fetch when `(now - fetched_at) > 0.8 * jwks_cache_ttl_s`.
- Uses `PyJWT` with `cryptography` extra (already transitively available via reach-web).
- Raises `AuthError(reason, message)` — never leaks raw exception text.
- **Startup guard:** raises `ConfigurationError` at construction time if `keycloak_url` is empty and `enforce: true`.

### 4.6 `provider/composite.py` — `CompositeAuthProvider`

Routes `verify(token)` by the JWT `iss` claim to the appropriate sub-provider. Drop-in point for children B/C/D — add a provider to the composite at startup, zero middleware changes.

### 4.7 `provider/static.py` — `StaticAuthProvider`

Used in tests/CI only. HS256, known secret, returns a fixed `AuthContext`. **Activated only when `provider: "static"` is explicitly set in config — never inferred from an empty `keycloak_url`.** Constant-time comparison via `hmac.compare_digest`.

### 4.8 `client.py` — `ServiceAuthClient`

```
ServiceAuthClient
├── __init__(config: DpgAuthConfig)
├── get_token() -> str             # blocking; cached or fresh
├── aget_token() -> str            # async variant
├── make_sync_client(**kwargs) -> httpx.Client    # token-injecting wrapper
└── make_async_client(**kwargs) -> httpx.AsyncClient
```

- Token fetched via `POST <keycloak_url>/realms/<realm>/protocol/openid-connect/token` (grant_type=client_credentials).
- Thread-safe via `threading.Lock` (sync) and `asyncio.Lock` (async).
- `make_sync_client` returns an `httpx.Client` subclass that injects `Authorization: Bearer <token>` on every request and retries once on 401 (evicting cached token first).
- Startup: `get_token()` retries with exponential backoff (3 attempts, 1s/2s delays) before raising — mitigates Keycloak startup latency.

### 4.9 `middleware/verify.py` — `VerifyJwtMiddleware`

Starlette ASGI middleware added via `app.add_middleware()`.

```
Request path:
  1. path in bypass_paths           → pass-through
  2. not enabled                    → pass-through (killswitch)
  3. Extract Authorization: Bearer
  4. Missing token + enforce=True   → 401
  5. Missing token + enforce=False  → log warning; pass-through (shadow)
  6. provider.verify(token)
  7. AuthError + enforce=True       → 401
  8. AuthError + enforce=False      → log; pass-through (shadow)
  9. set_auth_context(ctx); pass-through
```

Log fields: `operation`, `status`, `caller_id`, `service_role`, `latency_ms`. No raw token logged.

### 4.10 `middleware/authorize.py` — `AuthorizeMiddleware`

Runs after `VerifyJwtMiddleware`. Starlette adds outermost-last, so register AuthorizeMiddleware first.

```
Request path:
  1. path in bypass_paths            → pass-through
  2. ctx is None + enforce=True      → 403
  3. allow_callers is empty          → pass-through (allow all — only if explicitly set [])
  4. ctx.service_role not in allow   → 403 (enforce) or log+pass (shadow)
  5. pass-through
```

### 4.11 `build_auth_provider()` Factory

In `dpg_auth/__init__.py`:
- Returns `StaticAuthProvider` if `config.provider == "static"`.
- Returns `CompositeAuthProvider([KeycloakAuthProvider(config)])` if `config.provider == "keycloak"`.
- Raises `ConfigurationError` if `config.provider == "keycloak"` and `config.keycloak_url` is empty and `config.enforce == True` (fail-closed guard).
- Raises `ValueError` for unknown `provider` values.

---

## 5. Infrastructure: Ring 0 Keycloak

### 5.1 Docker Compose Addition

Add to `automation/docker/docker-compose.dev.yml`:

```yaml
keycloak:
  image: quay.io/keycloak/keycloak:24.0
  container_name: keycloak
  command: ["start-dev", "--import-realm"]
  environment:
    - KEYCLOAK_ADMIN=admin
    - KEYCLOAK_ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD:-admin}
  ports:
    - "8180:8080"       # host:8180 -> container:8080
  volumes:
    - ./keycloak/realms:/opt/keycloak/data/import:ro
    - keycloak_data:/opt/keycloak/data
  networks:
    - dpg_net
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8080/health/ready || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 10
    start_period: 60s
  restart: unless-stopped
```

Add `keycloak_data:` to the `volumes:` section. All 8 DPG services must add `depends_on: keycloak: condition: service_healthy` to their compose entries.

> [!IMPORTANT]
> Within `dpg_net`, services reach Keycloak at `http://keycloak:8080` (container port). The host port 8180 is for local development browser access only. `keycloak_url` in config YAML must be `http://keycloak:8080`.

### 5.2 Realm Import JSON

`automation/docker/keycloak/realms/ai-diffusion-platform.json`:
- Realm `ai-diffusion-platform`, `enabled: true`, `accessTokenLifespan: 300`.
- One service-account client per block: `svc-agent-core`, `svc-knowledge-engine`, `svc-memory-layer`, `svc-trust-layer`, `svc-action-gateway`, `svc-reach-layer`, `svc-observability-layer`, `svc-dev-kit`.
- All clients: `serviceAccountsEnabled: true`, `publicClient: false`, `clientAuthenticatorType: client-secret`. Client secrets = placeholder values (never real secrets in VCS).
- Protocol mapper: Hardcoded Claim mapper on each client, `claimName: "role"`, `claimValue: "service:<block_name>"`, added to `access_token`. E.g. `svc-agent-core` → `"role": "service:agent_core"`.

### 5.3 Production Onboarding Script

`automation/keycloak/onboard_platform_realm.sh`: idempotent shell script using Keycloak Admin REST API. Parameterised by `KC_URL`, `KC_ADMIN`, `KC_ADMIN_PASSWORD`. Creates realm, clients, mappers. Exports generated secrets to stdout for operator to place in secrets manager.

---

## 6. Config Schema Changes (All 8 Blocks)

### 6.1 Runtime Schema Addition

In each `<block>/src/schema/config.py`, add a local inline `DpgAuthConfig` mirror class and a `dpg_auth` field to the top-level `MergedConfig`. Includes the new `provider` field:

```python
class DpgAuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = True
    enforce: bool = False
    provider: str = "keycloak"    # "keycloak" | "static"
    keycloak_url: str = ""
    realm: str = "ai-diffusion-platform"
    client_id: str = ""           # set per-block in framework YAML
    client_secret: str = ""       # injected from env var; never committed
    token_ttl_margin_s: int = Field(default=30, ge=0)
    jwks_cache_ttl_s: int = Field(default=300, ge=0)
    allow_callers: list[str] = Field(default_factory=list)
    bypass_paths: list[str] = Field(default_factory=lambda: ["/health"])

class MergedConfig(BaseModel):
    ...
    dpg_auth: DpgAuthConfig = Field(default_factory=DpgAuthConfig)
```

This satisfies the "schema/config.py imports from pydantic only" constraint.

### 6.2 Dev-Kit Mirror (per block)

`dev-kit/dev_kit/schemas/domain/<block>.py`: Add `DpgAuthSection` with identical fields (including `provider`). Add `dpg_auth: Optional[DpgAuthSection] = None` to the block's domain section.

### 6.3 Dev-Kit Flat Copy

`dev-kit/dev_kit/schema.py`: Add `DpgAuthSection` and reference it in each block's flat-copy class.

### 6.4 Framework Defaults YAML — Per-Block Allowlists

> [!IMPORTANT]
> **Per-block `allow_callers` and `client_id` (reviewer Must-fix #1, Should-fix #5):** Each block ships with real values. The `allow_callers` lists are taken directly from spec §4.3. They only take effect when `enforce: true`; zero lockout risk during shadow. `client_id` is not secret — set in framework YAML, not via env injection.

Each `dev-kit/dpg/<block>.yaml` gets:

```yaml
dpg_auth:
  enabled: true
  enforce: false
  provider: "keycloak"
  keycloak_url: "http://keycloak:8080"
  realm: "ai-diffusion-platform"
  client_id: "<block-specific — see table below>"
  client_secret: ""   # injected from env var DPG_AUTH_<BLOCK>_CLIENT_SECRET only
  token_ttl_margin_s: 30
  jwks_cache_ttl_s: 300
  allow_callers: [<block-specific — see table below>]
  bypass_paths: ["/health"]
```

**Per-block values (spec §4.3 allowlist table):**

| Block (callee) | `client_id` | `allow_callers` |
|---|---|---|
| Agent Core | `svc-agent-core` | `["service:reach_layer"]` |
| Knowledge Engine | `svc-knowledge-engine` | `["service:agent_core", "service:reach_layer"]` |
| Memory Layer | `svc-memory-layer` | `["service:agent_core", "service:reach_layer"]` |
| Trust Layer | `svc-trust-layer` | `["service:agent_core"]` |
| Action Gateway | `svc-action-gateway` | `["service:agent_core"]` |
| Observability Layer | `svc-observability-layer` | `["service:agent_core"]` |
| Reach Layer (Web) | `svc-reach-layer` | `["service:dev_kit"]` |
| Dev-Kit | `svc-dev-kit` | `[]` *(no inbound service calls; allow-all is safe — dev-kit is operator-facing only)* |

> Reach Web is a **callee** (dev-kit → reach `/upload`), so it needs `VerifyJwtMiddleware` + extended `bypass_paths` for user-facing routes. MCP is **child D's surface** — not included here.

### 6.5 Client Secret Injection Pattern

`client_secret` defaults to `""`. Each block's `main.py` reads the per-block env var (e.g., `DPG_AUTH_AGENT_CORE_CLIENT_SECRET`) and mutates `config["dpg_auth"]["client_secret"]` before constructing `ServiceAuthClient`. Docker Compose injects these env vars. `ServiceAuthClient.__init__` raises `ValueError` if `client_secret` is empty when `enabled=True`, `provider="keycloak"`, and `keycloak_url` is set.

**`client_id` is not secret** — set in each block's framework YAML; no env injection needed.

---

## 7. Caller-Side Changes: HTTP Clients

### 7.1 Pattern

Each HTTP client constructor gains `auth_client: Optional[ServiceAuthClient] = None` (default `None` = backward compatible). When provided, the client uses `auth_client.make_sync_client()` / `make_async_client()` instead of bare `httpx`. The token-injecting wrapper handles 401 retry internally.

### 7.2 Modified Files

**Sync clients** (`agent_core/src/http_clients/`):
- `trust_layer.py`, `knowledge_engine.py`, `memory_layer.py`, `observability_layer.py`, `action_gateway.py`

**Async clients** (`agent_core/src/http_clients/async_/`):
- `trust_layer.py`, `knowledge_engine.py`, `memory_layer.py`, `observability_layer.py`, `action_gateway.py`

**Reach Layer** (caller of KE and Agent Core):
- Wherever Reach Web makes HTTP calls to downstream services.

### 7.3 `_build_app()` Wiring Change

```python
from dpg_auth import ServiceAuthClient, DpgAuthConfig, build_auth_provider

auth_cfg = DpgAuthConfig.model_validate(config.get("dpg_auth", {}))
# Override client_secret from env only (client_id is already in framework YAML)
secret = os.getenv("DPG_AUTH_<BLOCK>_CLIENT_SECRET", "")
if secret:
    auth_cfg = auth_cfg.model_copy(update={"client_secret": secret})
auth_client = ServiceAuthClient(auth_cfg)

memory   = MemoryLayerHttpClient(config, auth_client=auth_client)
trust    = TrustLayerHttpClient(config, auth_client=auth_client)
ke       = HttpKnowledgeEngineClient(config, auth_client=auth_client)
learning = ObservabilityLayerHttpClient(config, auth_client=auth_client)
gateway  = ActionGatewayHttpClient(config, auth_client=auth_client)
# ... async variants similarly
```

---

## 8. Callee-Side Changes: Middleware

### 8.1 Registration Pattern (each server factory, excluding Reach MCP)

```python
from dpg_auth import build_auth_provider, DpgAuthConfig
from dpg_auth.middleware.verify import VerifyJwtMiddleware
from dpg_auth.middleware.authorize import AuthorizeMiddleware

auth_cfg = DpgAuthConfig.model_validate(config.get("dpg_auth", {}))
provider = build_auth_provider(auth_cfg)  # KeycloakAuthProvider or StaticAuthProvider

# Starlette wraps outermost-last: add AuthorizeMiddleware first.
app.add_middleware(AuthorizeMiddleware, auth_config=auth_cfg)
app.add_middleware(VerifyJwtMiddleware, auth_provider=provider, auth_config=auth_cfg)
```

### 8.2 Bypass Paths

Default `["/health"]`. Extend per block via config:
- **Reach Web:** `bypass_paths` are `/auth/*`, `/chat`, `/health`, `/static/*` and other browser-facing routes. The middleware guards only the service path (dev-kit -> reach-web, e.g. `/upload`). Browser routes are out of scope for Child A and will be handled under Child B (which manages end-user OIDC/OTP tokens).

### 8.3 Scope: MCP Excluded from Child A

> [!IMPORTANT]
> **Reach MCP excluded (reviewer Must-fix #4):** `VerifyJwtMiddleware` is **not** added to `reach_layer/mcp/src/server.py` in child A. Inter-DPG service traffic does not enter via the MCP server; adding middleware there blurs ownership with child D. The existing `_authenticate_request()` static API key remains unchanged until child D.

### 8.4 Modified Server Factories

The following **7** server factories receive the middleware addition (shadow mode = enforce:false by default):

| Block | Factory file |
|---|---|
| Agent Core | `agent_core/src/servers/orchestration_server.py` |
| Knowledge Engine | `knowledge_engine/main.py` |
| Memory Layer | `memory_layer/main.py` |
| Trust Layer | `trust_layer/src/server.py` |
| Action Gateway | `action_gateway/src/server.py` |
| Observability Layer | `observability_layer/src/server.py` |
| Reach Web | `reach_layer/web/web_reach.py` (with extended `bypass_paths`) |

Reach MCP: **excluded** — child D owns this surface.

---

## 9. X-API-Key Retirement

> [!IMPORTANT]
> **Static key retirement required (reviewer Must-fix #4):** Spec §4.5 requires retiring the dev-kit↔reach↔KE static API-key chain in the same operation each block flips to `enforce: true`. No permanent dual-mode is permitted. This is incorporated into Step 13 below.

The two files containing static API-key checks:
- `knowledge_engine/src/auth.py` — `verify_api_key()` function and its route-level `Depends`
- `reach_layer/web/src/auth.py` — `verify_api_key()` for the ingest path (not the Google SSO `Reason` enum — that is preserved)

**Retirement plan per block:**
- When Knowledge Engine is flipped to `enforce: true` (Step 13): remove `verify_api_key()` from `knowledge_engine/src/auth.py` and all `Depends(verify_api_key)` usages on `/ingest` and related endpoints.
- When Reach Web is flipped to `enforce: true` (Step 13): remove `verify_api_key()` from the ingest path in `reach_layer/web/src/auth.py`.
- The caller (Reach Web → KE) instead relies on the JWT Bearer token already injected by `ServiceAuthClient`.

**Note on Google SSO:** The `Reason` enum and Google SSO flow in `reach_layer/web/src/auth.py` are **not touched**. These protect user-facing endpoints, which are out of scope for child A.

---

## 10. Order of Implementation

> [!IMPORTANT]
> **Enforce order restored to spec §6 (reviewer Must-fix #2):** The original plan ordered enforcement as `Obs→Trust→Mem→KE→AG→AC` (least-sensitive first). The approved spec §6 specifies `AG→Memory→Trust→KE→AC→Obs` (most-sensitive first) — closing the actual open hole (AG `/execute`, bypassing Agent Core's consent gate) earliest. The spec rationale: 48h of shadow logging already de-risks configs before any block enforces; sequencing by risk is unnecessary when all blocks run shadow first.

| Step | What | Why this order |
|---|---|---|
| 1 | `dpg_auth` package skeleton + `DpgAuthConfig` (with `provider` field) | Foundation; all else imports from here |
| 2 | `AuthContext` + `ContextVar` + `StructuredLogFilter` | No deps; required by all other modules |
| 3 | `StaticAuthProvider` + ABC + tests | Needed by middleware tests; no Keycloak required |
| 4 | `KeycloakAuthProvider` + `CompositeAuthProvider` + `build_auth_provider()` factory + tests | Core crypto; mock JWKS in tests; factory includes fail-closed guard |
| 5 | `VerifyJwtMiddleware` + `AuthorizeMiddleware` + tests | Integration point; uses StaticAuthProvider in tests |
| 6 | `ServiceAuthClient` + tests | Caller-side; mock token endpoint in tests |
| 7 | Keycloak Docker Compose + realm import JSON + onboarding script | Ring 0 infrastructure; before enforce:true ever set |
| 8 | Config schema updates for all 8 blocks (runtime + dev-kit + YAML), including `provider` field + per-block `client_id` + per-block `allow_callers` from §6.4 table | Must precede code that loads new config key |
| 9 | Caller-side HTTP client wiring: Agent Core (10 files) | Agent Core is the sole caller of all 5 downstreams |
| 10 | Caller-side HTTP client wiring: Reach Layer Web | Reach Web calls KE and Agent Core |
| 11 | Callee-side middleware on 7 block server factories (enforce=false); Reach MCP excluded | Shadow mode; safe to deploy before full provisioning |
| 12 | Integration test: Docker Compose + live Keycloak | End-to-end validation before enforce:true |
| 13 | Set enforce:true per block in spec §6 order + retire X-API-Key on each flip: **AG → Memory → Trust → KE → AC → Obs → Reach Web** | Spec-ordered lockdown; KE and Reach Web static keys retired on their respective flips |

### Dependencies Between Steps

```
1 → 2 → 3 → 4 → 5
1 → 6
1 → 7 (parallel with 2-6)
8 must precede any container restart with new YAML
9 requires 1,6,8
10 requires 9
11 requires 1,5,8
12 requires 7,9,10,11
13 requires 12
```

---

## 11. Risks and Unknowns

> [!WARNING]
> **Risk 1 — Keycloak startup latency.** Keycloak `start-dev` takes 30–60 seconds. All DPG services need `depends_on: keycloak: condition: service_healthy` added to docker-compose. The healthcheck curl endpoint is `/health/ready` on port 8080.

> [!WARNING]
> **Risk 2 — `ServiceAuthClient` called during `_build_app()`.** `ActionGatewayHttpClient.__init__` calls `_fetch_tool_definitions()` synchronously at construction. If `ServiceAuthClient.get_token()` is also called at construction and Keycloak is not yet ready, startup fails. Mitigation: `get_token()` retries with exponential backoff (3 attempts, 1s/2s delays) before raising.

> [!CAUTION]
> **Risk 3 — `extra="forbid"` schema breakage during rolling deploy.** If the YAML is updated before the code is deployed, startup crashes. Rule: always deploy schema-aware code (Step 8) before deploying new YAML (Step 11).

> [!IMPORTANT]
> **Risk 4 — Client secret management.** `client_secret` in YAML must always be empty string `""`. Secrets are only injected via env var at runtime. Add a CI check scanning for non-empty `client_secret` in committed YAML files.

> [!NOTE]
> **Risk 5 — Reach Web bypass path list.** The bypass paths are finalized as `["/auth/*", "/chat", "/health", "/static/*"]`. The middleware guards only the service path (`dev-kit` -> `reach-web`, e.g., `/upload`). User-facing endpoints are out of scope for Child A and will bypass this middleware.

> [!NOTE]
> **Risk 6 — X-API-Key retirement breaks callers not yet migrated.** The static key on `/ingest` is the Reach Web → KE path. Once KE enforces and the key is retired, Reach Web must already be provisioned with a valid service token. Step 13 ordering ensures KE flips after Reach Web's `ServiceAuthClient` is wired (Step 10).

> [!NOTE]
> **Unknown 1 — Keycloak role claim path.** Confirm the claim appears at top-level (`claims["role"]`) vs nested (`claims["realm_access"]["roles"]`). `KeycloakAuthProvider` should try `claims.get("role")` first, fall back to `claims.get("realm_access", {}).get("roles", [])`.

> [!NOTE]
> **Decision — `PyJWT` + `cryptography` choice.** Verified and confirmed. `PyJWT` with the `cryptography` extra is the chosen library for RS256 token verification, as it is already present transitively via `reach_layer/web` and avoids introducing a new dependency.

---

## 12. Testing Strategy

### 12.1 `dpg_auth` Package Tests

| Test file | Coverage |
|---|---|
| `test_config.py` | Valid/invalid `DpgAuthConfig`; `provider` field validation; fail-closed when `keycloak` + empty URL + enforce |
| `test_context.py` | Frozen dataclass, ContextVar async isolation |
| `test_provider_static.py` | Valid token, expired, wrong secret; explicit `provider="static"` activation only |
| `test_provider_keycloak.py` | JWKS fetch (mocked), RS256 verify, exp, kid rotation, cache refresh; fail-closed guard |
| `test_provider_composite.py` | Routes by iss, unknown issuer → AuthError |
| `test_client.py` | Caching, refresh-before-exp, 401 retry, header injection |
| `test_middleware_verify.py` | Pass, 401 block, shadow pass-through, bypass path |
| `test_middleware_authorize.py` | Role match, role mismatch → 403, shadow → pass, populated allow_callers |

**Test deps (dpg_auth only):** `pytest`, `pytest-asyncio`, `pytest-httpx` (mock JWKS/token endpoints), `cryptography` (generate test RSA key pair and JWTs).

### 12.2 Coverage Target

> [!IMPORTANT]
> **≥85% line coverage required (reviewer Must-fix #3):** The `dpg_auth` package is security-critical shared code trusted by every service. The spec §3.2/§7 deliberately set 85% — higher than the repo's general 70% floor (agent_core/KE). Restore 85%.

### 12.3 Per-Block Unit Tests

- `<block>/tests/test_schema_config.py`: Assert `dpg_auth` section accepted with `provider` field; extra keys rejected; `allow_callers` populated per-block.
- `<block>/tests/test_http_clients.py` (callers): Assert `Authorization: Bearer` header present when `auth_client` provided; assert no header when `auth_client=None`.

### 12.4 Integration Tests

Docker Compose stack including Keycloak. Verify:
1. `/health` endpoints: succeed without token (bypass path).
2. Action Gateway `POST /execute` with valid service token: shadow mode logs success.
3. Action Gateway `POST /execute` without token: shadow mode logs warning, still responds 200.
4. After setting `enforce: true` on Action Gateway: same request without token → 401.
5. After retiring X-API-Key on KE and Reach Web: `/ingest` with service token succeeds; with old static key → 401.

---

## 13. Dev-Kit Sync Checklist (per block)

Per `.claude/rules/runtime-devkit-sync.md`:

- [ ] `<block>/src/schema/config.py` — inline `DpgAuthConfig` mirror (with `provider` field) + `dpg_auth` field in `MergedConfig`
- [ ] `dev-kit/dev_kit/schemas/domain/<block>.py` — `DpgAuthSection` (with `provider` field)
- [ ] `dev-kit/dev_kit/schema.py` — flat-file copy update
- [ ] `dev-kit/dpg/<block>.yaml` — `dpg_auth:` defaults block with real `client_id`, real `allow_callers`, `provider: "keycloak"`
- [ ] `dev-kit/dev_kit/agent/field_rules/<block>.py` — add `dpg_auth` field rules
- [ ] `dev-kit/tests/schemas/domain/test_<block>.py` — accept-valid + reject-invalid (including `provider` field, populated `allow_callers`)
- [ ] Rebuild dev-kit Docker image; run wizard end-to-end; confirm `"validator": "runtime_baked"`

---

## 14. Backwards Compatibility

| Mechanism | Guarantee |
|---|---|
| `auth_client=None` default on all HTTP clients | Existing tests pass unchanged; behaviour identical to today |
| `enforce: false` default in all YAML | No request ever blocked during rollout |
| `bypass_paths: ["/health"]` default | Docker healthchecks unaffected |
| `provider: "keycloak"` default | No behaviour change vs. old infer-from-URL when URL is populated |
| `enabled: false` killswitch | Middleware becomes a complete no-op; instant recovery |
| `Reason` enum in `reach_layer/web/src/auth.py` | Not touched; scoped to Google SSO |
| Static API-key in `reach_layer/mcp/src/server.py` | Not touched; child D scope |
| X-API-Key retirement gated on enforce flip | Static keys remain active until the explicit per-block enforce step; no surprise breakage |

---

## 15. Security Considerations

1. **No PII or token values in logs** — only `caller_id`, `service_role`, `latency_ms`.
2. **Constant-time comparison in StaticAuthProvider** — `hmac.compare_digest` (consistent with MCP).
3. **Tokens never in URLs** — always `Authorization: Bearer` header.
4. **5-minute token lifetime** — `accessTokenLifespan: 300` in Keycloak; limits blast radius.
5. **JWKS refresh-ahead** — no thundering herd on key rotation.
6. **`enabled: false` killswitch** — instant recovery if Keycloak is unreachable.
7. **Secrets via env vars only** — `client_secret` in YAML always `""`.
8. **`client_secret` typed `str`** in block schema mirrors (Pydantic only — satisfies Dockerfile bake constraint); typed `SecretStr` in the `dpg_auth` library itself for runtime protection.
9. **Fail-closed guard** — `build_auth_provider()` raises `ConfigurationError` if `provider="keycloak"`, `enforce=True`, and `keycloak_url` is empty. No silent fallback to static mode in production.
10. **Explicit provider selection** — `provider: "keycloak"|"static"` is a config field, not inferred from URL presence. Prevents prod misconfiguration silently disabling JWT verification.
11. **X-API-Key retirement is mandatory** — no permanent dual-mode after enforce; retiring old credentials removes the pre-auth bypass surface area.

---

## 16. Assumptions to Validate Before Coding

1. **Keycloak container port is `8080` within `dpg_net`** — `keycloak_url` default must be `http://keycloak:8080`.
2. **Hardcoded Claim mapper places `role` as a top-level JWT claim** — validate by decoding a token after realm import.
3. **`PyJWT` + `cryptography` extra** is confirmed as the chosen JWT library (already present transitively via `reach_layer/web`).
4. **`pytest-httpx` is acceptable for mocking JWKS/token HTTP calls** in `dpg_auth` tests.
5. **`ServiceAuthClient` retry-on-startup is sufficient** to handle Keycloak readiness — no additional startup probe needed in each block's `main.py`.
6. **Reach Web bypass path list** is finalized as `["/auth/*", "/chat", "/health", "/static/*"]`.
7. **Per-block `allow_callers` table in §6.4** reflects the accurate caller graph — verify against `ARCHITECTURE.md` approved-direct-calls before coding.
8. **PR and Branch Layout** — Implement via a single rolling branch/PR with commits per block. The runtime config schema changes and dev-kit mirror schemas for each block must land together in the same commit to satisfy the `runtime-devkit-sync` discipline.

---

## 17. Proposed File Change Summary

### New Files

| File | Purpose |
|---|---|
| `dpg_auth/pyproject.toml` | Package manifest (mirrors `observability_layer/pyproject.toml` pattern) |
| `dpg_auth/src/dpg_auth/__init__.py` | Public API; `build_auth_provider()` factory (fail-closed guard) |
| `dpg_auth/src/dpg_auth/config.py` | `DpgAuthConfig` (self-contained Pydantic; includes `provider` field) |
| `dpg_auth/src/dpg_auth/context.py` | `AuthContext` + `ContextVar` |
| `dpg_auth/src/dpg_auth/logging.py` | `StructuredLogFilter` |
| `dpg_auth/src/dpg_auth/client.py` | `ServiceAuthClient` |
| `dpg_auth/src/dpg_auth/provider/base.py` | `AuthProviderBase` ABC |
| `dpg_auth/src/dpg_auth/provider/keycloak.py` | `KeycloakAuthProvider` (fail-closed startup guard) |
| `dpg_auth/src/dpg_auth/provider/composite.py` | `CompositeAuthProvider` |
| `dpg_auth/src/dpg_auth/provider/static.py` | `StaticAuthProvider` (explicit opt-in only) |
| `dpg_auth/src/dpg_auth/middleware/verify.py` | `VerifyJwtMiddleware` |
| `dpg_auth/src/dpg_auth/middleware/authorize.py` | `AuthorizeMiddleware` |
| `dpg_auth/tests/` (8 test files) | Full test coverage (>=85%) |
| `automation/docker/keycloak/realms/ai-diffusion-platform.json` | Baked realm import (placeholder secrets) |
| `automation/keycloak/onboard_platform_realm.sh` | Production onboarding script |

### Modified Files

| File | Change |
|---|---|
| `automation/docker/docker-compose.dev.yml` | Add `keycloak` service + `keycloak_data` volume + `depends_on` on all 8 services |
| `agent_core/main.py` | Construct `ServiceAuthClient`; pass to all HTTP clients; env var for `client_secret` only |
| `agent_core/src/http_clients/*.py` (5 files) | Add `auth_client` param; use token-injecting client |
| `agent_core/src/http_clients/async_/*.py` (5 files) | Same, async |
| `agent_core/src/servers/orchestration_server.py` | Add middlewares |
| `agent_core/src/schema/config.py` | Inline `DpgAuthConfig` mirror + `dpg_auth` field |
| `agent_core/pyproject.toml` | Add `dpg-auth` dependency |
| `trust_layer/src/server.py` | Add middlewares |
| `trust_layer/src/schema/config.py` | Inline `DpgAuthConfig` mirror + `dpg_auth` field |
| `trust_layer/pyproject.toml` | Add `dpg-auth` dep |
| `knowledge_engine/main.py` | Add middlewares |
| `knowledge_engine/src/schema/config.py` | Add `dpg_auth` section |
| `knowledge_engine/src/auth.py` | Retire `verify_api_key()` on enforce flip (Step 13) |
| `knowledge_engine/pyproject.toml` | Add `dpg-auth` dep |
| `memory_layer/main.py` | Add middlewares |
| `memory_layer/src/schema/config.py` | Add `dpg_auth` section |
| `memory_layer/pyproject.toml` | Add `dpg-auth` dep |
| `action_gateway/src/server.py` | Add middlewares |
| `action_gateway/src/schema/config.py` | Add `dpg_auth` section |
| `action_gateway/pyproject.toml` | Add `dpg-auth` dep |
| `observability_layer/src/server.py` | Add middlewares |
| `observability_layer/src/schema/config.py` | Add `dpg_auth` section |
| `observability_layer/pyproject.toml` | Add `dpg-auth` dep |
| `reach_layer/web/web_reach.py` | Add middlewares (extended bypass_paths) + `ServiceAuthClient` for caller side |
| `reach_layer/web/src/auth.py` | Retire `verify_api_key()` ingest path on enforce flip; Google SSO preserved |
| `reach_layer/web/pyproject.toml` | Add `dpg-auth` dep |
| `dev-kit/dev_kit/schemas/domain/*.py` (8 files) | Add `DpgAuthSection` (with `provider` field) |
| `dev-kit/dev_kit/schema.py` | Add `DpgAuthSection` to flat-file copy |
| `dev-kit/dpg/*.yaml` (8 files) | Add `dpg_auth:` defaults block with real `client_id`, `allow_callers`, `provider` |
| `dev-kit/dev_kit/agent/field_rules/*.py` (8 files) | Add `dpg_auth` field rules |
| `dev-kit/tests/schemas/domain/test_*.py` (8 files) | Add auth config tests |

**Not modified (child A scope exclusion):**
- `reach_layer/mcp/src/server.py` — child D owns MCP auth
- `reach_layer/web/src/auth.py` Google SSO `Reason` enum — preserved

---

## 18. Change Log (PR #362 Review Revisions)

All changes from the original plan are motivated by reviewer **AniketSaki**'s CHANGES_REQUESTED review on PR #362 (6 inline comments + review summary).

| # | Reviewer comment | Priority | Change made to plan |
|---|---|---|---|
| 1 | Must-fix #1: `allow_callers: []` allow-all default defeats child A | Must-fix | §4.2, §6.4: Framework YAMLs ship real per-block allowlists from spec §4.3 table. Q3 resolved: spec table ships as framework defaults, not domain-YAML opt-in. |
| 2 | Must-fix #2: Enforce order `Obs→Trust→Mem→KE→AG→AC` reversed from spec §6 | Must-fix | §10: Enforce order changed to `AG→Memory→Trust→KE→AC→Obs` (spec §6) with rationale documented. |
| 3 | Must-fix #3: Coverage dropped 85%→70% for security-critical code | Must-fix | §12.2: Coverage target restored to >=85%. |
| 4 | Must-fix #4: X-API-Key retirement missing; MCP is child D scope | Must-fix | §9 added (X-API-Key retirement, gated on enforce flip). §8.3 added (MCP explicitly excluded). Step 13 includes static key retirement on enforce. §3.4 and §17 updated. |
| 5 | Should-fix #5: `client_id` stays empty — env only overrides `client_secret` | Should-fix | §4.2, §6.4, §6.5, §7.3: `client_id` set per-block in framework YAML. Only `client_secret` goes through env injection. |
| 6 | Should-fix #6: Inferring `StaticAuthProvider` from empty `keycloak_url` is fail-open | Should-fix | §4.2: Added `provider: str = "keycloak"` to `DpgAuthConfig`. §4.7: `StaticAuthProvider` activated only when `provider="static"` is explicitly set. §4.11: `build_auth_provider()` includes fail-closed guard. §15 security items 9-10 added. Q5 resolved. |
| 7 | Should-fix #7: (Implicit in Must-fix #4) MCP/X-API-Key dual-mode | Should-fix | Addressed by items 4 and 6 above. |

---

## 19. Resolved Questions

All open questions have been fully resolved by the PR #362 review discussion (reviewer: **AniketSaki**, CHANGES_REQUESTED review on 2026-07-02):

### Q1: `PyJWT` vs `python-jose`?
- **Decision:** Use `PyJWT` with the `cryptography` extra.
- **Location in Plan:** §4.5, §11 (Decision), §12.1, and §16 (Item 3).
- **PR Discussion Source:** Reviewer summary answer: *"- Q1 (PyJWT vs python-jose): PyJWT + cryptography, agreed — already present transitively via reach-web; don't add a second JWT lib."*

### Q2: Schema changes — 1 PR or 8?
- **Decision:** Implement as a single rolling branch and PR for Child A. Each commit represents a block task, wherein the block's runtime config schema changes and its `dev-kit` mirror schemas are updated together.
- **Location in Plan:** §10 and §16 (Item 8).
- **PR Discussion Source:** Reviewer summary answer: *"- Q2 (1 PR vs 8): Neither. Per our workflow (one branch per plan, tasks as commits, single rolling PR), child A is one branch/PR with per-block commits; runtime + dev-kit mirror land together within each block's commit (that's what runtime-devkit-sync requires)."*

### Q3: `allow_callers` framework defaults (domain YAML vs framework YAML)
- **Decision:** Framework-default the real spec §4.3 allowlists in framework YAML, not as opt-in/allow-all.
- **Location in Plan:** §4.2 and §6.4.
- **PR Discussion Source:** Inline comment #1 and reviewer summary: *"- Q3 (allow_callers): see must-fix #1 — framework-default the real §4.3 allowlists, not allow-all opt-in."*

### Q4: `dpg_auth` directory structure
- **Decision:** Confirmed. Top-level `dpg_auth/` path dependency (analogous to `dpg_telemetry`) is the correct structure.
- **Location in Plan:** §3.4, §4.1, and §17.
- **PR Discussion Source:** Reviewer summary: *"- dpg_auth as a top-level path-dep like dpg_telemetry"*

### Q5: Static provider fallback behavior
- **Decision:** Explicit `provider` config parameter (`provider: "keycloak" | "static"`) instead of implicit empty-string inference.
- **Location in Plan:** §4.2, §4.7, §4.11, §12.1, and §15 (Items 9, 10).
- **PR Discussion Source:** Inline comment #3 and reviewer summary.

### Q6: Reach Web bypass path final list
- **Decision:** The bypass paths are finalized as `["/auth/*", "/chat", "/health", "/static/*"]`. The middleware guards only the service path (dev-kit -> reach-web, e.g., `/upload`).
- **Location in Plan:** §8.2, §11 (Risk 5), and §16 (Item 6).
- **PR Discussion Source:** Reviewer summary answer: *"- Q5 (bypass list): agreed it must be pinned before middleware lands on Reach Web. Frame it as: middleware guards the dev-kit→reach service path; bypass every browser-facing route (/chat, /auth/*, /health, static) — and note those browser routes are child B's actual auth surface."*

### Q7: Per-block `allow_callers` table accuracy
- **Decision:** The spec §4.3 table is correct and was fully adopted in the plan's framework defaults.
- **Location in Plan:** §6.4.
- **PR Discussion Source:** Inline comment #1.
