# Configurable Identity and Human Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The voice agent answers "who are you / are you a human / let me talk to a person" truthfully from per-use-case config. When the use case allows it, it also sends a signed handoff of the caller's details to a configured webhook, and says "passed on" only if delivery succeeded.

**Architecture:**
- **Identity:** a new optional `identity` config block, rendered as `<identity>` in tier 1 of the prompt.
- **Trigger:** "I want a human" becomes the act-intent `human_request` (a new `human` NLU topic). Agent Core handles it before routing, the same way `language_switch_request` is handled.
- **Handoff turn:** in that one turn, the orchestrator calls `trust.escalate(...)` with a payload built from session state. It then writes `handoff_*` and `close_return_to`, moves to the `handoff` phase, and speaks one fixed line with no LLM call.
- **Delivery:** Trust Layer gets the missing `webhook` queue backend: HTTPS, HMAC-signed, 3 s timeout, one retry. It reports `delivered`.
- **After the handoff:** the `handoff` phase declares `pending: close_confirm` and reuses D3's confirm-then-end routing.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI (trust_layer), httpx (both), pytest; YAML domain config; dev-kit mirrors.

**Spec:** `docs/superpowers/specs/2026-10-02-identity-and-human-handoff-design.md` (commits 4dd1b0f, 3c88859).

**Base:** before Task 1, the controller rebases `spec/identity-handoff` onto `fix/voice-turn-defects`, after D3 (4b34a13) is reviewed. Tasks rely on D3's `confirm_close`, `close_confirm` and `close_return_to`.

## Global Constraints

- **Default-off.** With `human_handoff: none` and no webhook env, nothing changes except the honest identity answer. `trust.hitl.queue_backend` stays `log` by default.
- **"Delivered" means a 2xx from the webhook. Nothing else counts:**
  - the `log` backend returns `delivered=false, reason="log_only"`;
  - unsupported backends return `queued=false, delivered=false, reason="unsupported_backend"`.
- **Webhook env vars:**
  - `HITL_WEBHOOK_URL`: HTTPS only, unless `HITL_WEBHOOK_ALLOW_HTTP=1`.
  - `HITL_WEBHOOK_SECRET`: required.
  - If either is missing, the result is `delivered=false, reason="misconfigured"`.
- **Delivery rules:**
  - signature header `X-Handoff-Signature: sha256=<hex HMAC-SHA256 of the raw body>`;
  - timeout 3.0 s; one retry on a timeout or 5xx;
  - `reason` is one of `delivered | log_only | misconfigured | timeout | http_<code> | error | unsupported_backend`.
- **The payload is built from session state only.** `summary` holds the last `handoff.summary_turns` (default 6) exchanges, each field capped at 300 characters.
- **Never log the payload, URL or secret.** Logs carry only `ticket_id`, `delivered`, `reason` and `latency_ms`.
- **At most one delivered handoff per call.** After a delivered handoff, a second `human_request` speaks the `already` line and sends no new escalation. After a failed one, a second request may try again.
- **Spoken lines come from config, never from the LLM:** `handoff.lines.{delivered, failed, already}`, and `identity.no_handoff_line`.
- **Runtime↔dev-kit sync** (`.claude/rules/runtime-devkit-sync.md`): every runtime schema change updates, in the same commit:
  - the domain mirror;
  - FIELD_RULES;
  - the flat `dev-kit/dev_kit/schema.py`;
  - `DOMAIN_SECTION_SCHEMAS`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push.
- **Git safety.**
  - Never run `git stash`, `git reset --hard`, `git clean` or `git add -A`/`git add .`. Add files by path only.
  - An untracked stray file `docs/superpowers/specs/2026-09-09-voicera-integration-analysis.md` may exist; leave it.
- **Test commands** (counts before Task 1: agent_core 1383 passed / 1 skipped):
  - agent_core: `cd agent_core && uv run pytest -q`
  - trust_layer: `cd trust_layer && uv run --extra dev pytest -q` (159 passed, 1 pre-existing failure `test_server.py::test_check_consent_returns_granted`)
  - dev-kit: `cd dev-kit && uv run pytest -q` (1 pre-existing failure `test_dpg_yaml_validates[reach_layer]`)

## Review Focus

1. **A webhook that answers 2xx after the timeout, or only on the retry.** Expected: `delivered=true` only if the response was received within an attempt. A late success after we gave up must not be counted. Tested in Task 2.
2. **The caller asks for a human twice in one call, or after a failed delivery.** Expected:
   - after `delivered`, the second request gets the `already` line with no new escalation;
   - after `failed`, a second request may retry once more, because the spec only caps successful handoffs; the cap is one *delivered* handoff per call.
   Tested in Task 6.
3. **`human_request` when the use case has `human_handoff: none`, or the `handoff` phase is missing from the workflow.** Expected: no escalation and no routing change; the `<identity>` rule answers with `no_handoff_line`. Tested in Task 6.
4. **Payload text that contains newlines, Devanagari or very long turns.** Expected: valid UTF-8 JSON with fields capped at 300 characters, and the HMAC computed over the exact bytes sent. Tested in Tasks 2 and 5.
5. **Trust Layer unreachable from Agent Core.** The client already returns `{"queued": False}`. Expected: Agent Core treats a missing `delivered` key as failed, speaks the `failed` line, and the turn never errors. Tested in Task 6.

---

## File Structure

