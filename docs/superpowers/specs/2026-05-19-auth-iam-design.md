# Auth & IAM across the DPG framework

**Status:** design approved 2026-05-19, implementation deferred
**Owner:** Aniket Sakinala
**Scope:** all 7 DPG building blocks + Dev-Kit

> Single source of architectural truth remains [`ARCHITECTURE.md`](../../../ARCHITECTURE.md). This spec defines a new cross-cutting concern (authentication, authorization, identity propagation, rate limiting, log/eval context) that touches every block.

---

## 1. Goals & non-goals

### Goals (v1)

1. Every external request is authenticated against a trusted OIDC issuer before any DPG processes it.
2. Every internal inter-service call carries a verifiable workload identity and propagates the acting user and tenant.
3. Authentication is pluggable via an `AuthProviderBase` abstraction. Standalone deployments use a bundled Keycloak; integrated deployments point at a shared instance or swap providers.
4. Every log line and every Observability event carries `tenant_id`, `user_id`, `request_id`, `caller`.
5. Rate limiting per `tenant + subject` at the two external ingresses (Reach Layer, Dev-Kit).
6. Multi-tenant via realm-per-tenant. Tenant isolation is verifiable from the JWT `iss` claim alone.
7. Rollout is incremental and revertable via an `enforcement: shadow` mode.

### Non-goals (v1)

- Token exchange / on-behalf-of flow (Approach B below). The abstraction supports it; it is not implemented in v1.
- mTLS / SPIFFE service identity (Approach C).
- Per-endpoint fine-grained OAuth scopes for non-MCP/A2A traffic.
- Inter-service rate limiting between trusted callees.
- Cross-deployment A2A federation. The A2A protocol surface is included; cross-realm trust is a later spec.
- An operator admin UI for managing users or tenants. Use Keycloak's admin console.

---

## 2. Use cases (the 8 actors)

| # | Actor | Auth concern |
|---|---|---|
| 1 | End user — Reach Web (browser SPA) | OIDC code+PKCE login (Keycloak federates Google / BetterAuth upstream); session JWT |
| 2 | End user — Reach Voice (Vobiz / telephony) | No interactive login. Channel adapter mints short-lived JWT keyed by verified E.164 phone |
| 3 | End user — Reach WhatsApp | Same model as voice: HMAC-verified webhook → minted phone JWT |
| 4 | Operators — Dev-Kit Configuration Agent | OIDC login; `operator` role; per-tenant authz; audit log of config changes |
| 5 | Inter-service DPG ↔ DPG | Verifiable workload identity (Keycloak service-account client_credentials) + propagated acting-user JWT |
| 6 | MCP — agent exposing tools to other agents | `mcp_client:<id>` role via client_credentials; coarse allowlist now, fine scopes in v2 |
| 7 | Future A2A — agent ↔ agent peer | `a2a_peer:<id>` role; identical wire model to MCP; cross-deployment trust deferred |
| 8 | Multi-tenancy (cross-cutting) | `tenant_id` on every identity, log line, rate-limit bucket, and Memory/KE partition |

MCP and A2A traffic enters through Reach Layer, not Agent Core. Reach is the only ingress for agent traffic (end users, MCP, A2A); Dev-Kit is a separate ingress for operators. Two external ingresses overall.

---

## 3. Approach choice

Three coherent end-to-end approaches were considered:

- **Approach A — JWT-everywhere, user-token forwarded.** Reach receives the user JWT and forwards it on every downstream call. Every DPG verifies the same JWT. Caller-service identity is implicit.
- **Approach B — Token exchange / on-behalf-of.** Each DPG holds its own service identity; user identity rides as an `act` claim that callees verify alongside the caller's service token.
- **Approach C — JWT for users + mTLS/SPIFFE for services.** Service identity comes from TLS workload certs; user identity from JWT.

**Decision: Approach A for v1, with the abstraction shaped so Approach B is a drop-in upgrade later.** Only `AuthForwardingAsyncClient` (Section 5) changes between A and B; every other component is untouched.

