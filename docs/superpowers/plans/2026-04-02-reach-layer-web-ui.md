# Reach Layer Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CLI stub with a browser-based chat interface served by a minimal FastAPI app inside the Reach Layer container.

**Architecture:** A FastAPI server (`server.py`) serves a single-page vanilla-JS chat UI (`web/index.html`) and proxies `POST /chat` → Agent Core `POST /process_turn`. `WebReachLayer` implements `ReachLayerBase` for the web channel. The browser generates a UUID session ID on load; User ID is entered once in a setup form and persists in `localStorage`.

**Design note on "static files" option (issue #23):** Pure static files would require CORS headers on Agent Core (currently absent) and would expose the Agent Core internal URL to the browser. The FastAPI proxy approach is the correct implementation — no CORS changes needed elsewhere.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (already a dep), PyYAML (already a dep), vanilla HTML + CSS + JS (no build step, no npm)

**Design decision — history fetch (single call):** The Reach Layer FastAPI server calls Memory Layer's `GET /users/{user_id}/active-history` in a single request. Memory Layer internally resolves the most recent active session from Redis and fetches its SQLite chat history, returning `{session_id, turns}` together. This is a deliberate, documented exception to the "only Agent Core calls other blocks" rule, justified by: (1) read-only GET with no side effects on the orchestration path, (2) dev/demo scope, (3) avoids pass-through proxy on Agent Core for a UI-only concern. History is loaded **once** on page load; subsequent turns update in-memory JS state only — no per-turn DB fetch.

**Design decision — session ID lookup strategy:** Chat history in SQLite is keyed by `session_id`. The browser always calls `GET /user-history/{user_id}` on the Reach Layer when the user is identified. This single call returns both `session_id` (most recent Redis-active session, or null) and formatted chat turns. If an active session is found, it is used and saved to localStorage. If Redis TTL has expired, `session_id` is null — the browser generates a new UUID and the user starts a fresh chat. This is intentional: sessions are not stored indefinitely. No separate `/sessions/{user_id}` or `/history/{session_id}` endpoints are needed on the Reach Layer.

**Design decision — K8s/ingress:** The FastAPI server is packaged as a container and deployed as a single-replica Kubernetes Deployment. Endpoints for Agent Core and Memory Layer are injected via env vars (`AGENT_CORE_ENDPOINT`, `MEMORY_LAYER_ENDPOINT`) so the same image runs in both local-docker and K8s without config changes. A ClusterIP Service + Ingress resource exposes port 8005 to the browser.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| **Create** | `reach_layer/src/base.py` | `ReachLayerBase` ABC + `TurnInput` / `TurnResult` dataclasses (moved from cli_reach.py) |
| **Create** | `reach_layer/config_loader.py` | `load_yaml()` and `deep_merge()` utilities (extracted from main.py so server.py can share them) |
| **Create** | `reach_layer/src/web_reach.py` | `WebReachLayer` — builds `TurnInput` from HTTP params, formats `TurnResult` to JSON dict |
| **Modify** | `memory_layer/src/memory_layer.py` | Add `get_history_for_active_session(user_id)` — combines active session lookup with history fetch |
| **Modify** | `memory_layer/src/server.py` | Add `GET /users/{user_id}/active-history` endpoint |
| **Modify** | `memory_layer/tests/test_server.py` | Add tests for the new endpoint |
| **Create** | `reach_layer/server.py` | FastAPI app: `GET /` (HTML), `POST /chat` (Agent Core proxy), `GET /user-history/{user_id}` (Memory Layer proxy, returns session_id + turns), `GET /health` |
| **Create** | `reach_layer/web/index.html` | Single-file vanilla JS chat UI — setup screen + chat screen; loads history on return visit |
| **Create** | `reach_layer/k8s/reach-layer.yaml` | K8s Deployment + ClusterIP Service + Ingress manifests |
| **Create** | `reach_layer/tests/test_web_reach.py` | Unit tests for `WebReachLayer` |
| **Create** | `reach_layer/tests/test_server.py` | Tests for server endpoints (TestClient, mocked httpx) |
| **Modify** | `reach_layer/src/cli_reach.py` | Remove local `TurnInput`/`TurnResult` definitions; import from `src.base` |
| **Modify** | `reach_layer/main.py` | Remove `_load_yaml`/`_deep_merge` definitions; import from `config_loader` |
| **Rename** | `reach_layer/tests/test_main.py` → `reach_layer/tests/test_config_loader.py` | Update imports to match new module name |
| **Modify** | `reach_layer/pyproject.toml` | Add `fastapi`, `uvicorn[standard]`; add `pytest-asyncio` to dev deps; update coverage source |
| **Modify** | `reach_layer/config/dpg.yaml` | Add `reach_layer.web.title` key |
| **Modify** | `reach_layer/Dockerfile` | Change CMD to start uvicorn; keep CLI accessible via override |
| **Modify** | `automation/docker/docker-compose.dev.yml` | Expose port 8005, remove interactive flags, start with default services |

---

## Task 0: Add `GET /users/{user_id}/active-history` to Memory Layer

**Files:**
- Modify: `memory_layer/src/memory_layer.py`
- Modify: `memory_layer/src/server.py`
- Modify: `memory_layer/tests/test_server.py`

- [ ] **Step 1: Write the failing tests in `memory_layer/tests/test_server.py`**

Add after the last `test_get_active_sessions_*` test (before the `DELETE /user` section):

```python
# ---------------------------------------------------------------------------
# GET /users/{user_id}/active-history — normal execution
# ---------------------------------------------------------------------------

def test_get_active_history_returns_session_and_turns(client, mock_memory):
    mock_memory.get_history_for_active_session.return_value = {
        "session_id": "sess-abc",
        "turns": [
            {"turn_id": "t1", "session_id": "sess-abc", "user_message": "hello",
             "system_message": "hi there", "timestamp": "2026-04-02T10:00:00"},
        ],
    }
    response = client.get("/users/user-1/active-history")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-abc"
    assert len(data["turns"]) == 1
    assert data["turns"][0]["user_message"] == "hello"


# ---------------------------------------------------------------------------
# GET /users/{user_id}/active-history — no active session
# ---------------------------------------------------------------------------

def test_get_active_history_no_session_returns_null_and_empty_turns(client, mock_memory):
    mock_memory.get_history_for_active_session.return_value = {
        "session_id": None,
        "turns": [],
    }
    response = client.get("/users/new-user/active-history")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /users/{user_id}/active-history — failure scenarios
# ---------------------------------------------------------------------------

def test_get_active_history_exception_returns_null(client, mock_memory):
    mock_memory.get_history_for_active_session.side_effect = RuntimeError("redis down")
    response = client.get("/users/user-1/active-history")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


def test_get_active_history_empty_user_id_returns_null(client):
    response = client.get("/users/   /active-history")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/memory_layer
uv run pytest tests/test_server.py -k "active_history" -v
```

Expected: `AttributeError: Mock object has no attribute 'get_history_for_active_session'` — method doesn't exist yet.

- [ ] **Step 3: Add `get_history_for_active_session` to `memory_layer/src/memory_layer.py`**

Add after the existing `get_chat_history` method (line ~584):

```python
def get_history_for_active_session(self, user_id: str) -> dict:
    """Return the most recent active session and its full chat history for a user.

    Combines active session lookup with history retrieval to avoid multiple
    round-trips from callers that need both the session_id and turn history.

    Args:
        user_id: The user identifier.

    Returns:
        Dict with session_id (str or None) and turns (list[dict]).
        Returns {"session_id": None, "turns": []} if no active session exists
        or user_id is empty.
    """
    if not user_id:
        return {"session_id": None, "turns": []}
    sessions = self.get_active_sessions(user_id)
    if not sessions:
        return {"session_id": None, "turns": []}
    session_id = sessions[0]["session_id"]
    turns = self.get_chat_history(session_id)
    return {"session_id": session_id, "turns": turns}
```

- [ ] **Step 4: Add `GET /users/{user_id}/active-history` to `memory_layer/src/server.py`**

Add after the `GET /sessions/{user_id}` endpoint (before `DELETE /user/{user_id}`):

```python
@app.get("/users/{user_id}/active-history")
def get_active_history(user_id: str) -> dict:
    """Return the most recent active session and its chat history for a user.

    Intended for the web UI to restore a returning user's session in a single
    call — avoids a separate session lookup and history fetch round-trip.

    Args:
        user_id: URL path parameter identifying the user.

    Returns:
        Dict with session_id (str or None) and turns (list[dict]).
        Returns {"session_id": None, "turns": []} if no active session or error.
    """
    start = time.time()
    user_id = user_id.strip()
    if not user_id:
        return {"session_id": None, "turns": []}
    try:
        result = memory.get_history_for_active_session(user_id)
        logger.info(
            "memory_server.get_active_history",
            extra={
                "operation": "server.get_active_history",
                "status": "success",
                "user_id": user_id,
                "found": result["session_id"] is not None,
                "turn_count": len(result["turns"]),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return result
    except Exception as e:
        logger.error(
            "memory_server.get_active_history_error",
            extra={
                "operation": "server.get_active_history",
                "status": "failure",
                "user_id": user_id,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {"session_id": None, "turns": []}
```

- [ ] **Step 5: Run tests — confirm they pass**

```bash
uv run pytest tests/test_server.py -k "active_history" -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Run full Memory Layer test suite**

```bash
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add memory_layer/src/memory_layer.py memory_layer/src/server.py memory_layer/tests/test_server.py
git commit -m "feat(memory_layer): add GET /users/{user_id}/active-history endpoint"
```

---

## Task 1: Create `ReachLayerBase` and extract shared data classes

**Files:**
- Create: `reach_layer/src/base.py`
- Modify: `reach_layer/src/cli_reach.py`

- [ ] **Step 1: Create `reach_layer/src/base.py`**

```python
"""
reach_layer/src/base.py

ReachLayerBase — abstract channel adapter interface for the DPG Reach Layer block.
All concrete channel adapters (CLI, web, WhatsApp, VOIP) inherit from this class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TurnInput:
    """Normalised inbound message from any channel."""

    session_id: str
    user_message: str
    channel: str
    timestamp_ms: int
    user_id: Optional[str] = None


@dataclass
class TurnResult:
    """Normalised outbound response from Agent Core."""

    session_id: str
    response_text: str
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0


class ReachLayerBase(ABC):
    """Abstract base class for all Reach Layer channel adapters.

    Concrete implementations normalise a specific inbound channel (CLI, web,
    WhatsApp, VOIP) into TurnInput objects and deliver TurnResult responses
    back over the same channel.
    """

    @abstractmethod
    def receive(self) -> TurnInput:
        """Read the next user turn from the channel.

        Returns:
            TurnInput populated with session_id, user_message, channel, and
            timestamp_ms. Returns an empty TurnInput if the channel has no
            pending message (non-blocking adapters).
        """

    @abstractmethod
    def deliver(self, result: TurnResult) -> None:
        """Send the agent response back to the user on this channel.

        Args:
            result: The TurnResult from Agent Core to deliver.
        """
```

- [ ] **Step 2: Update `reach_layer/src/cli_reach.py` — remove local dataclasses, import from base**

Find the section at the top of `cli_reach.py` where `TurnInput` and `TurnResult` are defined as local dataclasses. Replace those definitions with an import, and add `ReachLayerBase` to the class hierarchy:

```python
# Remove these local dataclass definitions:
#   @dataclass
#   class TurnInput: ...
#   @dataclass
#   class TurnResult: ...

# Add at top of file:
from src.base import ReachLayerBase, TurnInput, TurnResult

# Change class definition from:
#   class CLIReachLayer:
# To:
class CLIReachLayer(ReachLayerBase):
```

- [ ] **Step 3: Run existing CLI tests to confirm nothing broke**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest tests/test_cli_reach.py -v
```

Expected: all tests pass (same count as before).

- [ ] **Step 4: Commit**

```bash
git add reach_layer/src/base.py reach_layer/src/cli_reach.py
git commit -m "feat(reach_layer): add ReachLayerBase ABC and move shared dataclasses to base.py"
```

---

## Task 2: Extract config utilities to `config_loader.py`

**Files:**
- Create: `reach_layer/config_loader.py`
- Modify: `reach_layer/main.py`
- Rename + modify: `reach_layer/tests/test_main.py` → `reach_layer/tests/test_config_loader.py`

- [ ] **Step 1: Read `reach_layer/main.py`** to copy the exact `_load_yaml` and `_deep_merge` implementations.

- [ ] **Step 2: Create `reach_layer/config_loader.py`**

```python
"""
reach_layer/config_loader.py

Config loading utilities for the DPG Reach Layer block.
Loads and deep-merges two YAML files: framework defaults (dpg.yaml) overridden
by domain-specific values (domain.yaml). Called once at startup by main.py and server.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str) -> dict:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Absolute or relative path to the YAML file.

    Returns:
        Parsed YAML as a dict. Returns empty dict if the file contains only
        whitespace or YAML null.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict.

    Nested dicts are merged recursively. Scalar values in override win.
    Neither input dict is mutated.

    Args:
        base:     The default configuration dict.
        override: Domain-specific values that take precedence.

    Returns:
        Merged dict combining base and override.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(dpg_path: str, domain_path: str) -> dict:
    """Load and merge the two reach_layer config files.

    Loads framework defaults from dpg_path, then overlays domain-specific
    values from domain_path. Missing domain config is treated as empty (no override).

    Args:
        dpg_path:    Path to config/dpg.yaml (framework defaults).
        domain_path: Path to config/domain.yaml (domain overrides).

    Returns:
        Merged configuration dict.

    Raises:
        FileNotFoundError: If dpg_path does not exist.
    """
    base = load_yaml(dpg_path)
    try:
        override = load_yaml(domain_path)
    except FileNotFoundError:
        override = {}
    return deep_merge(base, override)
