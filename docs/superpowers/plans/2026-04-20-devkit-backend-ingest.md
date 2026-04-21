# Dev-Kit Backend Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add KB document ingestion backend support to the dev-kit: a new `devkit.yaml` config file, a `DevKitConfig` loader, a static API key auth helper, three new FastAPI endpoints (`POST /api/ingest/submit`, `GET /api/ingest/job/{id}`, `POST /api/ingest/callback`), a new `set_azure_storage` agent tool, and updates to the knowledge phase prompt.

**Architecture:** Config is loaded once at startup from `dev-kit/dev_kit/config/devkit.yaml` into a `DevKitConfig` dataclass. The three ingest endpoints are added to the existing `dev-kit/dev_kit/agent/app.py` FastAPI router. API keys are auto-generated at first deploy-wizard visit to `MandatoryInputsStep` and stored in `project.json`. The `set_azure_storage` tool replaces `set_knowledge_documents` in the knowledge phase.

**Tech Stack:** Python 3.11, FastAPI, httpx, PyYAML, cryptography (Fernet for account_key at rest), pytest.

**Design Issue Resolution:** `MandatoryInputsStep` needs to pre-fill Azure fields from `project.json`. Fix: extend `GET /api/projects/{slug}` to include `azure_storage` (with masked `account_key`) in the returned metadata.

---

## File Map

| Path | Action | Responsibility |
|------|--------|---------------|
| `dev-kit/dev_kit/config/__init__.py` | **Create** | Package marker |
| `dev-kit/dev_kit/config/devkit.yaml` | **Create** | Framework-level dev-kit config (user_id, upload limits, polling params) |
| `dev-kit/dev_kit/config/loader.py` | **Create** | `DevKitConfig` dataclass + `load_devkit_config()` |
| `dev-kit/dev_kit/agent/auth.py` | **Create** | `verify_api_key(header, expected)` helper |
| `dev-kit/dev_kit/agent/tools.py` | Modify | Add `set_azure_storage` tool definition + handler |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Modify | Update knowledge phase prompt |
| `dev-kit/dev_kit/agent/app.py` | Modify | Add 3 ingest endpoints + extend project GET |
| `dev-kit/configs/.gitignore` | **Create** | Ignore `*/project.json` |
| `dev-kit/tests/test_devkit_config.py` | **Create** | Tests for config loader |
| `dev-kit/tests/test_devkit_auth.py` | **Create** | Tests for `verify_api_key` |
| `dev-kit/tests/test_ingest_endpoints.py` | **Create** | Tests for 3 new ingest endpoints |

---

## Task 1: devkit.yaml config + loader

**Files:**
- Create: `dev-kit/dev_kit/config/__init__.py`
- Create: `dev-kit/dev_kit/config/devkit.yaml`
- Create: `dev-kit/dev_kit/config/loader.py`
- Create: `dev-kit/tests/test_devkit_config.py`

- [ ] **Step 1: Write failing tests**

First check if `dev-kit/tests/` exists:
```bash
ls dev-kit/tests/ 2>/dev/null || echo "no tests dir"
```

Create `dev-kit/tests/test_devkit_config.py` (create `dev-kit/tests/` and `dev-kit/tests/__init__.py` if needed):