| File | Responsibility |
|---|---|
| `trust_layer/src/blocks/hitl.py` | `escalate()` accepts `handoff`, returns `delivered` and `reason`; the backends are dispatched here. |
| `trust_layer/src/blocks/hitl_webhook.py` (new) | `deliver_webhook(payload: dict, *, url, secret, allow_http, timeout_s, client) -> tuple[bool, str]`: signing, timeout, retry. |
| `trust_layer/src/models.py`, `trust_layer/src/server.py`, `trust_layer/src/orchestrator.py` | Thread the optional `handoff` field and the `delivered`/`reason` fields through. |
| `agent_core/src/interfaces/{trust_layer.py, async_/trust_layer.py}`, `agent_core/src/http_clients/{trust_layer.py, async_/trust_layer.py}` | `escalate(..., handoff: dict \| None = None)`. |
| `agent_core/src/schema/config.py` | `IdentityConfig`, `HandoffLines`, `HandoffConfig`; `MergedConfig.identity` and `.handoff`. |
| `agent_core/src/identity.py` (new) | `render_identity(identity: dict \| None) -> str`, the `<identity>` body. |
| `agent_core/src/handoff.py` (new) | `build_handoff_payload(...) -> dict` and `choose_handoff_line(...) -> tuple[str, str]`. These are pure functions. |
| `agent_core/src/manager_agent.py`, `agent_core/main.py` | The `identity` kwarg, and the tier 1 `<identity>` block. |
| `agent_core/src/orchestrator.py` | The `human_request` pre-routing handler, in both the sync and stream paths. |
| `dev-kit/configs/blue-dots/agent_core.yaml` | The `identity` block, the `handoff` block, the `handoff` subagent, the `human` topic plus act-intent row and NLU examples. The hard-coded disclosure rule is removed. |
| `dev-kit/dev_kit/schemas/domain/agent_core.py`, `dev-kit/dev_kit/schemas/validation.py`, `dev-kit/dev_kit/agent/field_rules/agent_core.py`, `dev-kit/dev_kit/schema.py` | The sync mirrors. |
| `agent_core/eval/nlu/cases/scenarios.jsonl` | NLU replay cases for `human_request` and identity questions. |

---

### Task 1: Trust escalate carries a handoff and reports delivery

**Files:**
- Modify:
  - `trust_layer/src/models.py:153-184`
  - `trust_layer/src/blocks/hitl.py:51-140`
  - `trust_layer/src/orchestrator.py:144`
  - `trust_layer/src/server.py:248-280`
- Test:
  - `trust_layer/tests/blocks/test_hitl.py`
  - `trust_layer/tests/test_server.py`

**Interfaces:**
- **Produces:**
  - `HiTLBlock.escalate(session_id: str, escalation_reason: str, user_message: str, workflow_step: str, handoff: dict | None = None) -> dict`. The returned dict has keys `queued: bool`, `delivered: bool`, `reason: str`, `ticket_id: str` and `holding_message: str`.
  - `TrustLayer.escalate(...)` takes the same parameters, including `handoff`.
  - `HiTLEscalateRequest.handoff: dict | None = None`.
  - `HiTLEscalateResponse` gains `delivered: bool = False` and `reason: str = ""`.
  - The hook `HiTLBlock._deliver(ticket_id: str, handoff: dict | None) -> tuple[bool, str]`. Task 2 fills in the webhook branch.

- [ ] **Step 1: Write the failing tests** (append to `trust_layer/tests/blocks/test_hitl.py`)

```python
def _cfg(backend="log"):
    return {"trust": {"hitl": {"queue_backend": backend, "holding_message": "hold", "notification_webhook": None}}}


def test_log_backend_is_queued_but_not_delivered():
    r = HiTLBlock(_cfg("log")).escalate("s1", "human_request", "msg", "job_match", handoff={"a": 1})
    assert r["queued"] is True and r["delivered"] is False and r["reason"] == "log_only"
    assert r["ticket_id"].startswith("TKT-")


def test_unsupported_backend_is_not_queued():
    r = HiTLBlock(_cfg("redis")).escalate("s1", "human_request", "msg", "job_match")
    assert r["queued"] is False and r["delivered"] is False and r["reason"] == "unsupported_backend"


def test_payload_is_never_logged(caplog):
    caplog.set_level("DEBUG")
    HiTLBlock(_cfg("log")).escalate("s1", "human_request", "msg", "job_match",
                                    handoff={"caller": {"phone": "919900001000"}})
    assert "919900001000" not in caplog.text
```

In `trust_layer/tests/test_server.py`, next to `test_escalate_returns_ticket`, add:

```python
def test_escalate_passes_handoff_and_returns_delivery(client):
    r = client.post("/escalate", json={"session_id": "s1", "escalation_reason": "human_request",
                                       "user_message": "m", "workflow_step": "job_match",
                                       "handoff": {"ticket_hint": 1}})
    body = r.json()
    assert r.status_code == 200 and body["delivered"] is False and body["reason"] == "log_only"
```

Use the existing `client` fixture. If its config has no `hitl` block, build a client the way `test_escalate_returns_ticket` does around line 239.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd trust_layer && uv run --extra dev pytest -q tests/blocks/test_hitl.py tests/test_server.py -k "delivered or unsupported or never_logged or passes_handoff"`
Expected: FAIL with `KeyError: 'delivered'`, `TypeError: unexpected keyword 'handoff'`, or a 422.

- [ ] **Step 3: Implement**

`models.py`:

```python
class HiTLEscalateRequest(BaseModel):
    session_id: str
    escalation_reason: str
    user_message: str
    workflow_step: str
    handoff: dict | None = None


class HiTLEscalateResponse(BaseModel):
    queued: bool
    ticket_id: str
    holding_message: str
    delivered: bool = False
    reason: str = ""
```

`hitl.py`. In `escalate`:
- add `handoff: dict | None = None`;
- replace `self._write_to_queue(...)` with `queued, delivered, reason = self._deliver(ticket_id, session_id, escalation_reason, workflow_step, handoff)`;
- return `{"queued": queued, "delivered": delivered, "reason": reason, "ticket_id": ticket_id, "holding_message": self._holding_message}`;
- in the existing `hitl_block.escalated` log extras, add `delivered` and `reason`, but never `handoff`.

```python
    def _deliver(self, ticket_id: str, session_id: str, escalation_reason: str,
                 workflow_step: str, handoff: dict | None) -> tuple[bool, bool, str]:
        """Write to the configured backend. Returns (queued, delivered, reason)."""
        if self._queue_backend == "log":
            logger.warning("hitl_block.escalation_queued", extra={
                "operation": "hitl_block.queue_write", "status": "success", "ticket_id": ticket_id,
                "session_id": session_id, "escalation_reason": escalation_reason,
                "workflow_step": workflow_step})
            return True, False, "log_only"
        logger.warning("hitl_block.unsupported_backend", extra={
            "operation": "hitl_block.queue_write", "status": "skipped", "ticket_id": ticket_id,
            "backend": self._queue_backend})
        return False, False, "unsupported_backend"