```

- [ ] **Step 3: Update `reach_layer/main.py`**

Remove the `_load_yaml` and `_deep_merge` function definitions from `main.py`. Add an import at the top:

```python
from config_loader import load_config
```

Replace the config-loading block (wherever `_load_yaml` and `_deep_merge` are called) with:

```python
config = load_config("config/dpg.yaml", "config/domain.yaml")
```

- [ ] **Step 4: Rename and update the test file**

```bash
mv reach_layer/tests/test_main.py reach_layer/tests/test_config_loader.py
```

In `test_config_loader.py`, replace all imports of `_load_yaml`/`_deep_merge` from `main` with:

```python
from config_loader import load_yaml, deep_merge, load_config
```

Update any test that called `_load_yaml` or `_deep_merge` to call `load_yaml` or `deep_merge` (drop the underscore — they are now public).

- [ ] **Step 5: Run all reach_layer tests**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/config_loader.py reach_layer/main.py reach_layer/tests/test_config_loader.py
git commit -m "refactor(reach_layer): extract config loading to config_loader.py"
```

---

## Task 3: Implement `WebReachLayer` (TDD)

**Files:**
- Create: `reach_layer/tests/test_web_reach.py`
- Create: `reach_layer/src/web_reach.py`

- [ ] **Step 1: Write failing tests in `reach_layer/tests/test_web_reach.py`**