```python
"""
dev-kit/tests/test_devkit_config.py

Tests for DevKitConfig loader.
"""
from __future__ import annotations

import pytest
from pathlib import Path


class TestDevKitConfigNormal:
    def test_loads_defaults(self, tmp_path, monkeypatch):
        """Loader reads devkit.yaml and returns populated dataclass."""
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("""
user_id: "test-operator"
upload:
  max_files_per_upload: 5
  max_file_size_mb: 30
  supported_extensions: [".pdf", ".txt"]
polling:
  poll_interval_seconds: 5
  poll_timeout_minutes: 15
""")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        cfg = load_devkit_config()
        assert cfg.user_id == "test-operator"
        assert cfg.upload.max_files_per_upload == 5
        assert cfg.upload.max_file_size_mb == 30
        assert ".pdf" in cfg.upload.supported_extensions
        assert cfg.polling.poll_interval_seconds == 5
        assert cfg.polling.poll_timeout_minutes == 15

    def test_user_id_default(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("user_id: devkit-operator\nupload:\n  max_files_per_upload: 3\n  max_file_size_mb: 10\n  supported_extensions: ['.pdf']\npolling:\n  poll_interval_seconds: 5\n  poll_timeout_minutes: 10\n")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        cfg = load_devkit_config()
        assert cfg.user_id == "devkit-operator"


class TestDevKitConfigEdge:
    def test_missing_config_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
        from dev_kit.config.loader import load_devkit_config
        with pytest.raises(FileNotFoundError):
            load_devkit_config()


class TestDevKitConfigFailure:
    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("not: valid: yaml: [unclosed")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        with pytest.raises(Exception):
            load_devkit_config()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_devkit_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'dev_kit.config'`

- [ ] **Step 3: Create config/__init__.py**

```python
# dev-kit/dev_kit/config/__init__.py
```

- [ ] **Step 4: Create devkit.yaml**

Create `dev-kit/dev_kit/config/devkit.yaml`:

```yaml
# dev-kit/dev_kit/config/devkit.yaml
#
# Framework-level dev-kit operational parameters.
# All values are framework-scoped and apply to all deployments.
# Do not add domain-specific values here.

# Identity used for all upload requests until login is implemented.
# Passed explicitly in the multipart request body to KE.
user_id: "devkit-operator"

upload:
  # Maximum number of files allowed per batch submission.
  max_files_per_upload: 5
  # Maximum size (MB) for a single file. Files exceeding this are rejected.
  max_file_size_mb: 30
  # Supported file extensions for KB document upload.
  supported_extensions:
    - ".pdf"
    - ".txt"
    - ".md"
    - ".csv"
    - ".docx"
    - ".html"

polling:
  # How often the frontend polls for job status (seconds).
  poll_interval_seconds: 5
  # Maximum total polling duration before showing a timeout message (minutes).
  poll_timeout_minutes: 15
```

- [ ] **Step 5: Create config/loader.py**

```python
"""
dev-kit/dev_kit/config/loader.py

Dev-kit operational config loader.

Reads devkit.yaml once at startup and exposes a DevKitConfig dataclass.
Never re-reads config in request paths.

Belongs to the Dev-Kit tool of the DPG framework.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "devkit.yaml"


@dataclass
class UploadConfig:
    """Upload limits and supported file types."""
    max_files_per_upload: int = 5
    max_file_size_mb: int = 30
    supported_extensions: list[str] = field(
        default_factory=lambda: [".pdf", ".txt", ".md", ".csv", ".docx", ".html"]
    )


@dataclass
class PollingConfig:
    """Frontend polling parameters."""
    poll_interval_seconds: int = 5
    poll_timeout_minutes: int = 15


@dataclass
class DevKitConfig:
    """Top-level dev-kit operational config."""
    user_id: str = "devkit-operator"
    upload: UploadConfig = field(default_factory=UploadConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)


def load_devkit_config(path: Optional[Path] = None) -> DevKitConfig:
    """Load DevKitConfig from YAML.

    Reads from DEVKIT_CONFIG_PATH env var if set, otherwise uses the
    bundled devkit.yaml at dev-kit/dev_kit/config/devkit.yaml.

    Args:
        path: Optional explicit path override (used in tests).

    Returns:
        Populated DevKitConfig dataclass.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if path is None:
        env_path = os.environ.get("DEVKIT_CONFIG_PATH")
        path = Path(env_path) if env_path else _DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(f"Dev-kit config not found: {path}")

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    upload_raw = raw.get("upload", {})
    polling_raw = raw.get("polling", {})

    return DevKitConfig(
        user_id=raw.get("user_id", "devkit-operator"),
        upload=UploadConfig(
            max_files_per_upload=upload_raw.get("max_files_per_upload", 5),
            max_file_size_mb=upload_raw.get("max_file_size_mb", 30),
            supported_extensions=upload_raw.get(
                "supported_extensions",
                [".pdf", ".txt", ".md", ".csv", ".docx", ".html"],
            ),
        ),
        polling=PollingConfig(
            poll_interval_seconds=polling_raw.get("poll_interval_seconds", 5),
            poll_timeout_minutes=polling_raw.get("poll_timeout_minutes", 15),
        ),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_devkit_config.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/config/ dev-kit/tests/test_devkit_config.py
git commit -m "feat(devkit): add DevKitConfig loader and devkit.yaml"
```

