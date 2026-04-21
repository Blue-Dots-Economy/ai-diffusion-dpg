# Dev-Kit OpenAPI Spec Input + Response Transformation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators three ways to input an OpenAPI spec (URL fetch, file upload, paste) and — after each REST API tool is added — guide them to define which response fields the LLM should see, writing that mapping into the Action Gateway config.

**Architecture:**

- **URL fetch**: new `fetch_openapi_spec_from_url` tool in `tools.py` — httpx GET, YAML/JSON parse, same candidate output as `parse_openapi_spec`.
- **File upload**: frontend-only — Chat.jsx reads file text client-side, injects spec content as a chat message so Claude can call the existing `parse_openapi_spec` tool with no new backend endpoint.
- **Response transformation**: new `set_response_transformation` tool writes `response.field_mapping` (JSONPath → target name per field) into the accumulator for any previously-added REST API tool; new `update_tool_response_mapping` method in `accumulator.py` handles the mutation.
- The tools-phase prompt is updated throughout each task to guide Claude through all three input methods and the post-tool response mapping step.

**Tech Stack:** Python 3.11, FastAPI, httpx, pytest, React 18, Vitest + RTL.

---

## File Map

| Path | Action | Responsibility |
|------|--------|---------------|
| `dev-kit/dev_kit/agent/tools.py` | Modify | Add `fetch_openapi_spec_from_url` and `set_response_transformation` tools + handlers |
| `dev-kit/dev_kit/agent/accumulator.py` | Modify | Add `update_tool_response_mapping` method |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Modify | Update tools phase prompt for URL input, file upload, and response mapping |
| `dev-kit/dev_kit/schemas/action_gateway.yaml` | Modify | Add `field_mapping` section to `response` block |
| `dev-kit/frontend/src/components/Chat.jsx` | Modify | Add file attachment button + FileReader injection |
| `dev-kit/frontend/src/api.js` | No change | (fetch/spec upload is client-side only) |
| `dev-kit/tests/test_tools_openapi.py` | **Create** | Tests for URL fetch and response transform tools |
| `dev-kit/frontend/src/components/__tests__/Chat.test.jsx` | **Create** | Tests for file attachment injection |

---

## Task 1: fetch_openapi_spec_from_url tool

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Create: `dev-kit/tests/test_tools_openapi.py`

**What it does:** Takes a URL, fetches the YAML or JSON with httpx (timeout 15s), validates it is a valid OpenAPI 3.x dict (has `openapi` key starting with `"3"` and `paths` key), calls the existing `parse_openapi_spec` parser, and returns the same JSON array of candidates that `_handle_parse_openapi_spec` returns.

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_tools_openapi.py`:

```python
"""
dev-kit/tests/test_tools_openapi.py

Tests for fetch_openapi_spec_from_url and set_response_transformation tool handlers.
"""
from __future__ import annotations

import json
import os
import pytest
import respx
import httpx as _httpx
from unittest.mock import MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler


@pytest.fixture
def handler():
    acc = ConfigAccumulator()
    state = {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    return ToolHandler(acc, state)


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/search": {
            "post": {
                "summary": "Search for jobs",
                "parameters": [],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            }
                        }
                    }
                },
            }
        }
    },
}


# ---------------------------------------------------------------------------
# fetch_openapi_spec_from_url — normal
# ---------------------------------------------------------------------------