```python
"""
reach_layer/tests/test_web_reach.py

Unit tests for WebReachLayer.
"""

import time
import pytest
from src.web_reach import WebReachLayer
from src.base import TurnInput, TurnResult

VALID_CONFIG = {
    "agent_core_client": {
        "endpoint": "http://agent-core:8000/process_turn",
        "timeout_s": 30.0,
    }
}


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_init_raises_on_none_config():
    with pytest.raises(ValueError, match="config"):
        WebReachLayer(None)


def test_init_raises_on_missing_agent_core_client():
    with pytest.raises(ValueError, match="agent_core_client"):
        WebReachLayer({})


# ---------------------------------------------------------------------------
# receive() — base class contract (not used in web loop, returns empty TurnInput)
# ---------------------------------------------------------------------------

def test_receive_returns_turn_input():
    layer = WebReachLayer(VALID_CONFIG)
    result = layer.receive()
    assert isinstance(result, TurnInput)
    assert result.channel == "web"


def test_receive_returns_empty_session_id():
    layer = WebReachLayer(VALID_CONFIG)
    result = layer.receive()
    assert result.session_id == ""
    assert result.user_message == ""


# ---------------------------------------------------------------------------
# deliver() — base class contract (no-op for web, result returned via HTTP)
# ---------------------------------------------------------------------------

def test_deliver_does_not_raise():
    layer = WebReachLayer(VALID_CONFIG)
    result = TurnResult(session_id="s1", response_text="Hello")
    layer.deliver(result)  # should not raise


# ---------------------------------------------------------------------------
# build_turn_input()
# ---------------------------------------------------------------------------

def test_build_turn_input_returns_turn_input():
    layer = WebReachLayer(VALID_CONFIG)
    before = int(time.time() * 1000)
    ti = layer.build_turn_input("s1", "u1", "hello")
    after = int(time.time() * 1000)
    assert isinstance(ti, TurnInput)
    assert ti.session_id == "s1"
    assert ti.user_id == "u1"
    assert ti.user_message == "hello"
    assert ti.channel == "web"
    assert before <= ti.timestamp_ms <= after


def test_build_turn_input_empty_user_id():
    layer = WebReachLayer(VALID_CONFIG)
    ti = layer.build_turn_input("s1", "", "hi")
    assert ti.user_id == ""


def test_build_turn_input_empty_message():
    layer = WebReachLayer(VALID_CONFIG)
    ti = layer.build_turn_input("s1", "u1", "")
    assert ti.user_message == ""


def test_build_turn_input_raises_on_none_session_id():
    layer = WebReachLayer(VALID_CONFIG)
    with pytest.raises(ValueError, match="session_id"):
        layer.build_turn_input(None, "u1", "hello")


# ---------------------------------------------------------------------------
# format_result()
# ---------------------------------------------------------------------------

def test_format_result_includes_required_keys():
    layer = WebReachLayer(VALID_CONFIG)
    result = TurnResult(
        session_id="s1",
        response_text="Hi there",
        was_escalated=False,
        latency_ms=120,
    )
    d = layer.format_result(result)
    assert d["response_text"] == "Hi there"
    assert d["was_escalated"] is False
    assert d["session_id"] == "s1"
    assert d["latency_ms"] == 120


def test_format_result_escalated_flag():
    layer = WebReachLayer(VALID_CONFIG)
    result = TurnResult(session_id="s1", response_text="Escalating", was_escalated=True)
    d = layer.format_result(result)
    assert d["was_escalated"] is True


def test_format_result_raises_on_none():
    layer = WebReachLayer(VALID_CONFIG)
    with pytest.raises(ValueError, match="result"):
        layer.format_result(None)
```

