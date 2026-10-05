# Tool-Result Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store unexpired tool results in Memory Layer, show them to the LLM as `<known_facts>` so it can skip repeat calls, clear them on writes, and add a grounded `remember` tool for caller-chosen values.

**Architecture:**
- Memory Layer gets a `ToolResultStore`: one Redis key per entry, a write-time TTL, and an HMAC-pseudonymised owner.
- The store is returned in the context bundle and written through one batch endpoint.
- Agent Core builds a per-turn `TurnToolCache` from the bundle. It uses the cache to:
  - render `<known_facts>` in the prompt;
  - filter the tool replay;
  - serve hits before execution;
  - record live results and invalidations.
- A `RememberTool` validates grounding in Agent Core. Type and enum are validated in Memory Layer through a strict write.
- Action Gateway only adds a `projected` flag.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, redis-py ≥ 4.2, fakeredis (tests), pytest, `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-30-tool-result-persistence-design.md` — read it before starting.

## Global Constraints

- Baseline branch: `origin/deploy/voicera-vm`. At execution time, cut the implementation branch `feat/tool-result-persistence` from it, in a separate worktree (superpowers:using-git-worktrees). Never work in the user's `ai-diffusion-dpg` checkout.
- Run tests per module: `cd <module> && uv run pytest …`.
- Every new or changed public function, class and method gets a Google-style docstring (`.claude/rules/code-documentation.md`).
- Structured logs carry `operation`, `status` and, where relevant, `latency_ms`. Never log argument values, result data, phone numbers or raw `user_id` (`.claude/rules/logging-observability.md`).
- Coverage must stay ≥ 70% per module.
- Cache TTL is counted from the write and is never extended by a read.
- Scopes: `session | user` only. There is no cross-user scope.
- Keys: `ml:tr:{s|u}:{pseudonym}:{tool}:{args_hash}`. Index: `ml:tr:idx:{s|u}:{pseudonym}`. Pseudonym: `HMAC-SHA256(TOOL_RESULT_KEY_SECRET, id)[:32]`. If the secret is unset, the store is disabled: reads return `[]` and writes are no-ops.
- Only results with `projected=True` whose `result_text` parses as JSON are stored. Errors are never stored.
- A tool with no `cache`, `invalidates` or `memory_tool` config behaves exactly as today.
- The sync (`process_turn`) and stream (`stream_turn`) paths must behave identically. The stream path persists after each live call; the sync path persists once at the end of the turn.
- The repo is public: no vulnerability details in commit messages.
- Do not push, open PRs or merge without the user's explicit per-action approval.

## Review Focus

These are the failure modes most likely to bite, with the task that pins each one:
1. **A write tool completes on the stream path, then the turn is interrupted.** The invalidation must still reach Memory Layer, otherwise stale `fetch_profile` data is served next turn. Pinned in Task 12, by persisting after each call.
2. **A cached tool's exchange drops out of the replay.** `grounded_params` must still accept an ID that is only present in the stored result. Pinned in Tasks 7 and 11.
3. **Redis loses the entry key but the index still lists it** (eviction, manual delete). Reads must skip the entry and clean the index, not fail. Pinned in Task 3.
4. **The LLM sends `force_refresh` to a tool Action Gateway validates strictly.** It must be stripped before execute, otherwise the upstream receives an unknown param. Pinned in Tasks 6 and 10.
5. **`remember` is called before any tool has returned anything.** With `grounded_in` set, it must reject. The lenient "nothing fetched yet" rule that `grounded_params` uses must not apply here. Pinned in Tasks 7 and 8.

---

## File Structure

| File | Responsibility |
|---|---|
| `memory_layer/src/tool_result_store.py` (new) | Redis storage of tool-result entries: pseudonymised keys, put, read, invalidate, delete_owner |
| `memory_layer/src/session_store.py` | Expose the redis client (`client` property) |
| `memory_layer/src/memory_layer.py` | Wire the store into context_bundle / flush_session / delete_user; `apply_tool_results`; `write_strict` |
| `memory_layer/src/server.py` | `POST /tool_results/apply`, `POST /write_strict` |
| `action_gateway/src/models.py`, `adapters/rest_api.py`, `server.py` | `projected` flag |
| `agent_core/src/models.py` | `ContextBundle.tool_results`, `ToolResult.projected` |
| `agent_core/src/interfaces/{,async_/}memory_layer.py`, `http_clients/{,async_/}memory_layer.py`, `http_clients/{,async_/}action_gateway.py` | New client methods and `projected` parsing |
| `agent_core/src/tool_results.py` (new) | Policies from config, `args_hash`, `TurnToolCache`, `augment_tool_definitions`, outcome logging and metric |
| `agent_core/src/remember.py` (new) | `RememberTool`: definition, grounding check, handle (sync and async) |
| `agent_core/src/manager_agent.py` | Grounding-refusal fix; `ungrounded_params(stored_results, strict)`; cache and remember in `run_turn` |
| `agent_core/src/orchestrator.py` | Build the per-turn cache; replay filter; `<known_facts>`; augmented tools; stream execute sites; persistence |
| `agent_core/src/schema/config.py` | Runtime schema for `cache`, `invalidates`, `tool_results`, `memory_tool` |
| `dev-kit/dev_kit/schemas/domain/agent_core.py`, `dev_kit/schemas/validation.py`, `dev_kit/schema.py`, `dev_kit/schemas/cross_block_validation.py` | Dev-kit schema and cross-block rules |
| `dev-kit/configs/blue-dots/*.yaml` | Blue Dots rollout |

---

### Task 1: Fix the sync-path grounding refusal (missing `tool_use_id`)

On `deploy/voicera-vm`, `ManagerAgent.run_turn` builds the ungrounded-parameter refusal without `tool_use_id`. That is a required dataclass field, so a `TypeError` is raised whenever the model sends an ungrounded value on `/process_turn`. Later tasks extend this exact code, so it is fixed first.

**Files:**
- Modify: `agent_core/src/manager_agent.py` (the `if _ungrounded:` branch inside `run_turn`, ~line 424)
- Test: `agent_core/tests/test_manager_agent.py`

**Interfaces:**
- Consumes: none
- Produces: none (bug fix)

- [ ] **Step 1: Write the failing test.** Append to `agent_core/tests/test_manager_agent.py`:

```python
def test_run_turn_ungrounded_refusal_does_not_crash():
    """An invented id must produce a refusal tool_result, not a TypeError."""
    tc = ToolCall(tool_name="apply_job", tool_use_id="tu_apply",
                  input_params={"profile_item_id": "invented-id"})
    initial = _tool_response(tc)
    followup = _text_response("Let me check that again.")
    agent, llm, registry, gateway, _ = _make_manager(llm_responses=[initial, followup])
    agent._grounded_params = {"apply_job": {"profile_item_id": ["fetch_profile"]}}
    earlier = [
        Message(role="assistant", content=[ToolUseBlock(
            tool_use_id="tu_fp", tool_name="fetch_profile", input={})]),
        Message(role="user", content=[ToolResultBlock(
            tool_use_id="tu_fp", content='{"items":[{"item_id":"real-id"}]}')]),
    ]

    text, _, results = agent.run_turn(earlier + list(MESSAGES), SESSION_ID, initial)

    assert text == "Let me check that again."
    gateway.execute.assert_not_called()
    assert results[0].tool_use_id == "tu_apply"
    assert results[0].error == "UNGROUNDED_PARAMETER"
```

If `Message`, `ToolUseBlock` or `ToolResultBlock` are not already imported at the top of the file, add them to the existing `from src.chat_provider.types import (...)` block.