Reasoning: A solves all 8 use cases today, fits the existing docker-compose deployment, supports both Keycloak and BetterAuth via a CompositeAuthProvider, and the contract changes are minimal. B's extra rigor pays off only once we have untrusted internal callers or cross-deployment A2A federation, neither of which exists yet.

---

## 4. Identity provider

### 4.1 Bundled default: Keycloak

Keycloak is bundled as the standalone-deployment default IdP. Integrated deployments either point at a shared Keycloak instance (set `KEYCLOAK_URL`) or substitute another provider via the pluggable abstraction.

Reasoning:
- Adjacent deployments already run Keycloak; lowest friction for the combined-deployment story.
- Realms map cleanly to tenants (see 4.2).
- Keycloak supports OAuth 2.0 token exchange natively, required for the Approach B upgrade path.
- Federation: a single Keycloak realm can federate Google, BetterAuth, SAML, LDAP, etc., as upstream identity sources. DPGs verify only the Keycloak-issued JWT regardless of upstream login method — collapsing the multi-issuer problem at the IdP boundary, not at every DPG.

### 4.2 Multi-tenancy: realm per tenant

Each tenant (e.g., `kkb`, `healthcare`, `telco`) gets its own Keycloak realm. Hard isolation: separate users, separate clients, separate federation. The DPG verifier resolves which realm to use from the JWT `iss` claim (`https://kc.example.com/realms/<tenant>`). The `kid` lookup is scoped to that realm only; cross-realm `kid` collisions are not possible.

Provisioning a new tenant requires creating a realm (one-time onboarding script). Day-to-day operation does not.

### 4.3 Caller identity for voice / WhatsApp

Anonymous voice and WhatsApp callers cannot log in interactively. After the channel adapter verifies the inbound webhook (Vobiz signature, Meta HMAC), the adapter calls a small `mint_caller_token(tenant_id, phone, channel)` endpoint on the Auth service. It is implemented either as a custom Keycloak extension or, equivalently, as a small FastAPI sidecar that uses Keycloak's Admin API + token exchange. The endpoint returns a short-lived (15 min) JWT:

```
sub:    "phone:+919876543210"
role:   "end_user"
tenant: "kkb"
channel: "voice"
exp:    <now + 15min>
iss:    https://kc.example.com/realms/kkb
```

The endpoint is itself protected: only Reach channel service-accounts may call it. Centralised signing key, easy revocation, no per-channel secrets.

### 4.4 Operator login (Dev-Kit)

Operators log into Dev-Kit via an OIDC Authorization Code + PKCE flow against Keycloak (whatever upstream provider Keycloak federates is transparent to Dev-Kit). The JWT carries `role: operator` plus a `tenants` claim restricting which tenant configs the operator may edit. Audit logging of config changes is in Dev-Kit's own scope, not this spec.

---

## 5. Components — the `dpg_auth` shared library

All seven DPGs and Dev-Kit `uv add` one shared package. Single source of truth for the auth contract.

```
dpg_auth/
├── provider/
│   ├── base.py              AuthProviderBase (ABC)
│   ├── keycloak.py          KeycloakAuthProvider
│   ├── betterauth.py        BetterAuthProvider  (stub-grade in v1)
│   ├── static.py            StaticAuthProvider   (tests + CI)
│   └── composite.py         CompositeAuthProvider (route by `iss` claim)
├── middleware/
│   ├── verify.py            VerifyJwtMiddleware (FastAPI)
│   ├── ratelimit.py         RateLimitMiddleware (FastAPI; Redis-backed)
│   └── enforce.py           EnforcementMode    ("shadow" | "enforce")
├── context.py               ContextVars + AuthContext dataclass
├── logging.py               StructuredLogFilter (reads contextvars)
├── http_client.py           AuthForwardingAsyncClient (httpx)
└── config.py                AuthConfig (pydantic; loaded once at startup)
```

Library location TBD by the implementation plan — either as a top-level package or under `dev-kit/dpg/`. The choice does not affect the design.

### 5.1 `AuthProviderBase`