- [ ] **Step 2: Run tests — confirm they all fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest tests/test_web_reach.py -v
```

Expected: `ModuleNotFoundError` or all FAIL (web_reach.py doesn't exist yet).

- [ ] **Step 3: Create `reach_layer/src/web_reach.py`**

```python
"""
reach_layer/src/web_reach.py

WebReachLayer — web channel adapter for the DPG Reach Layer block.
Maps HTTP request parameters to TurnInput and formats TurnResult for JSON
responses. Used by server.py; does not own the HTTP call to Agent Core.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from src.base import ReachLayerBase, TurnInput, TurnResult

logger = logging.getLogger(__name__)


class WebReachLayer(ReachLayerBase):
    """Web channel adapter — maps HTTP request/response to TurnInput/TurnResult.

    This adapter is stateless; one instance is shared across all requests in
    server.py. Unlike CLIReachLayer, it does not run a blocking receive loop.
    receive() and deliver() satisfy the ReachLayerBase contract but are not
    called directly — server.py uses build_turn_input() and format_result().

    Args:
        config: Merged YAML config dict. Must contain 'agent_core_client' key.

    Raises:
        ValueError: If config is None or missing 'agent_core_client'.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the web channel adapter from merged YAML config."""
        if config is None:
            raise ValueError("config must not be None")
        if "agent_core_client" not in config:
            raise ValueError("config missing required key: agent_core_client")
        self._endpoint: str = config["agent_core_client"].get("endpoint", "")
        self._timeout_s: float = float(config["agent_core_client"].get("timeout_s", 30.0))
        logger.info(
            "web_reach_layer.init",
            extra={
                "operation": "web_reach.init",
                "status": "success",
            },
        )

    # ------------------------------------------------------------------
    # ReachLayerBase interface — not used in web request loop
    # ------------------------------------------------------------------

    def receive(self) -> TurnInput:
        """Return an empty TurnInput — not used in web mode.

        The web adapter receives input via HTTP parameters passed to
        build_turn_input(), not via a blocking stdin loop.

        Returns:
            Empty TurnInput with channel='web'.
        """
        return TurnInput(session_id="", user_message="", channel="web", timestamp_ms=0)

    def deliver(self, result: TurnResult) -> None:
        """No-op — not used in web mode.

        The web adapter returns results directly via the HTTP response.
        server.py calls format_result() instead.

        Args:
            result: Unused in web mode.
        """

    # ------------------------------------------------------------------
    # Web-specific public methods
    # ------------------------------------------------------------------

    def build_turn_input(
        self, session_id: str, user_id: str, user_message: str
    ) -> TurnInput:
        """Build a TurnInput from an incoming web request.

        Args:
            session_id:   Browser-generated UUID session identifier.
            user_id:      User identifier entered in the setup form.
            user_message: Message text submitted by the user.

        Returns:
            TurnInput ready to be serialised and sent to Agent Core.

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        return TurnInput(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message if user_message is not None else "",
            channel="web",
            timestamp_ms=int(time.time() * 1000),
        )

    def format_result(self, result: TurnResult) -> dict:
        """Format a TurnResult for JSON HTTP response.

        Args:
            result: The TurnResult returned by Agent Core.

        Returns:
            Dict with keys: response_text, was_escalated, session_id, latency_ms.

        Raises:
            ValueError: If result is None.
        """
        if result is None:
            raise ValueError("result must not be None")
        return {
            "response_text": result.response_text,
            "was_escalated": result.was_escalated,
            "session_id": result.session_id,
            "latency_ms": result.latency_ms,
        }
```

- [ ] **Step 4: Run tests — confirm they all pass**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest tests/test_web_reach.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/src/web_reach.py reach_layer/tests/test_web_reach.py
git commit -m "feat(reach_layer): add WebReachLayer with TDD"
```

---

## Task 4: Implement `server.py` (TDD)

**Files:**
- Create: `reach_layer/tests/test_server.py`
- Create: `reach_layer/server.py`

- [ ] **Step 1: Add `fastapi`, `uvicorn`, `pytest-asyncio`, `httpx` (test client) to `pyproject.toml`**

```toml
dependencies = [
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-mock>=3.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",   # required by FastAPI TestClient
]
```

Also update `[tool.coverage.run]` to include `server.py`:

```toml
[tool.coverage.run]
source = ["src", "server.py", "config_loader.py"]
```

Install the new deps:

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv add fastapi "uvicorn[standard]"
uv add --dev pytest-asyncio
```

- [ ] **Step 2: Write failing tests in `reach_layer/tests/test_server.py`**

```python
"""
reach_layer/tests/test_server.py

Integration tests for the FastAPI server (server.py).
All Agent Core HTTP calls are mocked — no real network requests.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: patch config loading so server.py starts without real YAML files,
# then import the FastAPI app.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    fake_config = {
        "server": {"host": "0.0.0.0", "port": 8005},
        "reach_layer": {"web": {"title": "DPG Chat Demo"}},
        "agent_core_client": {
            "endpoint": "http://agent-core:8000/process_turn",
            "timeout_s": 30.0,
        },
    }
    with patch("server.load_config", return_value=fake_config):
        import server
        yield TestClient(server.app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET / — serves HTML
# ---------------------------------------------------------------------------

def test_index_returns_html(client):
    with patch("server.Path") as mock_path:
        mock_path.return_value.read_text.return_value = "<html><body>Chat</body></html>"
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# POST /chat — happy path
# ---------------------------------------------------------------------------

def test_chat_returns_response_text(client):
    agent_core_payload = {
        "session_id": "s1",
        "response_text": "Hello! How can I help you?",
        "was_escalated": False,
        "was_tool_used": False,
        "model_used": "claude-sonnet",
        "latency_ms": 300,
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = agent_core_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("server.httpx.post", return_value=mock_resp):
        response = client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "Hello"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["response_text"] == "Hello! How can I help you?"
    assert data["was_escalated"] is False
    assert data["session_id"] == "s1"


def test_chat_passes_session_and_user_id_to_agent_core(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "session_id": "abc", "response_text": "ok",
        "was_escalated": False, "was_tool_used": False,
        "model_used": "", "latency_ms": 0,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("server.httpx.post", return_value=mock_resp) as mock_post:
        client.post("/chat", json={"session_id": "abc", "user_id": "phone123", "message": "hi"})

    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs["json"]
    assert body["session_id"] == "abc"
    assert body["user_id"] == "phone123"
    assert body["channel"] == "web"


# ---------------------------------------------------------------------------
# POST /chat — escalated response
# ---------------------------------------------------------------------------

def test_chat_escalated_flag_propagated(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "session_id": "s1", "response_text": "Connecting you now",
        "was_escalated": True, "was_tool_used": False,
        "model_used": "", "latency_ms": 0,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("server.httpx.post", return_value=mock_resp):
        response = client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "help"},
        )

    assert response.json()["was_escalated"] is True


# ---------------------------------------------------------------------------
# POST /chat — Agent Core connection failure → 502
# ---------------------------------------------------------------------------

def test_chat_returns_502_on_agent_core_connection_error(client):
    import httpx as httpx_module
    with patch("server.httpx.post", side_effect=httpx_module.ConnectError("refused")):
        response = client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "hello"},
        )
    assert response.status_code == 502


def test_chat_returns_502_on_agent_core_timeout(client):
    import httpx as httpx_module
    with patch("server.httpx.post", side_effect=httpx_module.TimeoutException("timeout")):
        response = client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "hello"},
        )
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# POST /chat — validation: missing fields → 422
# ---------------------------------------------------------------------------

def test_chat_missing_session_id_returns_422(client):
    response = client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert response.status_code == 422


def test_chat_missing_message_returns_422(client):
    response = client.post("/chat", json={"session_id": "s1", "user_id": "u1"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — normal: active session with turns
# ---------------------------------------------------------------------------

def test_user_history_returns_session_id_and_formatted_turns(client):
    ml_response = {
        "session_id": "sess-abc",
        "turns": [
            {"user_message": "hello", "system_message": "Hi there!", "turn_id": "t1",
             "session_id": "sess-abc", "timestamp": "2026-01-01T00:00:00"},
            {"user_message": "bye", "system_message": "Goodbye!", "turn_id": "t2",
             "session_id": "sess-abc", "timestamp": "2026-01-01T00:00:01"},
        ],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = ml_response
    mock_resp.raise_for_status = MagicMock()

    with patch("server.httpx.get", return_value=mock_resp):
        response = client.get("/user-history/user-42")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-abc"
    assert data["turns"] == [
        {"role": "user",  "text": "hello"},
        {"role": "agent", "text": "Hi there!"},
        {"role": "user",  "text": "bye"},
        {"role": "agent", "text": "Goodbye!"},
    ]


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — no active session
# ---------------------------------------------------------------------------

def test_user_history_returns_null_session_and_empty_turns_when_no_session(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"session_id": None, "turns": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("server.httpx.get", return_value=mock_resp):
        response = client.get("/user-history/new-user")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — Memory Layer unreachable → graceful fallback
# ---------------------------------------------------------------------------

def test_user_history_returns_null_on_memory_layer_connect_error(client):
    import httpx as httpx_module
    with patch("server.httpx.get", side_effect=httpx_module.ConnectError("refused")):
        response = client.get("/user-history/user-42")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


def test_user_history_returns_null_on_memory_layer_timeout(client):
    import httpx as httpx_module
    with patch("server.httpx.get", side_effect=httpx_module.TimeoutException("timeout")):
        response = client.get("/user-history/user-42")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — empty user_id returns null
# ---------------------------------------------------------------------------

def test_user_history_empty_user_id_returns_null(client):
    response = client.get("/user-history/   ")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []
```

- [ ] **Step 3: Run tests — confirm they all fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest tests/test_server.py -v
```

Expected: `ModuleNotFoundError` — `server.py` doesn't exist yet.

- [ ] **Step 4: Create `reach_layer/server.py`**

```python
"""
reach_layer/server.py