```

Delete `_write_to_queue` and the old `TODO(GH-hitl)` comment block that claimed `queued=True`. Update its unit test, if one exists, to call `_deliver`.

- **`orchestrator.py:144`:** add `handoff: dict | None = None` and pass it on.
- **`server.py`:** pass `request.handoff`. In the exception fallback, return `HiTLEscalateResponse(queued=False, ticket_id="", holding_message="", delivered=False, reason="error")`.

- [ ] **Step 4: Run the trust_layer suite**

Run: `cd trust_layer && uv run --extra dev pytest -q`
Expected: the new tests pass. The only failure is the pre-existing `test_check_consent_returns_granted`.

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/models.py trust_layer/src/blocks/hitl.py trust_layer/src/orchestrator.py trust_layer/src/server.py trust_layer/tests/blocks/test_hitl.py trust_layer/tests/test_server.py
git commit -m "feat(trust_layer): escalate carries a handoff and reports delivery; unsupported backends are not queued"
```

---

### Task 2: Webhook queue backend

**Files:**
- Create: `trust_layer/src/blocks/hitl_webhook.py`
- Modify: `trust_layer/src/blocks/hitl.py` (the `_deliver` webhook branch, and the constructor reading env)
- Test: `trust_layer/tests/blocks/test_hitl_webhook.py`

**Interfaces:**
- **Consumes:** `HiTLBlock._deliver` from Task 1.
- **Produces:**
  - `deliver_webhook(body: bytes, *, url: str, secret: str, timeout_s: float = 3.0, client: httpx.Client | None = None) -> tuple[bool, str]`, which returns `(delivered, reason)`;
  - `sign(body: bytes, secret: str) -> str`, which returns `"sha256=<hex>"`;
  - `webhook_settings(environ: Mapping[str, str]) -> tuple[str | None, str | None, bool]`, which returns `(url, secret, allow_http)`.

- [ ] **Step 1: Write the failing tests**

```python
# trust_layer/tests/blocks/test_hitl_webhook.py
import hashlib
import hmac
import json

import httpx

from trust_layer.src.blocks.hitl import HiTLBlock
from trust_layer.src.blocks.hitl_webhook import deliver_webhook, sign, webhook_settings

SECRET = "s3cret"
BODY = json.dumps({"caller": {"name": "रमेश"}, "summary": [{"caller": "a\nb"}]}, ensure_ascii=False).encode()


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sign_is_hmac_sha256_of_raw_body():
    assert sign(BODY, SECRET) == "sha256=" + hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()


def test_2xx_is_delivered_and_signed():
    seen = {}

    def h(req):
        seen["sig"], seen["body"] = req.headers["X-Handoff-Signature"], req.content
        return httpx.Response(202)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (True, "delivered")
    assert seen["body"] == BODY and seen["sig"] == sign(BODY, SECRET)


def test_5xx_retries_once_then_fails():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "http_503")
    assert len(calls) == 2


def test_4xx_does_not_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "http_400")
    assert len(calls) == 1


def test_timeout_then_success_on_retry_is_delivered():
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (True, "delivered")


def test_timeout_twice_is_timeout():
    def h(req):
        raise httpx.ReadTimeout("slow")

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "timeout")


def test_settings_require_https_unless_allowed():
    assert webhook_settings({"HITL_WEBHOOK_URL": "http://x", "HITL_WEBHOOK_SECRET": "s"}) == (None, "s", False)
    assert webhook_settings({"HITL_WEBHOOK_URL": "http://x", "HITL_WEBHOOK_SECRET": "s",
                             "HITL_WEBHOOK_ALLOW_HTTP": "1"}) == ("http://x", "s", True)
    assert webhook_settings({}) == (None, None, False)


def test_block_webhook_misconfigured_without_env(monkeypatch):
    monkeypatch.delenv("HITL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HITL_WEBHOOK_SECRET", raising=False)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    r = HiTLBlock(cfg).escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is False and r["delivered"] is False and r["reason"] == "misconfigured"


def test_block_webhook_delivers(monkeypatch):
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://x/hook")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    blk = HiTLBlock(cfg, http_client=_client(lambda req: httpx.Response(200)))
    r = blk.escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is True and r["delivered"] is True and r["reason"] == "delivered"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd trust_layer && uv run --extra dev pytest -q tests/blocks/test_hitl_webhook.py`
Expected: FAIL with `ModuleNotFoundError: trust_layer.src.blocks.hitl_webhook`.

- [ ] **Step 3: Implement**

```python
# trust_layer/src/blocks/hitl_webhook.py
"""HiTL webhook delivery: HTTPS POST of a signed handoff payload (spec §5.3)."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

import httpx

TIMEOUT_S = 3.0


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the exact request body, as sent in X-Handoff-Signature."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def webhook_settings(environ: Mapping[str, str]) -> tuple[str | None, str | None, bool]:
    """(url, secret, allow_http) from env; url is None when missing or not HTTPS (unless allowed)."""
    allow_http = environ.get("HITL_WEBHOOK_ALLOW_HTTP") == "1"
    url = (environ.get("HITL_WEBHOOK_URL") or "").strip() or None
    if url and not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
        url = None
    secret = environ.get("HITL_WEBHOOK_SECRET") or None
    return url, secret, allow_http


def deliver_webhook(body: bytes, *, url: str, secret: str, timeout_s: float = TIMEOUT_S,
                    client: httpx.Client | None = None) -> tuple[bool, str]:
    """POST once, retry once on timeout/5xx. Returns (delivered, reason). Never raises."""
    headers = {"Content-Type": "application/json; charset=utf-8", "X-Handoff-Signature": sign(body, secret)}
    own = client is None
    cl = client or httpx.Client(timeout=timeout_s)
    reason = "error"
    try:
        for _attempt in range(2):
            try:
                r = cl.post(url, content=body, headers=headers, timeout=timeout_s)
            except httpx.TimeoutException:
                reason = "timeout"
                continue
            except httpx.HTTPError:
                return False, "error"
            if 200 <= r.status_code < 300:
                return True, "delivered"
            reason = f"http_{r.status_code}"
            if r.status_code < 500:
                return False, reason
        return False, reason
    finally:
        if own:
            cl.close()
```

In `hitl.py`:
- **Constructor:** change it to `__init__(self, config: dict, http_client: httpx.Client | None = None)`. Store `self._http = http_client`.
- **`_deliver`:** before the unsupported-backend fallback, add:

```python
        if self._queue_backend == "webhook":
            url, secret, _ = webhook_settings(os.environ)
            if not url or not secret:
                return False, False, "misconfigured"
            body = json.dumps({"ticket_id": ticket_id, **(handoff or {})}, ensure_ascii=False).encode("utf-8")
            delivered, reason = deliver_webhook(body, url=url, secret=secret, client=self._http)
            return True, delivered, reason
```