```python
class AuthProviderBase(ABC):
    @abstractmethod
    async def verify(self, token: str) -> AuthContext:
        """Verify a bearer JWT and return a normalised AuthContext.

        Raises AuthError(reason=Reason) on any verification failure.
        Implementations are responsible for JWKS caching, clock skew
        tolerance, issuer/audience checks, and signature validation.
        """

    @abstractmethod
    async def mint_caller_token(
        self, tenant_id: str, phone: str, channel: str
    ) -> str:
        """Mint a short-lived caller JWT for an anonymous voice/WA caller."""

    @abstractmethod
    async def issuer_metadata(self, tenant_id: str) -> IssuerMetadata:
        """Return OIDC discovery metadata for a tenant. Cached."""
```

```python
@dataclass(frozen=True)
class AuthContext:
    subject: str           # e.g. "google:1234..." | "phone:+91..." | "client:agent_core"
    tenant_id: str         # canonical: from realm name
    role: str              # end_user | operator | service:<name> | mcp_client:<id> | a2a_peer:<id>
    issuer: str            # raw `iss` claim
    token_id: str          # `jti` — for revocation/dedup
    expires_at: int        # `exp`
    raw_claims: Mapping[str, Any]
```

The `Reason` enum reuses the existing one in `reach_layer/web/src/auth.py` (`MISSING`, `INVALID`, `EXPIRED`, `AUDIENCE`, `ISSUER`, …) extended as needed.

### 5.2 `VerifyJwtMiddleware`

Reads `Authorization: Bearer <jwt>`, resolves which `AuthProviderBase` to call (CompositeAuthProvider routes by `iss`), calls `verify`, populates the `AuthContext` contextvar and OTel baggage. Logs `auth_verify` event with `status`, `latency_ms`, `subject`, `tenant_id`, `role`.

- **`enforce` mode:** on failure, returns 401 with `{reason, message}`.
- **`shadow` mode:** on failure, logs with `would_have_blocked=true` and passes through. Used during Ring 1 rollout.
- **Bypass paths:** configurable per DPG (e.g. `/healthz`, `/metrics`, `/docs`).

### 5.3 `RateLimitMiddleware`

Runs after `VerifyJwtMiddleware`, so it sees `AuthContext`. Bucket key: `tenant_id:subject:endpoint_group`. Storage: Memory Layer Redis, sliding-window via Lua script (atomic). Rules in YAML:

```yaml
auth:
  rate_limit:
    end_user:    { "/turn":      "60/min" }
    mcp_client:  { "/mcp/*":     "600/min" }
    a2a_peer:    { "/a2a/*":     "120/min" }
    operator:    { "/configs/*": "120/min" }
```

On breach: 429 + `Retry-After`. On Redis-down: fails open, logs `status=skipped reason=redis_down`. Enabled only on Reach Layer and Dev-Kit; disabled on inner DPGs in v1.

### 5.4 `AuthForwardingAsyncClient`

Drop-in `httpx.AsyncClient` subclass used by every outbound inter-service call (Agent Core → KE/Memory/Trust/AG/Obs, Reach → Memory/KE). On every request:

- Reads the current `AuthContext` contextvar.
- Injects `Authorization: Bearer <same JWT>`.
- Injects `traceparent` + `baggage` headers carrying `tenant_id`, `user_id`, `request_id`, `caller`.

This is the v1 implementation of Approach A. To move to Approach B later, swap only this class to fetch a service-token and attach an `act` claim. No caller-site changes.

### 5.5 Logging context

Auth middleware sets `AuthContext` once per request, stored in Python `contextvars` AND OTel baggage. A `StructuredLogFilter` (installed via `dpg_auth.logging.install_filter()`) reads contextvars for every log record. The OTel exporter (already wired across the stack) carries baggage across service boundaries. Observability Layer turn events read the same contextvars.

Required fields on every request-scoped log line: `operation`, `status`, `latency_ms` (for external calls), plus `tenant_id`, `user_id`, `request_id`, `caller`.

### 5.6 Configuration shape

Each DPG's layered YAML (`dev-kit/dpg/<module>.yaml` + `dev-kit/configs/<domain>/<module>.yaml`):