FastAPI web server for the DPG Reach Layer block — web channel adapter.
Serves the browser-based chat UI and proxies /chat requests to Agent Core.

Run:
    uv run python server.py
    uv run uvicorn server:app --host 0.0.0.0 --port 8005 --reload
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from config_loader import load_config
from src.base import TurnResult
from src.web_reach import WebReachLayer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — loaded once at startup
# Env vars override YAML for endpoint URLs so the same image runs in
# both local-docker and Kubernetes without config file changes.
# ---------------------------------------------------------------------------

_config = load_config("config/dpg.yaml", "config/domain.yaml")
_web_reach = WebReachLayer(_config)
_endpoint: str = os.environ.get("AGENT_CORE_ENDPOINT") or _config["agent_core_client"]["endpoint"]
_timeout_s: float = float(_config["agent_core_client"].get("timeout_s", 30.0))
_memory_endpoint: str = os.environ.get("MEMORY_LAYER_ENDPOINT") or _config["memory_layer_client"]["endpoint"]
_memory_timeout_s: float = float(_config["memory_layer_client"].get("timeout_s", 10.0))
_title: str = _config.get("reach_layer", {}).get("web", {}).get("title", "DPG Chat")
_host: str = _config.get("server", {}).get("host", "0.0.0.0")
_port: int = int(_config.get("server", {}).get("port", 8005))

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title=_title)