Add the imports `import json, os` and `from trust_layer.src.blocks.hitl_webhook import deliver_webhook, webhook_settings`, matching the existing import style in the file; check the import path used by `hitl.py` itself.

- [ ] **Step 4: Run the trust_layer suite**

Run: `cd trust_layer && uv run --extra dev pytest -q`
Expected: all new tests pass. The only failure is the pre-existing one.

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/blocks/hitl_webhook.py trust_layer/src/blocks/hitl.py trust_layer/tests/blocks/test_hitl_webhook.py
git commit -m "feat(trust_layer): signed HTTPS webhook backend for HiTL handoffs"
```

---

### Task 3: Agent Core trust clients accept a handoff

**Files:**
- Modify:
  - `agent_core/src/interfaces/trust_layer.py:65`
  - `agent_core/src/interfaces/async_/trust_layer.py:43-50`
  - `agent_core/src/http_clients/trust_layer.py:345`
  - `agent_core/src/http_clients/async_/trust_layer.py:272-318`
- Test:
  - `agent_core/tests/test_http_clients.py`, or the existing async trust client test file; find it with `grep -rln "escalate" agent_core/tests`.

**Interfaces:**
- **Produces:** `async def escalate(self, session_id: str, escalation_reason: str, user_message: str, workflow_step: str, handoff: dict | None = None) -> dict`. The sync client has the same signature.
  - The JSON body includes `"handoff": handoff` only when it is not `None`.
  - The failure dict `_ESCALATE_FAILED` gains `"delivered": False, "reason": "unreachable"`.

- [ ] **Step 1: Write the failing test.** Use the file's existing httpx mock style, for example `respx` or `httpx.MockTransport`, and whatever that file already uses:

```python
async def test_async_escalate_sends_handoff_and_returns_delivery(...):
    # mock POST {endpoint}/escalate → 200 {"queued": true, "delivered": true, "reason": "delivered",
    #                                       "ticket_id": "TKT-1", "holding_message": ""}
    r = await client.escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["delivered"] is True
    assert captured_json["handoff"] == {"x": 1}


async def test_async_escalate_unreachable_is_not_delivered(...):
    # mock raises httpx.ConnectError twice
    r = await client.escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is False and r["delivered"] is False and r["reason"] == "unreachable"
```

Write these concretely in the file's existing fixture style. Copy the setup of its existing escalate or check_output tests verbatim and change only the endpoint, the body and the asserts. Add the matching sync test.

- [ ] **Step 2: Run to verify it fails.** Run: `cd agent_core && uv run pytest -q -k escalate`. Expected: FAIL with `unexpected keyword argument 'handoff'` or a missing `delivered`.

- [ ] **Step 3: Implement.** Add the parameter to both abstract interfaces and both clients. Build the body as `{"session_id": ..., "escalation_reason": ..., "user_message": user_message or "", "workflow_step": ..., **({"handoff": handoff} if handoff is not None else {})}`, and extend `_ESCALATE_FAILED`. Update any fake trust classes in `agent_core/tests/fakes.py` that implement `escalate` so they accept `handoff=None`.

- [ ] **Step 4: Run the agent_core suite.** Run: `cd agent_core && uv run pytest -q`. Expected: all pass (1383 plus the new ones).

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/interfaces/trust_layer.py agent_core/src/interfaces/async_/trust_layer.py agent_core/src/http_clients/trust_layer.py agent_core/src/http_clients/async_/trust_layer.py agent_core/tests/<the test file> agent_core/tests/fakes.py
git commit -m "feat(agent_core): trust escalate client carries a handoff payload"
```

---

### Task 4: `identity` and `handoff` config blocks (with dev-kit sync)

**Files:**
- Modify:
  - `agent_core/src/schema/config.py` (classes above `MergedConfig` at 966; fields after line 1009)
  - `dev-kit/dev_kit/schemas/domain/agent_core.py` (next to `HitlSection` around 805)
  - `dev-kit/dev_kit/schemas/validation.py:35-62`
  - `dev-kit/dev_kit/agent/field_rules/agent_core.py` (near the `hitl.response_message` rule around 496)
  - `dev-kit/dev_kit/schema.py` (near `HitlConfig` at 457 and the merged class around 784)
- Test:
  - `agent_core/tests/test_schema_config.py` (or the file holding `MergedConfig` tests; find it with `grep -rln "MergedConfig" agent_core/tests`)
  - `dev-kit/tests/schemas/domain/test_agent_core.py`

**Interfaces:**
- **Produces:**
  - `IdentityConfig` (frozen, extra=forbid):
    - `name: str = Field(min_length=1)`
    - `kind: Literal["ai_assistant"] = "ai_assistant"`
    - `operator: str = Field(min_length=1)`
    - `disclosure: str = Field(min_length=1)`
    - `human_handoff: Literal["none", "request"] = "none"`
    - `no_handoff_line: str = Field(min_length=1)`
  - `HandoffLines`: `delivered: str`, `failed: str`, `already: str` (each `min_length=1`).
  - `HandoffConfig`: `lines: HandoffLines`, `summary_turns: int = Field(default=6, ge=1, le=20)`.
  - `MergedConfig.identity: Optional[IdentityConfig] = None` and `MergedConfig.handoff: Optional[HandoffConfig] = None`.
  - **Validator:** if `identity.human_handoff == "request"`, both `handoff` and a workflow subagent with id `handoff` must exist. Raise `ValueError("identity.human_handoff=request needs a handoff block and a 'handoff' subagent")`.

- [ ] **Step 1: Write the failing tests**

```python
# in agent_core's MergedConfig test file (reuse its minimal valid config helper/fixture)
import pytest
from pydantic import ValidationError

IDENT = {"name": "ब्लू डॉट्स सहायक", "operator": "Blue Dots",
         "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "no_handoff_line": "अभी कोई इंसान उपलब्ध नहीं है।"}
LINES = {"lines": {"delivered": "d", "failed": "f", "already": "a"}}


def test_identity_optional_and_defaults(minimal_config):
    cfg = MergedConfig.model_validate({**minimal_config, "identity": IDENT})
    assert cfg.identity.human_handoff == "none" and cfg.identity.kind == "ai_assistant"
    assert MergedConfig.model_validate(minimal_config).identity is None


def test_request_requires_handoff_block_and_subagent(minimal_config):
    with pytest.raises(ValidationError, match="human_handoff=request"):
        MergedConfig.model_validate({**minimal_config, "identity": {**IDENT, "human_handoff": "request"}})


def test_handoff_summary_turns_bounds(minimal_config):
    with pytest.raises(ValidationError):
        MergedConfig.model_validate({**minimal_config, "handoff": {**LINES, "summary_turns": 0}})
```