- [ ] **Step 2: Run the test and check it fails.**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py::test_run_turn_ungrounded_refusal_does_not_crash -v`
Expected: FAIL with `TypeError: ToolResult.__init__() missing 1 required positional argument: 'tool_use_id'`

- [ ] **Step 3: Fix.** In `manager_agent.py`, in the `if _ungrounded:` branch, change the constructor call to:

```python
                    tool_result = ToolResult(
                        tool_use_id=tool_call.tool_use_id,
                        tool_name=tool_call.tool_name,
                        success=False,
                        result={},
                        error="UNGROUNDED_PARAMETER",
                        result_text=(
```

(Leave the rest of the call unchanged.)

- [ ] **Step 4: Run the tests and check they pass.**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -v`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "fix(agent-core): give the ungrounded-parameter refusal its tool_use_id

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Action Gateway `projected` flag

**Files:**
- Modify: `action_gateway/src/models.py` (`ToolResult`, `ExecuteResponse`)
- Modify: `action_gateway/src/adapters/rest_api.py` (success `return ToolResult(...)`, ~line 887)
- Modify: `action_gateway/src/server.py` (main-path `ExecuteResponse(...)`, ~line 167)
- Test: `action_gateway/tests/test_rest_api_adapter.py`, `action_gateway/tests/test_server.py`

**Interfaces:**
- Produces: `ToolResult.projected: bool = False` and `ExecuteResponse.projected: bool = False`. The JSON key is `projected` in the `/execute` response.

- [ ] **Step 1: Write failing tests.** In `test_rest_api_adapter.py`, inside `class TestRestApiAdapterProjectionInvariant`, add:

```python
    async def test_projected_flag_true_when_projection_applied(self, rest_projection_config):
        adapter = RestApiAdapter(rest_projection_config)
        mock_resp = make_mock_response(200, {"results": [{"name": "a", "extra": 1}]})
        with patch.object(adapter, "_http_client") as mock_client:
            mock_client.request = AsyncMock(return_value=mock_resp)
            result = await adapter.execute("test_market_lookup", {"q": "x"}, "sess-proj-1")
        assert result.success is True
        assert result.projected is True
```

Then add a sibling test in `class TestRestApiAdapterExecute`, using that class's existing no-projection config fixture and a successful mock response, with the same steps as its first success test, asserting `result.projected is False`.

In `test_server.py`, add a test that uses the existing adapter-mock pattern of that file. It returns `ToolResult(tool_use_id="", tool_name="t", result={}, success=True, projected=True)` from the mocked adapter, and asserts `response.json()["projected"] is True`.

- [ ] **Step 2: Run and check they fail.**

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py tests/test_server.py -v -k projected`
Expected: FAIL (`AttributeError`/`ValidationError`: no `projected`)

- [ ] **Step 3: Implement.** In `models.py`, add to both `ToolResult` and `ExecuteResponse`, after `session_values`:

```python
    projected: bool = False
```

Document it in each class's `Attributes:` docstring as: `projected: True when a response.projection shaped result_text. Agent Core only stores projected results.`

In `rest_api.py`, change the success return to:

```python
        return ToolResult(
            tool_use_id="",
            tool_name=tool_name,
            result=result_dict,
            success=True,
            result_text=result_text,
            session_values=session_values,
            projected=projected is not None,
        )
```

In `server.py`, add `projected=result.projected,` to the main-path `ExecuteResponse(...)`.

- [ ] **Step 4: Run the whole module and check it passes.**

Run: `cd action_gateway && uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add action_gateway/
git commit -m "feat(action-gateway): report whether a result was projected

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Memory Layer `ToolResultStore`

**Files:**
- Create: `memory_layer/src/tool_result_store.py`
- Modify: `memory_layer/pyproject.toml` (`redis>=4.2`; add `fakeredis>=2.26` to `[project.optional-dependencies] dev`)
- Test: `memory_layer/tests/test_tool_result_store.py`

**Interfaces:**
- Produces:
  - `class ToolResultStore(client, secret: str, session_ttl_seconds: int, max_user_ttl_seconds: int)`
  - `.enabled -> bool`
  - `.pseudonym(value: str) -> str`
  - `.put(scope: str, owner_id: str, tool: str, args_hash: str, data: Any, ttl_seconds: int, origin: str = "turn", now: float | None = None) -> bool`
  - `.read(session_id: str, user_id: str, now: float | None = None) -> list[dict]`, where each entry dict has keys `tool, args_hash, data, fetched_at, expires_at, origin, scope`
  - `.invalidate(scope: str, owner_id: str, tool: str) -> int`
  - `.delete_owner(scope: str, owner_id: str) -> None`
  - Every method returns a safe default when disabled or on Redis error. None of them raise.

- [ ] **Step 1: Add dev dependencies.** In `memory_layer/pyproject.toml`:
  - change `"redis>=4.0",` to `"redis>=4.2",`, because `expire(nx=, gt=)` needs 4.2;
  - append `"fakeredis>=2.26",` to the `dev` extras.

Run: `cd memory_layer && uv sync --extra dev`

- [ ] **Step 2: Write failing tests.** Create `memory_layer/tests/test_tool_result_store.py`:

```python
"""Unit tests for ToolResultStore (fakeredis-backed)."""

from __future__ import annotations

import json

import fakeredis
import pytest

from src.tool_result_store import ToolResultStore

SECRET = "test-secret"


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def store(client):
    return ToolResultStore(client, SECRET, session_ttl_seconds=3600, max_user_ttl_seconds=86400)


def test_disabled_without_secret(client):
    s = ToolResultStore(client, "", session_ttl_seconds=3600, max_user_ttl_seconds=86400)
    assert s.enabled is False
    assert s.put("user", "u1", "fetch_profile", "ab12", {"a": 1}, 60) is False
    assert s.read("s1", "u1") == []
    assert client.keys("*") == []


def test_pseudonym_is_stable_and_not_raw(store):
    p = store.pseudonym("919876543210")
    assert p == store.pseudonym("919876543210")
    assert "919876543210" not in p
    assert len(p) == 32


def test_put_then_read_returns_entry(store):
    assert store.put("user", "u1", "fetch_profile", "ab12", {"items": []}, 60, now=1000.0)
    entries = store.read("s1", "u1", now=1010.0)
    assert len(entries) == 1
    e = entries[0]
    assert (e["tool"], e["args_hash"], e["data"], e["scope"]) == ("fetch_profile", "ab12", {"items": []}, "user")
    assert e["fetched_at"] == 1000.0 and e["expires_at"] == 1060.0


def test_key_contains_no_raw_ids(store, client):
    store.put("session", "919876543210", "fetch_jobs", "ab12", {}, 60)
    assert all("919876543210" not in k for k in client.keys("*"))


def test_redis_ttl_set_from_write_and_read_does_not_extend(store, client):
    store.put("user", "u1", "fetch_profile", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    before = client.ttl(key)
    store.read("s1", "u1")
    assert 0 < client.ttl(key) <= before <= 60


def test_index_gets_expiry_and_grows_to_longest_entry(store, client):
    store.put("user", "u1", "a", "ab12", {}, 60)
    idx = client.keys("ml:tr:idx:u:*")[0]
    assert 0 < client.ttl(idx) <= 60          # NX set it; GT alone would leave -1
    store.put("user", "u1", "b", "ab12", {}, 600)
    assert client.ttl(idx) > 60               # GT extended it
    store.put("user", "u1", "c", "ab12", {}, 30)
    assert client.ttl(idx) > 60               # a shorter entry never shrinks it


def test_ttl_clamped_to_scope_cap(store, client):
    store.put("session", "s1", "t", "ab12", {}, 999999)
    key = [k for k in client.keys("ml:tr:s:*") if ":idx:" not in k][0]
    assert client.ttl(key) <= 3600


def test_expired_by_clock_is_filtered(store):
    store.put("user", "u1", "t", "ab12", {}, 60, now=1000.0)
    assert store.read("s1", "u1", now=1061.0) == []


def test_missing_entry_key_is_skipped_and_index_cleaned(store, client):
    store.put("user", "u1", "t", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    client.delete(key)
    assert store.read("s1", "u1") == []
    idx = client.keys("ml:tr:idx:u:*")
    assert idx == [] or client.smembers(idx[0]) == set()


def test_corrupt_entry_is_skipped(store, client):
    store.put("user", "u1", "t", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    client.set(key, "not-json", ex=60)
    assert store.read("s1", "u1") == []


def test_session_and_user_scopes_both_read(store):
    store.put("session", "s1", "fetch_jobs", "aa11", {"j": 1}, 60)
    store.put("user", "u1", "fetch_profile", "bb22", {"p": 1}, 60)
    tools = sorted(e["tool"] for e in store.read("s1", "u1"))
    assert tools == ["fetch_jobs", "fetch_profile"]


def test_invalidate_removes_all_entries_for_tool_only(store):
    store.put("user", "u1", "fetch_profile", "aa11", {}, 60)
    store.put("user", "u1", "fetch_profile", "bb22", {}, 60)
    store.put("user", "u1", "fetch_jobs", "cc33", {}, 60)
    assert store.invalidate("user", "u1", "fetch_profile") == 2
    assert [e["tool"] for e in store.read("s1", "u1")] == ["fetch_jobs"]


def test_delete_owner_removes_entries_and_index(store, client):
    store.put("user", "u1", "t", "aa11", {}, 60)
    store.delete_owner("user", "u1")
    assert client.keys("ml:tr:*") == []


def test_redis_errors_are_swallowed(store, client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(client, "pipeline", boom)
    assert store.put("user", "u1", "t", "aa11", {}, 60) is False
    assert store.read("s1", "u1") == []
    assert store.invalidate("user", "u1", "t") == 0
    store.delete_owner("user", "u1")  # does not raise


def test_rejects_unknown_scope_and_bad_names(store):
    assert store.put("agent", "u1", "t", "aa11", {}, 60) is False
    assert store.put("user", "u1", "bad:tool", "aa11", {}, 60) is False
    assert store.put("user", "u1", "t", "XYZ", {}, 60) is False
```

- [ ] **Step 3: Run and check they fail.**

Run: `cd memory_layer && uv run pytest tests/test_tool_result_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tool_result_store'`

- [ ] **Step 4: Implement.** Create `memory_layer/src/tool_result_store.py`:

```python
"""
memory_layer/src/tool_result_store.py

ToolResultStore — Redis storage for tool-result entries (spec §6).

One key per entry, TTL fixed at write time:
  ml:tr:{s|u}:{owner}:{tool}:{args_hash}   entry JSON, SET ... EX ttl
  ml:tr:idx:{s|u}:{owner}                  SET of that owner's entry keys
owner = HMAC-SHA256(secret, session_id|user_id)[:32] — never the raw id.

Disabled (reads empty, writes no-op) when no secret is configured. Never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "ml:tr"
_SCOPE_LETTER = {"session": "s", "user": "u"}
_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HASH_RE = re.compile(r"^[a-f0-9]{4,64}$")


class ToolResultStore:
    """Stores projected tool results per session or per user.

    Args:
        client: A redis-py client created with ``decode_responses=True``.
        secret: HMAC key for owner pseudonyms. Empty disables the store.
        session_ttl_seconds: Upper bound for session-scope TTLs.
        max_user_ttl_seconds: Upper bound for user-scope TTLs.
    """

    def __init__(self, client, secret: str, session_ttl_seconds: int,
                 max_user_ttl_seconds: int) -> None:
        self._client = client
        self._secret = (secret or "").encode()
        self._max_ttl = {"session": int(session_ttl_seconds), "user": int(max_user_ttl_seconds)}
        if not self._secret:
            logger.warning(
                "tool_result_store.disabled",
                extra={"operation": "tool_result_store.init", "status": "disabled",
                       "reason": "TOOL_RESULT_KEY_SECRET not set"},
            )

    @property
    def enabled(self) -> bool:
        """True when a secret is configured."""
        return bool(self._secret)

    def pseudonym(self, value: str) -> str:
        """Return the 32-hex-char HMAC pseudonym for an owner id."""
        return hmac.new(self._secret, str(value).encode(), hashlib.sha256).hexdigest()[:32]

    def _idx(self, scope: str, owner_id: str) -> str:
        return f"{_PREFIX}:idx:{_SCOPE_LETTER[scope]}:{self.pseudonym(owner_id)}"

    def _key_prefix(self, scope: str, owner_id: str) -> str:
        return f"{_PREFIX}:{_SCOPE_LETTER[scope]}:{self.pseudonym(owner_id)}:"

    def put(self, scope: str, owner_id: str, tool: str, args_hash: str, data: Any,
            ttl_seconds: int, origin: str = "turn", now: float | None = None) -> bool:
        """Store one entry with a TTL fixed now. Returns True when stored."""
        if (not self.enabled or scope not in _SCOPE_LETTER or not owner_id
                or not _TOOL_RE.match(tool or "") or not _HASH_RE.match(args_hash or "")):
            return False
        ttl = min(int(ttl_seconds), self._max_ttl[scope])
        if ttl <= 0:
            return False
        now = time.time() if now is None else now
        key = f"{self._key_prefix(scope, owner_id)}{tool}:{args_hash}"
        idx = self._idx(scope, owner_id)
        value = json.dumps({
            "tool": tool, "args_hash": args_hash, "data": data, "fetched_at": now,
            "expires_at": now + ttl, "origin": origin, "scope": scope,
        })
        start = time.time()
        try:
            pipe = self._client.pipeline()
            pipe.set(key, value, ex=ttl)
            pipe.sadd(idx, key)
            pipe.expire(idx, ttl, nx=True)   # first expiry for a new index
            pipe.expire(idx, ttl, gt=True)   # only ever extends it
            pipe.execute()
            logger.info("tool_result_store.put", extra={
                "operation": "tool_result_store.put", "status": "success", "tool": tool,
                "scope": scope, "ttl_s": ttl, "latency_ms": int((time.time() - start) * 1000)})
            return True
        except Exception as e:
            logger.error("tool_result_store.put_error", extra={
                "operation": "tool_result_store.put", "status": "failure", "tool": tool,
                "error": type(e).__name__})
            return False

    def read(self, session_id: str, user_id: str, now: float | None = None) -> list[dict]:
        """Return unexpired entries for the session and the user. Cleans dead index members."""
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        idxs = [self._idx("session", session_id), self._idx("user", user_id)]
        start = time.time()
        try:
            pipe = self._client.pipeline()
            for idx in idxs:
                pipe.smembers(idx)
            members = pipe.execute()
            owner_of: dict[str, str] = {}
            for idx, keys in zip(idxs, members):
                for k in sorted(keys or ()):
                    owner_of[k] = idx
            if not owner_of:
                return []
            keys = list(owner_of)
            raws = self._client.mget(keys)
            out: list[dict] = []
            dead: dict[str, list[str]] = {}
            for key, raw in zip(keys, raws):
                try:
                    entry = json.loads(raw) if raw is not None else None
                except (TypeError, ValueError):
                    entry = None
                if not isinstance(entry, dict):
                    dead.setdefault(owner_of[key], []).append(key)
                    continue
                if float(entry.get("expires_at", 0)) <= now:
                    continue
                out.append(entry)
            if dead:
                pipe = self._client.pipeline()
                for idx, ks in dead.items():
                    pipe.srem(idx, *ks)
                pipe.execute()
            logger.info("tool_result_store.read", extra={
                "operation": "tool_result_store.read", "status": "success", "entries": len(out),
                "latency_ms": int((time.time() - start) * 1000)})
            return out
        except Exception as e:
            logger.error("tool_result_store.read_error", extra={
                "operation": "tool_result_store.read", "status": "failure", "error": type(e).__name__})
            return []

    def invalidate(self, scope: str, owner_id: str, tool: str) -> int:
        """Delete every entry of ``tool`` for this owner. Returns the count deleted."""
        if not self.enabled or scope not in _SCOPE_LETTER or not owner_id:
            return 0
        idx = self._idx(scope, owner_id)
        prefix = f"{self._key_prefix(scope, owner_id)}{tool}:"
        try:
            doomed = [k for k in (self._client.smembers(idx) or ()) if k.startswith(prefix)]
            if doomed:
                pipe = self._client.pipeline()
                pipe.delete(*doomed)
                pipe.srem(idx, *doomed)
                pipe.execute()
            logger.info("tool_result_store.invalidate", extra={
                "operation": "tool_result_store.invalidate", "status": "success",
                "tool": tool, "scope": scope, "deleted": len(doomed)})
            return len(doomed)
        except Exception as e:
            logger.error("tool_result_store.invalidate_error", extra={
                "operation": "tool_result_store.invalidate", "status": "failure",
                "error": type(e).__name__})
            return 0

    def delete_owner(self, scope: str, owner_id: str) -> None:
        """Delete all of an owner's entries and its index (session end, erasure)."""
        if not self.enabled or scope not in _SCOPE_LETTER or not owner_id:
            return
        idx = self._idx(scope, owner_id)
        try:
            keys = list(self._client.smembers(idx) or ())
            pipe = self._client.pipeline()
            if keys:
                pipe.delete(*keys)
            pipe.delete(idx)
            pipe.execute()
        except Exception as e:
            logger.error("tool_result_store.delete_owner_error", extra={
                "operation": "tool_result_store.delete_owner", "status": "failure",
                "error": type(e).__name__})
```

Note: `test_redis_errors_are_swallowed` patches `pipeline`, so `invalidate` fails in the pipeline only when there are doomed keys. `smembers` still works, the set is empty, and the method returns 0. Either way the assertion holds.

- [ ] **Step 5: Run and check it passes.**

Run: `cd memory_layer && uv run pytest tests/test_tool_result_store.py -v`
Expected: all PASS

- [ ] **Step 6: Commit.**

```bash
git add memory_layer/pyproject.toml memory_layer/src/tool_result_store.py memory_layer/tests/test_tool_result_store.py
git commit -m "feat(memory-layer): per-entry tool-result store with write-time TTL

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the store into `MemoryLayer` and add `write_strict`

**Files:**
- Modify: `memory_layer/src/session_store.py` (add a `client` property)
- Modify: `memory_layer/src/memory_layer.py`
- Test: `memory_layer/tests/test_memory_layer.py`

**Interfaces:**
- Consumes: `ToolResultStore` (Task 3)
- Produces:
  - The `context_bundle(...)` dict gains a `"tool_results": list[dict]` key on every return path, including the error fallback, where it is `[]`.
  - `MemoryLayer.apply_tool_results(session_id: str, user_id: str, invalidate: list[str], puts: list[dict]) -> None`. Each put dict has `scope, tool, args_hash, data, ttl_seconds, origin`. Invalidations are applied before puts. Never raises.
  - `MemoryLayer.write_strict(session_id, user_id, scope, key, value) -> tuple[bool, str]`
  - `flush_session` also deletes the session-scope entries; `delete_user` also deletes the user-scope entries.
  - Env: `TOOL_RESULT_KEY_SECRET`, and `TOOL_RESULT_MAX_USER_TTL_SECONDS` (default `86400`).

- [ ] **Step 1: Write failing tests.** In `test_memory_layer.py`:
  - Extend `_make_layer()` to also patch `src.memory_layer.ToolResultStore`. Put its mock in `stores["tool_results"]`, with `mock_trs.read.return_value = []`.
  - Add `"status": {"type": "enum", "values": ["a", "b"]}` and `"count": {"type": "int"}` to `MINIMAL_CONFIG`'s session schema. If `MINIMAL_CONFIG` lacks a persistent `declared_fields` list, add `state.persistent.graph.subnodes.UserProfile.declared_fields: ["name"]`.
  - Then append these tests:

```python
def test_context_bundle_includes_tool_results_existing_session():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = True
    stores["redis"].get_session.return_value = {}
    stores["tool_results"].read.return_value = [{"tool": "t"}]
    bundle = layer.context_bundle("s1", "u1")
    assert bundle["tool_results"] == [{"tool": "t"}]
    stores["tool_results"].read.assert_called_once_with("s1", "u1")


def test_context_bundle_includes_tool_results_new_session():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["tool_results"].read.return_value = [{"tool": "t"}]
    bundle = layer.context_bundle("s1", "u1")
    assert bundle["tool_results"] == [{"tool": "t"}]


def test_context_bundle_error_fallback_has_empty_tool_results():
    layer, stores = _make_layer()
    stores["redis"].session_exists.side_effect = RuntimeError("x")
    assert layer.context_bundle("s1", "u1")["tool_results"] == []


def test_apply_tool_results_invalidates_both_scopes_then_puts():
    layer, stores = _make_layer()
    trs = stores["tool_results"]
    layer.apply_tool_results("s1", "u1", ["fetch_profile"], [
        {"scope": "user", "tool": "fetch_profile", "args_hash": "ab12", "data": {},
         "ttl_seconds": 60, "origin": "turn"},
        {"scope": "session", "tool": "fetch_jobs", "args_hash": "cd34", "data": [],
         "ttl_seconds": 60, "origin": "turn"},
    ])
    names = [c[0] for c in trs.method_calls]
    assert names == ["invalidate", "invalidate", "put", "put"]
    trs.invalidate.assert_any_call("session", "s1", "fetch_profile")
    trs.invalidate.assert_any_call("user", "u1", "fetch_profile")
    trs.put.assert_any_call("user", "u1", "fetch_profile", "ab12", {}, 60, origin="turn")
    trs.put.assert_any_call("session", "s1", "fetch_jobs", "cd34", [], 60, origin="turn")


def test_apply_tool_results_never_raises():
    layer, stores = _make_layer()
    stores["tool_results"].invalidate.side_effect = RuntimeError("x")
    layer.apply_tool_results("s1", "u1", ["t"], [])  # no exception


def test_flush_session_deletes_session_tool_results():
    layer, stores = _make_layer()
    stores["redis"].get_session.return_value = {}
    layer.flush_session("s1", "u1", "done")
    stores["tool_results"].delete_owner.assert_called_once_with("session", "s1")


def test_delete_user_deletes_user_tool_results():
    layer, stores = _make_layer()
    layer.delete_user("u1")
    stores["tool_results"].delete_owner.assert_called_once_with("user", "u1")


@pytest.mark.parametrize("scope,key,value,ok", [
    ("session", "status", "a", True),
    ("session", "status", "zzz", False),
    ("session", "count", "7", True),
    ("session", "count", "seven", False),
    ("session", "undeclared", "x", False),
    ("persistent", "name", "Asha", True),
    ("persistent", "undeclared", "x", False),
    ("signal", "status", "a", False),
])
def test_write_strict(scope, key, value, ok):
    layer, stores = _make_layer()
    accepted, reason = layer.write_strict("s1", "u1", scope, key, value)
    assert accepted is ok
    assert (reason == "") is ok
    if ok:
        assert stores["redis"].set_session_field.called or stores["user"].upsert_profile_field.called
    else:
        stores["redis"].set_session_field.assert_not_called()
        stores["user"].upsert_profile_field.assert_not_called()
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd memory_layer && uv run pytest tests/test_memory_layer.py -v -k "tool_results or write_strict"`
Expected: FAIL (`AttributeError`: `src.memory_layer` has no `ToolResultStore` / no `write_strict`)

- [ ] **Step 3: Implement.**
  1. In `session_store.py`, add to `RedisSessionStore`:

```python
    @property
    def client(self):
        """The underlying redis-py client, shared with ToolResultStore."""
        return self._client
```

  2. In `memory_layer.py`, add `from tool_result_store import ToolResultStore` next to the other store imports.

  3. In `MemoryLayer.__init__`, directly after `self._redis = RedisSessionStore(config, self._ttl_seconds)`:

```python
        self._tool_results = ToolResultStore(
            self._redis.client,
            os.environ.get("TOOL_RESULT_KEY_SECRET", ""),
            session_ttl_seconds=self._ttl_seconds,
            max_user_ttl_seconds=int(os.environ.get("TOOL_RESULT_MAX_USER_TTL_SECONDS", "86400")),
        )
```

  4. In `context_bundle`, immediately before the success `logger.info("memory_layer.context_bundle", ...)`, add:

```python
            bundle["tool_results"] = self._tool_results.read(session_id, user_id)
```

  Change the error fallback to `return {"session": {}, "profile": {}, "journey": None, "tool_results": []}`.

  5. In `flush_session`, after `self._redis.delete_session(session_id)`, add `self._tool_results.delete_owner("session", session_id)`. In `delete_user`, after `self._redis.delete_user_index(user_id)`, add `self._tool_results.delete_owner("user", user_id)`. Update both docstrings.

  6. Add the methods:

```python
    def apply_tool_results(self, session_id: str, user_id: str,
                           invalidate: list[str], puts: list[dict]) -> None:
        """Apply one turn's tool-result changes: invalidations first, then new entries.

        Args:
            session_id: Owner for session-scope entries.
            user_id: Owner for user-scope entries.
            invalidate: Tool names whose entries are deleted in both scopes.
            puts: Entries ``{scope, tool, args_hash, data, ttl_seconds, origin}``.
        """
        start = time.time()
        try:
            for tool in invalidate or []:
                self._tool_results.invalidate("session", session_id, tool)
                self._tool_results.invalidate("user", user_id, tool)
            for p in puts or []:
                owner = session_id if p.get("scope") == "session" else user_id
                self._tool_results.put(
                    p.get("scope", ""), owner, p.get("tool", ""), p.get("args_hash", ""),
                    p.get("data"), int(p.get("ttl_seconds", 0)), origin=p.get("origin", "turn"),
                )
            logger.info("memory_layer.apply_tool_results", extra={
                "operation": "memory_layer.apply_tool_results", "status": "success",
                "invalidated": len(invalidate or []), "stored": len(puts or []),
                "latency_ms": int((time.time() - start) * 1000)})
        except Exception as e:
            logger.error("memory_layer.apply_tool_results_error", extra={
                "operation": "memory_layer.apply_tool_results", "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000)})

    def write_strict(self, session_id: str, user_id: str, scope: str, key: str,
                     value: Any) -> tuple[bool, str]:
        """Write only a declared field whose value matches its declared type/enum.

        Unlike :meth:`write`, never falls back to ad-hoc storage.

        Returns:
            ``(True, "")`` when written, else ``(False, reason)``.
        """
        if scope == "session":
            fdef = self._schema.get(key)
            if not isinstance(fdef, dict):
                return False, f"{key} is not a declared session field"
            ftype = fdef.get("type")
            if ftype == "enum" and str(value) not in (fdef.get("values") or []):
                return False, f"{value!r} is not one of {fdef.get('values')}"
            if ftype == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return False, "expected an integer"
            if ftype == "list" and not isinstance(value, list):
                return False, "expected a list"
        elif scope == "persistent":
            if key not in self._declared_fields:
                return False, f"{key} is not a declared profile field"
        else:
            return False, f"unsupported scope {scope}"
        self.write(session_id, user_id, scope, key, value)
        return True, ""
```

- [ ] **Step 4: Run the whole module and check it passes.**

Run: `cd memory_layer && uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add memory_layer/src memory_layer/tests
git commit -m "feat(memory-layer): return, apply and erase tool results; add strict writes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Memory Layer HTTP endpoints

**Files:**
- Modify: `memory_layer/src/server.py`
- Test: `memory_layer/tests/test_server.py`

**Interfaces:**
- Consumes: `MemoryLayer.apply_tool_results`, `MemoryLayer.write_strict` (Task 4)
- Produces:
  - `POST /tool_results/apply` with body `{session_id, user_id, invalidate: [str], puts: [{scope, tool, args_hash, data, ttl_seconds, origin}]}` → `{"status": "ok"}`.
  - `POST /write_strict` with body `{session_id, user_id, scope, key, value}` → `{"status": "ok"|"rejected", "reason": str}`.
  - The `/context_bundle` fallbacks include `"tool_results": []`.

- [ ] **Step 1: Write failing tests.** Append to `test_server.py`:

```python
def test_apply_tool_results_forwards(client, mock_memory):
    body = {"session_id": "s1", "user_id": "u1", "invalidate": ["fetch_profile"],
            "puts": [{"scope": "user", "tool": "fetch_profile", "args_hash": "ab12",
                      "data": {"a": 1}, "ttl_seconds": 60, "origin": "turn"}]}
    r = client.post("/tool_results/apply", json=body)
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    args = mock_memory.apply_tool_results.call_args[0]
    assert args[0:3] == ("s1", "u1", ["fetch_profile"])
    assert args[3][0]["tool"] == "fetch_profile"


def test_apply_tool_results_rejects_bad_tool_name(client, mock_memory):
    body = {"session_id": "s1", "user_id": "u1", "puts": [
        {"scope": "user", "tool": "bad:tool", "args_hash": "ab12", "data": {}, "ttl_seconds": 60}]}
    assert client.post("/tool_results/apply", json=body).status_code == 422
    mock_memory.apply_tool_results.assert_not_called()


def test_apply_tool_results_empty_ids_is_noop(client, mock_memory):
    r = client.post("/tool_results/apply", json={"session_id": " ", "user_id": "u1"})
    assert r.json() == {"status": "ok"}
    mock_memory.apply_tool_results.assert_not_called()


def test_write_strict_ok_and_rejected(client, mock_memory):
    mock_memory.write_strict.return_value = (True, "")
    r = client.post("/write_strict", json={"session_id": "s1", "user_id": "u1",
                                           "scope": "session", "key": "k", "value": "v"})
    assert r.json() == {"status": "ok", "reason": ""}
    mock_memory.write_strict.return_value = (False, "nope")
    r = client.post("/write_strict", json={"session_id": "s1", "user_id": "u1",
                                           "scope": "session", "key": "k", "value": "v"})
    assert r.json() == {"status": "rejected", "reason": "nope"}


def test_write_strict_exception_is_rejected(client, mock_memory):
    mock_memory.write_strict.side_effect = RuntimeError("x")
    r = client.post("/write_strict", json={"session_id": "s1", "user_id": "u1",
                                           "scope": "session", "key": "k", "value": "v"})
    assert r.json()["status"] == "rejected"
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd memory_layer && uv run pytest tests/test_server.py -v -k "tool_results or write_strict"`
Expected: FAIL (404)

- [ ] **Step 3: Implement.** In `server.py`, next to the other request models (add `Literal` to the typing import and `Field` to the pydantic import):

```python
class ToolResultPut(BaseModel):
    scope: Literal["session", "user"]
    tool: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    args_hash: str = Field(pattern=r"^[a-f0-9]{4,64}$")
    data: Any = None
    ttl_seconds: int = Field(gt=0)
    origin: Literal["turn", "bootstrap"] = "turn"


class ApplyToolResultsRequest(BaseModel):
    session_id: str
    user_id: str
    invalidate: list[str] = Field(default_factory=list)
    puts: list[ToolResultPut] = Field(default_factory=list)


class StrictWriteResponse(BaseModel):
    status: Literal["ok", "rejected"]
    reason: str = ""
```

Inside `create_app`, after `/write`:

```python
    @app.post("/tool_results/apply")
    def apply_tool_results(request: ApplyToolResultsRequest) -> StatusResponse:
        """Apply a batch of tool-result invalidations and entries. Always 200."""
        session_id, user_id = request.session_id.strip(), request.user_id.strip()
        if not session_id or not user_id:
            return StatusResponse(status="ok")
        try:
            memory.apply_tool_results(session_id, user_id, list(request.invalidate),
                                      [p.model_dump() for p in request.puts])
        except Exception as e:
            logger.error("memory_server.apply_tool_results_error", extra={
                "operation": "memory_server.apply_tool_results", "status": "failure",
                "error": f"{type(e).__name__}: {e}"})
        return StatusResponse(status="ok")

    @app.post("/write_strict")
    def write_strict(request: WriteRequest) -> StrictWriteResponse:
        """Write a declared field only if its value validates."""
        session_id, user_id = request.session_id.strip(), request.user_id.strip()
        if not session_id or not user_id or not request.key:
            return StrictWriteResponse(status="rejected", reason="missing ids or key")
        try:
            ok, reason = memory.write_strict(session_id, user_id, request.scope,
                                             request.key, request.value)
        except Exception as e:
            logger.error("memory_server.write_strict_error", extra={
                "operation": "memory_server.write_strict", "status": "failure",
                "error": f"{type(e).__name__}: {e}"})
            return StrictWriteResponse(status="rejected", reason="memory layer error")
        return StrictWriteResponse(status="ok" if ok else "rejected", reason=reason)
```

In `/context_bundle`, change both fallback returns to `{"session": {}, "profile": {}, "journey": None, "tool_results": []}`. Add both routes to the module docstring's route list.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd memory_layer && uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add memory_layer/src/server.py memory_layer/tests/test_server.py
git commit -m "feat(memory-layer): expose tool-result apply and strict-write endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Agent Core models and HTTP clients

**Files:**
- Modify: `agent_core/src/models.py` (`ContextBundle`, `ToolResult`)
- Modify: `agent_core/src/interfaces/memory_layer.py`, `agent_core/src/interfaces/async_/memory_layer.py`
- Modify: `agent_core/src/http_clients/memory_layer.py`, `agent_core/src/http_clients/async_/memory_layer.py`
- Modify: `agent_core/src/http_clients/action_gateway.py`, `agent_core/src/http_clients/async_/action_gateway.py`
- Test: `agent_core/tests/test_memory_http_client.py`, `agent_core/tests/test_http_clients.py`

**Interfaces:**
- Produces:
  - `ContextBundle.tool_results: list[dict] = field(default_factory=list)`. `ContextBundle.empty()` still works.
  - `ToolResult.projected: bool = False`
  - Sync client: `MemoryLayerHttpClient.apply_tool_results(session_id: str, user_id: str, batch: dict) -> None` (`batch = {"invalidate": [...], "puts": [...]}`; never raises) and `MemoryLayerHttpClient.write_strict(session_id, user_id, scope, key, value) -> tuple[bool, str]` (on transport failure returns `(False, "memory layer unavailable")`).
  - Async client: the same two methods as `async def`.
  - Both interface ABCs get these as `@abstractmethod`s, documented.
  - Both Action Gateway clients set `projected=bool(data.get("projected", False))`.

- [ ] **Step 1: Write failing tests.** In `test_memory_http_client.py`, follow the file's existing `httpx` patching pattern. Add:
  - a sync test where `/context_bundle` returns `{"session": {}, "profile": {}, "journey": None, "tool_results": [{"tool": "t"}]}`, asserting `bundle.tool_results == [{"tool": "t"}]`;
  - a test where the response has no `tool_results` key, asserting `bundle.tool_results == []`;
  - a test that `apply_tool_results("s1", "u1", {"invalidate": ["t"], "puts": []})` posts to `/tool_results/apply` with `json={"session_id": "s1", "user_id": "u1", "invalidate": ["t"], "puts": []}`;
  - a test that `apply_tool_results` swallows `httpx.ConnectError`;
  - a test that `write_strict` returns `(True, "")` for `{"status": "ok", "reason": ""}`, `(False, "nope")` for `{"status": "rejected", "reason": "nope"}`, and `(False, "memory layer unavailable")` on `httpx.ConnectError`;
  - async equivalents of the above, using the file's async pattern (`AsyncMock` on the shared client's `post`).

In `test_http_clients.py`, add one sync and one async Action Gateway execute test with `"projected": True` in the response JSON, asserting `result.projected is True`, and one without the key, asserting `False`.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_memory_http_client.py tests/test_http_clients.py -v -k "tool_results or write_strict or projected"`
Expected: FAIL

- [ ] **Step 3: Implement.** In `models.py`:
  - `ContextBundle`: add `tool_results: list[dict] = field(default_factory=list)` after `journey`, and document it as "Unexpired tool-result entries from Memory Layer (spec §6)".
  - `ToolResult`: add `projected: bool = False` after `session_values`.
  - Make sure `field` is imported from `dataclasses`.

Sync memory client `context_bundle`: build the bundle with `tool_results=data.get("tool_results") or []`. Add:

```python
    def apply_tool_results(self, session_id: str, user_id: str, batch: dict) -> None:
        """POST /tool_results/apply. Logs and swallows every failure.

        Args:
            session_id: Session owner.
            user_id: User owner.
            batch: ``{"invalidate": [tool, ...], "puts": [entry, ...]}``.
        """
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/tool_results/apply",
                json={"session_id": session_id, "user_id": user_id,
                      "invalidate": list(batch.get("invalidate") or []),
                      "puts": list(batch.get("puts") or [])},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error("memory_client.apply_tool_results_error", extra={
                "operation": "memory_client.apply_tool_results", "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000)})

    def write_strict(self, session_id: str, user_id: str, scope: str, key: str,
                     value) -> tuple[bool, str]:
        """POST /write_strict. Returns ``(accepted, reason)``; never raises."""
        try:
            resp = httpx.post(
                f"{self._endpoint}/write_strict",
                json={"session_id": session_id, "user_id": user_id, "scope": scope,
                      "key": key, "value": value},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("status") == "ok", str(data.get("reason") or "")
        except Exception as e:
            logger.error("memory_client.write_strict_error", extra={
                "operation": "memory_client.write_strict", "status": "failure",
                "error": f"{type(e).__name__}: {e}"})
            return False, "memory layer unavailable"
```

Async client: the same two methods as `async def`, using the shared `self._client.post(...)` and `await`. Mirror the structure of its existing `write`. Also add `tool_results=data.get("tool_results") or []` in its `context_bundle`. If the attribute names `_endpoint` / `_timeout_s` / `_client` differ in either client, use the names that file already uses.

Interfaces: add both methods as `@abstractmethod`s, with docstrings matching the ones above.

Action Gateway clients: add `projected=bool(data.get("projected", False)),` to each `ToolResult(...)` built from the response.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run pytest -q`
Expected: all PASS. If any hand-written test fake subclasses a memory interface, add no-op implementations of the two methods to it.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src agent_core/tests
git commit -m "feat(agent-core): carry tool results and the projected flag through the clients

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Grounding sees stored results; strict mode

**Files:**
- Modify: `agent_core/src/manager_agent.py` (`ungrounded_params`, `ManagerAgent._ungrounded_params`)
- Test: `agent_core/tests/test_manager_agent.py`

**Interfaces:**
- Produces:
  - `ungrounded_params(spec, tool_call, messages, stored_results: dict[str, list[str]] | None = None, strict: bool = False) -> set[str]`
  - `ManagerAgent._ungrounded_params(self, tool_call, messages, stored_results=None) -> set[str]`
  - Stored texts count exactly like tool_result blocks of that tool.
  - With `strict=True` and nothing seen at all, every supplied param is reported missing.

- [ ] **Step 1: Write failing tests.** Append, using the file's existing `_call` helper from the grounding section:

```python
def test_stored_result_grounds_value_when_exchange_not_in_messages():
    from src.manager_agent import ungrounded_params
    spec = {"profile_item_id": ["fetch_profile"]}
    tc = _call("apply_job", {"profile_item_id": "real-id"})
    stored = {"fetch_profile": ['{"items":[{"item_id":"real-id"}]}']}
    assert ungrounded_params(spec, tc, [], stored_results=stored) == set()


def test_stored_result_from_other_tool_does_not_ground():
    from src.manager_agent import ungrounded_params
    spec = {"profile_item_id": ["fetch_profile"]}
    tc = _call("apply_job", {"profile_item_id": "job-id"})
    stored = {"fetch_jobs": ['[{"item_id":"job-id"}]']}
    assert ungrounded_params(spec, tc, [], stored_results=stored) == {"profile_item_id"}


def test_strict_rejects_when_nothing_seen():
    from src.manager_agent import ungrounded_params
    tc = _call("remember", {"value": "x"})
    assert ungrounded_params({"value": ["fetch_profile"]}, tc, [], strict=True) == {"value"}
    assert ungrounded_params({"value": ["fetch_profile"]}, tc, []) == set()   # lenient default unchanged
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -v -k "stored_result or strict_rejects"`
Expected: FAIL (`TypeError`: unexpected keyword argument)

- [ ] **Step 3: Implement.**
  - Add the two keyword parameters to the `ungrounded_params` signature, and document them under `Args:`:
    - `stored_results`: tool name → serialised results that are no longer in the message list (spec §7.4);
    - `strict`: reject when nothing has been fetched at all (used by `remember`).
  - Immediately before `if not seen_any:`, insert:

```python
    for src, texts in (stored_results or {}).items():
        for text in texts or []:
            if isinstance(text, str) and text:
                seen_any.append(text)
                by_tool.setdefault(str(src), []).append(text)
```

  - Replace the body of the `if not seen_any:` block (keep its comment) with:

```python
        if not strict:
            return set()
        return {n for n in spec if (tool_call.input_params or {}).get(n) not in (None, "")}
```

  - Change the wrapper:

```python
    def _ungrounded_params(self, tool_call, messages: list,
                           stored_results: dict[str, list[str]] | None = None) -> set[str]:
        """Instance wrapper around :func:`ungrounded_params` for this agent's config."""
        return ungrounded_params(
            self._grounded_params.get(tool_call.tool_name) or {}, tool_call, messages,
            stored_results=stored_results,
        )
```

- [ ] **Step 4: Run and check it passes.**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent-core): ground ids against stored tool results; strict mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Runtime config schema

**Files:**
- Modify: `agent_core/src/schema/config.py`
- Test: `agent_core/tests/test_schema_config.py`

**Interfaces:**
- Produces:
  - `ToolCacheConfig(scope: Literal["session","user"], ttl_seconds: int > 0, keep: list[str], vary_on: list[str])`
  - `ConnectorDef.cache: Optional[ToolCacheConfig] = None`
  - `ConnectorDef.invalidates: list[str] = []`
  - `ToolResultsConfig(max_user_ttl_seconds: int > 0 = 86400)`
  - `MemoryToolField(scope: Literal["session","persistent"], description: str = "", grounded_in: list[str] = [])`
  - `MemoryToolConfig(name: str = "remember", fields: dict[str, MemoryToolField])` with at least 1 field
  - `MergedConfig.tool_results: ToolResultsConfig`, `MergedConfig.memory_tool: Optional[MemoryToolConfig] = None`
- Validators (raise `ValueError`):
  - `cache` present on a `write`/`identity` connector;
  - `invalidates` present on a non-write connector;
  - an `invalidates` target that is not a `read` connector name;
  - a user-scope `ttl_seconds` greater than `tool_results.max_user_ttl_seconds`;
  - a `grounded_in` name that is not a connector name;
  - `memory_tool.name` equal to a connector name.

- [ ] **Step 1: Write failing tests.** Append to `test_schema_config.py`. Use the file's existing way of building a minimal valid merged config, called here `_base()`: a helper returning a deep copy of the file's minimal valid dict. Add such a helper if the file has none.

```python
import copy
import pytest
from pydantic import ValidationError
from src.schema.config import MergedConfig


def _with(conn_read=None, conn_write=None, **top):
    cfg = copy.deepcopy(_base())
    cfg.setdefault("connectors", {})
    cfg["connectors"]["read"] = conn_read or []
    cfg["connectors"]["write"] = conn_write or []
    cfg.update(top)
    return cfg


READ = {"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 1800, "keep": ["items"]}}
WRITE = {"name": "save_profile", "invalidates": ["fetch_profile"]}
MT = {"name": "remember", "fields": {"profile_item_id": {
    "scope": "session", "grounded_in": ["fetch_profile", "save_profile"]}}}


def test_valid_cache_invalidates_memory_tool():
    MergedConfig.model_validate(_with([READ], [WRITE], memory_tool=MT))


@pytest.mark.parametrize("cfg", [
    _with([], [{"name": "save_profile", "cache": {"scope": "user", "ttl_seconds": 60}}]),
    _with([{"name": "fetch_profile", "invalidates": ["x"]}], []),
    _with([READ], [{"name": "save_profile", "invalidates": ["nope"]}]),
    _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 90000}}], []),
    _with([{"name": "fetch_profile", "cache": {"scope": "agent", "ttl_seconds": 60}}], []),
    _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 0}}], []),
    _with([READ], [WRITE], memory_tool={"fields": {"f": {"scope": "session", "grounded_in": ["ghost"]}}}),
    _with([READ], [WRITE], memory_tool={"name": "fetch_profile", "fields": {"f": {"scope": "session"}}}),
    _with([READ], [WRITE], memory_tool={"fields": {}}),
])
def test_invalid_configs_rejected(cfg):
    with pytest.raises(ValidationError):
        MergedConfig.model_validate(cfg)


def test_user_ttl_cap_is_configurable():
    cfg = _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 90000}}], [],
                tool_results={"max_user_ttl_seconds": 100000})
    MergedConfig.model_validate(cfg)
```

If the file validates through a different entry point (e.g. `MergedConfig.validate_full(...)`), use that instead of `model_validate`, and assert on the exception type it raises.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -v -k "cache or invalid_configs or ttl_cap"`
Expected: FAIL (extra fields forbidden)

- [ ] **Step 3: Implement.**
  - Add `model_validator` to the pydantic import.
  - Above `ConnectorDef`:

```python
class ToolCacheConfig(BaseModel):
    """Per-connector tool-result cache rule (tool-result persistence spec §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["session", "user"]
    ttl_seconds: int = Field(gt=0)
    keep: list[str] = Field(default_factory=list)
    vary_on: list[str] = Field(default_factory=list)
```

  - Add to `ConnectorDef`:

```python
    cache: Optional[ToolCacheConfig] = None
    invalidates: list[str] = Field(default_factory=list)
```

  - Add to `ConnectorsConfig`:

```python
    @model_validator(mode="after")
    def _check_cache_rules(self) -> "ConnectorsConfig":
        read_names = {c.name for c in self.read}
        for group_name in ("write", "identity"):
            for c in getattr(self, group_name):
                if c.cache is not None:
                    raise ValueError(f"connector '{c.name}': cache is only allowed on read connectors")
        for group_name in ("read", "identity"):
            for c in getattr(self, group_name):
                if c.invalidates:
                    raise ValueError(f"connector '{c.name}': invalidates is only allowed on write connectors")
        for c in self.write:
            for target in c.invalidates:
                if target not in read_names:
                    raise ValueError(f"connector '{c.name}': invalidates unknown read connector '{target}'")
        return self
```

  - Above `MergedConfig`:

```python
class ToolResultsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_user_ttl_seconds: int = Field(default=86400, gt=0)


class MemoryToolField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["session", "persistent"]
    description: str = ""
    grounded_in: list[str] = Field(default_factory=list)


class MemoryToolConfig(BaseModel):
    """The framework `remember` tool (tool-result persistence spec §8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "remember"
    fields: dict[str, MemoryToolField] = Field(min_length=1)
```

  - Add to `MergedConfig`:

```python
    tool_results: ToolResultsConfig = Field(default_factory=ToolResultsConfig)
    memory_tool: Optional[MemoryToolConfig] = None

    @model_validator(mode="after")
    def _check_tool_result_rules(self) -> "MergedConfig":
        c = self.connectors
        all_conns = [*c.read, *c.write, *c.identity, *c.internal]
        names = {x.name for x in all_conns}
        cap = self.tool_results.max_user_ttl_seconds
        for x in c.read:
            if x.cache and x.cache.scope == "user" and x.cache.ttl_seconds > cap:
                raise ValueError(f"connector '{x.name}': user-scope ttl_seconds {x.cache.ttl_seconds} "
                                 f"exceeds tool_results.max_user_ttl_seconds {cap}")
        if self.memory_tool:
            if self.memory_tool.name in names:
                raise ValueError(f"memory_tool.name '{self.memory_tool.name}' collides with a connector")
            for fname, f in self.memory_tool.fields.items():
                for src in f.grounded_in:
                    if src not in names:
                        raise ValueError(f"memory_tool.fields.{fname}.grounded_in: unknown connector '{src}'")
        return self
```

  - If `MergedConfig` already has a `model_validator`, keep both; they are independent.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/schema/config.py agent_core/tests/test_schema_config.py
git commit -m "feat(agent-core): schema for tool-result cache, invalidation and memory tool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `tool_results.py` — policies and the per-turn cache

**Files:**
- Create: `agent_core/src/tool_results.py`
- Test: `agent_core/tests/test_tool_results.py`

**Interfaces:**
- Consumes: `ToolCall`, `ToolResult` (with `projected`) from `src.models`
- Produces:
  - `FORCE_REFRESH = "force_refresh"`
  - `CachePolicy(tool, scope, ttl_seconds, keep: tuple, vary_on: tuple)` (frozen)
  - `ToolResultPolicies(cache: dict[str, CachePolicy], invalidates: dict[str, tuple[str, ...]])` with `.from_config(config: dict | None)`
  - `args_hash(input_params: dict | None, vary_values: dict | None = None) -> str` (32 hex chars, ignores `force_refresh`)
  - `TurnToolCache(policies, entries: list[dict], session: dict, now: Callable[[], float] = time.time)` with methods:
    - `.lookup(tc) -> ToolResult | None`
    - `.prepare(tc) -> ToolCall`
    - `.after_call(tc, result) -> None`
    - `.fresh_tools() -> set[str]`
    - `.stored_results_by_tool() -> dict[str, list[str]]`
    - `.render_known_facts() -> str`
    - `.has_pending() -> bool`
    - `.drain_batch() -> dict` (`{"invalidate": [...], "puts": [...]}`, then clears pending)
  - `augment_tool_definitions(defs: list[dict] | None, policies, remember_definition: dict | None = None) -> list[dict] | None`

- [ ] **Step 1: Write failing tests.** Create `agent_core/tests/test_tool_results.py`:

```python
"""Unit tests for the per-turn tool-result cache (spec §7)."""

from __future__ import annotations

import json

from src.models import ToolCall, ToolResult
from src.tool_results import (
    FORCE_REFRESH, ToolResultPolicies, TurnToolCache, args_hash, augment_tool_definitions,
)

CONFIG = {"connectors": {
    "read": [
        {"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 1800,
                                           "keep": ["acting_as_user_id", "items"]}},
        {"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600,
                                        "vary_on": ["trade", "location"]}},
        {"name": "uncached"},
    ],
    "write": [{"name": "save_profile", "invalidates": ["fetch_profile"]}],
}}
POL = ToolResultPolicies.from_config(CONFIG)
NOW = 10_000.0


def clock(t=NOW):
    return lambda: t


def tc(name, params=None, tid="tu1"):
    return ToolCall(tool_name=name, tool_use_id=tid, input_params=params or {})


def live(name, data, projected=True, success=True):
    return ToolResult(tool_use_id="x", tool_name=name, result={}, success=success,
                      result_text=json.dumps(data), projected=projected)


def entry(tool, data, h, fetched=NOW - 180, ttl=1800, scope="user"):
    return {"tool": tool, "args_hash": h, "data": data, "fetched_at": fetched,
            "expires_at": fetched + ttl, "origin": "turn", "scope": scope}


def test_policies_from_config():
    assert set(POL.cache) == {"fetch_profile", "fetch_jobs"}
    assert POL.invalidates == {"save_profile": ("fetch_profile",)}
    assert ToolResultPolicies.from_config(None).cache == {}


def test_args_hash_stable_sorted_and_ignores_force_refresh():
    assert args_hash({"a": 1, "b": 2}) == args_hash({"b": 2, "a": 1})
    assert args_hash({"a": 1, FORCE_REFRESH: True}) == args_hash({"a": 1})
    assert args_hash({"a": 1}, {"trade": "x"}) != args_hash({"a": 1}, {"trade": "y"})
    assert len(args_hash({})) == 32


def test_hit_returns_stored_data_labelled_with_age():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {"items": [1]}, h)], {}, clock())
    r = cache.lookup(tc("fetch_profile", tid="tu9"))
    assert r is not None and r.success and r.tool_use_id == "tu9"
    assert r.result_text.startswith("(stored result, fetched 3 min ago)")
    assert '"items": [1]' in r.result_text


def test_miss_for_uncached_tool_other_args_and_expired():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h, fetched=NOW - 2000)], {}, clock())
    assert cache.lookup(tc("fetch_profile")) is None        # expired
    assert cache.lookup(tc("uncached")) is None
    cache2 = TurnToolCache(POL, [entry("fetch_jobs", [], args_hash({}, {"trade": "a", "location": "b"}),
                                       scope="session")], {"trade": "a", "location": "c"}, clock())
    assert cache2.lookup(tc("fetch_jobs")) is None           # vary_on changed