class ChatRequest(BaseModel):
    """Inbound chat message from the browser."""

    session_id: str
    user_id: str
    message: str


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the single-page chat UI.

    Returns:
        HTML content of web/index.html.
    """
    html = Path("web/index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.post("/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    """Proxy a user message to Agent Core and return the response.

    Builds a TurnInput from the request, POSTs to Agent Core /process_turn,
    and returns the formatted TurnResult as JSON.

    Args:
        req: ChatRequest with session_id, user_id, and message.

    Returns:
        JSON with response_text, was_escalated, session_id, latency_ms.
        Returns HTTP 502 if Agent Core is unreachable or times out.
    """
    turn_input = _web_reach.build_turn_input(req.session_id, req.user_id, req.message)
    start = time.time()
    try:
        response = httpx.post(
            _endpoint,
            json={
                "session_id": turn_input.session_id,
                "user_message": turn_input.user_message,
                "channel": turn_input.channel,
                "timestamp_ms": turn_input.timestamp_ms,
                "user_id": turn_input.user_id,
            },
            timeout=_timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        result = TurnResult(
            session_id=data.get("session_id", req.session_id),
            response_text=data.get("response_text", ""),
            was_escalated=data.get("was_escalated", False),
            was_tool_used=data.get("was_tool_used", False),
            model_used=data.get("model_used", ""),
            latency_ms=data.get("latency_ms", int((time.time() - start) * 1000)),
        )
        logger.info(
            "web_reach.chat",
            extra={
                "operation": "server.chat",
                "status": "success",
                "session_id": req.session_id,
                "latency_ms": int((time.time() - start) * 1000),
                "was_escalated": result.was_escalated,
            },
        )
        return JSONResponse(content=_web_reach.format_result(result))

    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.error(
            "web_reach.chat_error",
            extra={
                "operation": "server.chat",
                "status": "failure",
                "session_id": req.session_id,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return JSONResponse(
            status_code=502,
            content={"error": "Agent Core is unavailable. Please try again shortly."},
        )
    except Exception as e:
        logger.error(
            "web_reach.chat_unexpected_error",
            extra={
                "operation": "server.chat",
                "status": "failure",
                "session_id": req.session_id,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return JSONResponse(
            status_code=502,
            content={"error": "Unexpected error processing your request."},
        )


@app.get("/user-history/{user_id}")
async def user_history(user_id: str) -> JSONResponse:
    """Fetch the most recent active session and chat history for a user from Memory Layer.

    Called once when the user is identified (page load or setup form submit).
    Returns both the session_id and formatted turns in a single request so the
    browser can resume a previous session without a separate session lookup.
    Never polled per-turn — subsequent turns update in-memory JS state only.

    Args:
        user_id: The user ID entered in the setup form.

    Returns:
        JSON with session_id (str or null) and turns (list[{role, text}]).
        Returns {"session_id": null, "turns": []} if Memory Layer is unreachable.
    """
    if not user_id.strip():
        return JSONResponse(content={"session_id": None, "turns": []})
    start = time.time()
    try:
        resp = httpx.get(
            f"{_memory_endpoint}/users/{user_id}/active-history",
            timeout=_memory_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()  # {session_id, turns: list[dict]} from Memory Layer
        turns = []
        for t in data.get("turns", []):
            if t.get("user_message"):
                turns.append({"role": "user", "text": t["user_message"]})
            if t.get("system_message"):
                turns.append({"role": "agent", "text": t["system_message"]})
        logger.info(
            "web_reach.user_history",
            extra={
                "operation": "server.user_history",
                "status": "success",
                "user_id": user_id,
                "found": data.get("session_id") is not None,
                "turn_count": len(turns),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return JSONResponse(content={"session_id": data.get("session_id"), "turns": turns})
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning(
            "web_reach.user_history_unavailable",
            extra={
                "operation": "server.user_history",
                "status": "skipped",
                "user_id": user_id,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return JSONResponse(content={"session_id": None, "turns": []})
    except Exception as e:
        logger.error(
            "web_reach.user_history_error",
            extra={
                "operation": "server.user_history",
                "status": "failure",
                "user_id": user_id,
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return JSONResponse(content={"session_id": None, "turns": []})


@app.get("/health")
async def health() -> dict:
    """Liveness probe.

    Returns:
        Dict with status 'ok'.
    """
    return {"status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=_host, port=_port)
```

- [ ] **Step 5: Run tests — confirm they all pass**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest tests/test_server.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add reach_layer/server.py reach_layer/tests/test_server.py reach_layer/pyproject.toml
git commit -m "feat(reach_layer): add FastAPI server with /chat proxy endpoint and tests"
```

---

## Task 5: Create the browser chat UI (`web/index.html`)

**Files:**
- Create: `reach_layer/web/index.html`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer/web
```

- [ ] **Step 2: Create `reach_layer/web/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DPG Chat Demo</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #f0f2f5;
      height: 100dvh;
      display: flex;
      flex-direction: column;
    }

    /* ── Setup screen ────────────────────────────────────────── */
    #setup {
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
    }
    .setup-card {
      background: #fff;
      padding: 40px 32px;
      border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.10);
      width: 340px;
    }
    .setup-card h1 { font-size: 1.375rem; margin-bottom: 6px; color: #111; }
    .setup-card p  { color: #6b7280; font-size: 0.875rem; margin-bottom: 28px; }
    label {
      display: block;
      font-size: 0.8125rem;
      font-weight: 600;
      color: #374151;
      margin-bottom: 6px;
    }
    input[type=text] {
      width: 100%;
      padding: 10px 14px;
      border: 1.5px solid #d1d5db;
      border-radius: 10px;
      font-size: 1rem;
      outline: none;
      transition: border-color .15s;
      margin-bottom: 20px;
    }
    input[type=text]:focus { border-color: #4f46e5; }
    .btn-primary {
      width: 100%;
      padding: 11px;
      background: #4f46e5;
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background .15s;
    }
    .btn-primary:hover { background: #4338ca; }

    /* ── Loading screen ──────────────────────────────────────── */
    #loading {
      display: none;
      align-items: center;
      justify-content: center;
      flex: 1;
    }

    /* ── Chat screen ─────────────────────────────────────────── */
    #chat { display: none; flex-direction: column; flex: 1; min-height: 0; }

    .chat-header {
      padding: 14px 20px;
      background: #fff;
      border-bottom: 1px solid #e5e7eb;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .chat-header h2 { font-size: 1rem; font-weight: 700; color: #111; }
    .user-badge {
      font-size: 0.75rem;
      color: #6b7280;
      background: #f3f4f6;
      padding: 4px 12px;
      border-radius: 20px;
    }

    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 20px 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .msg {
      max-width: 72%;
      padding: 10px 15px;
      border-radius: 18px;
      font-size: 0.9375rem;
      line-height: 1.5;
      word-break: break-word;
    }
    .msg.user {
      background: #4f46e5;
      color: #fff;
      align-self: flex-end;
      border-bottom-right-radius: 4px;
    }
    .msg.agent {
      background: #fff;
      color: #111;
      align-self: flex-start;
      border-bottom-left-radius: 4px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    .msg.agent.escalated {
      border-left: 3px solid #f59e0b;
      padding-left: 12px;
    }
    .escalation-label {
      display: block;
      font-size: 0.6875rem;
      font-weight: 600;
      color: #f59e0b;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-top: 6px;
    }
    .msg.error-msg {
      background: #fef2f2;
      color: #dc2626;
      align-self: flex-start;
      border-radius: 10px;
      font-size: 0.875rem;
    }

    /* Loading dots */
    .msg.loading {
      background: #fff;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
      align-self: flex-start;
      border-bottom-left-radius: 4px;
      padding: 14px 18px;
    }
    .dots span {
      display: inline-block;
      width: 7px; height: 7px;
      background: #9ca3af;
      border-radius: 50%;
      margin: 0 2px;
      animation: bounce 1.1s ease-in-out infinite;
    }
    .dots span:nth-child(2) { animation-delay: .18s; }
    .dots span:nth-child(3) { animation-delay: .36s; }
    @keyframes bounce {
      0%, 75%, 100% { transform: translateY(0); }
      35% { transform: translateY(-7px); }
    }

    /* Input bar */
    .input-bar {
      padding: 14px 16px;
      background: #fff;
      border-top: 1px solid #e5e7eb;
      display: flex;
      gap: 10px;
      flex-shrink: 0;
    }
    .input-bar input {
      flex: 1;
      padding: 10px 16px;
      border: 1.5px solid #e5e7eb;
      border-radius: 24px;
      font-size: 0.9375rem;
      outline: none;
      transition: border-color .15s;
    }
    .input-bar input:focus { border-color: #4f46e5; }
    .input-bar button {
      padding: 10px 22px;
      background: #4f46e5;
      color: #fff;
      border: none;
      border-radius: 24px;
      font-size: 0.9375rem;
      font-weight: 600;
      cursor: pointer;
      transition: background .15s;
    }
    .input-bar button:hover:not(:disabled) { background: #4338ca; }
    .input-bar button:disabled { background: #c7d2fe; cursor: not-allowed; }
  </style>
</head>
<body>

<!-- ── Setup screen ─────────────────────────────────────────── -->
<div id="setup">
  <div class="setup-card">
    <h1>DPG Chat Demo</h1>
    <p>Enter your user ID (phone number or any identifier) to start.</p>
    <label for="userIdInput">User ID</label>
    <input type="text" id="userIdInput" placeholder="e.g. 9876543210" autocomplete="off" />
    <button class="btn-primary" id="startBtn">Start Chat</button>
  </div>
</div>

<!-- ── Loading screen ───────────────────────────────────────── -->
<div id="loading">
  <div class="dots"><span></span><span></span><span></span></div>
</div>

<!-- ── Chat screen ─────────────────────────────────────────── -->
<div id="chat">
  <div class="chat-header">
    <h2>DPG Chat</h2>
    <span class="user-badge" id="userBadge"></span>
  </div>
  <div class="messages" id="messages"></div>
  <div class="input-bar">
    <input type="text" id="msgInput" placeholder="Type a message…" autocomplete="off" />
    <button id="sendBtn">Send</button>
  </div>
</div>

<script>
  'use strict';

  let sessionId = null;
  let userId    = null;

  /* ── UUID v4 ───────────────────────────────────────────────── */
  function uuid4() {
    return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, c =>
      (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
    );
  }

  /* ── Setup ─────────────────────────────────────────────────── */
  document.getElementById('startBtn').addEventListener('click', startSession);
  document.getElementById('userIdInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') startSession();
  });

  async function startSession() {
    const input = document.getElementById('userIdInput').value.trim();
    if (!input) { document.getElementById('userIdInput').focus(); return; }

    const btn = document.getElementById('startBtn');
    btn.disabled  = true;
    btn.textContent = 'Loading…';

    userId = input;
    localStorage.setItem('dpg_user_id', userId);

    // Single call to Memory Layer (via Reach Layer proxy) returns both the
    // active session_id and chat history together. If no active session exists
    // (new user or TTL expired), session_id is null and we generate a new UUID.
    const { resolvedSession, initialTurns } = await fetchUserHistory(userId);
    sessionId = resolvedSession || uuid4();
    localStorage.setItem('dpg_session_id', sessionId);

    btn.disabled    = false;
    btn.textContent = 'Start Chat';

    document.getElementById('userBadge').textContent = userId;
    document.getElementById('setup').style.display = 'none';
    document.getElementById('chat').style.display  = 'flex';
    for (const t of initialTurns) {
      appendMessage(t.text, t.role === 'user' ? 'user' : 'agent');
    }
    document.getElementById('msgInput').focus();
  }

  /* ── Messaging ─────────────────────────────────────────────── */
  document.getElementById('sendBtn').addEventListener('click', sendMessage);
  document.getElementById('msgInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  function appendMessage(text, role, escalated = false) {
    const msgs = document.getElementById('messages');
    const div  = document.createElement('div');
    div.className = 'msg ' + role + (escalated ? ' escalated' : '');
    div.textContent = text;
    if (escalated) {
      const label = document.createElement('span');
      label.className = 'escalation-label';
      label.textContent = '⚠ Escalated to human agent';
      div.appendChild(label);
    }
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function showLoading() {
    const msgs = document.getElementById('messages');
    const div  = document.createElement('div');
    div.className = 'msg loading';
    div.id = 'loading-dot';
    div.innerHTML = '<div class="dots"><span></span><span></span><span></span></div>';
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function hideLoading() {
    const el = document.getElementById('loading-dot');
    if (el) el.remove();
  }

  /* ── Fetch active session + history for a user (single call) ── */
  async function fetchUserHistory(uid) {
    try {
      const resp = await fetch(`/user-history/${encodeURIComponent(uid)}`);
      if (!resp.ok) return { resolvedSession: null, initialTurns: [] };
      const data = await resp.json();  // {session_id, turns: [{role, text}, ...]}
      return {
        resolvedSession: data.session_id || null,
        initialTurns:    data.turns       || [],
      };
    } catch (_) {
      // Memory Layer unreachable — caller falls back to new session
      return { resolvedSession: null, initialTurns: [] };
    }
  }

  async function sendMessage() {
    const input = document.getElementById('msgInput');
    const btn   = document.getElementById('sendBtn');
    const text  = input.value.trim();
    if (!text || btn.disabled) return;

    input.value   = '';
    btn.disabled  = true;
    appendMessage(text, 'user');
    showLoading();

    try {
      const resp = await fetch('/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ session_id: sessionId, user_id: userId, message: text }),
      });
      hideLoading();
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        appendMessage(err.error || `Server error (${resp.status})`, 'error-msg');
      } else {
        const data = await resp.json();
        appendMessage(data.response_text, 'agent', data.was_escalated);
      }
    } catch (err) {
      hideLoading();
      appendMessage('Could not reach the server. Please try again.', 'error-msg');
    } finally {
      btn.disabled = false;
      input.focus();
    }
  }

  /* ── Restore session on page load ──────────────────────────── */
  window.addEventListener('load', async () => {
    const savedUser = localStorage.getItem('dpg_user_id');
    if (!savedUser) return;  // First ever visit — show setup form as-is

    // userId known (same or different browser). Hide setup, show loading spinner
    // while Memory Layer resolves the active session and history.
    document.getElementById('setup').style.display   = 'none';
    document.getElementById('loading').style.display = 'flex';

    const { resolvedSession, initialTurns } = await fetchUserHistory(savedUser);

    document.getElementById('loading').style.display = 'none';

    if (resolvedSession) {
      // Active session found — go straight to chat with history
      userId    = savedUser;
      sessionId = resolvedSession;
      localStorage.setItem('dpg_session_id', sessionId);
      document.getElementById('userBadge').textContent = userId;
      document.getElementById('chat').style.display  = 'flex';
      for (const t of initialTurns) {
        appendMessage(t.text, t.role === 'user' ? 'user' : 'agent');
      }
    } else {
      // No active session (TTL expired) — show setup form with userId pre-filled
      document.getElementById('userIdInput').value   = savedUser;
      document.getElementById('setup').style.display = 'flex';
    }
  });
</script>

</body>
</html>
```

- [ ] **Step 3: Smoke-test the UI manually**

Start the server locally (requires Agent Core to be running, or skip/mock):

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run python server.py
# Open http://localhost:8005 in browser
```

Verify:
- Setup form appears on first load
- Entering a user ID and clicking Start Chat shows the chat screen
- User badge shows the entered ID
- Sending a message shows a loading indicator
- Response displays in a chat bubble
- `localStorage` persists both user ID and session ID on refresh
- Reloading the page skips the setup form and goes directly to the chat screen
- Prior conversation turns load into the chat on return (if Agent Core + Memory Layer are running)
- After history loads, sending a new message appends directly to the UI without a DB re-fetch

- [ ] **Step 4: Commit**

```bash
git add reach_layer/web/index.html
git commit -m "feat(reach_layer): add single-page chat UI (vanilla JS, no build step)"
```

---

## Task 6: Update config, Dockerfile, and docker-compose

**Files:**
- Modify: `reach_layer/config/dpg.yaml`
- Modify: `reach_layer/Dockerfile`
- Modify: `automation/docker/docker-compose.dev.yml`

- [ ] **Step 1: Update `reach_layer/config/dpg.yaml`** — add web title key

```yaml
# reach_layer/config/dpg.yaml — DPG framework defaults.
# Endpoint URLs here are for local-docker. In Kubernetes, override with
# AGENT_CORE_ENDPOINT and MEMORY_LAYER_ENDPOINT env vars (see k8s/reach-layer.yaml).

server:
  host: 0.0.0.0
  port: 8005

reach_layer:
  cli:
    prompt: "You: "
    agent_prefix: "Agent: "
  web:
    title: "DPG Chat Demo"

agent_core_client:
  endpoint: "http://localhost:8000/process_turn"
  timeout_s: 30.0

memory_layer_client:
  endpoint: "http://localhost:8002"
  timeout_s: 10.0
```

- [ ] **Step 2: Update `reach_layer/Dockerfile`**

Read the current Dockerfile first. Then:
- Change `CMD` from `uv run python main.py` to `uv run python server.py`
- Keep everything else identical (two-stage build, non-root user)
- Add `EXPOSE 8005` before CMD (good practice for documentation; docker-compose maps the port)

The new last two lines should be:
```dockerfile
EXPOSE 8005
CMD ["uv", "run", "python", "server.py"]
```

CLI mode remains accessible via:
```bash
docker run --rm -it <image> uv run python main.py
```

- [ ] **Step 3: Update `automation/docker/docker-compose.dev.yml`** — make reach_layer a web server

Find the `reach_layer` service definition. Change:

```yaml
# Before
reach_layer:
  ...
  stdin_open: true
  tty: true
  profiles:
    - cli
  # (no ports mapping)

# After
reach_layer:
  ...
  ports:
    - "8005:8005"
  # Remove stdin_open, tty, and profiles entries
```

The service will now start automatically with `docker compose up -d` and be accessible at `http://localhost:8005`.

- [ ] **Step 4: Run full test suite one final time**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer
uv run pytest -v --cov=src --cov=server.py --cov=config_loader.py --cov-report=term-missing
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/config/dpg.yaml reach_layer/Dockerfile automation/docker/docker-compose.dev.yml
git commit -m "feat(reach_layer): update config, Dockerfile, and compose for web server mode"
```

---

## Task 7: Add Kubernetes manifests

**Files:**
- Create: `reach_layer/k8s/reach-layer.yaml`

No unit tests for Kubernetes manifests. Verification is a dry-run `kubectl apply`.

- [ ] **Step 1: Create `reach_layer/k8s/` directory**

```bash
mkdir -p /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer/k8s
```

- [ ] **Step 2: Create `reach_layer/k8s/reach-layer.yaml`**

```yaml
# reach_layer/k8s/reach-layer.yaml
#
# Kubernetes manifests for the DPG Reach Layer web UI.
# All DPG services are assumed to be in the same namespace and reachable
# via Kubernetes DNS (e.g. http://agent-core:8000).
#
# Deploy:
#   kubectl apply -f reach_layer/k8s/reach-layer.yaml -n <namespace>
#
# Endpoint URLs are injected via env vars so the image is environment-agnostic.
# Local-docker defaults in config/dpg.yaml are ignored when these vars are set.
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reach-layer
  labels:
    app: reach-layer
spec:
  replicas: 1
  selector:
    matchLabels:
      app: reach-layer
  template:
    metadata:
      labels:
        app: reach-layer
    spec:
      containers:
        - name: reach-layer
          image: dpg/reach-layer:latest        # replace with your registry path
          ports:
            - containerPort: 8005
          env:
            - name: AGENT_CORE_ENDPOINT
              value: "http://agent-core:8000/process_turn"
            - name: MEMORY_LAYER_ENDPOINT
              value: "http://memory-layer:8002"
          readinessProbe:
            httpGet:
              path: /health
              port: 8005
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8005
            initialDelaySeconds: 10
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: reach-layer
  labels:
    app: reach-layer
spec:
  selector:
    app: reach-layer
  ports:
    - port: 8005
      targetPort: 8005
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: reach-layer
  # Add TLS + host annotations here for production (cert-manager, nginx, etc.)
spec:
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: reach-layer
                port:
                  number: 8005
```

How the traffic flows in K8s:

```
Browser → Ingress (port 80/443) → reach-layer Service (:8005)
                                           ↓
                              reach-layer Pod (FastAPI)
                              ├── POST /chat              → agent-core Service (:8000)
                              └── GET /user-history/*     → memory-layer Service (:8002)
```

- The Ingress controller (typically nginx-ingress) terminates HTTP/HTTPS and routes to the ClusterIP Service.
- To add a hostname (e.g. `chat.yourdomain.com`), add `rules[0].host: chat.yourdomain.com` and a TLS block.
- `agent-core` and `memory-layer` are the Kubernetes Service names for those DPG blocks. Adjust to match your namespace deployment.

- [ ] **Step 3: Verify manifest syntax (dry-run)**

```bash
kubectl apply --dry-run=client -f /Users/srivastha/KKB/Github/ai-diffusion-dpg/reach_layer/k8s/reach-layer.yaml
```

Expected output:
```
deployment.apps/reach-layer configured (dry run)
service/reach-layer configured (dry run)
ingress.networking.k8s.io/reach-layer configured (dry run)
```

- [ ] **Step 4: Commit**

```bash
git add reach_layer/k8s/reach-layer.yaml
git commit -m "feat(reach_layer): add K8s Deployment + Service + Ingress manifests"
```

---

## Spec Coverage Review

| Issue requirement | Covered by |
|---|---|
| User ID / session setup screen | Task 5 — setup form in `index.html` |
| User ID persists for session; changing starts fresh | Task 5 — `localStorage` + new `sessionId = uuid4()` on submit |
| Single-page chat UI: input, send, scroll history | Task 5 — full UI in `index.html` |
| Loading indicator while waiting for Agent Core | Task 5 — `.msg.loading` + `.dots` animation |
| Connects to Agent Core `POST /process_turn` | Task 4 — `server.py` `POST /chat` → Agent Core |
| Lightweight: no heavy framework | Task 5 — vanilla JS, no npm, no build step |
| Single deployable artifact (FastAPI) | Task 4 — `server.py` serves both HTML and API |
| Implements `ReachLayerBase` (`receive()`/`deliver()`) | Task 1 — base.py created; Task 3 — `WebReachLayer` inherits from it |
| Session ID generated client-side (UUID) | Task 5 — `uuid4()` in browser JS |
| Agent Core endpoint configurable via YAML | Task 6 — `agent_core_client.endpoint` in `dpg.yaml` |
| No auth, no multi-user | No implementation needed (by design) |
| Chat history loads on return visit (from SQLite via Memory Layer) | Task 0 — `GET /users/{user_id}/active-history` in Memory Layer; Task 5 — `GET /user-history/{user_id}` in Reach Layer; Task 6 — `fetchUserHistory()` in `index.html` |
| Returning user in different browser resumes active session | Task 0/5/6 — single call returns both session_id and turns; no separate session lookup needed |
| Per-turn UI updates are in-memory only (no DB fetch per turn) | Task 5 — `sendMessage()` appends to DOM directly; `loadHistory()` called only on page load |
| Deployed in Kubernetes, exposed via Ingress | Task 7 — `k8s/reach-layer.yaml` (Deployment + Service + Ingress) |
| Endpoints configurable without image rebuild (K8s env vars) | Task 4 — `os.environ.get("AGENT_CORE_ENDPOINT")` / `os.environ.get("MEMORY_LAYER_ENDPOINT")` in `server.py` |
| Not production — dev/demo adapter only | Plan scope is correctly limited |

**Note:** `test_main.py` rename to `test_config_loader.py` (Task 2) is required by the project rule "test file names must match the module they test."