```yaml
auth:
  enabled: true
  enforcement: enforce           # or "shadow" during Ring 1
  provider:
    type: keycloak               # keycloak | betterauth | static | composite
    base_url: ${KEYCLOAK_URL}
    realms: ["kkb", "demo"]
    service_account:
      client_id: ${SERVICE_CLIENT_ID}
      client_secret: ${SERVICE_CLIENT_SECRET}
    jwks_cache_ttl_s: 600
  bypass_paths: ["/healthz", "/metrics"]
  rate_limit:
    enabled: true                # false for non-ingress DPGs
    storage_url: ${REDIS_URL}
    rules: { ... }
```

`composite` provider takes a list of providers and picks one per request by `iss` claim — that is how BetterAuth tokens get accepted alongside Keycloak tokens during transitional periods.

**Dual schema update required.** Per the known `dual_pydantic_schemas` concern, every new auth key must be added to BOTH `dev-kit/.../schemas/dpg/<module>.py` AND each module's local `base.schema.config.MergedConfig`, otherwise services crash at startup with `extra_forbidden`.

---

## 6. Data flow — three canonical paths

### 6.1 End-user web turn

```
1. Browser  → Reach /turn
              Authorization: Bearer <keycloak_user_jwt>

2. Reach middleware:
   • VerifyJwt: keycloak.verify() → AuthContext{sub="google:42", tenant="kkb", role="end_user"}
   • RateLimit: bucket "kkb:google:42:/turn"  → OK
   • Set contextvars + OTel baggage
   • Log "request_in" {tenant=kkb user=google:42 endpoint=/turn request_id=...}

3. Reach → Agent Core /process_turn
   AuthForwardingAsyncClient injects the same Authorization header
   + traceparent + baggage(tenant=kkb,user=google:42,caller=reach_layer)

4. Agent Core middleware:
   • VerifyJwt → same AuthContext (re-verified locally; JWKS cached)
   • No rate-limit (internal DPG)
   • contextvars now populated

5. Agent Core → Trust /check/input, KE /retrieve, Memory /context_bundle, AG /execute, Obs /emit
   AuthForwardingAsyncClient injects the header at every hop. Each callee verifies.
   Every log line at every hop carries tenant + user + request_id.

6. Response back through Agent Core → Reach → Browser.
7. [async] Observability event includes the same tenant + user + request_id.
```

When we move to Approach B later, only step 3 / 5's outbound call shape changes — every other component is untouched.

### 6.2 Voice call

```
1. Vobiz webhook → Reach /voip/inbound
   Body: CallUUID=..., From=+919876543210, signature header

2. Reach Voice middleware:
   • Verify webhook signature with Vobiz shared secret (existing pattern)
   • Resolve tenant from inbound DID or campaign config → "kkb"
   • Call auth_provider.mint_caller_token(tenant_id="kkb",
                                          phone="+919876543210",
                                          channel="voice")
     → Reach's Keycloak service account authenticates against
       POST {KEYCLOAK_URL}/realms/kkb/mint_caller
     → returns short-lived JWT (sub="phone:+91...", role="end_user", channel="voice")
   • Establish session with this JWT in contextvars
   • All downstream Pipecat pipeline frames use AuthForwardingAsyncClient
     when calling Agent Core /stream_turn

3. Same as 6.1 from step 4 onward.
```

WhatsApp is identical with Meta HMAC instead of Vobiz signature.

A returning verified caller (e.g., via in-call OTP) may swap a `phone:` JWT for a `user:` JWT mid-session via a separate `verify_caller` endpoint. Design surface present in the abstraction; not implemented in v1.

### 6.3 MCP client

```
1. MCP client obtains a JWT via Keycloak client_credentials:
   POST {KEYCLOAK_URL}/realms/<tenant>/protocol/openid-connect/token
     grant_type=client_credentials
     client_id=mcp:partner_xyz  client_secret=...
   → JWT with sub="mcp:partner_xyz", role="mcp_client:partner_xyz"

2. MCP client → Reach /mcp/<tool>
   Authorization: Bearer <mcp_jwt>

3. Reach MCP middleware:
   • VerifyJwt → AuthContext (role=mcp_client:partner_xyz)
   • RateLimit: bucket "kkb:mcp:partner_xyz:/mcp/*"  → policy-specific limit
   • v1: coarse role-allowlist check (is mcp_client:* allowed on this endpoint?)
   • v2: fine-grained `scope` claim check → per-tool authz

4. Same as 6.1 from step 4 onward.
```