class TestFetchOpenApiSpecFromUrl:
    @respx.mock
    def test_fetches_json_spec_and_returns_candidates(self, handler):
        import yaml as _yaml
        respx.get("https://api.example.com/openapi.json").mock(
            return_value=_httpx.Response(200, json=MINIMAL_SPEC)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/openapi.json"})
        candidates = json.loads(result)
        assert isinstance(candidates, list)
        assert len(candidates) == 1
        assert candidates[0]["path"] == "/search"
        assert candidates[0]["method"] == "POST"
        assert candidates[0]["base_url"] == "https://api.example.com"

    @respx.mock
    def test_fetches_yaml_spec(self, handler):
        import yaml as _yaml
        spec_yaml = _yaml.dump(MINIMAL_SPEC)
        respx.get("https://api.example.com/openapi.yaml").mock(
            return_value=_httpx.Response(200, content=spec_yaml.encode(), headers={"content-type": "text/yaml"})
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/openapi.yaml"})
        candidates = json.loads(result)
        assert candidates[0]["path"] == "/search"

    @respx.mock
    def test_returns_error_on_http_failure(self, handler):
        respx.get("https://bad.example.com/spec.json").mock(
            return_value=_httpx.Response(404)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://bad.example.com/spec.json"})
        assert result.startswith("ERROR")

    @respx.mock
    def test_returns_error_when_missing_paths_key(self, handler):
        bad_spec = {"openapi": "3.0.0", "info": {"title": "Bad"}}
        respx.get("https://api.example.com/bad.json").mock(
            return_value=_httpx.Response(200, json=bad_spec)
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://api.example.com/bad.json"})
        assert result.startswith("ERROR")

    @respx.mock
    def test_returns_error_on_connect_failure(self, handler):
        respx.get("https://unreachable.example.com/spec.json").mock(
            side_effect=_httpx.ConnectError("refused")
        )
        result = handler.dispatch("fetch_openapi_spec_from_url", {"url": "https://unreachable.example.com/spec.json"})
        assert result.startswith("ERROR")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/test_tools_openapi.py::TestFetchOpenApiSpecFromUrl -v
```

Expected: `FAILED` — `fetch_openapi_spec_from_url` not in dispatch table.

- [ ] **Step 3: Add tool definition to TOOL_DEFINITIONS in tools.py**

In `dev-kit/dev_kit/agent/tools.py`, after the `parse_openapi_spec` entry (around line 198) add:

```python
    {
        "name": "fetch_openapi_spec_from_url",
        "description": (
            "Fetch an OpenAPI 3.0/3.1 spec from a URL and return candidate tool definitions. "
            "Use this when the user pastes a URL to their API spec. "
            "Supports JSON and YAML. Returns the same candidate list as parse_openapi_spec."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the OpenAPI spec file (JSON or YAML), e.g. https://api.example.com/openapi.yaml",
                },
            },
            "required": ["url"],
        },
    },
```

- [ ] **Step 4: Add handler method to ToolHandler**

In `dev-kit/dev_kit/agent/tools.py`, add `"fetch_openapi_spec_from_url": self._handle_fetch_openapi_spec_from_url` to the `handlers` dict inside `dispatch()`, then add the method after `_handle_parse_openapi_spec`:

```python
    def _handle_fetch_openapi_spec_from_url(self, inputs: dict) -> str:
        """Fetch an OpenAPI spec from a URL and return candidate tool definitions as JSON.

        Downloads the spec via httpx (JSON or YAML), validates it is an OpenAPI 3.x
        document, parses it, and returns the same candidate array as
        _handle_parse_openapi_spec.

        Args:
            inputs: Dict with 'url' key containing the spec URL.

        Returns:
            JSON array of candidate tool dicts, or an ERROR string on failure.
        """
        import json
        import yaml as _yaml
        import httpx
        from dev_kit.agent.openapi_parser import parse_openapi_spec
        import logging as _log
        import time

        url = inputs.get("url", "").strip()
        if not url:
            return "ERROR: url is required"

        start = time.time()
        try:
            response = httpx.get(url, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"ERROR: HTTP {exc.response.status_code} fetching {url}"
        except httpx.HTTPError as exc:
            return f"ERROR: could not fetch spec from {url} — {exc}"

        content = response.text
        try:
            try:
                spec = json.loads(content)
            except json.JSONDecodeError:
                spec = _yaml.safe_load(content)
            if not isinstance(spec, dict):
                return "ERROR: fetched content is not a JSON/YAML object"
        except Exception as exc:
            return f"ERROR: could not parse fetched content — {exc}"

        try:
            tools = parse_openapi_spec(spec)
        except ValueError as exc:
            return f"ERROR: {exc}"

        candidates = [
            {
                "suggested_id": t.suggested_id,
                "path": t.path,
                "method": t.method,
                "description": t.description,
                "base_url": t.base_url,
                "param_names": [p.name for p in t.params],
                "auth_type": t.auth_type,
                "auth_header": t.auth_header,
            }
            for t in tools
        ]
        logger.info(
            "fetch_openapi_spec_from_url",
            extra={
                "operation": "tools.fetch_openapi_spec_from_url",
                "status": "success",
                "url": url,
                "endpoint_count": len(candidates),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return json.dumps(candidates, ensure_ascii=False, indent=2)
```

- [ ] **Step 5: Update the tools phase prompt to mention URL input**

In `dev-kit/dev_kit/agent/prompts/phases.py`, find the `if phase == "tools":` block (around line 194). Replace the Path A block with:

```python
            "**Path A — User has an OpenAPI spec (3 ways to provide it):**\n"
            "  1a. **URL** — User provides a URL to their spec file. Call `fetch_openapi_spec_from_url(url)` directly.\n"
            "  1b. **File upload** — User says they've uploaded a file and includes the spec content in their message. Call `parse_openapi_spec(spec_json)` with the full spec text.\n"
            "  1c. **Paste** — User pastes the YAML or JSON directly. Call `parse_openapi_spec(spec_json)` with the pasted text.\n"
            "  2. Present the returned candidates and confirm which ones to add.\n"
            "  3. Call `add_rest_api_tool` once per confirmed tool.\n"
            "  4. After adding, ask for response field mapping — see **Response Transformation** below.\n\n"
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/test_tools_openapi.py::TestFetchOpenApiSpecFromUrl -v
```

Expected: all 5 tests PASS.

- [ ] **Step 7: Run full dev-kit test suite**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: all existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/dev_kit/agent/prompts/phases.py dev-kit/tests/test_tools_openapi.py
git commit -m "feat(devkit): add fetch_openapi_spec_from_url tool for URL-based spec input"
```

---

## Task 2: File attachment button in Chat UI

**Files:**
- Modify: `dev-kit/frontend/src/components/Chat.jsx`
- Create: `dev-kit/frontend/src/components/__tests__/Chat.test.jsx`

**Approach:** A paperclip button next to the textarea opens a file picker (`accept=".yaml,.yml,.json"`). The selected file is read client-side with the browser's `FileReader` API. On load, a user message is sent to the chat:

```
[Attached: {filename}]

{file_content}
```

Claude receives this message and calls `parse_openapi_spec` with the pasted spec text. No backend endpoint needed. The file size is limited to 500 KB client-side to prevent oversized messages.

- [ ] **Step 1: Write failing tests**

Create `dev-kit/frontend/src/components/__tests__/Chat.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock the api module
vi.mock('../../api', () => ({
  api: {
    getHistory: vi.fn().mockResolvedValue([]),
    getCheckpoints: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getConfigs: vi.fn().mockResolvedValue([]),
    chat: vi.fn().mockResolvedValue({ reply: 'ok', phase: 'tools', graph: null, checkpoint_created: null }),
  },
}))

// ThemeContext mock
vi.mock('../../ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggle: vi.fn() }),
}))

// Sub-component mocks
vi.mock('../PhaseBar', () => ({ default: () => null }))
vi.mock('../FlowGraph', () => ({ default: () => null }))
vi.mock('../YamlPanel', () => ({ default: () => null }))
vi.mock('../DiffModal', () => ({ default: () => null }))

import Chat from '../Chat'
import { api } from '../../api'

describe('Chat — file attachment', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders a file attachment button', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByTitle(/attach spec file/i)).toBeInTheDocument()
    })
  })

  it('sends a chat message containing file content when a file is attached', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    expect(fileInput).toBeTruthy()

    const specContent = 'openapi: "3.0.0"\npaths:\n  /test:\n    get:\n      summary: Test'
    const file = new File([specContent], 'api.yaml', { type: 'text/yaml' })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    await waitFor(() => {
      expect(api.chat).toHaveBeenCalledWith(
        'test-project',
        expect.stringContaining('[Attached: api.yaml]')
      )
      expect(api.chat).toHaveBeenCalledWith(
        'test-project',
        expect.stringContaining(specContent)
      )
    })
  })

  it('does not send a message if file exceeds 500 KB', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    const bigContent = 'x'.repeat(600 * 1024)  // 600 KB
    const file = new File([bigContent], 'big.yaml', { type: 'text/yaml' })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    // No message should be sent
    expect(api.chat).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/frontend && npx vitest run src/components/__tests__/Chat.test.jsx 2>&1 | tail -20
```

Expected: FAIL — no attachment button in Chat.

- [ ] **Step 3: Add file attachment to Chat.jsx**

In `dev-kit/frontend/src/components/Chat.jsx`:

**3a. Add `attachFile` handler function** (before the `return` statement, after the `toggleYaml` function):

```jsx
  async function attachFile(e) {
    const file = e.target.files?.[0]
    if (!file) return

    // Reset the input so the same file can be re-selected
    e.target.value = ''

    const MAX_BYTES = 500 * 1024  // 500 KB
    if (file.size > MAX_BYTES) {
      alert(`File "${file.name}" is too large (${(file.size / 1024).toFixed(0)} KB). Maximum is 500 KB.`)
      return
    }

    const reader = new FileReader()
    reader.onload = async (ev) => {
      const content = ev.target.result
      const message = `[Attached: ${file.name}]\n\n${content}`
      setMessages(m => [...m, { role: 'user', text: message }])
      setLoading(true)
      try {
        const res = await api.chat(slug, message)
        if (res.reply) {
          setMessages(m => [...m, { role: 'assistant', text: res.reply }])
        }
        setPhase(res.phase)
        if (res.graph) setGraph(res.graph)
        if (res.checkpoint_created) {
          api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
        }
        api.getConfigs(slug).then(setConfigs).catch(() => {})
      } catch (err) {
        setMessages(m => [...m, { role: 'error', text: `Error: ${err.message}` }])
      } finally {
        setLoading(false)
      }
    }
    reader.readAsText(file)
  }
```

**3b. Add hidden file input and paperclip button** to the `<form>` in the JSX (inside the form, before the `<textarea>`). The form currently starts at around line 223. Add these two elements immediately before the `<textarea>`:

```jsx
            {/* Hidden file input for spec upload — attach button triggers this */}
            <input
              type="file"
              accept=".yaml,.yml,.json"
              className="hidden"
              id="spec-file-input"
              onChange={attachFile}
              disabled={loading}
            />
            <label
              htmlFor="spec-file-input"
              title="Attach spec file (.yaml, .yml, .json)"
              className={`flex items-center justify-center w-9 h-9 rounded-xl cursor-pointer transition-colors self-end shrink-0 ${
                loading ? 'text-gray-600 cursor-not-allowed' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
              }`}
            >
              📎
            </label>
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/frontend && npx vitest run src/components/__tests__/Chat.test.jsx 2>&1 | tail -20
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run full frontend test suite**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/frontend && npx vitest run 2>&1 | tail -10
```

All existing tests must still pass.

- [ ] **Step 6: Update phase prompt to mention file upload**

In `dev-kit/dev_kit/agent/prompts/phases.py` tools phase, the Path A section added in Task 1 already mentions file upload. No additional change needed.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/frontend/src/components/Chat.jsx dev-kit/frontend/src/components/__tests__/Chat.test.jsx
git commit -m "feat(devkit-frontend): add OpenAPI spec file attachment button to Chat"
```

---

## Task 3: Response transformation tool

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Modify: `dev-kit/dev_kit/schemas/action_gateway.yaml`
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Modify: `dev-kit/tests/test_tools_openapi.py`

**What it does:** After `add_rest_api_tool` adds a tool, Claude asks the user which response fields are needed by the LLM, then calls `set_response_transformation(tool_id, fields)`. Each field has a `source` (JSONPath in the API response, e.g. `results[*].title`), a `target` (name the LLM sees), a `type`, and an optional `description`. This writes `response.field_mapping` into the tool's config in the accumulator.

### 3a: Accumulator method

- [ ] **Step 1: Write failing test for accumulator**

Append to `dev-kit/tests/test_tools_openapi.py`:

```python
# ---------------------------------------------------------------------------
# set_response_transformation
# ---------------------------------------------------------------------------

class TestSetResponseTransformation:
    def _add_sample_tool(self, handler):
        """Helper: add a REST API tool so transformation tests have a target."""
        handler.dispatch("add_rest_api_tool", {
            "id": "job_search",
            "category": "read",
            "description": "Search for job listings",
            "base_url": "https://api.example.com",
            "auth_type": "api_key",
            "auth_header": "X-API-Key",
            "auth_secret_env": "JOB_API_KEY",
            "endpoints": [{"name": "search", "method": "POST", "path": "/search", "params": []}],
        })

    def test_sets_field_mapping_on_tool(self, handler):
        self._add_sample_tool(handler)
        fields = [
            {"source": "results[*].title", "target": "job_title", "type": "string", "description": "Job title"},
            {"source": "results[*].employer_name", "target": "company", "type": "string"},
        ]
        result = handler.dispatch("set_response_transformation", {"tool_id": "job_search", "fields": fields})
        assert "job_search" in result
        # Verify it was written to accumulator
        tools = handler._acc.get_action_gateway_tools()
        job_tool = next(t for t in tools if t["id"] == "job_search")
        mapping = job_tool["response"]["field_mapping"]
        assert len(mapping) == 2
        assert mapping[0]["source"] == "results[*].title"
        assert mapping[0]["target"] == "job_title"
        assert mapping[1]["target"] == "company"

    def test_returns_error_for_nonexistent_tool(self, handler):
        result = handler.dispatch("set_response_transformation", {
            "tool_id": "nonexistent",
            "fields": [{"source": "data.id", "target": "id", "type": "string"}],
        })
        assert result.startswith("ERROR")

    def test_replaces_existing_mapping(self, handler):
        """Calling set_response_transformation twice replaces the previous mapping."""
        self._add_sample_tool(handler)
        handler.dispatch("set_response_transformation", {
            "tool_id": "job_search",
            "fields": [{"source": "old.path", "target": "old_field", "type": "string"}],
        })
        handler.dispatch("set_response_transformation", {
            "tool_id": "job_search",
            "fields": [{"source": "new.path", "target": "new_field", "type": "string"}],
        })
        tools = handler._acc.get_action_gateway_tools()
        job_tool = next(t for t in tools if t["id"] == "job_search")
        mapping = job_tool["response"]["field_mapping"]
        assert len(mapping) == 1
        assert mapping[0]["target"] == "new_field"

    def test_empty_fields_list_is_accepted(self, handler):
        """Empty field list clears the mapping without error."""
        self._add_sample_tool(handler)
        result = handler.dispatch("set_response_transformation", {
            "tool_id": "job_search",
            "fields": [],
        })
        assert "job_search" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/test_tools_openapi.py::TestSetResponseTransformation -v
```

Expected: FAIL — `set_response_transformation` not in dispatch table.

- [ ] **Step 3: Add `update_tool_response_mapping` to ConfigAccumulator**

In `dev-kit/dev_kit/agent/accumulator.py`, after the `get_action_gateway_tools` method, add:

```python
    def update_tool_response_mapping(self, tool_id: str, fields: list[dict]) -> None:
        """Set the response field_mapping for an existing action_gateway tool.

        Replaces any existing field_mapping with the provided list. An empty
        list clears the mapping.

        Args:
            tool_id: ID of the REST API tool to update.
            fields: List of field mapping dicts, each with at minimum
                    'source' (JSONPath) and 'target' (name for the LLM).

        Raises:
            ValueError: If no tool with the given id exists in action_gateway.
        """
        tools: list[dict] = self._data["action_gateway"].get("tools", [])
        for tool in tools:
            if tool.get("id") == tool_id:
                tool.setdefault("response", {})["field_mapping"] = deepcopy(fields)
                return
        raise ValueError(f"Tool {tool_id!r} not found in action_gateway — call add_rest_api_tool first")
```

- [ ] **Step 4: Add `set_response_transformation` tool definition to TOOL_DEFINITIONS**

In `dev-kit/dev_kit/agent/tools.py`, after the `add_rest_api_tool` entry, add:

```python
    {
        "name": "set_response_transformation",
        "description": (
            "Set the response field mapping for a REST API tool. "
            "Call this after add_rest_api_tool, once the user tells you which fields from the API response the LLM should see. "
            "Each field maps a JSONPath in the raw response (e.g. 'results[*].title') to a clean target name the LLM works with. "
            "Calling this again for the same tool replaces the previous mapping."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": "ID of the REST API tool to configure (must already exist via add_rest_api_tool)",
                },
                "fields": {
                    "type": "array",
                    "description": "Response fields to extract and expose to the LLM",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "JSONPath from the response root, e.g. 'results[*].title' or 'data.employer_name'",
                            },
                            "target": {
                                "type": "string",
                                "description": "Field name the LLM sees in the extracted result, e.g. 'job_title'",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["string", "integer", "number", "boolean", "array", "object"],
                                "default": "string",
                            },
                            "description": {
                                "type": "string",
                                "description": "Optional human-readable description of this field",
                            },
                        },
                        "required": ["source", "target"],
                    },
                },
            },
            "required": ["tool_id", "fields"],
        },
    },
