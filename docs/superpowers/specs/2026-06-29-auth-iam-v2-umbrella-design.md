# Auth & IAM v2 — umbrella design for ai-diffusion-dpg

**Status:** umbrella design, approved 2026-06-29 — decomposes into 4 child designs (A/B/C/D), each with its own spec → plan → build cycle.
**Owner:** Aniket Sakinala
**Supersedes:** [`2026-05-19-auth-iam-design.md`](2026-05-19-auth-iam-design.md) (umbrella issue #342). That spec is retained for history; this document is the authoritative cross-cutting contract going forward.
**Tracks:** #342 (umbrella), #105 (inter-DPG service auth), #107 (end-user OTP), #273 (operator access), #338 (MCP), PR #361 (shipped MCP API-key auth — reconciled here).

> Single source of architectural truth for block responsibilities remains [`ARCHITECTURE.md`](../../../ARCHITECTURE.md). This document defines the cross-cutting authentication, authorization, identity-propagation, and tenancy contract, and the decomposition of the work into independently-shippable child designs.

---

## 1. Why a fresh umbrella

The 2026-05-19 design (#342) was approved but never implemented, and three things have changed since:

1. **The network-common Keycloak direction is now firm.** The old spec assumed ai-diffusion bundles its *own* Keycloak as the standalone default. We now target a **single network-wide Keycloak instance** directly (the same one aggregator-dpg and Signals-DPG will converge on).
2. **MCP auth already shipped — with a different scheme.** PR #361 implemented **static API-key** auth for inbound MCP callers (`Authorization: Bearer <key>` matched against configured callers). The old spec specified Keycloak `client_credentials`. This document reconciles that drift (§5.D).
3. **Companion-deployment identity is now a first-class requirement.** ai-diffusion is most often deployed as a **companion to Signals-DPG** (e.g. on blue_dot: a voice + web chatbot for the *seeker* flow and another for the *provider* flow). In that mode the end-users already exist in the Signals realm — and a first-time voice/web caller must be **created** in that realm. This was not modelled before.

The result is **one umbrella contract + four child designs**, not one monolithic rollout.

---

## 2. Locked cross-cutting decisions

These are settled and bind every child design.

### 2.1 One IdP: the network-common Keycloak

All identities — end users, operators, services, and external agents — are verified against the **network-common Keycloak**. ai-diffusion does not bundle a throwaway IdP. Every credential type resolves to a **short-lived, Keycloak-issued token** that the shared `dpg_auth` library verifies identically at every hop.

Consequence: **Keycloak is a hard runtime dependency in every deployment.** Inter-service auth (child A) is therefore *not* zero-dependency — "stand up / reach Keycloak" is the true first step (Ring 0). In standalone deployments, **dev-kit deploys and owns the Keycloak server** (child C).

### 2.2 Realm target is a deployment-time choice, not a fixed property

ai-diffusion runs in two modes; the **target realm is configuration**, and the same code path serves both:

| Mode | Target realm | End-user lifecycle |
|---|---|---|
| **Standalone** | ai-diffusion **owns its realm** (one per use-case/installation), provisioned by dev-kit on the shared KC server | dev-kit/admin provisions; ai-diffusion JIT-creates callers |
| **Companion** (alongside Signals) | the **host Signals realm** | existing Signals users are **read, not recreated**; a first-time voice/web caller is **JIT-provisioned into the Signals realm** |

dev-kit multi-tenancy (child C) = an admin deploying **N use-cases, each its own realm**, on a single Keycloak server.

> **Realm-per-installation vs realm-per-network.** The recorded network direction is *realm-per-network*. ai-diffusion reconciles this by making the target realm configurable: companion deployments use the network/Signals realm (realm-per-network); standalone deployments use an ai-diffusion-owned realm (realm-per-installation). The verifier resolves the realm from the JWT `iss` claim regardless.

### 2.3 Four credential paths, all Keycloak tokens

| # | Actor | Credential | Notes |
|---|---|---|---|
| 1 | Inter-DPG service ↔ service | KC `client_credentials` **service-account** per DPG | child A |
| 2 | External MCP / agent | KC `client_credentials` client with **`private_key_jwt`** (asymmetric client auth) | child D; replaces #361 API key |
| 3 | End user — Reach Web | OIDC **code + PKCE**; OTP federated upstream at the IdP | child B |
| 4 | End user — Reach Voice / WhatsApp | short-lived **phone-minted JWT** (sub `phone:+E164`), minted after webhook-signature verification | child B |
| 5 | Operator — Dev-Kit | OIDC **code + PKCE**; `operator` role + `tenants` claim | child C |

The acting-user token is **forwarded** on every downstream hop (Approach A from the old spec); each callee re-verifies. The abstraction keeps token-exchange / on-behalf-of as a later drop-in (swap only `AuthForwardingAsyncClient`).

### 2.4 JIT-provisioning is first-class; companion **policy** defers to product

ai-diffusion owns a **read-or-create end-user in the target realm** capability. The **mechanism** (look up by phone/contact; create with KC Admin API if absent; mint a session/caller token) ships now and works for both modes.

The **companion-mode policy** — what a JIT-created caller *means* as a Signals **participant** (register-once-per-network, phone-as-identity, seeker/provider exclusivity, profile creation) — is **the only part of this work that still touches the parked product questions**. It is isolated behind a **pluggable `ProvisioningPolicy` seam** (child B): standalone mode ships a concrete policy now; companion mode ships a `read-only + deny-create` (or `flag-for-review`) default until product answers land, then swaps in the real policy with **no change to the mechanism**.

### 2.5 `dpg_auth` shared library is the vehicle

Carried over from the old spec (§5 there). One package `uv add`-ed by all 7 DPGs + dev-kit:

```
dpg_auth/
├── provider/         KeycloakAuthProvider, CompositeAuthProvider, StaticAuthProvider (tests)
├── middleware/       VerifyJwtMiddleware, RateLimitMiddleware, EnforcementMode (shadow|enforce)
├── context.py        AuthContext dataclass + contextvars
├── logging.py        StructuredLogFilter (tenant_id, user_id, request_id, caller)
├── http_client.py    AuthForwardingAsyncClient (httpx; forwards token + traceparent + baggage)
├── provisioning.py   ProvisioningPolicy ABC + read-or-create helpers (KC Admin API)   [NEW vs old spec]
└── config.py         AuthConfig (pydantic, loaded once at startup)
```

`AuthContext` (unchanged from old spec): `subject`, `tenant_id` (from realm), `role`, `issuer`, `token_id (jti)`, `expires_at`, `raw_claims`. Roles: `end_user | operator | service:<name> | mcp_client:<id> | a2a_peer:<id>`.

The **dual-Pydantic-schema discipline** (`dev-kit/.../schemas/dpg/<module>.py` AND each module's `MergedConfig`, per `.claude/rules/runtime-devkit-sync.md`) applies to every new `auth:` config key.

---

## 3. Decomposition into child designs

Each row becomes its own brainstorm → spec → plan → build cycle. This umbrella is the parent contract; children may not contradict §2.

| | Child design | Scope | GH | Depends on |
|---|---|---|---|---|
| **Ring 0** | **KC foundation + `dpg_auth`** | Reach/deploy KC; realm + client bootstrap; the shared `dpg_auth` library (verify, context, forwarding client, log filter); service-account clients; CI smoke (Keycloak in docker-compose with a baked realm import) | #342 | — |
| **A** | **Inter-DPG service auth** | `client_credentials` service-account per DPG; `VerifyJwtMiddleware` on all 7 blocks + Action Gateway; replace `httpx.AsyncClient` call-sites with `AuthForwardingAsyncClient`; shadow → enforce rollout | #105 | Ring 0 |
| **D** | **MCP inbound auth** | Migrate #361 API-key → KC `mcp_client:<id>` with `private_key_jwt`; operator-provisioned clients (v1), DCR via initial-access-token (future); coarse role allowlist now, fine scopes later; rate-limit at MCP ingress | #338, PR #361 | Ring 0 |
| **B** | **End-user IAM** | Reach Web OIDC code+PKCE (OTP federated upstream); Voice/WhatsApp phone-minted short-lived JWT after webhook-signature verify; realm-configurable read + JIT-provision via `ProvisioningPolicy` seam; companion policy = deferred-to-product default | #107 | Ring 0; companion policy → product |
| **C** | **Dev-kit multi-tenancy + operator access** | Operator OIDC login + `tenants` claim authz; realm provisioning/management across N tenants on one KC server; **KC deployment in standalone mode**; operator-access perimeter (#273 — Tailscale/known-operator list for dev-kit + grafana); config-change audit | #273 | Ring 0 |

**Suggested build sequence:** Ring 0 → **A** and **D** (independent, ship first) → **B** → **C** (operator-login part; its KC-deploy/realm-provisioning part folds into Ring 0). Each ring/child is independently revertable via `auth.enforcement: shadow|enforce` config flip.

---

## 4. Canonical data flows (cross-cutting reference)

These illustrate the shared contract; per-flow detail belongs in each child spec.

### 4.1 End-user web turn (child B + A)
```
Browser → Reach /turn   Authorization: Bearer <kc_user_jwt (code+PKCE)>
  Reach: VerifyJwt → AuthContext{sub, tenant=<realm>, role=end_user}; RateLimit; set contextvars+baggage
  Reach → Agent Core /process_turn   (AuthForwardingAsyncClient forwards the same JWT + traceparent + baggage)
  Agent Core re-verifies; → Trust/KE/Memory/AG/Obs, each hop forwards + re-verifies
```

### 4.2 Voice / WhatsApp turn (child B)
```
Vobiz/Meta webhook → Reach   (verify signature — existing pattern)
  Resolve target realm from DID/campaign config
  ProvisioningPolicy.read_or_create(realm, phone)   ← companion: read Signals realm; standalone: own realm
  Mint short-lived phone JWT (sub="phone:+E164", role=end_user, channel=voice)
  Establish session; downstream identical to 4.1 from Agent Core onward
```
Companion-mode `read_or_create` create-path obeys the deferred `ProvisioningPolicy` (default: deny-create / flag-for-review until product answers).

### 4.3 Inter-DPG call (child A)
```
DPG-X obtains client_credentials token (service:<x>) once, caches until exp
DPG-X → DPG-Y   Authorization: Bearer <service_token>  +  forwarded <user_token> when acting for a user
DPG-Y VerifyJwt → AuthContext{role=service:x}; authorize; proceed
```

### 4.4 MCP inbound (child D)
```
Agent holds private key; public JWKS registered on its KC client (mcp:partner)
Agent → KC token endpoint   grant=client_credentials, client_assertion=<private_key_jwt>
  → short-lived token  sub="mcp:partner", role="mcp_client:partner"
Agent → Reach /mcp/<tool>   Authorization: Bearer <token>
  Reach VerifyJwt → AuthContext; RateLimit (mcp bucket); coarse role allowlist (fine scopes = v2)
```

### 4.5 Failure-response contract (unchanged from old spec §6.4)
Stable reason codes: `missing` / `invalid` / `expired` / `audience` / `issuer` → 401; `forbidden` → 403; `rate_limited` → 429 + `Retry-After`; `auth_provider_down` → 503 (or serve from cached JWKS with warn-log).

---

## 5. Notes carried forward / reconciled

- **JWKS resilience, shadow-mode rollout, secrets-via-env, rate-limit edges** — adopt the old spec §7 verbatim in child A/Ring 0; not re-litigated here.
- **PR #361 reconciliation (child D):** the shipped API-key path is treated as legacy. Child D migrates external callers to `private_key_jwt` KC clients and **removes** the API-key code in the same ring it flips to enforce — no permanent dual-mode. Rationale: a static API key is a bearer shared secret (theft ⇒ impersonation); `private_key_jwt` keeps no shared secret on our side and issues only short-lived tokens.
- **A2A** remains future scope; identical wire model to MCP (`a2a_peer:<id>`), cross-deployment trust deferred.
- **Token exchange / OBO (Approach B)** remains the documented upgrade: swap only `AuthForwardingAsyncClient`.

---

## 6. Open questions (routed, not resolved here)

| # | Question | Routed to |
|---|---|---|
| 1 | Companion-mode participant policy for JIT-created callers (register-once, phone-as-identity, seeker/provider exclusivity, profile creation) | **Product** (the parked Keycloak/IAM questions); child B ships a deferred-default seam meanwhile |
| 2 | Does ai-diffusion's KC service-account need **Admin-API rights on the Signals realm** to JIT-create users, and who grants them? | child B + network-KC owners |
| 3 | Exact `dpg_auth` package location (top-level vs `dev-kit/dpg/`) | child A / Ring 0 implementation plan |
| 4 | Standalone KC deployment topology (sidecar vs shared server; HA) | child C |
| 5 | MCP fine-grained per-tool scopes (authz v2) | child D follow-up |

---

## 7. Next step

Per the brainstorm, the immediate next step is to **brainstorm child A (inter-DPG service auth) + Ring 0 foundation** end-to-end, producing its own spec. Children B/C/D follow.