def test_force_refresh_bypasses_and_is_stripped():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h)], {}, clock())
    call = tc("fetch_profile", {FORCE_REFRESH: True})
    assert cache.lookup(call) is None
    assert FORCE_REFRESH not in cache.prepare(call).input_params
    assert cache.prepare(tc("uncached", {"q": 1})).input_params == {"q": 1}


def test_after_call_stores_projected_json_with_keep():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("fetch_profile"), live("fetch_profile",
                     {"acting_as_user_id": "u", "items": [], "compliance": ["x"]}))
    batch = cache.drain_batch()
    assert batch["invalidate"] == []
    [put] = batch["puts"]
    assert put["data"] == {"acting_as_user_id": "u", "items": []}
    assert (put["scope"], put["ttl_seconds"], put["origin"]) == ("user", 1800, "turn")
    assert cache.lookup(tc("fetch_profile")) is not None     # same-turn overlay
    assert cache.drain_batch() == {"invalidate": [], "puts": []}


def test_after_call_skips_unprojected_invalid_and_failed():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, projected=False))
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, success=False))
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=True,
                     result_text="not json", projected=True)
    cache.after_call(tc("fetch_profile"), bad)
    assert not cache.has_pending()


def test_write_call_invalidates_even_on_failure_and_drops_same_turn_puts():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h)], {}, clock())
    cache.after_call(tc("fetch_profile", {"x": 1}), live("fetch_profile", {"items": []}))
    cache.after_call(tc("save_profile"), live("save_profile", {}, success=False))
    assert cache.lookup(tc("fetch_profile")) is None
    assert cache.drain_batch() == {"invalidate": ["fetch_profile"], "puts": []}


