# Reach Layer — Google Sign-On (Web Channel)

**Date:** 2026-04-13
**Status:** Draft — awaiting clarifications
**Scope:** `reach_layer/` module only (backend + React web UI)
**Tracks:** [issue #51](https://github.com/sanketika-labs/cdnproject/issues/51)

---

## Problem

The Reach Layer web channel has no authentication. `user_id` is typed into a manual setup screen, persisted in `localStorage`, and sent as a client-declared body field on every `POST /chat`. Any string is accepted. `GET /user-history/{user_id}` takes the user ID from the URL path, so any user can read any other user's history.

Acceptance criteria from issue #51:
- Unauthenticated users see a sign-in prompt, not the chat UI.
- Successful Google OAuth flow lands in chat with a valid session.
- `user_id` derived from Google identity is passed consistently to Agent Core.
- No PII (email, name) written to structured logs.
- Unit tests cover auth flow, unauthenticated redirect, and token-expiry handling.

Scope:
- Framework-level feature in `reach_layer/` — domain-agnostic. Every domain (KKB, future domains) inherits the same auth flow. Defaults are shipped in `reach_layer/config/dpg.yaml`; any domain can override knobs (TTL, cookie name) but the mechanism is identical.

Non-goals:
- No identity provider other than Google in this iteration.
- No multi-tenant account linking, admin UI, or user migration from old `guest_*` IDs.
- Telephony adapter is untouched — auth lives on the web channel only. Telephony `user_id` remains the phone number.

---

## Solution: GIS ID token → server-issued HttpOnly session cookie

Use Google Identity Services (GIS) on the frontend; verify the Google ID token on the backend; issue our own short-lived session JWT in an `HttpOnly; Secure; SameSite=Lax` cookie. Subsequent requests are authenticated from the cookie; `user_id` is always derived server-side.

This is the standard "SPA + trusted backend" pattern. It avoids storing Google tokens in `localStorage` (XSS-exposed) and the implicit-flow redirect dance.

```
┌──────────┐  1. GIS button        ┌──────────────┐
│ Browser  │──────────────────────▶│ Google       │
│ (React)  │◀──── id_token (JWT) ──│ accounts     │
└────┬─────┘                       └──────────────┘
     │ 2. POST /auth/google { credential }
     ▼
┌──────────────────────────────────────────────────┐
│ reach_layer (FastAPI)                            │
│   verify id_token with google-auth (JWKS, aud,   │
│     iss, exp, email_verified)                    │
│   user_id = "google:" + sub                      │
│   session_jwt = HS256({sub: user_id, ...}, TTL)  │
│   Set-Cookie: reach_session=…; HttpOnly; Secure; │
│               SameSite=Lax; Path=/               │
└─────────────────────┬────────────────────────────┘
                      │ 3. response { user_id, name, picture }
                      ▼
┌──────────┐  4. GET/POST /chat, /user-history, /auth/me
│ Browser  │     (cookie sent automatically)
└──────────┘
```

Key design choices:

| Decision | Choice | Why |
|---|---|---|
| Token location | `HttpOnly` cookie | JS cannot read → XSS can't steal session |
| Cookie attrs | `Secure; SameSite=Lax` | CSRF-safe, HTTPS-only in prod |
| Session token | Stateless HS256 JWT | Keeps Reach Layer stateless; aligns with current design |
| `user_id` shape | `google:<sub>` | Stable across email changes; opaque; namespace leaves room for future providers (e.g. `apple:<sub>`) without collisions |
| Identity passing | Cookie only; `user_id` removed from `/chat` body | Client cannot impersonate |
| Session TTL | 24h default, configurable | Revocation comes from short TTL; no server-side store needed |
| Revocation (v1) | None (rely on TTL) | Simple. If needed later, swap JWT for opaque ID + Redis — no API change |

---

## Identity & session model (post-implementation)

### `user_id`

| Channel | Today | After this change |
|---|---|---|
| Web (Reach Layer)    | User-typed string (`guest_XXXX`, anything), stored in `localStorage` | `"google:" + <google_sub>` — server-derived from the verified Google ID token. Clients cannot supply or change it. |
| Telephony (Voice)    | Caller phone number                                                   | Unchanged. |
| CLI (dev)            | Typed at prompt                                                       | Unchanged. |

- **`<google_sub>`** is Google's stable per-user, per-app identifier (a numeric string like `116547362554128745382`). It does **not** change if the user changes their Google email or display name, so profile continuity is preserved.
- **Why the `google:` prefix?** It namespaces the identity provider so future providers (Apple, Microsoft) don't collide with existing users. No provider prefix means a raw `sub` string could accidentally match a manually-created ID.
- **Example:** `google:116547362554128745382`. This is the exact value Memory Layer will key `UserProfile` nodes on, and the exact value Agent Core receives in `TurnInput.user_id` for every turn.
- **Existing `guest_*` users are not migrated.** Old profiles remain addressable by their old IDs; new web users create new Google-keyed profiles from scratch.

### `session_id`

| Concept | Definition | Source | Persistence |
|---|---|---|---|
| **`session_id`** (conversation) | The Agent Core / Memory Layer conversation identifier | Generated client-side on first turn via `crypto.randomUUID()`; reused across turns until "Clear chat" or page reload with no active conversation | Client-side state only |
| **Session cookie** (`reach_session`) | The auth session proving the user is who they claim to be | Issued by reach-layer on successful Google login (HS256 JWT) | `HttpOnly; Secure; SameSite=Lax` cookie, 24h TTL default |

**These two are intentionally decoupled** — the auth session ("I am user X") is separate from the conversation session ("this is a chat turn-stream"). One logged-in user can clear a chat and start a new conversation without re-authenticating. One expired auth session does not destroy the user's Memory Layer history; re-login restores it because `user_id` is stable.

### What the cookie contains

```json
{
  "sub":  "google:116547362554128745382",
  "name": "Asha Kumar",
  "iat":  1744512000,
  "exp":  1744598400
}
```

Signed with HS256 using `REACH_SESSION_SECRET`. Attributes: `HttpOnly; Secure; SameSite=Lax; Path=/`. JavaScript on the page cannot read this value.

### What every `POST /chat` looks like after implementation

```
POST /chat
Cookie: reach_session=<jwt>
Content-Type: application/json

{
  "session_id": "6f4a9b2e-5c81-4e3d-ad1e-7d6f9f3c0c1b",
  "message":    "yes apply karo"
}
```

Note: **no `user_id` in the body.** The backend derives it from the cookie; any client-supplied `user_id` is rejected. Agent Core then receives:

```python
TurnInput(
    session_id = "6f4a9b2e-5c81-4e3d-ad1e-7d6f9f3c0c1b",
    user_id    = "google:116547362554128745382",   # from cookie, not body
    user_message = "yes apply karo",
    channel    = "web",
    ...
)
```

### State at-a-glance

```
Google sub  ──┐
              │   verify + sign
              ▼
      reach_session cookie (24h, HS256 JWT)
              │
              ▼
   every /chat & /user-history request
              │
              ▼
      user_id = "google:<sub>"
              │
              ▼
   Agent Core → Memory Layer  (UserProfile keyed on this id)
```

---

## Architecture

### Request flow

```
Boot:                       Chat turn:                    Logout:
GET /auth/me                POST /chat {message}          POST /auth/logout
  cookie? yes → 200 user      cookie required             clears cookie
  cookie? no  → 401           user_id = cookie.sub
                              Body user_id ignored
```

### Endpoint map

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/health`                 | public | unchanged |
| GET  | `/app-config`             | public | **extended** to return `auth.enabled` + `auth.google.client_id` so SPA can render login |
| POST | `/auth/google`            | public | verify GIS credential; set session cookie; return safe profile |
| GET  | `/auth/me`                | required | return current user profile; 401 if no/invalid cookie |
| POST | `/auth/logout`            | public (no-op if absent) | clear cookie |
| POST | `/chat`                   | required | `user_id` derived from cookie; body `user_id` rejected/ignored |
| GET  | `/user-history`           | required | **path param removed**; history for authenticated user only |
| GET  | `/` + `/assets/*`         | public | SPA shell |

### New backend module

```
reach_layer/
  src/
    auth.py            # pure functions + exceptions
    dependencies.py    # get_current_user FastAPI dependency
```

`auth.py`:

```python
class AuthError(Exception):
    class Reason(enum.Enum):
        MISSING, INVALID, EXPIRED, AUDIENCE, ISSUER, UNVERIFIED_EMAIL = range(6)

@dataclass(frozen=True)
class GoogleIdentity:
    sub: str; email: str; name: str; picture: str | None

@dataclass(frozen=True)
class SessionClaims:
    user_id: str; display_name: str; exp: int

def verify_google_id_token(credential: str, client_id: str) -> GoogleIdentity: ...
def issue_session_token(user_id: str, display_name: str, ttl_s: int, secret: str) -> str: ...
def verify_session_token(token: str, secret: str) -> SessionClaims: ...
```

`dependencies.py`:

```python
async def get_current_user(request: Request, cfg = Depends(get_config)) -> CurrentUser:
    if not cfg.auth.enabled:
        return CurrentUser(user_id="dev-local", display_name="local dev")
    token = request.cookies.get(cfg.auth.session.cookie_name)
    if not token:
        raise HTTPException(401, "auth_missing")
    try:
        claims = verify_session_token(token, cfg.auth.session.secret)
    except AuthError as e:
        raise HTTPException(401, e.args[0])
    return CurrentUser(user_id=claims.user_id, display_name=claims.display_name)
```

### Frontend structure

```
reach_layer/web-src/src/
  components/screens/
    LoginScreen.jsx        # NEW — Google button, handles credential
    SetupScreen.jsx        # kept; rendered only when auth.enabled == false (local dev)
  hooks/
    useAuth.js             # NEW — /auth/me, login(credential), logout()
  api.js                   # MODIFIED — credentials: 'include'; remove user_id from body
  App.jsx                  # MODIFIED — boot branches on /auth/me
  main.jsx                 # MODIFIED — <GoogleOAuthProvider clientId={…}>
```

Boot branching (in `App.jsx`):

```
loading
  ├─ fetch /app-config
  ├─ if auth.enabled:
  │     fetch /auth/me
  │       200 → prefetch /user-history → chat
  │       401 → login
  └─ else:
        legacy setup → chat    (unchanged path for local dev)
```

---

## Config

Extend `reach_layer/config/dpg.yaml` (framework defaults, auth disabled):

```yaml
auth:
  enabled: false
  provider: google
  google:
    client_id: ""                    # public; safe to ship to SPA
  session:
    cookie_name: reach_session
    cookie_secure: true              # set false only for localhost plain-http dev
    cookie_samesite: lax
    ttl_seconds: 86400
    secret_env: REACH_SESSION_SECRET # config_loader resolves this from env
```

Domain override for KKB (`dev-kit/configs/kkb/reach_layer.yaml`):

```yaml
auth:
  enabled: true
  google:
    client_id: "${GOOGLE_CLIENT_ID}"
  session:
    ttl_seconds: 86400           # override here if a domain wants a different session length
```

Any domain (KKB today, others later) can opt in by setting `auth.enabled: true` and providing its own `GOOGLE_CLIENT_ID`. The session TTL, cookie name, and other knobs are all domain-overridable — the mechanism stays identical.

Env vars:

| Name | Required when | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID`       | `auth.enabled: true` | OAuth client ID; public |
| `REACH_SESSION_SECRET`   | `auth.enabled: true` | ≥32 random bytes; HS256 signing |

If `auth.enabled: true` and either env var is absent, the server refuses to start with a structured error log and non-zero exit.

---

## File changes

### New files

```
reach_layer/src/auth.py
reach_layer/src/dependencies.py
reach_layer/tests/test_auth.py
reach_layer/tests/test_auth_endpoints.py
reach_layer/web-src/src/components/screens/LoginScreen.jsx
reach_layer/web-src/src/hooks/useAuth.js
```

### Modified files

```
reach_layer/pyproject.toml            # +google-auth, +pyjwt[crypto]
reach_layer/server.py                 # +auth endpoints, +dependency on /chat, /user-history
reach_layer/config/dpg.yaml           # +auth block (disabled default)
dev-kit/dpg/reach_layer.yaml          # +auth block
dev-kit/configs/kkb/reach_layer.yaml  # enable auth + client_id env
reach_layer/web-src/package.json      # +@react-oauth/google
reach_layer/web-src/src/main.jsx      # wrap in <GoogleOAuthProvider>
reach_layer/web-src/src/App.jsx       # boot on /auth/me
reach_layer/web-src/src/api.js        # credentials: 'include', remove user_id body field
reach_layer/web-src/src/hooks/useChat.js  # drop userId param
reach_layer/web-src/src/components/ChatHeader.jsx  # sign-out button
automation/docker/docker-compose.dev.yml  # wire env vars for reach_layer
automation/helm/dpg/reach-layer/*         # secret + configmap entries
CLAUDE.md                             # update /user-history exception note
```

---

## Security

Mandatory checks (all server-side):
- `google-auth` verifies JWKS signature, `iss ∈ {accounts.google.com, https://accounts.google.com}`, `aud == client_id`, `exp`.
- Reject if `email_verified != True`.
- Our session JWT uses HS256 with `REACH_SESSION_SECRET`; verification on every protected request.
- Session cookie: `HttpOnly; Secure; SameSite=Lax; Path=/`.
- Logout clears cookie with `Max-Age=0; expires=epoch`.

Logging (per `.claude/rules/logging-observability.md`):
- Every auth event logs `operation`, `status`, `latency_ms`.
- **Never** log `credential`, `email`, `name`, `picture`, or raw `sub`.
- If a user ID correlates a log line, hash it: `user_id_hash = sha256(user_id)[:12]`.
- Failures log only `AuthError.Reason.<name>`, never the token contents.

CSRF posture:
- SameSite=Lax + JSON-only POST bodies make CSRF negligible here.
- Not adding a double-submit token in v1; documented as an explicit non-goal.

Rate limiting:
- **Deferred.** `/auth/google` is protected by Google's own JWKS signature check, so replayed garbage tokens are cheaply rejected. The only residual risk is wasted CPU if an attacker floods the endpoint. If we observe abuse, a ~30-line in-memory IP token bucket (e.g., 20 attempts / minute / IP → 429) is a quick follow-up. Documented as a deferred enhancement; not required for v1.

---

## Dev / prod wiring

**Dev (Vite 5174 → FastAPI 8005):**
- `vite.config.js` already proxies `/chat`, `/app-config`, `/user-history` — add `/auth/*`.
- Browser sees one origin (Vite); cookies work without CORS.
- For plain HTTP dev, flip `cookie_secure: false` in a local-only config; never in dpg.yaml.

**Prod (same-origin HTTPS):**
- Ingress terminates TLS; FastAPI trusts `X-Forwarded-Proto` via Starlette's `ProxyHeadersMiddleware`.
- Add `ProxyHeadersMiddleware` to the app if not already present.

### Kubernetes / Helm deployment

- **Ingress** — add a new `Ingress` resource in `automation/helm/dpg/reach-layer/templates/` that exposes reach-layer externally on the domain's hostname with TLS. `cookie_secure: true` requires HTTPS end-to-end. Ingress class and TLS issuer (e.g. cert-manager with Let's Encrypt) are domain-level values in `values.yaml`.
- **`GOOGLE_CLIENT_ID`** — stored in a `ConfigMap` (it's a public identifier; not a secret). Injected as an env var into the reach-layer Deployment.
- **`REACH_SESSION_SECRET`** — stored in a Kubernetes `Secret`. Two accepted patterns:
  1. **Recommended (safest common default):** Secret is created **once per cluster** out-of-band with a 32-byte random value (`openssl rand -base64 32`), not via Helm values. Helm only references it by name. This keeps the secret out of Git and CI variables, and rotating it is a one-liner `kubectl create secret ... --dry-run | kubectl apply -f -`.
  2. **For teams already using External Secrets Operator:** the Secret is synced from a vault (AWS Secrets Manager, GCP Secret Manager, Vault). Design supports this — the reach-layer Deployment references the Secret by name regardless of how it got there.
- **Startup guard** — reach-layer refuses to start if `auth.enabled: true` and either env var is missing or empty, with a single structured error log. Prevents silent mis-deployments.

### Google Cloud Console setup (one-time)

The OAuth client is **tied to a Google Cloud project, not a personal email**. Any Google account can create the project (that account becomes the initial Owner), but additional team members / a shared team email should be added as Owners so the project isn't locked to an individual. Ownership can be transferred without breaking the client ID.

Steps (done once per project):

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create / select a project (e.g. "ai-diffusion-dpg").
2. **APIs & Services → OAuth consent screen:** set app name ("Kaam Ki Baat"), support email, logo, privacy-policy URL. Publishing status "In production" when ready; "Testing" for early dev.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID → Web application.**
4. **Authorized JavaScript origins:** add every origin that will initiate a sign-in.
5. **No redirect URIs** (GIS uses `postMessage`, not the redirect flow).
6. Copy the generated `Client ID` (public string, format `…apps.googleusercontent.com`) → this is our `GOOGLE_CLIENT_ID`.

Dev vs. Prod client strategy — two accepted patterns:

| Pattern | Description | When to use |
|---|---|---|
| **Single client, multi-origin** | One OAuth client with all origins (localhost + staging + prod) listed | Simplest. Good for early-stage projects and small teams. |
| **Separate clients per environment** | One client per environment ("KKB Web — Dev", "KKB Web — Prod"), each with only its own origins | Production-grade. Limits blast radius if a dev client is compromised. Swap `GOOGLE_CLIENT_ID` via env var per environment. |

The design supports both patterns identically (just a different value in `GOOGLE_CLIENT_ID` per environment). Starting with the single-client pattern and splitting later is a pure config change.

Typical origins to register:
- `http://localhost:5174` (Vite dev server)
- `http://localhost:8005` (FastAPI dev)
- `https://<staging-host>` (staging Ingress)
- `https://<prod-host>` (production Ingress)

---

## Tests

### Backend (pytest + respx + FastAPI TestClient)

`test_auth.py` (pure):
- `verify_google_id_token`: valid (mocked `id_token.verify_oauth2_token`), expired, wrong `aud`, wrong `iss`, malformed, `email_verified=false`.
- `issue_session_token` + `verify_session_token`: roundtrip, expired, tampered payload, wrong secret.

`test_auth_endpoints.py`:
- `POST /auth/google`: valid credential → 200 + `Set-Cookie` + safe profile; invalid → 401.
- `GET /auth/me`: with cookie → 200; without → 401; expired cookie → 401.
- `POST /auth/logout`: returns 204 and clears cookie (`Max-Age=0`).
- `POST /chat` without cookie → 401; with cookie → delegate called with `user_id = cookie.sub`; body `user_id` is ignored.
- `GET /user-history` (no path param) → history of cookie user only.
- `auth.enabled: false` path: all endpoints work without cookie and `user_id = "dev-local"`.

Coverage target: ≥70% for new module (matches `.claude/rules/testing-requirements.md`).

### Frontend (Vitest + React Testing Library)

- `LoginScreen`: renders Google button; on credential success calls `/auth/google`.
- App boot: `/auth/me` 200 → chat; 401 → login.
- Protected request 401 → bounce to login (session expiry handling).
- Logout clears UI state and routes to login.

### Manual acceptance (maps to issue #51)

- [ ] Unauthenticated user sees login, not chat.
- [ ] Google flow lands in chat with working session.
- [ ] `user_id = google:<sub>` reaches Agent Core on every turn.
- [ ] No PII in logs (grep audit on `reach_layer` logs during a full turn).
- [ ] Unit tests listed above pass with ≥70% coverage on `auth.py`.

---

## Rollout order

1. **Backend auth module + unit tests.** No endpoints wired. Green PR.
2. **Endpoints** `POST /auth/google`, `GET /auth/me`, `POST /auth/logout`, still behind `auth.enabled: false`. Green PR.
3. **Dependency on `/chat` + `/user-history`.** Still off by default. Update `test_server.py` to supply a test cookie or run with `auth.enabled: false`.
4. **Frontend** `LoginScreen` + boot branching on `/app-config.auth.enabled`.
5. **Flip KKB domain to `auth.enabled: true`.** Wire `GOOGLE_CLIENT_ID` + `REACH_SESSION_SECRET` into compose + Helm secrets.
6. **Cleanup.** Remove the legacy `SetupScreen` path + `localStorage.user_id` once KKB has been stable in staging.

---

## Out of scope / deferred

- Multi-provider SSO (Apple, Microsoft) — can slot into the same dependency later by reusing `issue_session_token` and namespacing user IDs (`apple:<sub>`).
- Refresh tokens / sliding expiration — current 24h TTL (configurable via `auth.session.ttl_seconds`) + re-login is acceptable for v1.
- Server-side session revocation (force logout across devices / on secret rotation) — add a Redis session store when needed; API does not change.
- Migration of existing `guest_*` user IDs to `google:<sub>` — explicitly not doing it. Old profiles remain queryable by the old ID.
- Rate limiting on `/auth/google` — deferred; add on first sign of abuse.
- Telephony auth — out of scope; telephony `user_id` remains the caller's phone number.

---

## Compliance with project rules

- **Block boundaries (CLAUDE.md):** Reach Layer only. No new calls from other blocks. The approved `/user-history` exception is preserved but tightened (path param → cookie).
- **Base-class pattern:** `auth.py` uses pure functions + typed exceptions; no new abstractions needed (it's a single-provider PoC).
- **Error handling:** Every external call (Google JWKS fetch via `google-auth`) and session verification has structured `AuthError` with explicit reasons; no silent swallows.
- **Configuration discipline:** No hardcoded values. Client ID, cookie attrs, TTL, secret name all in YAML / env.
- **Logging:** Structured logs with `operation/status/error/latency_ms`; no PII.
- **Testing:** Normal + edge + failure coverage on the new module.

---

## Pre-requisites before implementation lands

1. **Google OAuth client must exist.** Someone with access to the product's Google Cloud project must create a "Web application" OAuth 2.0 client in Google Cloud Console → APIs & Services → Credentials. Authorized JavaScript origins: `http://localhost:5174`, `http://localhost:8005`, and the production URL. The resulting `GOOGLE_CLIENT_ID` is a public string — safe to commit to Helm ConfigMap.
2. **`REACH_SESSION_SECRET` generated once per environment** (`openssl rand -base64 32`) and stored as a Kubernetes Secret (or via External Secrets Operator if available).
3. **Ingress + TLS** configured for the reach-layer service in each target environment; `cookie_secure: true` depends on HTTPS end-to-end.