```

- [ ] **Step 5: Add handler method and dispatch entry**

In `dev-kit/dev_kit/agent/tools.py`:

**5a. Add to `handlers` dict inside `dispatch()`:**
```python
            "set_response_transformation": self._handle_set_response_transformation,
```

**5b. Add handler method after `_handle_add_rest_api_tool`:**
```python
    def _handle_set_response_transformation(self, inputs: dict) -> str:
        """Write response field_mapping for a REST API tool into the accumulator.

        Args:
            inputs: Dict with 'tool_id' (str) and 'fields' (list of dicts with
                    'source', 'target', optional 'type' and 'description').

        Returns:
            Confirmation string, or an ERROR string if the tool does not exist.
        """
        import json

        tool_id = inputs.get("tool_id", "")
        fields = inputs.get("fields", [])

        try:
            self._acc.update_tool_response_mapping(tool_id, fields)
        except ValueError as exc:
            return f"ERROR: {exc}"

        return (
            f"Response mapping set for tool '{tool_id}': "
            f"{len(fields)} field(s) — "
            + ", ".join(f["target"] for f in fields[:5])
            + ("…" if len(fields) > 5 else "")
        )
```

- [ ] **Step 6: Update action_gateway.yaml template**

In `dev-kit/dev_kit/schemas/action_gateway.yaml`, replace:
```yaml
    response:
      max_size_chars: 4000