A2A is identical at this layer; only the endpoint prefix and the role label differ (`a2a_peer:<id>`).

### 6.4 Failure-response contract

| Condition                            | Status | Body                                          |
|--------------------------------------|--------|-----------------------------------------------|
| Missing `Authorization`              | 401    | `{reason:"missing"}`                          |
| Invalid signature / JWKS mismatch    | 401    | `{reason:"invalid"}`                          |
| Expired token                        | 401    | `{reason:"expired"}`                          |
| Wrong audience / issuer              | 401    | `{reason:"audience"}` / `{reason:"issuer"}`   |
| Role not allowed for endpoint        | 403    | `{reason:"forbidden", role, endpoint}`        |
| Rate limit exceeded                  | 429    | `{reason:"rate_limited"}` + `Retry-After`     |
| Keycloak unreachable (JWKS fetch)    | 503    | `{reason:"auth_provider_down"}`               |
| Keycloak unreachable, JWKS cached    | continues from cache; warn-log only            |

Reason codes are stable identifiers. Callers map them to UX strings without parsing prose.

---

## 7. Resilience and edge handling

### JWKS fetch

- In-memory JWKS cache; TTL default 600s, refresh-ahead at 80% of TTL.
- Persistent on-disk fallback at `/var/cache/dpg_auth/jwks/<realm>.json` — snapshotted on every successful fetch; loaded on cold start if network is down.
- Background refresh with exponential backoff; never blocks the request path.
- Unknown `kid` triggers a single rate-limited refresh (max once / 30s).
- Both network and disk unavailable: 503 with `auth_provider_down` for protected paths; `/healthz` stays green so liveness probes do not flap.

### `mint_caller_token` failure

- Idempotent retry once with backoff, deduplicated by `CallUUID`.
- On final failure: Vobiz/WA gets a 200 + polite "service unavailable" channel response. Never 5xx (avoids carrier retry storms).
- Log + alert.

### JWT forwarding edge cases

- Forwarded token < 30s to expiry: warn-log. Downstream may 401; caller surfaces `session_expired`. (Refresh is the Approach-B upgrade.)
- Subject mismatch between hops: cannot happen in Approach A (same JWT). In B, callee verifier confirms `act.sub` matches what the caller-service is authorised to act for.
- Header tampering inside the trust boundary: rejected at every hop by signature verification. Nothing trusts a header without re-verifying.

### Rate-limit edge cases

- Redis down: fails open; logs `status=skipped reason=redis_down`. Configurable per deployment if a stricter posture is wanted.
- Clock skew between DPGs: irrelevant — sliding-window uses Redis `TIME` exclusively.
- Bucket bloat: keys TTL'd to 2× window. Cardinality bounded by `tenants × subjects × endpoint_groups`.

### Shadow-mode rollout safety

During Ring 1:
- All verification runs; failures are logged with `would_have_blocked=true`.
- Production traffic is unaffected.
- Observability Layer surfaces `would_have_blocked` counts per tenant per endpoint per reason; flip to `enforce` only when counts are zero or fully explained.

### Secrets management

- Keycloak service-account credentials are env vars sourced from secrets manager (Vault / k8s Secret / 1Password), never in YAML.
- YAML carries env-var *references* (`${SERVICE_CLIENT_SECRET}`) — same pattern as today's `ANTHROPIC_API_KEY`.
- Standalone dev uses a checked-in `.env.example` with placeholder values; real `.env` is gitignored.

### Backwards compatibility during rollout

Old static `X-API-Key` paths (KE upload, Dev-Kit) remain accepted only while `enforcement: shadow`. When Ring 2/3 flips a DPG to `enforce`, the X-API-Key code path is removed in the same PR. No "two modes forever" tech debt.

---

## 8. Testing strategy

### Layer 1 — `dpg_auth` library unit tests