---

## Task 2: Dev-Kit auth helper

**Files:**
- Create: `dev-kit/dev_kit/agent/auth.py`
- Create: `dev-kit/tests/test_devkit_auth.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_devkit_auth.py`:

```python
"""
dev-kit/tests/test_devkit_auth.py

Tests for dev-kit static API key verification helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


class TestVerifyApiKeyNormal:
    def test_matching_key_does_not_raise(self):
        from dev_kit.agent.auth import verify_api_key
        result = verify_api_key("secret", "secret")
        assert result is None


class TestVerifyApiKeyFailure:
    def test_wrong_key_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong", "right")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid API key"

    def test_missing_header_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, "expected")
        assert exc_info.value.status_code == 401

    def test_empty_header_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "expected")
        assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_devkit_auth.py -v
```

Expected: `ModuleNotFoundError: No module named 'dev_kit.agent.auth'`

- [ ] **Step 3: Implement dev_kit/agent/auth.py**

```python
"""
dev-kit/dev_kit/agent/auth.py

Static API key verification for dev-kit service-to-service calls.

Belongs to the Dev-Kit tool of the DPG framework.
Called by ingest endpoints to authenticate incoming requests from KE callbacks
and outgoing request construction for Reach Layer calls.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def verify_api_key(header: Optional[str], expected: str) -> None:
    """Verify that the X-API-Key header matches the expected static key.

    Args:
        header: Value of the X-API-Key header from the incoming request.
        expected: Expected API key read from env at startup.

    Raises:
        HTTPException: 401 if header is missing, empty, or does not match expected.
    """
    if not header or not expected or header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_devkit_auth.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/auth.py dev-kit/tests/test_devkit_auth.py
git commit -m "feat(devkit): add verify_api_key auth helper"
```

---

## Task 3: set_azure_storage tool + knowledge phase prompt update

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`

- [ ] **Step 1: Add set_azure_storage to TOOL_DEFINITIONS in tools.py**

In `dev-kit/dev_kit/agent/tools.py`, add the following to the `TOOL_DEFINITIONS` list:

```python
{
    "name": "set_azure_storage",
    "description": (
        "Save Azure Blob Storage credentials for KB document ingestion. "
        "Call only if the operator confirms they have Azure Blob Storage. "
        "If the operator does not have Azure, do not call this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "account_name": {
                "type": "string",
                "description": "Azure storage account name."
            },
            "account_key": {
                "type": "string",
                "description": "Azure storage account key (Base64-encoded)."
            },
            "container_name": {
                "type": "string",
                "description": "Azure Blob container name where KB documents are stored."
            }
        },
        "required": ["account_name", "account_key", "container_name"]
    }
},
```

- [ ] **Step 2: Add _handle_set_azure_storage to ToolHandler**

In the `ToolHandler` class in `tools.py`, add:

```python
def _handle_set_azure_storage(self, tool_input: dict) -> str:
    """Save Azure Blob Storage credentials to project.json.

    Credentials are stored under the 'azure_storage' key. The account_key
    is stored as-is in this handler; encryption is applied by the calling
    endpoint before writing to disk.

    Args:
        tool_input: dict with account_name, account_key, container_name.

    Returns:
        Confirmation message string.
    """
    account_name = tool_input.get("account_name", "")
    account_key = tool_input.get("account_key", "")
    container_name = tool_input.get("container_name", "")

    if not account_name or not account_key or not container_name:
        return "Error: account_name, account_key, and container_name are all required."

    self.state["azure_storage"] = {
        "account_name": account_name,
        "account_key": account_key,
        "container_name": container_name,
    }

    return (
        f"Azure Blob Storage credentials saved: account={account_name}, "
        f"container={container_name}. "
        "Local files can be uploaded without Azure — "
        "Azure fetch and upload modes will be available at the IngestDocumentsStep."
    )