```

with:
```yaml
    response:
      max_size_chars: 4000
      field_mapping:         # optional — if set, only these fields are sent to the LLM
        - source: ""         # JSONPath from response root, e.g. 'results[*].title' or 'data.name'
          target: ""         # field name the LLM sees, e.g. 'job_title'
          type: string       # string | integer | number | boolean | array | object
          description: ""    # optional human-readable description
```

- [ ] **Step 7: Update phase prompt to include response transformation step**

In `dev-kit/dev_kit/agent/prompts/phases.py`, find the `if phase == "tools":` block. The current "After each path" section says:
```
Ask: 'Are there any other tools to add?'
```

Replace the entire "**After each path — ALWAYS do this:**" block with:

```python
            "**After adding each REST API tool — ALWAYS do this:**\n"
            "  1. Ask: 'Can you share a sample JSON response from this endpoint? Or describe the key fields you need the AI to work with.'\n"
            "  2. Based on the user's answer, identify the fields they need and their JSONPaths in the response structure.\n"
            "     - For a flat response like {\"title\": \"...\", \"company\": \"...\"}, the source is just the key name: 'title', 'company'\n"
            "     - For nested/array responses like {\"results\": [{\"title\": \"...\"}]}, use JSONPath: 'results[*].title'\n"
            "  3. Confirm the field list with the user: 'I'll extract these fields: title → job_title, company → employer'. Does that look right?\n"
            "  4. Call `set_response_transformation(tool_id=<tool_id>, fields=[...])` with the confirmed fields.\n"
            "  5. Then ask: 'Are there any other tools to add? (OpenAPI spec URL, file attachment, MCP server, or describe manually)'\n"
            "     - If yes: repeat the appropriate path above.\n"
            "     - If no: proceed to completion.\n\n"
            "**If the user does not want response transformation (no filtering needed):**\n"
            "  Skip step 4 — do NOT call set_response_transformation. The full response (up to max_size_chars) is passed to the LLM.\n\n"
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/test_tools_openapi.py -v
```

Expected: all tests PASS (both `TestFetchOpenApiSpecFromUrl` and `TestSetResponseTransformation`).

- [ ] **Step 9: Run full dev-kit test suite**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/ -q --tb=short 2>&1 | tail -10
```