def test_store_after_invalidate_in_same_turn_survives():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("save_profile"), live("save_profile", {}))
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {"items": [2]}))
    batch = cache.drain_batch()
    assert batch["invalidate"] == ["fetch_profile"] and len(batch["puts"]) == 1


def test_fresh_tools_stored_results_and_ignores_unknown_or_malformed_entries():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {"items": ["id-1"]}, h),
                                entry("not_configured", {}, h), {"junk": True}, "nope"], {}, clock())
    assert cache.fresh_tools() == {"fetch_profile"}
    assert "id-1" in cache.stored_results_by_tool()["fetch_profile"][0]


def test_render_known_facts():
    assert TurnToolCache(POL, [], {}, clock()).render_known_facts() == ""
    h = args_hash({})
    text = TurnToolCache(POL, [entry("fetch_profile", {"items": []}, h)], {}, clock()).render_known_facts()
    assert "Use them instead of calling the tool again" in text
    assert "force_refresh" in text
    assert "- fetch_profile — fetched 3 min ago, valid for 27 more min:" in text


def test_augment_adds_force_refresh_and_remember_only_when_tools_present():
    defs = [{"name": "fetch_profile", "description": "d",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "uncached", "description": "d", "input_schema": {"type": "object"}}]
    rem = {"name": "remember", "description": "r", "input_schema": {"type": "object"}}
    out = augment_tool_definitions(defs, POL, rem)
    assert FORCE_REFRESH in out[0]["input_schema"]["properties"]
    assert FORCE_REFRESH not in (defs[0]["input_schema"]["properties"])   # input not mutated
    assert "properties" not in out[1]["input_schema"] or FORCE_REFRESH not in out[1]["input_schema"]["properties"]
    assert out[-1]["name"] == "remember"
    assert augment_tool_definitions([], POL, rem) == []
    assert augment_tool_definitions(None, POL, rem) is None
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_tool_results.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement.** Create `agent_core/src/tool_results.py`:

```python
"""
agent_core/src/tool_results.py

Per-turn view of stored tool results (tool-result persistence spec §7).

Agent Core builds one TurnToolCache per turn from ContextBundle.tool_results.
It renders <known_facts>, filters the replay, serves hits before execution,
records live projected results and write-tool invalidations, and hands the
pending changes to Memory Layer in one batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)

FORCE_REFRESH = "force_refresh"

_KNOWN_FACTS_HEADER = (
    "Results of earlier tool calls. Use them instead of calling the tool again.\n"
    "Call the tool only if what you need is missing here, or the caller says it "
    "changed — then pass force_refresh=true."
)
_FORCE_REFRESH_SCHEMA = {
    "type": "boolean",
    "description": "Set true only to bypass the stored result in <known_facts> and fetch live data.",
}

_counter = None


def _outcome_counter():
    """Lazily create the OTel counter; a no-op meter when OTel is not configured."""
    global _counter
    if _counter is None:
        from opentelemetry import metrics
        _counter = metrics.get_meter("agent_core.tool_results").create_counter(
            "agent_core.tool_result.outcomes_total",
            description="Tool-result cache outcomes, tagged by tool and outcome.",
        )
    return _counter


def _log(outcome: str, tool: str, scope: str = "", age_s: Optional[int] = None,
         ttl_s: Optional[int] = None) -> None:
    logger.info("tool_result", extra={
        "operation": "tool_results.turn_cache", "status": "success", "tool": tool,
        "scope": scope, "outcome": outcome, "age_s": age_s, "ttl_s": ttl_s})
    try:
        _outcome_counter().add(1, {"tool": tool, "outcome": outcome})
    except Exception:  # metrics must never break a turn
        pass


@dataclass(frozen=True)
class CachePolicy:
    """Cache rule for one read connector."""

    tool: str
    scope: str
    ttl_seconds: int
    keep: tuple[str, ...] = ()
    vary_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResultPolicies:
    """All cache and invalidation rules, read once from agent_core config."""

    cache: dict[str, CachePolicy] = field(default_factory=dict)
    invalidates: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict | None) -> "ToolResultPolicies":
        """Build from the merged agent_core config dict (already schema-validated)."""
        cache: dict[str, CachePolicy] = {}
        inval: dict[str, tuple[str, ...]] = {}
        for conns in ((config or {}).get("connectors") or {}).values():
            for c in conns or []:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                name = str(c["name"])
                cc = c.get("cache")
                if isinstance(cc, dict):
                    cache[name] = CachePolicy(
                        tool=name, scope=str(cc["scope"]), ttl_seconds=int(cc["ttl_seconds"]),
                        keep=tuple(cc.get("keep") or ()), vary_on=tuple(cc.get("vary_on") or ()),
                    )
                if c.get("invalidates"):
                    inval[name] = tuple(str(t) for t in c["invalidates"])
        return cls(cache=cache, invalidates=inval)


def args_hash(input_params: dict | None, vary_values: dict | None = None) -> str:
    """Stable hash of the LLM args (minus force_refresh) plus vary_on session values."""
    params = {k: v for k, v in (input_params or {}).items() if k != FORCE_REFRESH}
    payload = json.dumps({"a": params, "v": vary_values or {}}, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _ago(seconds: float) -> str:
    return "just now" if seconds < 60 else f"{int(seconds // 60)} min ago"


class TurnToolCache:
    """One turn's view of stored tool results, plus the changes it makes.

    Args:
        policies: Cache and invalidation rules.
        entries: ``ContextBundle.tool_results``; unknown or malformed entries are ignored.
        session: Session state, for ``vary_on`` values.
        now: Clock, injectable for tests.
    """

    def __init__(self, policies: ToolResultPolicies, entries: list[dict] | None,
                 session: dict | None, now: Callable[[], float] = time.time) -> None:
        self._p = policies
        self._session = session or {}
        self._now = now
        self._entries: dict[tuple[str, str], dict] = {}
        t = now()
        for e in entries or []:
            if not isinstance(e, dict) or e.get("tool") not in policies.cache:
                continue
            try:
                if float(e.get("expires_at", 0)) <= t:
                    continue
            except (TypeError, ValueError):
                continue
            self._entries[(str(e["tool"]), str(e.get("args_hash", "")))] = e
        self._puts: list[dict] = []
        self._invalidated: list[str] = []

    def _hash(self, tool_call: ToolCall) -> str:
        pol = self._p.cache[tool_call.tool_name]
        return args_hash(tool_call.input_params, {k: self._session.get(k) for k in pol.vary_on})

    def _fresh(self) -> dict[tuple[str, str], dict]:
        t = self._now()
        return {k: e for k, e in self._entries.items() if float(e["expires_at"]) > t}

    def lookup(self, tool_call: ToolCall) -> ToolResult | None:
        """Return a stored result for this exact call, or None to call live."""
        pol = self._p.cache.get(tool_call.tool_name)
        if pol is None:
            return None
        if (tool_call.input_params or {}).get(FORCE_REFRESH) is True:
            _log("refresh", tool_call.tool_name, pol.scope)
            return None
        e = self._fresh().get((tool_call.tool_name, self._hash(tool_call)))
        if e is None:
            _log("miss", tool_call.tool_name, pol.scope)
            return None
        age = self._now() - float(e["fetched_at"])
        _log("hit", tool_call.tool_name, pol.scope, age_s=int(age), ttl_s=pol.ttl_seconds)
        data = e["data"]
        return ToolResult(
            tool_use_id=tool_call.tool_use_id, tool_name=tool_call.tool_name,
            result=data if isinstance(data, dict) else {"items": data}, success=True,
            result_text=f"(stored result, fetched {_ago(age)}) {json.dumps(data, ensure_ascii=False)}",
        )

    def prepare(self, tool_call: ToolCall) -> ToolCall:
        """Return the call with the framework-only force_refresh param removed."""
        if FORCE_REFRESH not in (tool_call.input_params or {}):
            return tool_call
        return replace(tool_call, input_params={
            k: v for k, v in tool_call.input_params.items() if k != FORCE_REFRESH})

    def after_call(self, tool_call: ToolCall, result: ToolResult) -> None:
        """Record a live call: invalidate what a write affects, store a cacheable result."""
        for target in self._p.invalidates.get(tool_call.tool_name, ()):
            self._invalidate(target)
        pol = self._p.cache.get(tool_call.tool_name)
        if pol is None or not result.success:
            return
        if not getattr(result, "projected", False):
            _log("reject_unprojected", tool_call.tool_name, pol.scope)
            return
        try:
            data: Any = json.loads(result.result_text)
        except (TypeError, ValueError):
            _log("reject_invalid", tool_call.tool_name, pol.scope)
            return
        if pol.keep and isinstance(data, dict):
            data = {k: data[k] for k in pol.keep if k in data}
        h, t = self._hash(tool_call), self._now()
        self._entries[(tool_call.tool_name, h)] = {
            "tool": tool_call.tool_name, "args_hash": h, "data": data, "fetched_at": t,
            "expires_at": t + pol.ttl_seconds, "origin": "turn", "scope": pol.scope}
        self._puts.append({"scope": pol.scope, "tool": tool_call.tool_name, "args_hash": h,
                           "data": data, "ttl_seconds": pol.ttl_seconds, "origin": "turn"})
        _log("store", tool_call.tool_name, pol.scope, ttl_s=pol.ttl_seconds)

    def _invalidate(self, tool: str) -> None:
        self._entries = {k: v for k, v in self._entries.items() if k[0] != tool}
        self._puts = [p for p in self._puts if p["tool"] != tool]
        if tool not in self._invalidated:
            self._invalidated.append(tool)
        _log("invalidate", tool)

    def fresh_tools(self) -> set[str]:
        """Tools with at least one unexpired entry (their exchanges leave the replay)."""
        return {tool for tool, _ in self._fresh()}

    def stored_results_by_tool(self) -> dict[str, list[str]]:
        """Serialised unexpired data per tool, for grounding checks."""
        out: dict[str, list[str]] = {}
        for (tool, _), e in self._fresh().items():
            out.setdefault(tool, []).append(json.dumps(e["data"], ensure_ascii=False))
        return out

    def render_known_facts(self) -> str:
        """Body of the <known_facts> prompt block; empty when nothing is fresh."""
        t = self._now()
        lines = []
        for (tool, _), e in sorted(self._fresh().items()):
            remaining = max(1, int((float(e["expires_at"]) - t) // 60))
            lines.append(f"- {tool} — fetched {_ago(t - float(e['fetched_at']))}, valid for "
                         f"{remaining} more min: {json.dumps(e['data'], ensure_ascii=False)}")
        return _KNOWN_FACTS_HEADER + "\n" + "\n".join(lines) if lines else ""

    def has_pending(self) -> bool:
        """True when there are changes not yet sent to Memory Layer."""
        return bool(self._puts or self._invalidated)

    def drain_batch(self) -> dict:
        """Return pending changes for Memory Layer and clear them (the overlay stays)."""
        batch = {"invalidate": list(self._invalidated), "puts": list(self._puts)}
        self._invalidated, self._puts = [], []
        return batch


def augment_tool_definitions(defs: list[dict] | None, policies: ToolResultPolicies,
                             remember_definition: dict | None = None) -> list[dict] | None:
    """Add force_refresh to cached tools and the remember tool to a non-empty tool list.

    ``None`` and ``[]`` pass through unchanged: a subagent with no tools must not
    gain one. Input dicts are never mutated.
    """
    if not defs:
        return defs
    out: list[dict] = []
    for d in defs:
        if d.get("name") in policies.cache:
            schema = dict(d.get("input_schema") or {"type": "object"})
            props = dict(schema.get("properties") or {})
            props[FORCE_REFRESH] = dict(_FORCE_REFRESH_SCHEMA)
            schema["properties"] = props
            d = {**d, "input_schema": schema}
        out.append(d)
    if remember_definition and all(x.get("name") != remember_definition["name"] for x in out):
        out.append(remember_definition)
    return out
```