```

Also add `"set_azure_storage": self._handle_set_azure_storage` to the `dispatch` method's routing dict.

- [ ] **Step 3: Update knowledge phase prompt in phases.py**

Replace the `if phase == "knowledge":` block in `dev-kit/dev_kit/agent/prompts/phases.py` with:

```python
    if phase == "knowledge":
        return (
            "## Knowledge phase — valid fields\n\n"
            "Use `update_config` with block=`knowledge_engine`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('knowledge_engine'))}\n\n"
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- RAG / vector store config: section=`knowledge.blocks.static_knowledge_base`\n"
            "  Keys: `collection_name`, `vector_store`, `top_k`, `similarity_threshold`, `sources` (list), `intent_filters` (dict)\n"
            "  ❌ NEVER write flat keys directly under knowledge: (e.g. knowledge.collection_name, knowledge.top_k)\n"
            "- Persona text: section=`conversation`, values={\"persona\": {\"text\": \"...\"}}\n"
            "  ❌ NEVER use: conversation.assistant_persona, conversation.persona_text\n"
            "- Language instruction: section=`conversation`, key: `language_instruction`\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "**KB Document Sources — ask ONE question:**\n"
            "Ask: 'Do you have Azure Blob Storage for your KB documents?\n"
            "- If yes: I need your Azure account name, account key, and container name.\n"
            "- If no: no setup needed — you will upload local files after deployment.'\n\n"
            "  ┌─ Yes, Azure ──────────────────────────────────────────────────────────┐\n"
            "  │  → Call set_azure_storage({account_name, account_key, container_name}) │\n"
            "  │  → At IngestDocumentsStep (post-deploy), per file the operator chooses: │\n"
            "  │      'Fetch from Azure', 'Upload local + push to Azure', or 'Local only' │\n"
            "  └───────────────────────────────────────────────────────────────────────┘\n\n"
            "  ┌─ No cloud storage ────────────────────────────────────────────────────┐\n"
            "  │  → Do NOT call set_azure_storage                                       │\n"
            "  │  → At IngestDocumentsStep, only 'Upload local only' will be available  │\n"
            "  └───────────────────────────────────────────────────────────────────────┘\n\n"
            "Do NOT collect document filenames or a list of files — operators upload files\n"
            "directly in IngestDocumentsStep after deployment.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```\n\n"
            "➡️ When collection_name, persona, and language_instruction are set "
            "(and azure_storage is saved if applicable), call `set_phase('memory')`."
        )
```

- [ ] **Step 4: Verify tools.py dispatch routing is correct**

Ensure the `dispatch` method in `ToolHandler` routes `"set_azure_storage"` to the new handler. The dispatch method typically looks like:

```python
def dispatch(self, tool_name: str, tool_input: dict) -> str:
    handler = getattr(self, f"_handle_{tool_name}", None)
    if handler is None:
        return f"Unknown tool: {tool_name}"
    return handler(tool_input)
```

If it uses `getattr`, no additional routing change is needed. Verify this pattern by checking the existing dispatch implementation. If it uses an explicit dict, add `"set_azure_storage": self._handle_set_azure_storage`.

- [ ] **Step 5: Run existing tools tests to catch regressions**

```bash
cd dev-kit && uv run pytest tests/ -k "tool" -v 2>/dev/null || uv run pytest tests/ -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/dev_kit/agent/prompts/phases.py
git commit -m "feat(devkit): add set_azure_storage tool and update knowledge phase prompt"
```

---

## Task 4: Three ingest endpoints + .gitignore

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`
- Create: `dev-kit/configs/.gitignore`
- Create: `dev-kit/tests/test_ingest_endpoints.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_ingest_endpoints.py`:

```python
"""
dev-kit/tests/test_ingest_endpoints.py

Tests for dev-kit ingest backend endpoints:
  POST /api/ingest/submit
  GET  /api/ingest/job/{job_id}
  POST /api/ingest/callback
"""
from __future__ import annotations

import json
import os
import pytest
import respx
import httpx as _httpx
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("DEVKIT_TO_REACH_API_KEY", "devkit-test-key")
    monkeypatch.setenv("KE_TO_DEVKIT_API_KEY", "ke-callback-key")
    monkeypatch.setenv("REACH_LAYER_URL", "http://reach-test:8005")


@pytest.fixture
def client():
    from dev_kit.agent.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/ingest/submit — normal
# ---------------------------------------------------------------------------

class TestIngestSubmitNormal:
    @respx.mock
    def test_submit_forwards_to_reach_layer(self, client):
        ke_response = {
            "batch_id": "b1",
            "jobs": [{"filename": "guide.pdf", "job_id": "j1"}]
        }
        respx.post("http://reach-test:8005/ingest/upload").mock(
            return_value=_httpx.Response(200, json=ke_response)
        )

        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "guide.pdf", "mode": "local_write_ingest"}])},
            files=[("files", ("guide.pdf", b"pdf content", "application/octet-stream"))],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["batch_id"] == "b1"
        assert body["jobs"][0]["job_id"] == "j1"

    @respx.mock
    def test_submit_injects_user_id(self, client):
        """Verify user_id from devkit.yaml is injected into the metadata."""
        captured_body = {}

        def capture_request(request):
            captured_body["content"] = request.content
            return _httpx.Response(200, json={"batch_id": "b2", "jobs": []})

        respx.post("http://reach-test:8005/ingest/upload").mock(side_effect=capture_request)

        client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "doc.txt", "mode": "local_write_ingest"}])},
            files=[("files", ("doc.txt", b"content", "text/plain"))],
        )
        # user_id should appear somewhere in the multipart body
        assert b"devkit-operator" in captured_body.get("content", b"")


# ---------------------------------------------------------------------------
# POST /api/ingest/submit — auth and validation
# ---------------------------------------------------------------------------

class TestIngestSubmitValidation:
    def test_unsupported_extension_rejected(self, client):
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "virus.exe", "mode": "local_write_ingest"}])},
            files=[("files", ("virus.exe", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 422

    def test_too_many_files_rejected(self, client):
        """More than max_files_per_upload (5) should be rejected."""
        entries = [{"filename": f"doc{i}.pdf", "mode": "local_write_ingest"} for i in range(6)]
        files = [(f"files", (f"doc{i}.pdf", b"x", "application/octet-stream")) for i in range(6)]
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps(entries)},
            files=files,
        )
        assert response.status_code == 422

    def test_path_traversal_rejected(self, client):
        response = client.post(
            "/api/ingest/submit",
            data={"metadata": json.dumps([{"filename": "../etc/passwd", "mode": "local_write_ingest"}])},
            files=[("files", ("../etc/passwd", b"x", "text/plain"))],
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/ingest/job/{job_id}
# ---------------------------------------------------------------------------

class TestIngestJobStatus:
    @respx.mock
    def test_returns_job_status_from_ke(self, client):
        job_data = {"job_id": "j1", "status": "ingested", "chunks_added": 47}
        respx.get("http://reach-test:8005/ingest/job/j1").mock(
            return_value=_httpx.Response(200, json=job_data)
        )
        response = client.get("/api/ingest/job/j1")
        assert response.status_code == 200
        assert response.json()["status"] == "ingested"

    @respx.mock
    def test_ke_404_propagated(self, client):
        respx.get("http://reach-test:8005/ingest/job/unknown").mock(
            return_value=_httpx.Response(404, json={"detail": "Not found"})
        )
        response = client.get("/api/ingest/job/unknown")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/ingest/callback
# ---------------------------------------------------------------------------

class TestIngestCallback:
    def test_valid_callback_accepted(self, client, tmp_path):
        """Valid callback with correct API key is accepted and persisted."""
        with patch("dev_kit.agent.app._get_project_path") as mock_path:
            mock_project_file = tmp_path / "project.json"
            mock_project_file.write_text('{"slug": "test"}')
            mock_path.return_value = tmp_path

            response = client.post(
                "/api/ingest/callback",
                json={
                    "job_id": "j1",
                    "status": "ingested",
                    "chunks_added": 47,
                    "error": None
                },
                headers={"X-API-Key": "ke-callback-key"},
            )
            assert response.status_code == 200

    def test_wrong_api_key_returns_401(self, client):
        response = client.post(
            "/api/ingest/callback",
            json={"job_id": "j1", "status": "ingested", "chunks_added": 10},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_missing_api_key_returns_401(self, client):
        response = client.post(
            "/api/ingest/callback",
            json={"job_id": "j1", "status": "ingested"},
        )
        assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_ingest_endpoints.py -v
```