All tests must pass.

- [ ] **Step 10: Commit**

```bash
git add dev-kit/dev_kit/agent/tools.py dev-kit/dev_kit/agent/accumulator.py dev-kit/dev_kit/schemas/action_gateway.yaml dev-kit/dev_kit/agent/prompts/phases.py dev-kit/tests/test_tools_openapi.py
git commit -m "feat(devkit): add set_response_transformation tool and field_mapping to action_gateway config"
```

---

## Self-Review Checklist

**Spec coverage:**

| Requirement | Task | Status |
|-------------|------|--------|
| URL fetch for OpenAPI spec | Task 1 | ✅ `fetch_openapi_spec_from_url` tool |
| File upload in chat | Task 2 | ✅ file attachment button, FileReader, auto-sends to chat |
| Paste in chat (existing) | — | ✅ already works via `parse_openapi_spec` |
| All 3 paths lead to same parsing flow | Task 1 phase prompt | ✅ mentioned in Path A variants |
| Validate spec on input | Tasks 1+2 | ✅ `parse_openapi_spec` raises ValueError on invalid spec; URL handler validates `openapi` + `paths` keys |
| Ask user which response fields to extract | Task 3 phase prompt | ✅ "After adding each REST API tool" section |
| User provides sample response or field descriptions | Task 3 phase prompt | ✅ Step 1 of response mapping flow |
| Build field mapping / transformation | Task 3 | ✅ `set_response_transformation` tool + accumulator method |
| Write to config | Task 3 | ✅ `update_tool_response_mapping` writes to `response.field_mapping` |
| Schema template updated | Task 3 | ✅ `action_gateway.yaml` has `field_mapping` section |
| File size guard (avoid oversized messages) | Task 2 | ✅ 500 KB client-side limit with alert |

**Placeholder scan:** No TBDs, no "similar to" references, all code blocks complete.

**Type consistency:**
- `update_tool_response_mapping(tool_id: str, fields: list[dict])` — matches call in `_handle_set_response_transformation`
- `tool_id` (not `id`) — consistent across tool definition, handler input, and accumulator method
- `field_mapping` key — consistent across accumulator write, YAML template, and test assertions
- `fetch_openapi_spec_from_url` name — consistent across TOOL_DEFINITIONS, dispatch dict, and handler method name