- [ ] **Step 4: Run and check it passes.**

Run: `cd agent_core && uv run pytest tests/test_tool_results.py -v`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/tool_results.py agent_core/tests/test_tool_results.py
git commit -m "feat(agent-core): per-turn tool-result cache and known_facts rendering

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `remember.py` — the memory tool

**Files:**
- Create: `agent_core/src/remember.py`
- Test: `agent_core/tests/test_remember.py`

**Interfaces:**
- Consumes: `ungrounded_params(..., stored_results=, strict=True)` (Task 7); `ToolCall`, `ToolResult`
- Produces:
  - `RememberTool.from_config(config: dict | None) -> RememberTool | None` (None when there is no `memory_tool`)
  - `.name: str`
  - `.definition() -> dict`
  - `.handle(tool_call, messages, stored_results, write_strict: Callable[[str, str, Any], tuple[bool, str]], on_saved: Callable[[str, str, Any], None]) -> ToolResult`
  - `async .handle_async(tool_call, messages, stored_results, write_strict: Callable[[str, str, Any], Awaitable[tuple[bool, str]]], on_saved) -> ToolResult`
  - `write_strict(scope, key, value)`; `on_saved(scope, key, value)`
  - Rejections return `ToolResult(success=False, error="REMEMBER_REJECTED", result_text=<guidance>)`.

- [ ] **Step 1: Write failing tests.** Create `agent_core/tests/test_remember.py`:

```python
"""Unit tests for the framework remember tool (spec §8)."""

from __future__ import annotations

import pytest

from src.models import ToolCall
from src.remember import RememberTool

CONFIG = {"memory_tool": {"name": "remember", "fields": {
    "profile_item_id": {"scope": "session", "description": "chosen profile",
                        "grounded_in": ["fetch_profile", "save_profile"]},
    "profile_action": {"scope": "session"},
}}}
TOOL = RememberTool.from_config(CONFIG)
STORED = {"fetch_profile": ['{"items":[{"item_id":"p-1"},{"item_id":"p-2"}]}']}


def call(field, value):
    return ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": field, "value": value})


class Recorder:
    def __init__(self, result=(True, "")):
        self.result, self.writes, self.saved = result, [], []

    def write(self, scope, key, value):
        self.writes.append((scope, key, value))
        return self.result

    def on_saved(self, scope, key, value):
        self.saved.append((scope, key, value))


def test_from_config_none_without_section():
    assert RememberTool.from_config({}) is None


def test_definition_lists_fields_as_enum():
    d = TOOL.definition()
    assert d["name"] == "remember"
    assert d["input_schema"]["properties"]["field"]["enum"] == ["profile_action", "profile_item_id"]
    assert d["input_schema"]["required"] == ["field", "value"]


def test_grounded_value_is_written_and_reported():
    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "p-2"), [], STORED, rec.write, rec.on_saved)
    assert r.success and r.tool_use_id == "tu_r"
    assert rec.writes == [("session", "profile_item_id", "p-2")]
    assert rec.saved == [("session", "profile_item_id", "p-2")]


@pytest.mark.parametrize("field,value,stored", [
    ("profile_item_id", "invented", STORED),      # not in any result
    ("profile_item_id", "p-1", {}),              # nothing fetched: strict
    ("not_a_field", "x", STORED),
    ("profile_action", "", STORED),
])
def test_rejections_write_nothing(field, value, stored):
    rec = Recorder()
    r = TOOL.handle(call(field, value), [], stored, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_memory_layer_rejection_is_surfaced():
    rec = Recorder(result=(False, "'zzz' is not one of [...]"))
    r = TOOL.handle(call("profile_action", "zzz"), [], STORED, rec.write, rec.on_saved)
    assert r.success is False and "not one of" in r.result_text
    assert rec.saved == []


async def test_handle_async():
    rec = Recorder()

    async def awrite(scope, key, value):
        return rec.write(scope, key, value)

    r = await TOOL.handle_async(call("profile_action", "use_existing"), [], STORED, awrite, rec.on_saved)
    assert r.success and rec.saved == [("session", "profile_action", "use_existing")]
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_remember.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement.** Create `agent_core/src/remember.py`:

```python
"""
agent_core/src/remember.py

The framework `remember` tool (tool-result persistence spec §8).

Lets the LLM save a value the caller chose into a declared state field.
Grounding is checked here; type/enum is checked by Memory Layer's strict write.
Never sent to Action Gateway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from src.manager_agent import ungrounded_params
from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RememberField:
    """One writable field."""

    name: str
    scope: str
    description: str = ""
    grounded_in: tuple[str, ...] = ()


def _reject(tool_call: ToolCall, name: str, message: str) -> ToolResult:
    logger.info("tool_result", extra={"operation": "remember.handle", "status": "rejected",
                                      "tool": name, "outcome": "remember_reject"})
    return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=name, result={},
                      success=False, error="REMEMBER_REJECTED", result_text=message)


class RememberTool:
    """Validated writes of caller-chosen values.

    Args:
        name: Tool name the LLM calls.
        fields: Writable fields by name.
    """

    def __init__(self, name: str, fields: dict[str, RememberField]) -> None:
        self.name = name
        self._fields = fields

    @classmethod
    def from_config(cls, config: dict | None) -> "RememberTool | None":
        """Build from agent_core config; None when ``memory_tool`` is absent."""
        mt = (config or {}).get("memory_tool")
        if not isinstance(mt, dict) or not mt.get("fields"):
            return None
        fields = {
            str(n): RememberField(name=str(n), scope=str(f.get("scope", "session")),
                                  description=str(f.get("description") or ""),
                                  grounded_in=tuple(f.get("grounded_in") or ()))
            for n, f in mt["fields"].items()
        }
        return cls(str(mt.get("name") or "remember"), fields)

    def definition(self) -> dict:
        """Tool definition in the registry's ``{name, description, input_schema}`` shape."""
        names = sorted(self._fields)
        described = "; ".join(f"{n}: {self._fields[n].description}" for n in names
                              if self._fields[n].description)
        return {
            "name": self.name,
            "description": ("Save a value the caller chose, so later steps use it without you "
                            "repeating it. Only for the listed fields. " + described).strip(),
            "input_schema": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": names},
                               "value": {"type": "string"}},
                "required": ["field", "value"],
                "additionalProperties": False,
            },
        }

    def _check(self, tool_call: ToolCall, messages: list,
               stored_results: dict[str, list[str]]) -> Union[ToolResult, tuple[RememberField, Any]]:
        params = tool_call.input_params or {}
        f = self._fields.get(str(params.get("field")))
        if f is None:
            return _reject(tool_call, self.name,
                           f"You can only save these fields: {', '.join(sorted(self._fields))}.")
        value = params.get("value")
        if value in (None, ""):
            return _reject(tool_call, self.name, f"No value given for {f.name}.")
        if f.grounded_in:
            probe = ToolCall(tool_name=self.name, tool_use_id=tool_call.tool_use_id,
                             input_params={"value": value})
            if ungrounded_params({"value": list(f.grounded_in)}, probe, messages,
                                 stored_results=stored_results, strict=True):
                return _reject(tool_call, self.name,
                               f"{f.name} must be copied exactly from a "
                               f"{' or '.join(f.grounded_in)} result. Re-read it and try again.")
        return f, value

    def _done(self, tool_call: ToolCall, f: RememberField, value: Any, ok: bool, reason: str,
              on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        if not ok:
            return _reject(tool_call, self.name, f"Not saved: {reason}")
        on_saved(f.scope, f.name, value)
        logger.info("tool_result", extra={"operation": "remember.handle", "status": "success",
                                          "tool": self.name, "outcome": "remember_write"})
        return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=self.name,
                          result={"saved": f.name}, success=True, result_text=f"Saved {f.name}.")

    def handle(self, tool_call: ToolCall, messages: list, stored_results: dict[str, list[str]],
               write_strict: Callable[[str, str, Any], tuple[bool, str]],
               on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Validate and save (sync paths)."""
        checked = self._check(tool_call, messages, stored_results)
        if isinstance(checked, ToolResult):
            return checked
        f, value = checked
        ok, reason = write_strict(f.scope, f.name, value)
        return self._done(tool_call, f, value, ok, reason, on_saved)

    async def handle_async(self, tool_call: ToolCall, messages: list,
                           stored_results: dict[str, list[str]],
                           write_strict: Callable[[str, str, Any], Awaitable[tuple[bool, str]]],
                           on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Validate and save (stream path)."""
        checked = self._check(tool_call, messages, stored_results)
        if isinstance(checked, ToolResult):
            return checked
        f, value = checked
        ok, reason = await write_strict(f.scope, f.name, value)
        return self._done(tool_call, f, value, ok, reason, on_saved)
```

If importing `ungrounded_params` from `src.manager_agent` creates an import cycle (for example, if `manager_agent` later imports `remember`), move `ungrounded_params` into a new `src/grounding.py`, re-export it from `manager_agent`, and import it from `grounding` here.

- [ ] **Step 4: Run and check it passes.**

Run: `cd agent_core && uv run pytest tests/test_remember.py -v`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/remember.py agent_core/tests/test_remember.py
git commit -m "feat(agent-core): grounded remember tool for caller-chosen values

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Sync path — `ManagerAgent.run_turn` and `process_turn`

**Files:**
- Modify: `agent_core/src/manager_agent.py` (`run_turn`, `build_system_prompt`)
- Modify: `agent_core/src/orchestrator.py` (`AgentCore.__init__`, `process_turn`, `_prepend_tool_replay`, `_build_tool_exchange_messages`)
- Test: `agent_core/tests/test_manager_agent.py`, `agent_core/tests/test_orchestrator.py`, `agent_core/tests/test_stream_turn.py` (replay helper)

**Interfaces:**
- Consumes: `TurnToolCache`, `ToolResultPolicies`, `augment_tool_definitions` (Task 9); `RememberTool` (Task 10); `apply_tool_results`, `write_strict` (Task 6)
- Produces:
  - `run_turn(..., tool_cache: TurnToolCache | None = None, remember_name: str = "", remember_handler: Callable[[ToolCall, list], ToolResult] | None = None)`
  - `build_system_prompt(..., known_facts: str = "")`: renders `xml("known_facts", known_facts)` in Tier 3, right after `known_profile`
  - `_build_tool_exchange_messages(exchanges, undelivered_note="", skip_tools: frozenset | set = frozenset())`
  - `_prepend_tool_replay(..., skip_tools=frozenset())`
  - `AgentCore._tool_policies: ToolResultPolicies`, `AgentCore._remember: RememberTool | None`, built in `__init__` from `config`.

- [ ] **Step 1: Write failing tests.**

In `test_manager_agent.py`:

```python
from src.tool_results import ToolResultPolicies, TurnToolCache, args_hash

_POL = ToolResultPolicies.from_config({"connectors": {"read": [
    {"name": "get_balance", "cache": {"scope": "session", "ttl_seconds": 600}}]}})


def _entry():
    return {"tool": "get_balance", "args_hash": args_hash({"account": "12345"}),
            "data": {"balance": 100}, "fetched_at": 0, "expires_at": 9e12,
            "origin": "turn", "scope": "session"}


def test_run_turn_serves_cache_hit_without_gateway():
    tc = _tool_call()
    agent, llm, _, gateway, _ = _make_manager([_tool_response(tc), _text_response("100.")])
    cache = TurnToolCache(_POL, [_entry()], {})
    _, _, results = agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc), tool_cache=cache)
    gateway.execute.assert_not_called()
    assert results[0].result_text.startswith("(stored result")