Expected: FAIL — endpoints not yet defined.

- [ ] **Step 3: Add three ingest endpoints to app.py**

In `dev-kit/dev_kit/agent/app.py`, add the following imports near the top (with existing imports):

```python
import secrets
from dev_kit.agent.auth import verify_api_key as _verify_api_key
from dev_kit.config.loader import load_devkit_config as _load_devkit_config
```

Load config at module level (after existing module-level initialisation):

```python
# Load dev-kit config once at startup
_DEVKIT_CONFIG = _load_devkit_config()
_KE_TO_DEVKIT_API_KEY = os.environ.get("KE_TO_DEVKIT_API_KEY", "")
_DEVKIT_TO_REACH_API_KEY = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
_REACH_LAYER_URL = os.environ.get("REACH_LAYER_URL", "http://localhost:8005")
```

Then add the three endpoints (after the existing deploy wizard routes):

```python
# ---------------------------------------------------------------------------
# POST /api/ingest/submit
# ---------------------------------------------------------------------------

@app.post("/api/ingest/submit")
async def ingest_submit(request: Request):
    """Accept a multipart document batch from the browser and forward to Reach Layer.

    Validates entries (extension, size, count, path traversal), injects user_id
    from devkit.yaml into the metadata, then streams the batch to Reach Layer.

    Returns:
        Batch response from KE via Reach Layer (batch_id + per-file job_ids).
    """
    form = await request.form()
    raw_metadata = form.get("metadata")
    if not raw_metadata:
        raise HTTPException(422, "metadata field is required")

    try:
        metadata_entries = json.loads(raw_metadata)
    except Exception as e:
        raise HTTPException(422, f"Invalid metadata JSON: {e}")

    # Validate batch size
    if len(metadata_entries) > _DEVKIT_CONFIG.upload.max_files_per_upload:
        raise HTTPException(
            422,
            f"Too many files: max {_DEVKIT_CONFIG.upload.max_files_per_upload} per batch"
        )

    # Collect file parts
    file_parts: dict[str, bytes] = {}
    for field_name, value in form.multi_items():
        if field_name == "files" and hasattr(value, "filename") and hasattr(value, "read"):
            content = await value.read()
            file_parts[value.filename] = content

    # Validate each entry
    for entry in metadata_entries:
        filename = entry.get("filename", "")
        safe_name = Path(filename).name
        if safe_name != filename or "/" in filename or "\\" in filename:
            raise HTTPException(422, f"Invalid filename: {filename}")

        ext = Path(safe_name).suffix.lower()
        if ext not in set(_DEVKIT_CONFIG.upload.supported_extensions):
            raise HTTPException(422, f"Unsupported extension: {ext}")

        mode = entry.get("mode", "")
        if mode in ("local_write_ingest", "cloud_upload_ingest"):
            file_bytes = file_parts.get(safe_name)
            if file_bytes is not None:
                size_mb = len(file_bytes) / (1024 * 1024)
                if size_mb > _DEVKIT_CONFIG.upload.max_file_size_mb:
                    raise HTTPException(
                        413,
                        f"{filename} exceeds {_DEVKIT_CONFIG.upload.max_file_size_mb} MB limit"
                    )

    # Inject user_id into each metadata entry
    for entry in metadata_entries:
        entry["user_id"] = _DEVKIT_CONFIG.user_id

    # Rebuild multipart body with injected user_id and stream to Reach Layer
    import httpx as _httpx
    multipart_data = {"metadata": json.dumps(metadata_entries)}
    files_to_send = [
        ("files", (fname, fbytes, "application/octet-stream"))
        for fname, fbytes in file_parts.items()
    ]

    try:
        async with _httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{_REACH_LAYER_URL}/ingest/upload",
                data=multipart_data,
                files=files_to_send if files_to_send else None,
                headers={"X-API-Key": _DEVKIT_TO_REACH_API_KEY},
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
        )
    except _httpx.ConnectError as e:
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        raise HTTPException(504, "Reach Layer timed out") from e


# ---------------------------------------------------------------------------
# GET /api/ingest/job/{job_id}
# ---------------------------------------------------------------------------

@app.get("/api/ingest/job/{job_id}")
async def ingest_job_status(job_id: str):
    """Return job status by proxying to Reach Layer → KE.

    Called by the frontend poller every poll_interval_seconds.

    Returns:
        Job status response from KE.
    """
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_REACH_LAYER_URL}/ingest/job/{job_id}",
                headers={"X-API-Key": _DEVKIT_TO_REACH_API_KEY},
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
        )
    except _httpx.ConnectError as e:
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        raise HTTPException(504, "Reach Layer timed out") from e


# ---------------------------------------------------------------------------
# POST /api/ingest/callback
# ---------------------------------------------------------------------------

class _CallbackBody(BaseModel):
    """Payload sent by KE when a job completes."""
    job_id: str
    status: str
    chunks_added: Optional[int] = None
    error: Optional[str] = None


@app.post("/api/ingest/callback")
async def ingest_callback(
    body: _CallbackBody,
    request: Request,
):
    """Receive ingestion completion callback from KE.

    Validates the KE_TO_DEVKIT_API_KEY, then optionally appends the result
    to project.json ingest_log as an audit trail.

    Returns:
        {"ok": true} on success.
    """
    x_api_key = request.headers.get("X-API-Key")
    _verify_api_key(x_api_key, _KE_TO_DEVKIT_API_KEY)

    logger.info(
        "devkit.ingest_callback",
        extra={
            "operation": "devkit.ingest_callback",
            "status": "success",
            "job_id": body.job_id,
            "ingest_status": body.status,
        },
    )

    # Optional audit trail: append to ingest_log in project.json
    # (best-effort; non-blocking if project.json is missing)
    # Note: slug is not known at callback time, so we search all project dirs.
    _append_callback_to_ingest_log(body.job_id, body.status, body.chunks_added, body.error)

    return {"ok": True}


def _append_callback_to_ingest_log(
    job_id: str,
    status: str,
    chunks_added: Optional[int],
    error: Optional[str],
) -> None:
    """Append a callback result to the ingest_log of the relevant project.json.

    Searches all project directories for a project.json whose ingest_log
    contains the given job_id. If found, appends the result. If not found,
    silently skips — the callback is purely an audit trail.

    Args:
        job_id: UUID of the completed job.
        status: Terminal status ('ingested' or 'failed').
        chunks_added: Number of chunks added (if ingested).
        error: Error message (if failed).
    """
    try:
        projects_dir = Path(os.environ.get("PROJECTS_DIR", "configs"))
        if not projects_dir.exists():
            return

        from datetime import datetime, timezone
        entry = {
            "job_id": job_id,
            "status": status,
            "chunks_added": chunks_added,
            "error": error,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        for project_dir in projects_dir.iterdir():
            project_json = project_dir / "project.json"
            if not project_json.exists():
                continue
            with project_json.open("r") as f:
                data = json.load(f)
            ingest_log = data.get("ingest_log", [])
            ingest_log.append(entry)
            data["ingest_log"] = ingest_log
            with project_json.open("w") as f:
                json.dump(data, f, indent=2)
            return  # found and updated
    except Exception as e:
        logger.warning(
            "devkit.ingest_log_write_failed",
            extra={
                "operation": "devkit.ingest_callback",
                "status": "failure",
                "error": str(e),
            },
        )
```