If the file has no `minimal_config` fixture, build the minimal dict the way its existing tests do and name it explicitly. Then add dev-kit tests: `IdentitySection` accepts `IDENT` and rejects an empty `disclosure`, and `HandoffSection` accepts `LINES`.

- [ ] **Step 2: Run to verify they fail.** Run: `cd agent_core && uv run pytest -q -k "identity or handoff"`. Expected: FAIL on the unknown field `identity` (extra=forbid).

- [ ] **Step 3: Implement** the three runtime classes and the validator branch inside the existing `@model_validator(mode="after")` at around 1011. Mirror them for dev-kit:
  - `IdentitySection` and `HandoffSection` in `schemas/domain/agent_core.py`, using the same shape without `frozen`;
  - `("agent_core","identity")` and `("agent_core","handoff")` entries in `DOMAIN_SECTION_SCHEMAS`;
  - FIELD_RULES `identity.*` and `handoff.*`, with category `chat`, phase `language` (like `hitl.response_message`), `applies_if="has_hitl"` and the `pydantic_class` names;
  - in the flat `schema.py`, `identity: Optional[dict] = None` and `handoff: Optional[dict] = None`, like `session_bootstrap`.

- [ ] **Step 4: Run the agent_core and dev-kit suites.** Expected: all pass, except dev-kit's pre-existing `test_dpg_yaml_validates[reach_layer]`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/schema/config.py dev-kit/dev_kit/schemas/domain/agent_core.py dev-kit/dev_kit/schemas/validation.py dev-kit/dev_kit/agent/field_rules/agent_core.py dev-kit/dev_kit/schema.py <the two test files>
git commit -m "feat(config): identity and handoff blocks, mirrored in dev-kit"
```

---

### Task 5: `<identity>` prompt block and the handoff payload builder

**Files:**
- Create:
  - `agent_core/src/identity.py`
  - `agent_core/src/handoff.py`
- Modify:
  - `agent_core/src/manager_agent.py:270-281` (constructor) and `:735-740` (tier 1)
  - `agent_core/main.py:210-222`
- Test:
  - `agent_core/tests/test_identity_handoff.py` (new)
  - `agent_core/tests/test_manager_agent.py`

**Interfaces:**
- **Produces:**
  - `render_identity(identity: dict | None) -> str`, which returns `""` when it gets `None`.
  - `ManagerAgent(..., identity: dict | None = None)`.
  - Tier 1 becomes `join([xml("persona", …), xml("identity", render_identity(self._identity)), xml("channel_rules", …), xml("session_end_policy", …)])`.
  - `build_handoff_payload(*, ticket_hint: str, use_case: str, session: dict, phone: str, call_id: str, last_caller_turn: str, summary_turns: int, now_iso: str) -> dict`.
  - `choose_handoff_line(result: dict | None, lines: dict, already: bool) -> tuple[str, str]`, which returns `(line, outcome)`, where outcome is one of `delivered | failed | already`.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/test_identity_handoff.py
import json

from src.handoff import build_handoff_payload, choose_handoff_line
from src.identity import render_identity

IDENT = {"name": "ब्लू डॉट्स सहायक", "kind": "ai_assistant", "operator": "Blue Dots",
         "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "human_handoff": "none",
         "no_handoff_line": "अभी इस कॉल पर कोई इंसान उपलब्ध नहीं है।"}
LINES = {"delivered": "D", "failed": "F", "already": "A"}


def test_render_identity_none_and_rules():
    assert render_identity(None) == ""
    out = render_identity(IDENT)
    assert "ब्लू डॉट्स सहायक" in out and IDENT["disclosure"] in out and IDENT["no_handoff_line"] in out
    assert "Never claim to be human" in out


def test_render_identity_request_mode_omits_no_handoff_line():
    out = render_identity({**IDENT, "human_handoff": "request"})
    assert IDENT["no_handoff_line"] not in out and "handoff" in out.lower()


def test_payload_from_session_only_and_capped():
    long = "क" * 500
    session = {"name": "रमेश", "stored_trade": "Electrician", "stored_location": "Lucknow",
               "current_subagent_id": "job_match", "detected_language": "hi",
               "recent_turns": json.dumps([{"caller": f"c{i}\n{long}", "bot": f"b{i}"} for i in range(10)]),
               "last_application_id": "a-1", "applications_submitted": 1}
    p = build_handoff_payload(ticket_hint="", use_case="blue-dots", session=session, phone="919900001000",
                              call_id="c1", last_caller_turn=long, summary_turns=6, now_iso="2026-10-02T12:00:00Z")
    assert p["caller"] == {"phone": "919900001000", "name": "रमेश", "language": "hi"}
    assert p["context"]["step"] == "job_match" and p["context"]["trade"] == "Electrician"
    assert len(p["summary"]) == 6 and p["summary"][-1]["bot"] == "b9"
    assert all(len(t["caller"]) <= 300 and len(t["bot"]) <= 300 for t in p["summary"])
    assert len(p["context"]["last_caller_turn"]) <= 300
    assert p["reason"] == "human_request" and p["use_case"] == "blue-dots" and p["call_id"] == "c1"
    json.dumps(p, ensure_ascii=False)                 # serialisable


def test_payload_tolerates_missing_or_bad_recent_turns():
    p = build_handoff_payload(ticket_hint="", use_case="u", session={"recent_turns": "not json"}, phone="91",
                              call_id="", last_caller_turn="", summary_turns=6, now_iso="t")
    assert p["summary"] == [] and p["caller"]["name"] == ""


def test_choose_line():
    assert choose_handoff_line({"delivered": True}, LINES, already=False) == ("D", "delivered")
    assert choose_handoff_line({"queued": False}, LINES, already=False) == ("F", "failed")
    assert choose_handoff_line(None, LINES, already=False) == ("F", "failed")
    assert choose_handoff_line(None, LINES, already=True) == ("A", "already")
```

