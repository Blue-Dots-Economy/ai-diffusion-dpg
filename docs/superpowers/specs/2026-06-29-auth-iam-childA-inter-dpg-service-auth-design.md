# Child A — Ring 0 + inter-DPG service auth

**Status:** design approved 2026-06-29; ready for `writing-plans`.
**Owner:** Aniket Sakinala
**Parent:** [`2026-06-29-auth-iam-v2-umbrella-design.md`](2026-06-29-auth-iam-v2-umbrella-design.md) — this child must not contradict the umbrella's §2 locked decisions.
**Tracks:** #105 (inter-service auth — supersedes its "shared-token minimum-viable" strategy line), #342 (umbrella).
**Scope:** Ring 0 (Keycloak foundation + `dpg_auth` shared library) **and** Child A (inter-DPG service-to-service authentication). End-user, MCP, and dev-kit tenancy are children B/D/C — out of scope here.

---

## 1. Problem

Every DPG HTTP endpoint accepts requests from any caller with no authentication. On a shared Kubernetes pod network any pod can call `POST /execute` on Action Gateway directly (bypassing Agent Core's consent gate), read session state from Memory Layer, or forge Trust Layer results. Today's only auth is a static `X-API-Key` on the dev-kit↔reach↔KE ingest chain and Google SSO in reach-web — nothing protects service-to-service traffic.

This child closes that gap with **Keycloak-issued service-account tokens**, per the umbrella's all-in-Keycloak decision (umbrella §2.1/§2.3). It also lays the **Ring 0 foundation** (Keycloak + the `dpg_auth` shared library) that children B/C/D build on.

---

## 2. Current state (verified)

- **Agent Core is the only service making sync inter-DPG calls**, via ad-hoc `httpx` clients in `agent_core/src/http_clients/` (sync) and `agent_core/src/http_clients/async_/` (async): AC → Trust (`/check/input`, `/check/output`, `/assemble_constraints`, `/check/consent`, `/consent/verify`, `/escalate`), KE (`/retrieve`), Action Gateway (`/tools`, `/execute`), Memory (`/context_bundle`, `/write`, `/flush_session`), Observability. Plus **Reach-web → KE** (`/upload`) and **Reach-web → Agent Core** (`/process_turn`).
- **No shared HTTP-client base and no shared auth middleware.** Each FastAPI app is built by a `create_app()` factory in `<module>/src/server.py` (or `main.py`); the only middleware today is `FastAPIInstrumentor.instrument_app(app)` (OTel).
- **Existing auth:** static `X-API-Key` (`verify_api_key()` in `reach_layer/web/src/auth.py` and `knowledge_engine/src/auth.py`) on the ingest chain only; Google SSO + HS256 session JWT in reach-web (`Reason` enum reused below). **No JWT/OIDC/Keycloak/JWKS anywhere.**
- **Config:** each module deep-merges `config/dpg.yaml` + domain YAML and validates a strict `MergedConfig` (`<module>/src/schema/config.py`, `extra="forbid"`). dev-kit mirrors live at `dev-kit/dev_kit/schemas/domain/<module>.py` + `field_rules/<module>.py` (the `runtime-devkit-sync.md` discipline).
- **Shared-package precedent:** `dpg_telemetry` lives in `observability_layer/src/dpg_telemetry/` and is consumed via a **uv path dependency** (`observability-layer = { path = "../observability_layer" }`) in every module.
- **Docker:** `automation/docker/docker-compose.dev.yml` defines all 8 modules + redis/memgraph/jaeger/grafana. **No Keycloak.**

---

## 3. Ring 0 — Keycloak foundation

### 3.1 The `ai-diffusion-platform` realm (services only)

Service-account clients live in a **dedicated platform realm**, decoupled from the end-user realm. Service identity therefore has a **stable issuer in both deployment modes** — standalone and companion — so this child ships with zero dependency on children B/C's realm-target config. (In companion mode there are two realms: the Signals realm for end-users, the platform realm for services; the verifier routes by `iss`.)

- Add a **Keycloak** service to `automation/docker/docker-compose.dev.yml` (port `8180`, health check, persistent volume).
- Baked realm import `automation/docker/keycloak/realms/ai-diffusion-platform.json`:
  - Realm `ai-diffusion-platform`.
  - One confidential `client_credentials` service-account client per block: `svc-agent-core`, `svc-knowledge-engine`, `svc-memory-layer`, `svc-trust-layer`, `svc-action-gateway`, `svc-reach-layer`, `svc-observability-layer`, `svc-dev-kit`.
  - A client scope / protocol-mapper stamping `role: service:<name>` into the access token (e.g. `svc-agent-core` → `service:agent_core`).
- **Production:** the platform realm is provisioned on the **network-common Keycloak** via a one-time onboarding script (committed under `automation/`). Issuer: `<KC_URL>/realms/ai-diffusion-platform`.

### 3.2 The `dpg_auth` shared library

New **top-level package** `dpg_auth/` (its own `pyproject.toml`), added as `dpg-auth = { path = "../dpg_auth" }` to all 8 modules — same mechanism as `dpg_telemetry`, but a cleaner home (auth is not observability).

```
dpg_auth/
├── pyproject.toml
├── provider/
│   ├── base.py          AuthProviderBase (ABC): verify(token) -> AuthContext
│   ├── keycloak.py      KeycloakAuthProvider — offline JWKS verify (cache + refresh-ahead)
│   ├── composite.py     CompositeAuthProvider — route by `iss`
│   └── static.py        StaticAuthProvider — tests/CI (hand-signed tokens, mocked JWKS)
├── middleware/
│   ├── verify.py        VerifyJwtMiddleware (FastAPI; shadow|enforce; bypass paths)
│   └── authorize.py     AuthorizeMiddleware (per-callee allow_callers check)
├── client.py            ServiceAuthClient — own client_credentials token (cache+refresh)
│                        + token-injecting httpx wrapper (sync + async)
├── context.py           AuthContext (frozen dataclass) + contextvars
├── logging.py           StructuredLogFilter (install_filter())
└── config.py            AuthConfig (pydantic)
```

`AuthContext` (frozen): `subject`, `tenant_id` (realm), `role` (`service:<name>` in this child), `issuer`, `token_id` (`jti`), `expires_at`, `raw_claims`. Verification is **offline JWKS** (fetch + cache public keys per realm, refresh-ahead at 80% TTL, on-disk fallback, single rate-limited refresh on unknown `kid`); no per-request network hop to Keycloak. Reuse/extend the existing `Reason` enum (`MISSING`/`INVALID`/`EXPIRED`/`AUDIENCE`/`ISSUER`) from `reach_layer/web/src/auth.py`.

`ServiceAuthClient` obtains *this* DPG's own token once via `client_credentials` (`client_id`/`client_secret` from env, never YAML), caches it, refreshes ahead of `exp`, and exposes sync + async httpx wrappers that inject `Authorization: Bearer <service_token>` + `traceparent`/`baggage` on every outbound call.

**Coverage target:** `dpg_auth` ≥ 85% line coverage.

---

## 4. Child A — wiring service auth into the 8 modules

### 4.1 Config shape (added to every module)

Added to `<module>/src/schema/config.py` `MergedConfig` **and** mirrored in `dev-kit/dev_kit/schemas/domain/<module>.py`, `field_rules/<module>.py`, and `dev-kit/dpg/<module>.yaml` per `runtime-devkit-sync.md` (dual-schema discipline — omission crashes the service at boot with `extra_forbidden`).

```yaml
auth:
  enabled: true
  enforcement: shadow                       # shadow → enforce per ring (§6)
  issuer: ${KC_URL}/realms/ai-diffusion-platform
  jwks_cache_ttl_s: 600
  service_account:                          # this DPG's own identity (for outbound calls)
    client_id: ${SERVICE_CLIENT_ID}
    client_secret: ${SERVICE_CLIENT_SECRET}
  allow_callers:                            # per-callee inbound allowlist (§4.3)
    "/execute": ["service:agent_core"]
    "/tools":   ["service:agent_core"]
  bypass_paths: ["/healthz", "/metrics"]
```

### 4.2 Inbound — verify + authorize middleware

Each `create_app()` factory mounts, after OTel instrumentation:
1. `VerifyJwtMiddleware` — reads `Authorization: Bearer`, resolves provider by `iss` (CompositeAuthProvider), verifies, populates `AuthContext` contextvar + OTel baggage, logs `auth_verify {status, latency_ms, subject, role}`. `shadow`: on failure logs `would_have_blocked=true`, passes through. `enforce`: returns the §5 failure body. Bypass paths skip.
2. `AuthorizeMiddleware` — runs after verify; matches the request path to an `allow_callers` endpoint-group; if `AuthContext.role` ∉ allowed list → 403 `{reason:"forbidden", role, endpoint}`. Endpoints with no `allow_callers` entry require only a valid platform token (authn).

### 4.3 Per-callee allowlists (the #105 threat model)

Initial allowlists (refined during writing-plans against the verified call graph in §2):

| Callee | Endpoint group | Allowed callers |
|---|---|---|
| Action Gateway | `/execute`, `/tools` | `service:agent_core` |
| Memory Layer | `/write`, `/flush_session`, `/context_bundle` | `service:agent_core` |
| Trust Layer | `/check/*`, `/assemble_constraints`, `/consent/*`, `/escalate` | `service:agent_core` |
| Knowledge Engine | `/retrieve` | `service:agent_core` |
| Knowledge Engine | `/ingest`, `/upload` | `service:reach_layer`, `service:dev_kit` |
| Agent Core | `/process_turn`, `/stream_turn` | `service:reach_layer` |
| Observability | `/emit` (and any ingest) | all platform services (authn-only or broad allowlist) |

### 4.4 Outbound — token-injecting client

- Replace ad-hoc `httpx` construction in `agent_core/src/http_clients/*` (sync) and `agent_core/src/http_clients/async_/*` (async) with the `dpg_auth` token-injecting wrapper (preserving each client's existing timeout/endpoint config keys — `trust_client`, `ke_client`, `action_gateway_client`, etc.).
- Same for Reach-web → KE (`/upload`) and Reach-web → Agent Core (`/process_turn`).
- Each caller authenticates **as its own service identity**; no user token exists yet.

### 4.5 Retire the legacy `X-API-Key` chain

The dev-kit↔reach↔KE `X-API-Key` paths (`verify_api_key()`, `DEVKIT_TO_REACH_API_KEY`, `REACH_TO_KE_API_KEY`, `KE_TO_DEVKIT_API_KEY`) are replaced by platform service tokens. The X-API-Key code for a given DPG is **removed in the same PR that flips it to `enforce`** — no permanent dual-mode.

---

## 5. Failure-response contract

| Condition | Status | Body |
|---|---|---|
| Missing `Authorization` | 401 | `{reason:"missing"}` |
| Invalid signature / JWKS mismatch | 401 | `{reason:"invalid"}` |
| Expired token | 401 | `{reason:"expired"}` |
| Wrong issuer | 401 | `{reason:"issuer"}` |
| Caller role not allowed for endpoint | 403 | `{reason:"forbidden", role, endpoint}` |
| Keycloak unreachable, JWKS cached | continues from cache; warn-log only | |
| Keycloak unreachable, no cache | 503 | `{reason:"auth_provider_down"}` (protected paths; `/healthz` stays green) |

Reason codes are stable identifiers.

---

## 6. Rollout (each step independently revertable via the `auth.enforcement` flag)

- **Ring 0:** `dpg_auth` published intra-repo (≥85% cov); Keycloak in docker-compose healthy with the platform-realm import; CI smoke wired. **No DPG behaviour change.**
- **Child A — shadow:** every module mounts `VerifyJwtMiddleware`(shadow) + `AuthorizeMiddleware`; callers switch to the token-injecting client; `install_filter()` everywhere. Emit `auth_verify` + `would_have_blocked` counts. **Hold for 48 h** of clean shadow data before flipping anything.
- **Child A — enforce:** flip `enforcement: enforce` **per callee**, in order of sensitivity: Action Gateway → Memory → Trust → Knowledge Engine → Agent Core → Observability. After each flip verify 30 min of zero auth errors. Remove that DPG's `X-API-Key` path in the same PR (KE, reach).

---

## 7. Testing

- **Layer 1 — `dpg_auth` unit tests** (Keycloak mocked with `respx`, tokens hand-signed against a known keypair fed to the mocked JWKS): `KeycloakAuthProvider.verify` (valid / expired / bad-sig / wrong-iss / clock-skew), `CompositeAuthProvider` (routes by `iss`; unknown `iss` rejected), `VerifyJwtMiddleware` (200 + context / 401 bodies / bypass paths / shadow logs `would_have_blocked`), `AuthorizeMiddleware` (allowed role → 200 / disallowed → 403 / no-rule → authn-only), `ServiceAuthClient` (fetches+caches token, refresh-ahead, injects header; downstream 401 surfaced), `StructuredLogFilter`.
- **Layer 2 — per-module `tests/test_auth_integration.py`** with `StaticAuthProvider`: each protected endpoint with no token (401), wrong-caller token (403), right token (200), expired (401). Assert status, body, and structured-log fields via `caplog`.
- **Layer 3 — e2e docker smoke** (`scripts/smoke_auth.py`, CI on any `dpg_auth/` or auth-config change): Keycloak up; `svc-agent-core` gets a token; AC→AG `/execute` 200; direct `/execute` with no token 401; direct `/execute` with `svc-observability` token 403.

---

## 8. Out of scope (other children / later)

- End-user tokens + forwarding header, voice phone-minted JWT, JIT-provisioning → **child B**.
- MCP `private_key_jwt` clients → **child D**.
- Operator login, dev-kit multi-tenant realm provisioning, standalone KC deployment topology, #273 perimeter → **child C**.
- Token-exchange / OBO (Approach B), inter-service rate limiting, mTLS/SPIFFE, MCP fine-grained scopes → later specs.

---

## 9. Open questions for writing-plans

1. Final endpoint-group → `allow_callers` map (refine §4.3 against the exact route table).
2. Whether `dpg_auth.client` exposes one class with sync+async methods or two parallel classes mirroring the existing `http_clients/` vs `http_clients/async_/` split.
3. Exact Keycloak image/version + realm-export tooling for keeping `ai-diffusion-platform.json` in sync.
4. Enforce-flip ordering vs the 48 h shadow gate — per-callee vs all-at-once after one shadow window.