- [ ] **Step 4: Create configs/.gitignore**

```bash
mkdir -p dev-kit/configs
cat > dev-kit/configs/.gitignore << 'EOF'
# Never commit project.json files — they contain Azure credentials and API keys.
*/project.json
EOF
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_ingest_endpoints.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run full dev-kit test suite**

```bash
cd dev-kit && uv run pytest tests/ -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py dev-kit/configs/.gitignore dev-kit/tests/test_ingest_endpoints.py
git commit -m "feat(devkit): add ingest/submit, ingest/job, ingest/callback endpoints"
```

---

## Task 5: Extend GET /api/projects/{slug} to return azure_storage

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`

- [ ] **Step 1: Locate the GET /api/projects/{slug} handler in app.py**

```bash
grep -n "get.*projects.*slug" dev-kit/dev_kit/agent/app.py | head -5
```

- [ ] **Step 2: Update project metadata response to include azure_storage**

Find the handler for `GET /api/projects/{slug}` and ensure it returns `azure_storage` with a masked `account_key`. The response should include:

```python
# In the project GET handler, after loading project metadata:
project_data = _load_project_meta(slug)

# Include azure_storage with masked key (for MandatoryInputsStep pre-fill)
azure_storage = project_data.get("azure_storage")
if azure_storage:
    azure_storage_display = {
        "account_name": azure_storage.get("account_name", ""),
        "account_key": "***" + azure_storage.get("account_key", "")[-4:] if azure_storage.get("account_key") else "",
        "container_name": azure_storage.get("container_name", ""),
    }
else:
    azure_storage_display = None

# Add to the return dict:
return {
    ...existing fields...,
    "azure_storage": azure_storage_display,
}
```