def test_run_turn_miss_executes_and_records():
    tc = _tool_call()
    live = ToolResult(tool_use_id="tu_abc", tool_name="get_balance", result={}, success=True,
                      result_text='{"balance": 5}', projected=True)
    agent, _, _, gateway, _ = _make_manager([_tool_response(tc), _text_response()], tool_result=live)
    cache = TurnToolCache(_POL, [], {})
    agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc), tool_cache=cache)
    gateway.execute.assert_called_once()
    assert cache.drain_batch()["puts"][0]["data"] == {"balance": 5}


def test_run_turn_routes_remember_to_handler():
    tc = ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": "f", "value": "v"})
    agent, _, _, gateway, _ = _make_manager([_tool_response(tc), _text_response()])
    seen = []

    def handler(call, messages):
        seen.append(call.tool_use_id)
        return ToolResult(tool_use_id=call.tool_use_id, tool_name="remember", result={}, success=True,
                          result_text="Saved f.")

    agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc),
                   remember_name="remember", remember_handler=handler)
    assert seen == ["tu_r"]
    gateway.execute.assert_not_called()


def test_build_system_prompt_renders_known_facts():
    agent = _make_manager_for_prompt()
    prompt = agent.build_system_prompt("persona", "", "english", "web", {}, known_facts="- t — x")
    assert "<known_facts>" in _flat(prompt) and "- t — x" in _flat(prompt)
    assert "<known_facts>" not in _flat(agent.build_system_prompt("persona", "", "english", "web", {}))
```

In `test_stream_turn.py`, inside `class TestRecentToolExchangesHelpers`:

```python
    def test_build_messages_skips_listed_tools_pairwise(self):
        agent = _make_agent_core()
        ex = [{"tool_uses": [{"type": "tool_use", "id": "a", "name": "fetch_profile", "input": {}},
                             {"type": "tool_use", "id": "b", "name": "fetch_jobs", "input": {}}],
               "tool_results": [{"type": "tool_result", "tool_use_id": "a", "content": "P"},
                                {"type": "tool_result", "tool_use_id": "b", "content": "J"}]},
              {"tool_uses": [{"type": "tool_use", "id": "c", "name": "fetch_profile", "input": {}}],
               "tool_results": [{"type": "tool_result", "tool_use_id": "c", "content": "P2"}]}]
        msgs = agent._build_tool_exchange_messages(ex, skip_tools={"fetch_profile"})
        assert len(msgs) == 2                                  # second exchange dropped entirely
        assert [b.tool_use_id for b in msgs[1].content] == ["b"]
```

In `test_orchestrator.py`, add tests that build the agent with `_make_agent(...)`. Set `agent._tool_policies` to `_POL` (defined as above, for a `get_balance` tool) and `agent._memory.context_bundle.return_value` to `ContextBundle(session={"current_subagent_id": "market_truth"}, profile={}, journey=None, tool_results=[_entry()])`. Assert:
  - `agent._manager_agent.run_turn` was called with a `tool_cache` kwarg that is a `TurnToolCache`;
  - `agent._manager_agent.build_system_prompt` was called with `known_facts` containing `"get_balance"`;
  - after a `run_turn` side effect calls `kwargs["tool_cache"].after_call(...)` with a projected result, `agent._memory.apply_tool_results` was called once with `(session_id, user_id, batch)`, where `batch["puts"]` is non-empty;
  - with no pending changes, `apply_tool_results` is not called.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py tests/test_stream_turn.py tests/test_orchestrator.py -v -k "cache or remember or known_facts or skips_listed or tool_results"`
Expected: FAIL

- [ ] **Step 3: Implement `run_turn`.**
  - Add the three kwargs to the signature and document them.
  - In the `for tool_call in response_tool_calls:` loop, replace the block from `_ungrounded = self._ungrounded_params(tool_call, messages)` down to the final `else: tool_result = self._execute_tool(tool_call, session_id, user_id)` with:

```python
                if remember_handler is not None and tool_call.tool_name == remember_name:
                    tool_result = remember_handler(tool_call, messages)
                else:
                    _stored = tool_cache.stored_results_by_tool() if tool_cache else None
                    _ungrounded = self._ungrounded_params(tool_call, messages, _stored)
                    if _ungrounded:
                        # (keep the existing warning log and refusal ToolResult here, unchanged,
                        #  including tool_use_id=tool_call.tool_use_id from Task 1)
                        ...
                    elif self._registry.get_route(tool_call.tool_name) == "knowledge_engine":
                        tool_result = self._execute_knowledge_retrieval(tool_call, ke_context)
                    else:
                        _hit = tool_cache.lookup(tool_call) if tool_cache else None
                        if _hit is not None:
                            tool_result = _hit
                        else:
                            _call = tool_cache.prepare(tool_call) if tool_cache else tool_call
                            tool_result = self._execute_tool(_call, session_id, user_id)
                            if tool_cache:
                                tool_cache.after_call(tool_call, tool_result)
```

  The `...` stands for the existing refusal code moved one indent level deeper, not deleted. Move it verbatim.

- [ ] **Step 4: Implement `build_system_prompt`.** Add the `known_facts: str = ""` kwarg and its docstring. In the `tier3 = join([...])` list, insert `xml("known_facts", known_facts),` directly after `xml("known_profile", profile_body),`.

- [ ] **Step 5: Implement the replay filter.** In `_build_tool_exchange_messages`, add the `skip_tools` parameter. For each exchange:
  - build `names = {u.get("id", ""): u.get("name", "") for u in uses if isinstance(u, dict)}`;
  - filter `uses` to those whose `name` is not in `skip_tools`, and `results` to those whose `names.get(r.get("tool_use_id", ""))` is not in `skip_tools`;
  - then apply the existing "skip if either list is empty" rule to the filtered lists.

  In `_prepend_tool_replay`, add `skip_tools: frozenset | set = frozenset()` and pass it through. Log `skipped_tools=sorted(skip_tools)` in `orchestrator.tool_replay`. Update both docstrings.

- [ ] **Step 6: Implement `AgentCore` wiring.**
  - Imports: `from src.tool_results import ToolResultPolicies, TurnToolCache, augment_tool_definitions` and `from src.remember import RememberTool`.
  - At the end of `AgentCore.__init__`:

```python
        self._tool_policies = ToolResultPolicies.from_config(config)
        self._remember = RememberTool.from_config(config)
```

  - Add the helper:

```python
    def _remember_on_saved(self, bundle):
        """Callback that mirrors a remembered value into this turn's bundle."""
        def _on_saved(scope: str, key: str, value) -> None:
            (bundle.session if scope == "session" else bundle.profile)[key] = value
        return _on_saved
```

  In `process_turn`:
  1. Immediately before `system = self._manager_agent.build_system_prompt(`, add:
     `tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)`
  2. Add `known_facts=tool_cache.render_known_facts(),` to that call.
  3. Change the `_prepend_tool_replay(...)` call to pass `skip_tools=tool_cache.fresh_tools()`.
  4. After `active_tools = self._workflow.resolve_tools_for(next_subagent_id)`, add:

```python
        active_tools = augment_tool_definitions(
            active_tools, self._tool_policies,
            self._remember.definition() if self._remember else None,
        )
```

  5. In the `run_turn(...)` call, add:

```python
            tool_cache=tool_cache,
            remember_name=self._remember.name if self._remember else "",
            remember_handler=(
                (lambda _tc, _msgs: self._remember.handle(
                    _tc, _msgs, tool_cache.stored_results_by_tool(),
                    lambda scope, key, value: self._memory.write_strict(session_id, user_id, scope, key, value),
                    self._remember_on_saved(bundle),
                )) if self._remember else None
            ),
```

  6. Directly after the `for _tr in tool_results or []:` session_values loop, add:

```python
        if tool_cache.has_pending():
            try:
                self._memory.apply_tool_results(session_id, user_id, tool_cache.drain_batch())
            except Exception as e:
                logger.error("orchestrator.apply_tool_results_error", extra={
                    "operation": "orchestrator.process_turn", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}"})
```

  If `process_turn` reads the bundle under a different local name than `bundle`, use that name.

- [ ] **Step 7: Run the module and check it passes.**

Run: `cd agent_core && uv run pytest -q`
Expected: all PASS. Existing orchestrator tests use `MagicMock` managers, so the extra kwargs are accepted.

- [ ] **Step 8: Commit.**

```bash
git add agent_core/src agent_core/tests
git commit -m "feat(agent-core): serve and record tool results on the sync path

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Stream path — both execute sites, per-call persistence, parity

**Files:**
- Modify: `agent_core/src/orchestrator.py` (the stream turn method: prompt build ~4005, replay ~4030, `active_tools` ~4050, execute site 1 ~4237-4305, execute site 2 ~4451-4518)
- Test: `agent_core/tests/test_stream_turn.py`, `agent_core/tests/test_turn_path_identity_parity.py`

**Interfaces:**
- Consumes: everything from Task 11, plus the async `apply_tool_results` / `write_strict` (Task 6)
- Produces: stream behaviour identical to the sync path. `apply_tool_results` is awaited after each live call that leaves pending changes.

- [ ] **Step 1: Write failing tests.** Use `test_stream_turn.py`'s `_make_agent_core(...)` and its existing pattern for driving a tool round (the `ToolUseRequested` flow used by its current tool tests). Add:
  1. `test_stream_cache_hit_skips_gateway`: bundle `tool_results=[_entry()]`, policies with `get_balance` cached, and a model that calls `get_balance` with `{"account": "12345"}`. Assert `async_gateway.execute` is not awaited.
  2. `test_stream_live_call_persists_immediately`: a model calling a cached tool whose gateway returns a projected JSON result. Assert `async_memory.apply_tool_results` is awaited with a batch containing one put, and that it is awaited **before** the follow-up LLM call.
  3. `test_stream_write_invalidation_survives_interruption`: a model calling `save_profile` (`invalidates: [fetch_profile]`), then cancel the turn before completion using the file's existing interruption helper. Assert `apply_tool_results` was awaited with `{"invalidate": ["fetch_profile"], "puts": []}`.
  4. `test_stream_remember_routed_locally`: `agent._remember = RememberTool.from_config(...)`, a model calling `remember` with a grounded value, and `async_memory.write_strict = AsyncMock(return_value=(True, ""))`. Assert `write_strict` is awaited and the gateway is not.
  5. `test_stream_nested_round_uses_cache`: the same as (1), but the cached call happens in the second (nested) tool round.

  In `test_turn_path_identity_parity.py`, add one parametrised scenario per outcome (hit, miss+store, write invalidation, remember write, remember reject, ungrounded refusal), run through both `process_turn` and `stream_turn`. Assert the same tool_result texts and the same `apply_tool_results` batches. Follow that file's existing two-path harness.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run pytest tests/test_stream_turn.py tests/test_turn_path_identity_parity.py -v -k "cache or remember or invalidation or persist"`