In `test_manager_agent.py`, extend `_make_manager_for_prompt` so it accepts `identity=`. Add:

```python
def test_tier1_has_identity_block_when_configured():
    mgr = _make_manager_for_prompt(identity=IDENT)          # IDENT as above
    sp = mgr.build_system_prompt(...)                        # same args as test_build_system_prompt_tier1_has_cache_hint
    assert "<identity>" in sp.blocks[0].text and IDENT["disclosure"] in sp.blocks[0].text


def test_no_identity_block_when_absent():
    sp = _make_manager_for_prompt().build_system_prompt(...)
    assert "<identity>" not in _flat(sp)
```

- [ ] **Step 2: Run to verify they fail.** Run: `cd agent_core && uv run pytest -q tests/test_identity_handoff.py tests/test_manager_agent.py -k "identity or payload or choose_line"`. Expected: `ModuleNotFoundError: src.handoff`.

- [ ] **Step 3: Implement**

```python
# agent_core/src/identity.py
"""<identity> prompt block from the use-case identity config (identity/handoff spec §3.2)."""
from __future__ import annotations


def render_identity(identity: dict | None) -> str:
    """Body of the <identity> block, or '' when the use case has no identity config."""
    if not identity:
        return ""
    lines = [f"You are {identity['name']}, an AI assistant run by {identity['operator']}.",
             f"Disclosure line (say it verbatim, one sentence): {identity['disclosure']}",
             "When the caller asks who you are or who they are talking to, asks whether you are a human or "
             "a computer, or asks to speak to a person: say the disclosure line, then "]
    if identity.get("human_handoff", "none") == "request":
        lines[-1] += ("let the handoff flow handle a request for a person (the system speaks it); "
                      "then return to the open question.")
    else:
        lines[-1] += f"say verbatim: {identity['no_handoff_line']} Then return to the open question."
    lines.append("Never claim to be human. Never promise a callback, a counsellor or a person unless the "
                 "handoff step has reported success in this call.")
    return "\n".join(lines)
```

```python
# agent_core/src/handoff.py
"""Human handoff payload and spoken line (identity/handoff spec §4–§5). Pure functions; session data only."""
from __future__ import annotations

import json

CAP = 300


def _cap(v: object) -> str:
    return str(v or "")[:CAP]


def _recent(raw: object, n: int) -> list[dict]:
    try:
        turns = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (ValueError, TypeError):
        return []
    if not isinstance(turns, list):
        return []
    return [{"caller": _cap(t.get("caller")), "bot": _cap(t.get("bot"))}
            for t in turns[-n:] if isinstance(t, dict)]


def build_handoff_payload(*, ticket_hint: str, use_case: str, session: dict, phone: str, call_id: str,
                          last_caller_turn: str, summary_turns: int, now_iso: str) -> dict:
    """Handoff JSON for the webhook. ``ticket_hint`` is unused by Agent Core (Trust assigns the ticket)."""
    apps = []
    if str(session.get("last_application_id") or ""):
        apps.append({"application_id": _cap(session.get("last_application_id")), "status": "submitted"})
    return {
        "use_case": use_case, "reason": "human_request", "created_at": now_iso, "call_id": call_id,
        "caller": {"phone": phone, "name": _cap(session.get("name")),
                   "language": _cap(session.get("detected_language") or "")},
        "context": {"step": _cap(session.get("current_subagent_id")), "last_caller_turn": _cap(last_caller_turn),
                    "trade": _cap(session.get("stored_trade") or session.get("trade")),
                    "location": _cap(session.get("stored_location") or session.get("location")),
                    "applications": apps},
        "summary": _recent(session.get("recent_turns"), summary_turns),
    }


def choose_handoff_line(result: dict | None, lines: dict, already: bool) -> tuple[str, str]:
    """(line, outcome) — outcome is delivered | failed | already."""
    if already:
        return lines["already"], "already"
    if result and result.get("delivered") is True:
        return lines["delivered"], "delivered"
    return lines["failed"], "failed"
```

Before relying on the `recent_turns` element keys (`caller` and `bot`), check them against the writer near `orchestrator.py:3309`. If they differ, adapt `_recent` and the test.

- **`manager_agent.py`:** add the `identity` kwarg (store `self._identity = identity`) and the tier 1 `xml("identity", render_identity(self._identity))`.
- **`main.py`:** pass `identity=config.get("identity")`.