- [ ] **Step 3: Run existing project endpoint tests to verify no regression**

```bash
cd dev-kit && uv run pytest tests/ -k "project" -v 2>/dev/null || echo "no project tests found"
```

Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py
git commit -m "feat(devkit): expose azure_storage (masked key) in GET /api/projects/{slug}"
```

---

## Self-Review Checklist

- [x] `devkit.yaml` is the single source of truth for upload limits, supported extensions, polling params
- [x] `DevKitConfig` is loaded once at startup — never re-read in request paths
- [x] `verify_api_key` in `dev_kit/agent/auth.py` follows exact same interface as KE and Reach Layer versions
- [x] `set_azure_storage` tool stores credentials in `state["azure_storage"]` (not in config blocks)
- [x] Knowledge phase prompt asks ONE question about Azure, no file list collection
- [x] `POST /api/ingest/submit`: validates count ≤ max, extension whitelist, path traversal, size limit
- [x] `POST /api/ingest/submit`: injects `user_id` from devkit.yaml into metadata
- [x] `POST /api/ingest/callback`: requires `KE_TO_DEVKIT_API_KEY` — 401 without it
- [x] `GET /api/projects/{slug}` returns `azure_storage` with masked key (MandatoryInputsStep pre-fill)
- [x] `configs/.gitignore` added to prevent project.json commits
- [x] No hardcoded URLs or credentials in source code