| Component                        | Normal                                            | Edge                                       | Failure                                            |
|----------------------------------|---------------------------------------------------|--------------------------------------------|----------------------------------------------------|
| `KeycloakAuthProvider.verify`    | valid JWT → AuthContext                           | clock skew within tolerance                | expired, bad sig, wrong iss/aud, bad alg           |
| `CompositeAuthProvider.verify`   | routes by `iss`                                   | unknown `iss` falls through to last        | all providers reject                                |
| `StaticAuthProvider.verify`      | recognised YAML token → AuthContext               | empty/None token                           | unknown, malformed                                  |
| `mint_caller_token`              | returns JWT decodeable by same provider           | dup `CallUUID` returns same JWT (dedup)    | service-account creds wrong; Keycloak 5xx          |
| `VerifyJwtMiddleware`            | Bearer header → 200 + context set                 | bypass paths skip; case-insensitive header | missing/expired/forbidden → correct 401/403 body    |
| `RateLimitMiddleware`            | under-limit → 200                                 | exactly at limit; window boundary          | over-limit → 429 + Retry-After; Redis down → pass  |
| `AuthForwardingAsyncClient`      | injects Authorization + traceparent + baggage     | no AuthContext set → no headers, warn      | downstream 401 surfaced as `AuthForwardError`      |
| `StructuredLogFilter`            | record gets tenant/user/request_id                | no context → fields absent, not empty str  | filter exception does not drop the record           |
| `EnforcementMode.shadow`         | bad token → logs `would_have_blocked`, returns 200| missing token → logs, returns 200          | (no failure case)                                   |

Keycloak calls are mocked with `respx`. Tokens are hand-signed by tests with a known keypair fed into the mocked JWKS endpoint. No real Keycloak in unit tests.

### Layer 2 — Per-DPG integration tests

Each DPG gains `tests/test_auth_integration.py`. Mounts the app via `TestClient` with `StaticAuthProvider` from a fixture. Exercises every protected endpoint with: no token, wrong-role token, right token, expired token, over-limit (where rate-limited). Asserts status, body, and structured-log fields via `caplog`.

### Layer 3 — End-to-end Docker smoke test

`automation/docker/docker-compose.dev.yml` includes Keycloak with a baked realm import (`automation/docker/keycloak/realms/dpg-dev.json`): 1 test user, 1 service account per DPG, 1 MCP client. A `scripts/smoke_auth.py`:

1. Logs in as the test user → gets a JWT.
2. Calls Reach `/turn` end-to-end. Asserts 200.
3. Calls Reach `/turn` without a token. Asserts 401.
4. Calls Memory directly with the same JWT. Asserts 200.
5. Acquires MCP client_credentials token; calls Reach `/mcp/<tool>`. Asserts 200.
6. (v2) Same MCP call without scope. Asserts 403.

Runs in CI on every PR touching `dpg_auth/` or any DPG's auth-related code.

### Layer 4 — Manual rollout verification

The shadow-mode `would_have_blocked` dashboard in Observability is the gate, not just CI. Flip ring-by-ring only when shadow-mode is clean for 48 h.

### Coverage target

`dpg_auth/` itself: ≥ 85% line coverage. Per-DPG integration tests do not move the per-DPG number meaningfully; existing per-module coverage targets remain (≥ 70% on agent_core/KE).

### Not tested in unit tests