- [ ] **Step 4: Run the agent_core suite.** Expected: all pass. The balanced-XML and elision tests stay green.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/identity.py agent_core/src/handoff.py agent_core/src/manager_agent.py agent_core/main.py agent_core/tests/test_identity_handoff.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent_core): <identity> prompt block and handoff payload builder"
```

---

### Task 6: `human_request` handoff turn in the orchestrator (sync and stream)

**Files:**
- Modify:
  - `agent_core/src/orchestrator.py`: the sync path, next to the language-switch handler at about 1110; the stream path, next to its twin at about 4065. Read `fixed_opening` at 4194-4241 for how a fixed line is spoken (`_stream_termination_short_circuit(..., end_session=False)`).
  - `agent_core/src/schema/config.py:494`: `_FRAMEWORK_HANDLED_INTENTS` adds `"human_request"`.
- Test: `agent_core/tests/test_handoff_turn.py` (new). Copy the `stream_turn` real-workflow fixture style D3 used; find it with `grep -rln "confirm_close" agent_core/tests`.

**Interfaces:**
- **Consumes:**
  - `build_handoff_payload` and `choose_handoff_line` (Task 5);
  - `self._async_trust.escalate(..., handoff=)` (Task 3);
  - `config["identity"]` and `config["handoff"]` (Task 4).
- **Produces:**
  - `async def _handle_human_request_async(self, session_id, user_id, bundle, turn_input) -> str | None`, which returns the line to speak, or `None` when handoff is not enabled. Its sync twin is `_handle_human_request_sync`.
  - **Session writes:**
    - `handoff_status` (`delivered` / `failed`), and `handoff_ticket_id`;
    - `close_return_to`, which is the current subagent;
    - `current_subagent_id = "handoff"`.
  - **Signal:** `emit_signal("handoff", {"session_id", "turn_id", "timestamp_ms", "ticket_id", "outcome", "reason", "latency_ms"})`.

**Behaviour** (all of this must be covered by the tests):
1. The handler runs only when all of these hold:
   - `nlu_result.intent == "human_request"`;
   - `identity.human_handoff == "request"`;
   - a `handoff` block exists;
   - the workflow has a `handoff` subagent.
   Otherwise it returns `None`, and the turn continues normally: routing applies and the `<identity>` rule answers.
2. **`already` case:** if `bundle.session.get("handoff_status") == "delivered"`, there is no escalate call. Speak `lines.already`, write `close_return_to` and route to `handoff`.
3. **Otherwise:**
   - build the payload with `phone=user_id`, `call_id=session_id`, `use_case=<the domain slug; find where agent_core exposes it, e.g. config['observability']['domain'] or the DOMAIN env — check and use the existing source, never hard-code>` and `summary_turns=handoff.summary_turns`, using UTC now in ISO format;
   - `await escalate(session_id, "human_request", turn_input.user_message, current_subagent_id, handoff=payload)`, wrapped in try/except so that any exception means a failed result;
   - pick the line with `choose_handoff_line`;
   - write the session fields;
   - emit the signal with `self._async_learning` (guarded `if self._async_learning:`) on the stream path, or `self._learning` in a try/except on the sync path;
   - speak the line through the same fixed-line mechanism with `end_session=False`, and skip the LLM.
4. **After the turn:** the `handoff` subagent's pending `close_confirm` (Task 7 config) drives the next turn via D3's routing. Not covered here; the routing is tested in Task 7.

- [ ] **Step 1: Write the failing tests** (stream path; mirror one for the sync path with `process_turn`). Build the orchestrator from the real blue-dots workflow plus a test override that sets `identity.human_handoff: request` and adds a minimal `handoff` subagent and `handoff` block, the way D3's tests construct theirs. Fake the NLU to return intent `human_request`.

```python
async def test_human_request_delivered_speaks_line_and_routes(orchestrator_with_handoff, fake_trust, fake_memory):
    fake_trust.escalate_result = {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-1"}
    text, done = await run_stream_turn(orchestrator_with_handoff, "इंसान से बात कराओ", subagent="job_match")
    assert text == HANDOFF_LINES["delivered"] and done.session_ended is False
    assert fake_trust.escalate_calls[0]["handoff"]["reason"] == "human_request"
    assert fake_memory.session["handoff_status"] == "delivered"
    assert fake_memory.session["close_return_to"] == "job_match"
    assert fake_memory.session["current_subagent_id"] == "handoff"
    assert orchestrator_with_handoff.llm_calls == 0


async def test_human_request_failed_when_trust_unreachable(orchestrator_with_handoff, fake_trust, fake_memory):
    fake_trust.escalate_result = {"queued": False}                   # no 'delivered' key
    text, _ = await run_stream_turn(orchestrator_with_handoff, "इंसान से बात कराओ", subagent="job_match")
    assert text == HANDOFF_LINES["failed"] and fake_memory.session["handoff_status"] == "failed"


async def test_human_request_exception_is_failed_not_error(orchestrator_with_handoff, fake_trust):
    fake_trust.escalate_raises = RuntimeError("boom")
    text, done = await run_stream_turn(orchestrator_with_handoff, "इंसान से बात कराओ", subagent="job_match")
    assert text == HANDOFF_LINES["failed"] and done.error_type in (None, "")


async def test_second_request_after_delivery_is_already_and_no_new_escalation(orchestrator_with_handoff, fake_trust, fake_memory):
    fake_memory.session["handoff_status"] = "delivered"
    text, _ = await run_stream_turn(orchestrator_with_handoff, "इंसान से बात कराओ", subagent="job_match")
    assert text == HANDOFF_LINES["already"] and fake_trust.escalate_calls == []


async def test_second_request_after_failure_retries(orchestrator_with_handoff, fake_trust, fake_memory):
    fake_memory.session["handoff_status"] = "failed"
    fake_trust.escalate_result = {"queued": True, "delivered": True, "reason": "delivered", "ticket_id": "TKT-2"}
    text, _ = await run_stream_turn(orchestrator_with_handoff, "इंसान से बात कराओ", subagent="job_match")
    assert text == HANDOFF_LINES["delivered"] and len(fake_trust.escalate_calls) == 1


async def test_handoff_disabled_does_not_escalate(orchestrator_default_blue_dots, fake_trust):
    await run_stream_turn(orchestrator_default_blue_dots, "इंसान से बात कराओ", subagent="job_match")
    assert fake_trust.escalate_calls == []
```

Define `HANDOFF_LINES`, `run_stream_turn` and the fixtures at the top of the test file. Model them on D3's real-workflow `stream_turn` test, copying its fixture code and changing only the overrides above. `fake_trust` must record `escalate` kwargs and return `escalate_result` (or raise `escalate_raises`).

- [ ] **Step 2: Run to verify they fail.** Run: `cd agent_core && uv run pytest -q tests/test_handoff_turn.py`. Expected: FAIL, because the line spoken is the LLM's, not the handoff line, and `escalate_calls` is empty.

- [ ] **Step 3: Implement.**
  - Add `"human_request"` to `_FRAMEWORK_HANDLED_INTENTS`.
  - Implement `_handle_human_request_async` and `_handle_human_request_sync` per the Behaviour section.
  - Call them right after the language-switch block, at the same position on each path. On the stream path, when a line is returned:
    1. gather the session writes with `self._async_memory.write(session_id, user_id, "session", k, v)`;
    2. update `bundle.session`;
    3. `async for ev in self._stream_termination_short_circuit(..., message=line, subagent_id="handoff", end_session=False): yield ev`, using the exact call shape at 4194-4241;
    4. `return`.

    On the sync path, mirror how the sync language switch returns a `TurnResult`.
  - Never log the payload. Log `orchestrator.handoff` with `outcome`, `reason`, `ticket_id` and `latency_ms`.

- [ ] **Step 4: Run the agent_core suite.** Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/src/schema/config.py agent_core/tests/test_handoff_turn.py
git commit -m "feat(agent_core): human_request hands off via Trust and speaks a fixed line"
```

---

### Task 7: Blue Dots config, NLU cases and the disclosure rule

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml`:
  - `topics` at about line 679;
  - `act_intents` at about 719-733;
  - the NLU examples for topics;
  - the persona prose (from about 809) and the hard-coded disclosure rule (about 1097-1101);
  - a new `identity` block and `handoff` block, at top level next to `hitl:` (about 778);
  - a new `handoff` subagent, next to `confirm_close` (about 3113).
- Modify: `agent_core/eval/nlu/cases/scenarios.jsonl`.
- Test:
  - `agent_core/tests/test_blue_dots_dialogue_act_config.py` (the existing blue-dots config test);
  - the D3 real-workflow test file.

**Interfaces:**
- **Consumes:** Tasks 4–6.
- **Produces:**
  - the Blue Dots `identity` block, with `human_handoff: none` as shipped;
  - a `handoff` block with the three lines from spec §4.2;
  - a `handoff` subagent: `is_terminal: false`, `tools: []`, `pending: [{id: close_confirm, expects: "हाँ = कॉल ख़त्म करें / नहीं = बात जारी रखें"}]`, and routing copied from `confirm_close`'s `close_return_to` rules;
  - topic `human` and the act-intent rows `{ acts: [request_change], topic: human, intent: human_request }` and `{ acts: [ask], topic: human, intent: human_request }`.

**Exact YAML to add** (top level):

```yaml
identity:
  name: "ब्लू डॉट्स सहायक"
  kind: ai_assistant
  operator: "Blue Dots"
  disclosure: "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।"
  human_handoff: none            # flip to "request" once a handoff receiver exists (HITL_WEBHOOK_URL)
  no_handoff_line: "अभी इस कॉल पर कोई इंसान उपलब्ध नहीं है — मैं ही आपकी मदद कर सकती हूँ।"

handoff:
  summary_turns: 6
  lines:
    delivered: "मैंने आपकी बात टीम तक पहुँचा दी है, वे आपसे संपर्क करेंगे। क्या मैं और कुछ मदद करूँ, या कॉल यहीं ख़त्म करूँ?"
    failed: "अभी मैं आपकी बात आगे नहीं भेज पाई — मैं ही आपकी मदद कर सकती हूँ। आप क्या जानना चाहते हैं?"
    already: "आपकी बात पहले ही टीम तक पहुँच चुकी है।"
```

- [ ] **Step 1: Write the failing tests.**
  - The blue-dots config test loads the merged config and asserts:
    - `identity.human_handoff == "none"`;
    - the `handoff` subagent exists and declares pending `close_confirm`;
    - `human_request` appears in `act_intents`;
    - the old hard-coded line "जी, मैं एक AI असिस्टेंट हूँ" no longer appears anywhere in the YAML;
    - the persona prose no longer hard-codes the assistant's name: `identity.name` is the only place.
  - In the D3 real-workflow test file, a routing test enables `human_handoff: request` through the test override. Given the `handoff` subagent with `close_return_to=job_match`:
    - affirm on `close_confirm` → `ended`, session_ended True;
    - deny → `job_match`.
  - **NLU eval cases** (append to `scenarios.jsonl`, one JSON object per line, same schema as existing cases):
    - `"इंसान से बात कराओ"` in `job_match` → intent `human_request`, topic `human`;
    - `"किसी आदमी से बात करनी है"` → `human_request`;
    - `"क्या आप कंप्यूटर हैं?"` → acts `[ask]`, topic `identity`, intent `any_input`;
    - `"आप कौन हैं?"` → topic `identity`, intent `any_input`;
    - `"मुझे किसी से पूछना है कि सैलरी कब मिलेगी"` → NOT `human_request` (expect topic `salary` or `process`, intent `any_input`).

    Include `test_eval_case_pending_matches_the_resolver` (from D1) in the run, so the new cases' pending is validated.

- [ ] **Step 2: Run to verify they fail.** Run: `cd agent_core && uv run pytest -q tests/test_blue_dots_dialogue_act_config.py -k "identity or handoff or human"`. Expected: FAIL (no identity block).

- [ ] **Step 3: Implement the YAML:**
  - add the two top-level blocks above;
  - add `human` to `topics`, the two `act_intents` rows, and NLU topic examples for `human` with the Hindi phrasings from the eval cases;
  - add the `handoff` subagent;
  - remove the hard-coded disclosure rule, and replace the name in the persona prose with "you" wording, because the identity block now carries the name;
  - keep the female first-person style.

  If `MergedConfig` validation requires `human_request` in routing, it is already covered by `_FRAMEWORK_HANDLED_INTENTS` (Task 6).

- [ ] **Step 4: Run the agent_core and dev-kit suites,** and the offline NLU case loader test. Expected: all pass, apart from the pre-existing dev-kit failure.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/eval/nlu/cases/scenarios.jsonl agent_core/tests/test_blue_dots_dialogue_act_config.py <the D3 real-workflow test file>
git commit -m "feat(blue-dots): identity block, handoff phase and human_request intent (handoff off by default)"
```

---

### Task 8: End-to-end verification

No new production code goes in this task. Any defect found is fixed in its owning task's files, with a regression test.

- [ ] **Step 1: Run the docker runtime check.** Rebuild the dev-kit image: `docker build -f dev-kit/Dockerfile -t dpg-dev-kit .`. Then run the dev-kit's baked runtime schema validation of `dev-kit/configs/blue-dots`, the way the earlier `runtime_baked` check was run: start the container with `-e OPENAI_API_KEY=placeholder-not-used` and validate the blue-dots configs through the deploy gate. Expected: valid.
- [ ] **Step 2: Run a live handoff with a local receiver.** This needs the controller, with user approval for the OpenAI calls.
  1. Run a tiny HTTPS-less receiver on the host with `HITL_WEBHOOK_ALLOW_HTTP=1`. It is a 20-line `http.server` handler that checks the signature and appends each body to a file.
  2. Bring up the stack with the voice-bench harness, with three changes:
     - a voice-bench patch that sets `identity.human_handoff: request`;
     - `trust.hitl.queue_backend: webhook`;
     - the three `HITL_WEBHOOK_*` envs on `trust_layer`.
  3. Run the T11 and T05 personas.

  Expected:
  - the receiver gets exactly one valid signed payload per handoff call;
  - the bot speaks the `delivered` line;
  - "हाँ" ends the call;
  - TC12 and TC14 pass.
- [ ] **Step 3: Rerun with `human_handoff: none`.** T05 and T11 should produce the `no_handoff_line`, and the receiver should get nothing.
- [ ] **Step 4: Write it up.** Record the results in the PR description. local_docs gets a short note.