Expected: FAIL

- [ ] **Step 3: Implement the stream setup.** In the stream method:
  - Before its `build_system_prompt(` call, add `tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)`, and pass `known_facts=tool_cache.render_known_facts()`.
  - Pass `skip_tools=tool_cache.fresh_tools()` to its `_prepend_tool_replay(...)`.
  - Wrap its `active_tools = self._workflow.resolve_tools_for(...)` with `augment_tool_definitions(...)` exactly as in Task 11, Step 6.4.

  Add this helper method to `AgentCore`:

```python
    async def _persist_tool_cache(self, session_id: str, user_id: str, tool_cache) -> None:
        """Send pending tool-result changes now; a streaming turn may be interrupted later."""
        if not tool_cache.has_pending():
            return
        try:
            await self._async_memory.apply_tool_results(session_id, user_id, tool_cache.drain_batch())
        except Exception as e:
            logger.error("orchestrator.apply_tool_results_error", extra={
                "operation": "orchestrator.stream_turn", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}"})
```

- [ ] **Step 4: Implement execute site 1.**
  - Pass `stored_results=tool_cache.stored_results_by_tool()` to the `ungrounded_params(...)` call.
  - Replace the `if _refusal: … else: …` block with:

```python
                            if self._remember is not None and tc.tool_name == self._remember.name:
                                tool_result = await self._remember.handle_async(
                                    tc, messages, tool_cache.stored_results_by_tool(),
                                    lambda scope, key, value: self._async_memory.write_strict(
                                        session_id, user_id, scope, key, value),
                                    self._remember_on_saved(bundle),
                                )
                            elif _refusal:
                                tool_result = refusal_result(tc.tool_name, tc.tool_use_id, _refusal)
                            elif (_hit := tool_cache.lookup(tc)) is not None:
                                tool_result = _hit
                            else:
                                _turn_tool_counts[tc.tool_name] = _used + 1
                                tool_result = await self._async_gateway.execute(
                                    tool_cache.prepare(tc), session_id, user_id,
                                    session_values=self._tool_session_values(bundle),
                                )
                                await self._write_mapped_session_values(session_id, user_id, tool_result, bundle)
                                tool_cache.after_call(tc, tool_result)
                                await self._persist_tool_cache(session_id, user_id, tool_cache)
```

  The cap and grounding checks that compute `_refusal` stay above this block, unchanged. The `remember` branch comes first, so `remember` is never capped or grounding-refused by `grounded_params`.

- [ ] **Step 5: Implement execute site 2 (nested rounds).** Apply the same edit using that site's names: `_ung2`, `_refusal2`, `_caps2`, `_used2`.

- [ ] **Step 6: Run the module and check it passes.**

Run: `cd agent_core && uv run pytest -q`
Expected: all PASS

- [ ] **Step 7: Commit.**

```bash
git add agent_core/src/orchestrator.py agent_core/tests
git commit -m "feat(agent-core): serve and record tool results on the stream path

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Dev-kit schemas and cross-block rules

**Files:**
- Modify: `dev-kit/dev_kit/schemas/domain/agent_core.py` (`ConnectorDef`; new section models)
- Modify: `dev-kit/dev_kit/schemas/validation.py` (`DOMAIN_SECTION_SCHEMAS`)
- Modify: `dev-kit/dev_kit/schema.py` (loader models: `ConnectorDef`, `AgentCoreConfig`)
- Modify: `dev-kit/dev_kit/schemas/cross_block_validation.py`
- Test: `dev-kit/tests/schemas/test_cross_block_validation.py`, and the domain-schema tests next to it (follow the existing file for `connectors`)

**Interfaces:**
- Produces:
  - Dev-kit accepts `connectors.*[].cache`, `connectors.*[].invalidates`, top-level `tool_results` and top-level `memory_tool`, with the same shapes as Task 8.
  - Cross-block rule `_tool_result_memory_rules(ac: dict, ml: dict) -> list[str]`, gated `applicable_after("tools")`. It returns errors when:
    - a session-scope `ttl_seconds` exceeds `ml.state.session.ttl_minutes * 60`;
    - a `vary_on` entry is not in `ml.state.session.schema`;
    - a `memory_tool` field with `scope: session` is not in the session schema;
    - a `memory_tool` field with `scope: persistent` is not in `ml.state.persistent.graph.subnodes.UserProfile.declared_fields`.

- [ ] **Step 1: Write failing tests.** In `test_cross_block_validation.py`:

```python
from dev_kit.schemas.cross_block_validation import validate_cross_block

ML = {"state": {"session": {"ttl_minutes": 60, "schema": {
          "trade": {"type": "string"}, "profile_item_id": {"type": "string"}}},
      "persistent": {"graph": {"subnodes": {"UserProfile": {"declared_fields": ["name"]}}}}}}


def _ac(read=None, memory_tool=None):
    ac = {"connectors": {"read": read or [], "write": []}}
    if memory_tool:
        ac["memory_tool"] = memory_tool
    return ac


def _errs(ac, ml=ML):
    return [e for e in validate_cross_block({"agent_core": ac, "memory_layer": ml}, [])
            if "tool" in e or "memory_tool" in e or "vary_on" in e]


def test_valid_tool_result_config_has_no_errors():
    ac = _ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600, "vary_on": ["trade"]}}],
             {"fields": {"profile_item_id": {"scope": "session"}, "name": {"scope": "persistent"}}})
    assert _errs(ac) == []


def test_session_ttl_over_session_lifetime():
    ac = _ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 7200}}])
    assert any("ttl_seconds" in e for e in _errs(ac))


def test_undeclared_vary_on_and_memory_fields():
    ac = _ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 60, "vary_on": ["ghost"]}}],
             {"fields": {"nope": {"scope": "session"}, "nada": {"scope": "persistent"}}})
    errs = _errs(ac)
    assert any("vary_on" in e for e in errs)
    assert sum("memory_tool" in e for e in errs) == 2
```

In the domain-schema tests, add one test that an `agent_core` `connectors` section with `cache`/`invalidates` validates, and one that top-level `tool_results` and `memory_tool` sections validate. Use the file's existing call into `dev_kit.schemas.validation`.

- [ ] **Step 2: Run and check they fail.**

Run: `cd dev-kit && uv run pytest tests/schemas -v -k "tool_result or memory_tool or vary_on or cache"`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - **`dev_kit/schemas/domain/agent_core.py`:** add `ToolCacheConfig`, `ToolResultsSection`, `MemoryToolField` and `MemoryToolSection`, with exactly the fields, constraints and `extra="forbid"` of Task 8. Name the section models `ToolResultsSection` / `MemoryToolSection` to match this file's convention. Add `cache: Optional[ToolCacheConfig] = None` and `invalidates: list[str] = Field(default_factory=list)` to `ConnectorDef`. Add to `ConnectorsSection` the same `model_validator` as Task 8's `ConnectorsConfig._check_cache_rules`.
  - **`validation.py`:** register `("agent_core", "tool_results"): agent_core.ToolResultsSection` and `("agent_core", "memory_tool"): agent_core.MemoryToolSection`.
  - **`dev_kit/schema.py`:** add `cache: Optional[dict] = None` and `invalidates: list[str] = Field(default_factory=list)` to its `ConnectorDef`, and `tool_results: Optional[dict] = None` and `memory_tool: Optional[dict] = None` to `AgentCoreConfig`. This is the lenient loader model; it must carry the keys so they are not dropped.
  - **`cross_block_validation.py`:** add the helper below, and call it inside `validate_cross_block` next to the other rules as `if applicable_after("tools"): errors.extend(_tool_result_memory_rules(ac, blocks.get("memory_layer") or {}))`.

```python
def _tool_result_memory_rules(ac: dict, ml: dict) -> list[str]:
    """Cross-block rules for tool-result caching and the memory tool (agent_core ↔ memory_layer)."""
    errors: list[str] = []
    session = ((ml.get("state") or {}).get("session") or {})
    schema = session.get("schema") or {}
    session_ttl = int(session.get("ttl_minutes") or 60) * 60
    declared = (((((ml.get("state") or {}).get("persistent") or {}).get("graph") or {})
                 .get("subnodes") or {}).get("UserProfile") or {}).get("declared_fields") or []
    for group in (ac.get("connectors") or {}).values():
        for c in group or []:
            cache = (c or {}).get("cache") if isinstance(c, dict) else None
            if not isinstance(cache, dict):
                continue
            name = c.get("name", "?")
            if cache.get("scope") == "session" and int(cache.get("ttl_seconds") or 0) > session_ttl:
                errors.append(f"connectors.{name}.cache.ttl_seconds exceeds the session lifetime "
                              f"({session_ttl}s from memory_layer.state.session.ttl_minutes)")
            for f in cache.get("vary_on") or []:
                if f not in schema:
                    errors.append(f"connectors.{name}.cache.vary_on: '{f}' is not a declared session field")
    for fname, f in (((ac.get("memory_tool") or {}).get("fields")) or {}).items():
        scope = (f or {}).get("scope")
        if scope == "session" and fname not in schema:
            errors.append(f"memory_tool.fields.{fname}: not declared in memory_layer session schema")
        if scope == "persistent" and fname not in declared:
            errors.append(f"memory_tool.fields.{fname}: not in UserProfile.declared_fields")
    return errors
```

- [ ] **Step 4: Run and check it passes.**

Run: `cd dev-kit && uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit dev-kit/tests
git commit -m "feat(dev-kit): validate tool-result cache and memory tool config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Blue Dots rollout and docs

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml`, `dev-kit/configs/blue-dots/action_gateway.yaml`
- Modify: `dev-kit/dpg/agent_core.yaml` (documented, commented-out defaults for `cache`, `invalidates`, `tool_results`, `memory_tool`)
- Modify: `automation/deploy/shared-vm/env.example` and the memory-layer service env in `automation/deploy/shared-vm/docker-compose.yml` (`TOOL_RESULT_KEY_SECRET`)
- Modify: `CLAUDE.md` (the planned-work line that references the #18 cache layer)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Configure `fetch_profile`.** In `blue-dots/agent_core.yaml` under `connectors.read`, for `fetch_profile` add:

```yaml
      cache:
        scope: user
        ttl_seconds: 1800
        keep: [acting_as_user_id, items]
```

In `blue-dots/action_gateway.yaml`, narrow the `fetch_profile` `items` projection only if the prompts do not rely on the whole `item_state`. Check each `items[` / `item_state` reference in `blue-dots/agent_core.yaml` first. If they do rely on it, leave the projection and record the decision in a YAML comment.

- [ ] **Step 2: Configure `save_profile`.** Add `invalidates: [fetch_profile]` to `save_profile` under `connectors.write`.

- [ ] **Step 3: Add the memory tool.** Add at the top level of `blue-dots/agent_core.yaml`:

```yaml
memory_tool:
  name: remember
  fields:
    profile_item_id:
      scope: session
      description: "item_id of the profile the caller chose to use or update"
      grounded_in: [fetch_profile, save_profile]
    profile_action:
      scope: session
      description: "use_existing | create_new | update_existing, as the caller chose"
```

- [ ] **Step 4: Update the prompts.** In the `profile_choice` subagent prompt, add one instruction: after the caller picks a profile, call `remember` with `profile_item_id` copied from `<known_facts>`, and `profile_action`. Replace any "call once / has not been called yet" wording in `fetch_profile`'s `call_when` with: "Use the fetch_profile entry in <known_facts> when present; call only if it is absent or the caller says their details changed (then pass force_refresh=true)." Do **not** yet move `apply_job`'s `profile_item_id` to `source: session` (spec §12). That happens after a live run confirms `remember` is being called.

- [ ] **Step 5: Document the defaults.** In `dev-kit/dpg/agent_core.yaml`, add commented examples of each new key with a one-line explanation, pointing to the spec.

- [ ] **Step 6: Deploy env.** Add `TOOL_RESULT_KEY_SECRET=` (with a comment: "long random string; unset disables tool-result storage") to `env.example`, and pass it through to the memory-layer service in the shared-VM compose, following how that file passes other secrets. Do not commit a real value.

- [ ] **Step 7: Validate the whole Blue Dots config.**

Run: `cd agent_core && CONFIG_FOLDER=../dev-kit/configs/blue-dots uv run python -c "import main"`, or whichever config-validation entry point `agent_core/main.py:152-161` exposes. Also run the dev-kit validation over `dev-kit/configs/blue-dots` using that package's existing CLI or test helper.
Expected: no validation errors

- [ ] **Step 8: Run all module suites.**

Run: `for m in memory_layer action_gateway agent_core dev-kit; do (cd $m && uv run pytest -q) || exit 1; done`
Expected: all PASS

- [ ] **Step 9: Commit.**

```bash
git add dev-kit/configs/blue-dots dev-kit/dpg automation/deploy/shared-vm CLAUDE.md
git commit -m "feat(blue-dots): cache fetch_profile, invalidate on save, remember the chosen profile

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Deferred (not in this plan)

- **Skipping the follow-up LLM call after a `remember`-only tool round (spec §8, latency).** Each chat provider's tool-use message rules must be checked first, and the saving measured on a live call. File it as a follow-up issue once Task 12 is live.
- **Moving `apply_job` / `save_profile` IDs to `source: session`**, after a live run confirms `remember` works (spec §12).
- **Caching `fetch_jobs`** (`scope: session`, `vary_on: [trade, location]`): evaluate once `fetch_profile` caching is live.
- **Pinning the Redis image** (the floating `redis:7-alpine` currently resolves to 7.4, which is not OSS-licensed): separate automation ticket.