- Real Keycloak JWKS roundtrips (e2e only).
- Real Redis (use fakeredis; real Redis in e2e).
- Cryptographic correctness of the JWT library itself (trust PyJWT's own tests).

---

## 9. Rollout plan

Three rings, each independently revertable via YAML config flip + redeploy. No destructive migrations.

### Ring 0 — foundation

1. Create `dpg_auth/` package + tests (≥ 85% coverage). Not yet imported by anyone.
2. Add Keycloak to `automation/docker/docker-compose.dev.yml` with health check + persistent volume.
3. Commit `automation/docker/keycloak/realms/dpg-dev.json`:
   - Realm `dpg-dev` (kkb tenant for local dev).
   - Test users: `alice@example.com` (end_user), `op@example.com` (operator).
   - Service-account clients: `svc-reach`, `svc-agent-core`, `svc-memory`, `svc-ke`, `svc-trust`, `svc-ag`, `svc-obs`, `svc-dev-kit`.
   - MCP test client: `mcp-demo`.
   - Custom client scope `dpg_role` → JWT `role` claim mapper.
4. Land the `mint_caller_token` extension — either as a Keycloak SPI extension or, equivalently for v1, a small FastAPI sidecar using Admin API + token exchange. Same external contract.
5. CI smoke test wired.

**Done when:** `dpg_auth` is published intra-repo; docker stack boots with Keycloak healthy; no DPG behaviour change yet.

### Ring 1 — wire every DPG in shadow mode

For each of the 8 modules:
- `uv add dpg_auth`.
- Mount `VerifyJwtMiddleware(enforcement="shadow")` + `RateLimitMiddleware` (ingress modules only).
- Replace `httpx.AsyncClient()` call sites with `AuthForwardingAsyncClient()` (Reach → Memory/KE; Agent Core → all callees).
- Install `dpg_auth.logging.install_filter()`.
- Add module config to `dev-kit/dpg/<module>.yaml` + `dev-kit/configs/kkb/<module>.yaml`.
- Update Pydantic schemas in BOTH schema trees.

**Done when:** all 8 modules emit `auth_verify` events; `would_have_blocked` counts visible in Observability; no user-facing behaviour change.

**Hold for 48 h of shadow data.** Fix client misconfigurations before flipping.

### Ring 2 — enforce on external ingresses (Reach + Dev-Kit)

- Flip `enforcement: enforce` on Reach Layer + Dev-Kit.
- Remove the legacy Google-SSO-only path in `reach_layer/web/src/auth.py` (Keycloak now handles Google federation upstream). Reuse the existing `Reason` enum in the new code.
- Remove the static `X-API-Key` paths in KE upload and Dev-Kit.
- Reach web SPA switches to OIDC Authorization Code + PKCE against Keycloak (separate small spec if needed; library is unaffected).
- Voice / WA adapters start calling `mint_caller_token`. Old `user_id` from request body is no longer trusted on protected paths.

**Done when:** unauthenticated traffic to Reach + Dev-Kit gets 401; authenticated traffic flows end-to-end; rate-limit logs show non-zero buckets.

### Ring 3 — enforce on inner DPGs

Flip `enforcement: enforce` per DPG, in order: Agent Core → Memory → KE → Trust → AG → Observability. After each flip, verify 30 minutes of zero auth errors before moving to the next.

**Done when:** every DPG enforces; every log line carries `tenant_id`, `user_id`, `request_id`, `caller`. The `auth.enforcement` field can be cleaned up from configs in a follow-up.

### Follow-up specs (deliberately out of scope)

- Approach B (token exchange / OBO) — `AuthForwardingAsyncClient` swap only.
- MCP/A2A fine-grained scopes (authz model v2) — add `scope` claim checks.
- Operator audit log of config changes — Dev-Kit feature.
- Keycloak admin-UI access policy for tenant operators — ops/runbook.

### Time estimate

| Ring | Estimate |
|------|----------|
| Ring 0 | ~1 week |
| Ring 1 | ~2 weeks |
| Ring 2 | ~1 week |
| Ring 3 | ~1 week |
| **Total** | **~5 weeks of focused work** |

The implementation plan (separate document) will break this into checkpointed tasks.

---

## 10. Open questions deferred to implementation

These are intentionally not resolved here. They are implementation choices, not design choices, and should be settled in the writing-plans phase.

1. Exact location of the `dpg_auth` package (`dev-kit/dpg/dpg_auth/` vs. top-level).
2. Whether `mint_caller_token` ships as a Keycloak SPI extension (Java) or a FastAPI sidecar (Python). External contract is identical.
3. Concrete BetterAuth verifier implementation — placeholder in v1; written when a deployment actually requires direct BetterAuth-JWT acceptance.
4. Whether to consolidate the dual Pydantic schema trees as part of this work or leave it for a separate cleanup spec.
