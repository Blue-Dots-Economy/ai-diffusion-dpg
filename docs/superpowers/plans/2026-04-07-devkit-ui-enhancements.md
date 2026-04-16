# Dev-Kit UI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the dev-kit React UI with a live YAML preview panel in Chat, working YAML edit/save/cancel, config health dashboard, export-as-ZIP, diff view on checkpoint restore, clipboard copy, schema field guide, and delete-project support.

**Architecture:** All new UI is added to the existing React SPA in `dev-kit/frontend/src/`. Three new backend endpoints are added to `app.py` (export ZIP, checkpoint preview, schema descriptions). Two new React components are created (`YamlPanel.jsx`, `DiffModal.jsx`). Existing components are enhanced in-place.

**Tech Stack:** React 18, Tailwind CSS, CodeMirror 6 (already installed), FastAPI, Python `zipfile`/`yaml`. No new npm packages required.

---

## Bug fix noted during planning

`Dashboard.jsx` lists `learning_layer` in its `BLOCKS` array, but the backend `BLOCKS` list (accumulator.py) has `observability_layer`. Fix this in Task 7.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `dev-kit/dev_kit/agent/app.py` | Modify | 3 new endpoints: export ZIP, checkpoint preview, schema descriptions |
| `dev-kit/frontend/src/api.js` | Modify | 3 new API methods: `exportConfigs`, `getCheckpointPreview`, `getSchemaDescriptions` |
| `dev-kit/frontend/src/components/YamlPanel.jsx` | **Create** | Live YAML side-panel: block tabs, CodeMirror, edit/save/cancel, copy, field guide |
| `dev-kit/frontend/src/components/DiffModal.jsx` | **Create** | Before/after diff modal for checkpoint restore confirmation |
| `dev-kit/frontend/src/components/Chat.jsx` | Modify | Add "YAML" toggle, configs state, refresh after agent turn, diff modal integration |
| `dev-kit/frontend/src/components/ConfigEditor.jsx` | Modify | Fix cancel (store original), auto-readonly after save, copy button, field guide |
| `dev-kit/frontend/src/components/Dashboard.jsx` | Modify | Fix `learning_layer` → `observability_layer`, health banner, export button, polish |
| `dev-kit/frontend/src/components/ProjectList.jsx` | Modify | Delete project button with confirmation, UI polish |

---

## Task 1: Backend — Three New Endpoints

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`
- Test: `dev-kit/tests/test_app_endpoints.py` (create)

### 1a — Export ZIP endpoint

- [ ] **Step 1: Write the failing test**

```python
# dev-kit/tests/test_app_endpoints.py
import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from dev_kit.agent.app import app

client = TestClient(app)

def test_export_configs_returns_zip(tmp_path, monkeypatch):
    """Export endpoint returns a valid ZIP containing YAML files."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)

    # Create a minimal project
    slug = "test-export"
    project_path = tmp_path / slug
    project_path.mkdir()
    (project_path / "agent_core.yaml").write_text("server:\n  host: localhost\n")
    (project_path / "_meta").mkdir()
    (project_path / "_meta" / "project.json").write_text(
        '{"name": "Test", "description": "", "created_at": "2026-01-01T00:00:00Z"}'
    )

    res = client.get(f"/api/projects/{slug}/configs/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = z.namelist()
    assert "agent_core.yaml" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_export_configs_returns_zip -v
```

Expected: FAIL — `404` or route not found.

- [ ] **Step 3: Add the export endpoint to `app.py`**

Add after the `validate_all_configs` endpoint (around line 390). Add `import io, zipfile` at the top with existing imports, and add `StreamingResponse` to the fastapi.responses import:

```python
# at top of file, add to existing imports:
import io
import zipfile
from fastapi.responses import FileResponse, StreamingResponse
```

```python
@app.get("/api/projects/{slug}/configs/export")
def export_configs(slug: str):
    """Return all config YAML files for a project as a ZIP archive."""
    project_path = _get_project_path(slug)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for block in BLOCKS:
            config_file = project_path / f"{block}.yaml"
            content = config_file.read_text() if config_file.exists() else f"# {block}.yaml — not yet configured\n"
            zf.writestr(f"{block}.yaml", content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={slug}-configs.zip"},
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_export_configs_returns_zip -v
```

Expected: PASS.

### 1b — Checkpoint preview endpoint

- [ ] **Step 5: Write the failing test**

```python
# append to dev-kit/tests/test_app_endpoints.py

def test_checkpoint_preview_returns_configs(tmp_path, monkeypatch):
    """Preview endpoint returns config content as it would be after restore."""
    import json
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)

    slug = "test-preview"
    project_path = tmp_path / slug
    project_path.mkdir()
    (project_path / "_meta").mkdir()
    (project_path / "_meta" / "project.json").write_text(
        '{"name": "Test", "description": "", "created_at": "2026-01-01T00:00:00Z"}'
    )

    # Create a checkpoint
    phase = "01_overview"
    cp_dir = project_path / "_meta" / "checkpoints" / phase
    cp_dir.mkdir(parents=True)
    acc_data = {
        "data": {"agent_core": {"server": {"host": "0.0.0.0"}}, **{b: {} for b in ["knowledge_engine", "memory_layer", "trust_layer", "action_gateway", "reach_layer", "observability_layer"]}},
        "statuses": {"agent_core": "complete", **{b: "pending" for b in ["knowledge_engine", "memory_layer", "trust_layer", "action_gateway", "reach_layer", "observability_layer"]}},
    }
    (cp_dir / "accumulator.json").write_text(json.dumps(acc_data))
    (cp_dir / "summary.txt").write_text("Checkpoint summary")
    (cp_dir / "timestamp.json").write_text('{"created_at": "2026-01-01T00:00:00Z"}')

    res = client.get(f"/api/projects/{slug}/checkpoints/{phase}/preview")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    agent_core_entry = next(e for e in data if e["block"] == "agent_core")
    assert agent_core_entry["status"] == "complete"
    assert "host" in agent_core_entry["content"]
```

- [ ] **Step 6: Run test to verify it fails**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_checkpoint_preview_returns_configs -v
```

Expected: FAIL — 404.

- [ ] **Step 7: Add the checkpoint preview endpoint to `app.py`**

Add directly after the `restore_checkpoint_route` endpoint (around line 305):

```python
@app.get("/api/projects/{slug}/checkpoints/{phase}/preview")
def preview_checkpoint(slug: str, phase: str) -> list[dict]:
    """Return what configs would look like after restoring a checkpoint, without restoring."""
    project_path = _get_project_path(slug)
    cp_dir = project_path / "_meta" / "checkpoints" / phase
    if not cp_dir.exists():
        raise HTTPException(status_code=404, detail=f"Checkpoint '{phase}' not found")
    acc = ConfigAccumulator.from_dict(
        json.loads((cp_dir / "accumulator.json").read_text())
    )
    result = []
    for block in BLOCKS:
        data = acc.get_block(block)
        content = yaml.dump(data, allow_unicode=True, default_flow_style=False) if data else ""
        result.append({
            "block": block,
            "status": acc.get_status(block).value,
            "content": content,
        })
    return result
```

- [ ] **Step 8: Run test to verify it passes**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_checkpoint_preview_returns_configs -v
```

Expected: PASS.

### 1c — Schema descriptions endpoint

- [ ] **Step 9: Write the failing test**

```python
# append to dev-kit/tests/test_app_endpoints.py

def test_schema_descriptions_returns_key_map(tmp_path, monkeypatch):
    """Schema descriptions endpoint returns a dict of key → description for a block."""
    res = client.get("/api/schemas/reach_layer")
    assert res.status_code == 200
    data = res.json()
    assert "descriptions" in data
    # reach_layer template has known keys with comments
    assert "app_name" in data["descriptions"]
    assert len(data["descriptions"]["app_name"]) > 5
```

- [ ] **Step 10: Run test to verify it fails**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_schema_descriptions_returns_key_map -v
```

Expected: FAIL — 404.

- [ ] **Step 11: Add the schema descriptions endpoint to `app.py`**

Add after the export endpoint. Add `import re` at the top if not already present:

```python
_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

@app.get("/api/schemas/{block}")
def get_schema_descriptions(block: str) -> dict:
    """Return key-level descriptions parsed from the block's YAML template file.

    Template comments take the form:
        key: ""   # description text here
    Returns a flat dict mapping leaf key names to their description strings.
    """
    template_path = _SCHEMAS_DIR / f"{block}.yaml"
    if not template_path.exists():
        return {"block": block, "descriptions": {}}
    descriptions: dict[str, str] = {}
    for line in template_path.read_text().splitlines():
        m = re.match(r'\s+(\w+):\s+"[^"]*"\s*#\s*(.+)', line)
        if m:
            descriptions[m.group(1)] = m.group(2).strip()
    return {"block": block, "descriptions": descriptions}
```

- [ ] **Step 12: Run test to verify it passes**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py::test_schema_descriptions_returns_key_map -v
```

Expected: PASS.

- [ ] **Step 13: Run all new tests together**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py -v
```

Expected: All 3 PASS.

- [ ] **Step 14: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py dev-kit/tests/test_app_endpoints.py
git commit -m "feat: add export-zip, checkpoint-preview, and schema-descriptions endpoints"
```

---

## Task 2: `api.js` — Add New API Methods

**Files:**
- Modify: `dev-kit/frontend/src/api.js`

- [ ] **Step 1: Add three new methods to the `api` object**

Open `dev-kit/frontend/src/api.js`. The current `api` object ends with `getGraph`. Add the three new methods:

```javascript
// Replace the entire api export with:
export const api = {
  // Projects
  listProjects: () => request('GET', '/projects'),
  createProject: (name, description) => request('POST', '/projects', { name, description }),
  getProject: (slug) => request('GET', `/projects/${slug}`),
  deleteProject: (slug) => request('DELETE', `/projects/${slug}`),

  // Chat
  chat: (slug, message) => request('POST', `/projects/${slug}/chat`, { message }),
  getHistory: (slug) => request('GET', `/projects/${slug}/history`),

  // Checkpoints
  getCheckpoints: (slug) => request('GET', `/projects/${slug}/checkpoints`),
  restoreCheckpoint: (slug, phase) => request('POST', `/projects/${slug}/checkpoints/${phase}/restore`),
  getCheckpointPreview: (slug, phase) => request('GET', `/projects/${slug}/checkpoints/${phase}/preview`),

  // Configs
  getConfigs: (slug) => request('GET', `/projects/${slug}/configs`),
  getConfig: (slug, block) => request('GET', `/projects/${slug}/configs/${block}`),
  updateConfig: (slug, block, content) => request('PUT', `/projects/${slug}/configs/${block}`, { content }),
  validateConfigs: (slug) => request('POST', `/projects/${slug}/configs/validate`),
  exportConfigs: (slug) => `${BASE}/projects/${slug}/configs/export`,  // returns URL (used as href)

  // Workflow graph
  getGraph: (slug) => request('GET', `/projects/${slug}/workflow/graph`),

  // Schema descriptions
  getSchemaDescriptions: (block) => request('GET', `/schemas/${block}`),
}
```

Note: `exportConfigs` returns a URL string (not a fetch), so the frontend can use it as an `<a href>` for native browser download.

- [ ] **Step 2: Manual verification**

Start the dev server (`cd dev-kit/frontend && npm run dev`) and open browser DevTools console. Run:
```javascript
// In console:
fetch('/api/schemas/reach_layer').then(r => r.json()).then(console.log)
```
Expected: `{block: "reach_layer", descriptions: {app_name: "...", ...}}`

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/api.js
git commit -m "feat: add exportConfigs, getCheckpointPreview, getSchemaDescriptions to api client"
```

---

## Task 3: New `DiffModal.jsx` — Checkpoint Diff View

**Files:**
- Create: `dev-kit/frontend/src/components/DiffModal.jsx`

This modal shows what config content will change when a checkpoint is restored. It has block tabs and a side-by-side diff view.

- [ ] **Step 1: Create `DiffModal.jsx`**

```jsx
// dev-kit/frontend/src/components/DiffModal.jsx
import React, { useState } from 'react'

const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

/**
 * Compute a simple unified diff between two multi-line strings.
 * Returns an array of {type: 'same'|'add'|'remove', text: string}.
 */
function lineDiff(oldText, newText) {
  const a = (oldText || '').split('\n')
  const b = (newText || '').split('\n')
  const result = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i >= a.length) {
      result.push({ type: 'add', text: b[j++] })
    } else if (j >= b.length) {
      result.push({ type: 'remove', text: a[i++] })
    } else if (a[i] === b[j]) {
      result.push({ type: 'same', text: a[i] })
      i++; j++
    } else {
      const aNext = a.indexOf(b[j], i + 1)
      const bNext = b.indexOf(a[i], j + 1)
      if (aNext === -1 && bNext === -1) {
        result.push({ type: 'remove', text: a[i++] })
        result.push({ type: 'add', text: b[j++] })
      } else if (aNext !== -1 && (bNext === -1 || aNext - i <= bNext - j)) {
        result.push({ type: 'add', text: b[j++] })
      } else {
        result.push({ type: 'remove', text: a[i++] })
      }
    }
  }
  return result
}

/**
 * DiffModal — shows what configs will change when a checkpoint is restored.
 *
 * Props:
 *   currentConfigs  [{block, status, content}]  — current state
 *   previewConfigs  [{block, status, content}]   — state after restore
 *   checkpointPhase  string                       — phase being restored
 *   onConfirm        () => void                   — proceed with restore
 *   onCancel         () => void                   — abort
 */
export default function DiffModal({ currentConfigs, previewConfigs, checkpointPhase, onConfirm, onCancel }) {
  const [activeBlock, setActiveBlock] = useState('agent_core')

  const current = currentConfigs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }
  const preview = previewConfigs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }
  const diff = lineDiff(current.content, preview.content)
  const hasChanges = diff.some(d => d.type !== 'same')
  const changedBlocks = new Set(
    (previewConfigs || [])
      .filter(p => {
        const c = currentConfigs.find(x => x.block === p.block)
        return c?.content !== p.content
      })
      .map(p => p.block)
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-4xl mx-4 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div>
            <h2 className="font-semibold text-base">Restore Checkpoint</h2>
            <p className="text-gray-400 text-xs mt-0.5">
              Phase: <span className="font-mono text-blue-400">{checkpointPhase}</span>
              {changedBlocks.size === 0
                ? ' — no config changes'
                : ` — ${changedBlocks.size} block${changedBlocks.size !== 1 ? 's' : ''} will change`}
            </p>
          </div>
          <button onClick={onCancel} className="text-gray-500 hover:text-white text-xl leading-none">&times;</button>
        </div>

        {/* Block tabs */}
        <div className="flex gap-1 px-4 pt-3 overflow-x-auto border-b border-gray-800 pb-0">
          {Object.keys(BLOCK_LABELS).map(block => {
            const isChanged = changedBlocks.has(block)
            const isActive = block === activeBlock
            return (
              <button
                key={block}
                onClick={() => setActiveBlock(block)}
                className={[
                  'px-3 py-2 text-xs font-medium rounded-t-lg whitespace-nowrap border-b-2 transition-colors',
                  isActive ? 'border-blue-500 text-white bg-gray-800' : 'border-transparent text-gray-400 hover:text-gray-200',
                  isChanged && !isActive ? 'text-yellow-400' : '',
                ].filter(Boolean).join(' ')}
              >
                {BLOCK_LABELS[block]}
                {isChanged && <span className="ml-1 text-yellow-400">●</span>}
              </button>
            )
          })}
        </div>

        {/* Status row */}
        <div className="flex items-center gap-4 px-6 py-2 bg-gray-950 text-xs border-b border-gray-800">
          <span className="text-gray-500">Current:</span>
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_PILL[current.status] || STATUS_PILL.pending}`}>
            {current.status}
          </span>
          <span className="text-gray-600">→</span>
          <span className="text-gray-500">After restore:</span>
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_PILL[preview.status] || STATUS_PILL.pending}`}>
            {preview.status}
          </span>
        </div>

        {/* Diff body */}
        <div className="flex-1 overflow-y-auto font-mono text-xs px-6 py-3 bg-gray-950">
          {!hasChanges ? (
            <p className="text-gray-500 text-center py-8">No changes to this block.</p>
          ) : (
            diff.map((line, i) => (
              <div
                key={i}
                className={[
                  'px-2 py-0.5 whitespace-pre leading-5',
                  line.type === 'add' ? 'bg-green-950 text-green-300' : '',
                  line.type === 'remove' ? 'bg-red-950 text-red-300' : '',
                  line.type === 'same' ? 'text-gray-500' : '',
                ].filter(Boolean).join(' ')}
              >
                {line.type === 'add' ? '+ ' : line.type === 'remove' ? '- ' : '  '}
                {line.text}
              </div>
            ))
          )}
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 rounded-xl font-medium transition-colors"
          >
            Restore Checkpoint
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Manual test**

Start dev server. Open any project in Chat, click a completed phase checkpoint. Verify modal renders with block tabs and a Cancel / Restore button. (API call will 404 until Task 5 wires it up — that's fine at this stage, just check the component mounts.)

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/DiffModal.jsx
git commit -m "feat: add DiffModal component with line-diff algorithm for checkpoint restore preview"
```

---

## Task 4: New `YamlPanel.jsx` — Live YAML Side-Panel

**Files:**
- Create: `dev-kit/frontend/src/components/YamlPanel.jsx`

This panel is shown in Chat alongside the conversation. It receives `configs` from the parent and manages its own CodeMirror instance and edit state.

- [ ] **Step 1: Create `YamlPanel.jsx`**

```jsx
// dev-kit/frontend/src/components/YamlPanel.jsx
import React, { useEffect, useRef, useState, useCallback } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const BLOCKS = ['agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer', 'action_gateway', 'reach_layer', 'observability_layer']
const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability',
}

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

/**
 * YamlPanel — live YAML viewer and editor embedded in the Chat view.
 *
 * Props:
 *   slug     string                      — project slug
 *   configs  [{block, status, content}]  — current config data (parent manages fetching)
 *   onSaved  (block, updatedConfig) => void — called after successful save
 */
export default function YamlPanel({ slug, configs, onSaved }) {
  const [activeBlock, setActiveBlock] = useState('agent_core')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)
  const [copied, setCopied] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})

  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')  // stores content before editing starts (for cancel)

  const activeConfig = configs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }

  // Rebuild CodeMirror whenever the active block or its content changes (and not editing)
  useEffect(() => {
    if (!editorRef.current) return
    if (editing) return  // don't reset mid-edit
    viewRef.current?.destroy()
    const state = EditorState.create({
      doc: activeConfig.content || '',
      extensions: [basicSetup, yaml(), oneDark, EditorView.editable.of(false)],
    })
    viewRef.current = new EditorView({ state, parent: editorRef.current })
    return () => { viewRef.current?.destroy(); viewRef.current = null }
  }, [activeBlock, activeConfig.content, editing])

  // Load schema descriptions when block changes
  useEffect(() => {
    setDescriptions({})
    api.getSchemaDescriptions(activeBlock)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [activeBlock])

  function handleTabChange(block) {
    if (editing) {
      if (!window.confirm('You have unsaved changes. Switch block and discard them?')) return
      cancelEdit()
    }
    setActiveBlock(block)
    setValidationErrors([])
    setSaveMsg(null)
  }

  function startEdit() {
    originalRef.current = viewRef.current?.state.doc.toString() || ''
    viewRef.current?.dispatch({
      effects: EditorView.editable.reconfigure(EditorView.editable.of(true)),
    })
    setEditing(true)
    setSaveMsg(null)
    setValidationErrors([])
  }

  function cancelEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: { from: 0, to: viewRef.current.state.doc.length, insert: originalRef.current },
      effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
    })
    setEditing(false)
    setValidationErrors([])
    setSaveMsg(null)
  }

  async function handleSave() {
    if (!viewRef.current) return
    setSaving(true)
    setValidationErrors([])
    setSaveMsg(null)
    const content = viewRef.current.state.doc.toString()
    try {
      const result = await api.updateConfig(slug, activeBlock, content)
      viewRef.current.dispatch({
        effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
      })
      setEditing(false)
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved successfully.')
      onSaved?.(activeBlock, { block: activeBlock, status: result.status, content })
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleCopy() {
    const content = viewRef.current?.state.doc.toString() || activeConfig.content
    await navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const blockStatus = (block) => configs.find(c => c.block === block)?.status || 'pending'

  return (
    <div className="flex flex-col h-full bg-gray-950 border-l border-gray-800">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-900 border-b border-gray-800 shrink-0">
        <span className="text-xs font-semibold text-gray-300 uppercase tracking-wide">YAML Preview</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setShowGuide(g => !g)}
            title="Toggle field guide"
            className={`text-xs px-2 py-1 rounded-lg transition-colors ${showGuide ? 'bg-blue-800 text-blue-200' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}
          >
            ? Guide
          </button>
          <button
            onClick={handleCopy}
            title="Copy to clipboard"
            className="text-xs px-2 py-1 bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 rounded-lg transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* Block tabs */}
      <div className="flex overflow-x-auto border-b border-gray-800 bg-gray-900 shrink-0">
        {BLOCKS.map(block => {
          const st = blockStatus(block)
          const isActive = block === activeBlock
          return (
            <button
              key={block}
              onClick={() => handleTabChange(block)}
              className={[
                'flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors shrink-0',
                isActive ? 'border-blue-500 text-white bg-gray-800' : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800',
              ].join(' ')}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  st === 'complete' ? 'bg-green-400' :
                  st === 'draft' ? 'bg-yellow-400' :
                  st === 'stale' ? 'bg-red-400' : 'bg-gray-600'
                }`}
              />
              {BLOCK_LABELS[block]}
            </button>
          )
        })}
      </div>

      {/* Status + action row */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-gray-500">{activeBlock}.yaml</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full border ${STATUS_PILL[activeConfig.status] || STATUS_PILL.pending}`}>
            {activeConfig.status}
          </span>
        </div>
        <div className="flex gap-1.5">
          {!editing ? (
            <button
              onClick={startEdit}
              className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
            >
              Edit
            </button>
          ) : (
            <>
              <button
                onClick={cancelEdit}
                className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2.5 py-1 rounded-lg transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Save feedback + validation errors */}
      {saveMsg && (
        <div className={`px-3 py-1.5 text-xs border-b shrink-0 ${
          validationErrors.length > 0 ? 'bg-red-950 text-red-300 border-red-800' : 'bg-green-950 text-green-300 border-green-800'
        }`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5 pl-2">• {e}</div>)}
        </div>
      )}

      {/* Editor */}
      <div ref={editorRef} className="flex-1 overflow-auto text-xs min-h-0" />

      {/* Field guide */}
      {showGuide && Object.keys(descriptions).length > 0 && (
        <div className="border-t border-gray-800 bg-gray-900 max-h-48 overflow-y-auto shrink-0">
          <p className="px-3 pt-2 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide">Field Guide</p>
          {Object.entries(descriptions).map(([key, desc]) => (
            <div key={key} className="px-3 py-1 flex gap-2 text-xs">
              <span className="font-mono text-blue-400 shrink-0">{key}</span>
              <span className="text-gray-400">{desc}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Manual test (isolated)**

Temporarily import and render `YamlPanel` inside `App.jsx` with mock props to verify it renders without errors:

```jsx
// Temporary in App.jsx — remove after verifying:
import YamlPanel from './components/YamlPanel'
// ... inside return:
<YamlPanel slug="test" configs={[{block: 'agent_core', status: 'pending', content: 'server:\n  host: 0.0.0.0\n'}]} />
```

Check browser: tabs render, CodeMirror loads, Edit button appears.

- [ ] **Step 3: Remove temporary test code from App.jsx**

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/components/YamlPanel.jsx
git commit -m "feat: add YamlPanel component with tabs, CodeMirror, edit/save/cancel, copy, field guide"
```

---

## Task 5: `Chat.jsx` — Integrate YAML Panel and Diff Modal

**Files:**
- Modify: `dev-kit/frontend/src/components/Chat.jsx`

This is the main wiring task. After each agent turn, configs are re-fetched and passed to `YamlPanel`. Checkpoint restore now shows a diff modal before confirming.

- [ ] **Step 1: Replace `Chat.jsx` entirely with the enhanced version**

```jsx
// dev-kit/frontend/src/components/Chat.jsx
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import PhaseBar from './PhaseBar'
import FlowGraph from './FlowGraph'
import YamlPanel from './YamlPanel'
import DiffModal from './DiffModal'

export default function Chat({ slug, onDashboard, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState('overview')
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [checkpoints, setCheckpoints] = useState([])
  const [configs, setConfigs] = useState([])
  const [showGraph, setShowGraph] = useState(false)
  const [showYaml, setShowYaml] = useState(false)
  const [diffModal, setDiffModal] = useState(null)  // {phase, currentConfigs, previewConfigs} | null
  const bottomRef = useRef(null)

  useEffect(() => {
    api.getHistory(slug).then(history => {
      setMessages(history.map(m => ({ role: m.role, text: m.content })))
    }).catch(() => {})
    api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
    api.getGraph(slug).then(setGraph).catch(() => {})
    api.getConfigs(slug).then(setConfigs).catch(() => {})
  }, [slug])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(e) {
    e.preventDefault()
    if (!input.trim() || loading) return
    const userText = input.trim()
    setInput('')
    setMessages(m => [...m, { role: 'user', text: userText }])
    setLoading(true)
    try {
      const res = await api.chat(slug, userText)
      setMessages(m => [...m, { role: 'assistant', text: res.reply }])
      setPhase(res.phase)
      if (res.graph) setGraph(res.graph)
      if (res.checkpoint_created) {
        api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
      }
      // Refresh configs after every agent turn (agent may have updated them)
      api.getConfigs(slug).then(setConfigs).catch(() => {})
    } catch (err) {
      setMessages(m => [...m, { role: 'error', text: `Error: ${err.message}` }])
    } finally {
      setLoading(false)
    }
  }

  async function handleRestoreCheckpoint(checkpointPhase) {
    try {
      const [currentConfigs, previewConfigs] = await Promise.all([
        api.getConfigs(slug),
        api.getCheckpointPreview(slug, checkpointPhase),
      ])
      setDiffModal({ phase: checkpointPhase, currentConfigs, previewConfigs })
    } catch (err) {
      alert(`Failed to load checkpoint preview: ${err.message}`)
    }
  }

  async function confirmRestore() {
    if (!diffModal) return
    const checkpointPhase = diffModal.phase
    setDiffModal(null)
    try {
      await api.restoreCheckpoint(slug, checkpointPhase)
      setMessages([])
      const [history, project, newGraph, newCheckpoints, newConfigs] = await Promise.all([
        api.getHistory(slug),
        api.getProject(slug),
        api.getGraph(slug),
        api.getCheckpoints(slug),
        api.getConfigs(slug),
      ])
      setMessages(history.map(m => ({ role: m.role, text: m.content })))
      setPhase(project.current_phase)
      setGraph(newGraph)
      setCheckpoints(newCheckpoints)
      setConfigs(newConfigs)
    } catch (err) {
      alert(`Failed to restore: ${err.message}`)
    }
  }

  function handleConfigSaved(block, updatedConfig) {
    setConfigs(prev => prev.map(c => c.block === block ? updatedConfig : c))
  }

  // Only one side panel can be shown at a time
  function toggleGraph() {
    setShowGraph(g => !g)
    if (!showGraph) setShowYaml(false)
  }
  function toggleYaml() {
    setShowYaml(y => !y)
    if (!showYaml) setShowGraph(false)
  }

  const showSidePanel = showGraph || showYaml

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
          ← Projects
        </button>
        <span className="font-semibold text-sm text-gray-300">{slug}</span>
        <div className="flex gap-2">
          <button
            onClick={toggleGraph}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${showGraph ? 'bg-indigo-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-300'}`}
          >
            {showGraph ? 'Hide Graph' : 'Graph'}
          </button>
          <button
            onClick={toggleYaml}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${showYaml ? 'bg-indigo-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-300'}`}
          >
            {showYaml ? 'Hide YAML' : 'YAML'}
          </button>
          <button
            onClick={onDashboard}
            className="text-xs bg-blue-600 hover:bg-blue-500 px-3 py-1.5 rounded-lg transition-colors font-medium"
          >
            Dashboard
          </button>
        </div>
      </div>

      <PhaseBar currentPhase={phase} checkpoints={checkpoints} onRestoreCheckpoint={handleRestoreCheckpoint} />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Chat column */}
        <div className={`flex flex-col ${showSidePanel ? 'w-1/2' : 'w-full'} overflow-hidden transition-all`}>
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-center text-sm mt-12">
                Describe your AI agent use case to get started.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={[
                  'max-w-xl rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap',
                  m.role === 'user' ? 'bg-blue-600 text-white' : '',
                  m.role === 'assistant' ? 'bg-gray-800 text-gray-100' : '',
                  m.role === 'error' ? 'bg-red-900/60 text-red-200 border border-red-700' : '',
                ].filter(Boolean).join(' ')}>
                  {m.text}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-800 rounded-2xl px-4 py-2.5 text-sm text-gray-400">
                  <span className="inline-flex gap-1">
                    <span className="animate-bounce" style={{ animationDelay: '0ms' }}>●</span>
                    <span className="animate-bounce" style={{ animationDelay: '150ms' }}>●</span>
                    <span className="animate-bounce" style={{ animationDelay: '300ms' }}>●</span>
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form onSubmit={send} className="flex gap-2 px-4 py-3 border-t border-gray-800 bg-gray-900 shrink-0">
            <input
              className="flex-1 bg-gray-800 rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
              placeholder="Type your message…"
              value={input}
              onChange={e => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-xl px-4 py-2 text-sm font-medium transition-colors"
            >
              Send
            </button>
          </form>
        </div>

        {/* Side panel */}
        {showGraph && (
          <div className="w-1/2 border-l border-gray-800 bg-gray-950">
            <FlowGraph graph={graph} />
          </div>
        )}
        {showYaml && (
          <div className="w-1/2 border-l border-gray-800 min-h-0">
            <YamlPanel slug={slug} configs={configs} onSaved={handleConfigSaved} />
          </div>
        )}
      </div>

      {/* Diff modal */}
      {diffModal && (
        <DiffModal
          currentConfigs={diffModal.currentConfigs}
          previewConfigs={diffModal.previewConfigs}
          checkpointPhase={diffModal.phase}
          onConfirm={confirmRestore}
          onCancel={() => setDiffModal(null)}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Manual test**

1. Open a project in Chat.
2. Click "YAML" button — YamlPanel should appear on the right at 50% width.
3. Click "Graph" button — YAML should hide, graph should appear (mutually exclusive).
4. Send a message — after reply, click "YAML" and verify the content updates.
5. Click a phase checkpoint — DiffModal should appear with block tabs and diff.
6. Click Cancel — modal closes without restoring.
7. Click Restore — restore completes, configs refresh.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/Chat.jsx
git commit -m "feat: integrate YamlPanel and DiffModal into Chat; refresh configs after every agent turn"
```

---

## Task 6: Fix `ConfigEditor.jsx` — Cancel, Dirty State, Copy, Field Guide

**Files:**
- Modify: `dev-kit/frontend/src/components/ConfigEditor.jsx`

- [ ] **Step 1: Replace `ConfigEditor.jsx` entirely**

```jsx
// dev-kit/frontend/src/components/ConfigEditor.jsx
import React, { useEffect, useRef, useState } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

const DRAFT_BLOCKS = new Set(['trust_layer', 'action_gateway', 'reach_layer'])

export default function ConfigEditor({ slug, block, onBack }) {
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')
  const [status, setStatus] = useState('pending')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)
  const [copied, setCopied] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})

  useEffect(() => {
    api.getConfig(slug, block).then(({ content, status: s }) => {
      setStatus(s)
      if (viewRef.current) viewRef.current.destroy()
      if (!editorRef.current) return
      const state = EditorState.create({
        doc: content || '',
        extensions: [basicSetup, yaml(), oneDark, EditorView.editable.of(false)],
      })
      viewRef.current = new EditorView({ state, parent: editorRef.current })
    }).catch(() => {})
    return () => { viewRef.current?.destroy(); viewRef.current = null }
  }, [slug, block])

  useEffect(() => {
    api.getSchemaDescriptions(block)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [block])

  function startEdit() {
    if (!viewRef.current) return
    originalRef.current = viewRef.current.state.doc.toString()
    viewRef.current.dispatch({
      effects: EditorView.editable.reconfigure(EditorView.editable.of(true)),
    })
    setEditing(true)
    setSaveMsg(null)
    setValidationErrors([])
  }

  function cancelEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: { from: 0, to: viewRef.current.state.doc.length, insert: originalRef.current },
      effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
    })
    setEditing(false)
    setValidationErrors([])
    setSaveMsg(null)
  }

  async function handleSave() {
    if (!viewRef.current) return
    setSaving(true)
    setValidationErrors([])
    setSaveMsg(null)
    const content = viewRef.current.state.doc.toString()
    try {
      const result = await api.updateConfig(slug, block, content)
      setStatus(result.status)
      viewRef.current.dispatch({
        effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
      })
      setEditing(false)
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved successfully.')
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleCopy() {
    const content = viewRef.current?.state.doc.toString() || ''
    await navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
          ← Dashboard
        </button>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-300">{block}.yaml</span>
          <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_PILL[status] || STATUS_PILL.pending}`}>
            {status}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGuide(g => !g)}
            className={`text-xs px-2.5 py-1.5 rounded-lg transition-colors ${showGuide ? 'bg-indigo-700 text-indigo-200' : 'bg-gray-800 hover:bg-gray-700 text-gray-400'}`}
          >
            ? Guide
          </button>
          <button
            onClick={handleCopy}
            className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 px-2.5 py-1.5 rounded-lg transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
          {!editing ? (
            <button
              onClick={startEdit}
              className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded-lg transition-colors"
            >
              Edit
            </button>
          ) : (
            <>
              <button
                onClick={cancelEdit}
                className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2.5 py-1.5 rounded-lg transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      </div>

      {DRAFT_BLOCKS.has(block) && (
        <div className="px-4 py-1.5 bg-yellow-900/40 border-b border-yellow-800 text-yellow-400 text-xs shrink-0">
          This config block is a draft — the block template is not yet finalised.
        </div>
      )}

      {editing && (
        <div className="px-4 py-1.5 bg-indigo-900/30 border-b border-indigo-800 text-indigo-300 text-xs shrink-0">
          Editing — click Save to persist or Cancel to discard changes.
        </div>
      )}

      {saveMsg && (
        <div className={`px-4 py-1.5 text-xs border-b shrink-0 ${
          validationErrors.length > 0 ? 'bg-red-950 text-red-300 border-red-800' : 'bg-green-950 text-green-300 border-green-800'
        }`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5 pl-2">• {e}</div>)}
        </div>
      )}

      <div ref={editorRef} className="flex-1 overflow-auto text-sm min-h-0" />

      {/* Field guide */}
      {showGuide && Object.keys(descriptions).length > 0 && (
        <div className="border-t border-gray-800 bg-gray-900 max-h-52 overflow-y-auto shrink-0">
          <p className="px-4 pt-2 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide">Field Guide</p>
          {Object.entries(descriptions).map(([key, desc]) => (
            <div key={key} className="px-4 py-1 flex gap-3 text-xs border-b border-gray-800/50">
              <span className="font-mono text-blue-400 shrink-0 w-40">{key}</span>
              <span className="text-gray-400">{desc}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Manual test**

1. Open Dashboard → click a block → ConfigEditor opens.
2. Click "Edit" — "Editing" blue banner appears.
3. Change some content — the editor should be editable.
4. Click "Cancel" — content reverts to original, blue banner disappears.
5. Click "Edit" again → change content → click "Save" — status badge updates, "Editing" banner disappears, editor becomes read-only.
6. Click "Copy" — paste into notepad, verify YAML content.
7. Click "? Guide" — field descriptions appear at the bottom.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/ConfigEditor.jsx
git commit -m "fix: add cancel+dirty-state to ConfigEditor; add copy button and field guide"
```

---

## Task 7: `Dashboard.jsx` — Health Banner, Export Button, Polish

**Files:**
- Modify: `dev-kit/frontend/src/components/Dashboard.jsx`

- [ ] **Step 1: Replace `Dashboard.jsx` entirely**

```jsx
// dev-kit/frontend/src/components/Dashboard.jsx
import React, { useEffect, useState } from 'react'
import { api } from '../api'

const BLOCKS = ['agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer', 'action_gateway', 'reach_layer', 'observability_layer']
const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}
const BLOCK_DESC = {
  agent_core: 'Orchestrator & LLM caller',
  knowledge_engine: 'RAG & prompt assembly',
  memory_layer: 'Session & user state',
  trust_layer: 'Safety & content gate',
  action_gateway: 'External API connector',
  reach_layer: 'Channel UI & delivery',
  observability_layer: 'Telemetry & logging',
}
const STATUS_COLORS = {
  complete: 'border-green-700 bg-green-950/40',
  draft: 'border-yellow-700 bg-yellow-950/30',
  pending: 'border-gray-700 bg-gray-900',
  stale: 'border-red-700 bg-red-950/30',
}
const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

function HealthBanner({ configs }) {
  const counts = { complete: 0, draft: 0, stale: 0, pending: 0 }
  configs.forEach(c => { counts[c.status] = (counts[c.status] || 0) + 1 })
  const total = BLOCKS.length

  const allComplete = counts.complete === total
  const hasStale = counts.stale > 0

  return (
    <div className={`rounded-xl border px-4 py-3 mb-6 flex items-center justify-between ${
      allComplete ? 'border-green-700 bg-green-950/40' :
      hasStale ? 'border-red-700 bg-red-950/30' :
      'border-gray-700 bg-gray-900'
    }`}>
      <div className="flex items-center gap-3">
        <span className="text-xl">{allComplete ? '✅' : hasStale ? '⚠️' : '🔧'}</span>
        <div>
          <p className="text-sm font-medium">
            {allComplete ? 'All configs complete — ready to deploy' :
             hasStale ? 'Some configs have validation errors' :
             'Configuration in progress'}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {counts.complete}/{total} complete
            {counts.draft > 0 && ` · ${counts.draft} draft`}
            {counts.stale > 0 && ` · ${counts.stale} stale`}
            {counts.pending > 0 && ` · ${counts.pending} pending`}
          </p>
        </div>
      </div>
      <div className="flex gap-2">
        {hasStale && (
          <span className="text-xs text-red-400 bg-red-950 border border-red-800 px-2 py-1 rounded-lg">
            Fix stale configs
          </span>
        )}
      </div>
    </div>
  )
}

export default function Dashboard({ slug, onChat, onEditConfig, onBack }) {
  const [configs, setConfigs] = useState([])
  const [project, setProject] = useState(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    api.getConfigs(slug).then(setConfigs).catch(() => {})
    api.getProject(slug).then(setProject).catch(() => {})
  }, [slug])

  function handleExport() {
    setExporting(true)
    const url = api.exportConfigs(slug)
    const a = document.createElement('a')
    a.href = url
    a.download = `${slug}-configs.zip`
    a.click()
    setTimeout(() => setExporting(false), 1500)
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 px-6 py-8 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <button onClick={onBack} className="text-gray-400 hover:text-white text-sm mb-2 block transition-colors">
            ← Projects
          </button>
          <h1 className="text-2xl font-bold">{project?.name || slug}</h1>
          {project?.description && (
            <p className="text-gray-400 text-sm mt-1">{project.description}</p>
          )}
        </div>
        <div className="flex gap-2 mt-6">
          <button
            onClick={handleExport}
            disabled={exporting}
            className="text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 px-3 py-2 rounded-xl transition-colors flex items-center gap-1.5"
          >
            {exporting ? 'Exporting…' : '↓ Export ZIP'}
          </button>
          <button
            onClick={onChat}
            className="text-sm bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded-xl font-medium transition-colors"
          >
            Continue Configuration
          </button>
        </div>
      </div>

      <HealthBanner configs={configs} />

      {/* Config grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {BLOCKS.map(block => {
          const config = configs.find(c => c.block === block)
          const status = config?.status || 'pending'
          return (
            <button
              key={block}
              onClick={() => onEditConfig(block)}
              className={`border rounded-xl p-4 text-left hover:brightness-110 transition-all ${STATUS_COLORS[status]}`}
            >
              <div className="flex items-start justify-between mb-1.5">
                <span className="font-semibold text-sm">{BLOCK_LABELS[block]}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded-full border shrink-0 ml-2 ${STATUS_PILL[status]}`}>
                  {status}
                </span>
              </div>
              <p className="text-xs text-gray-500 mb-2">{BLOCK_DESC[block]}</p>
              <p className="text-xs text-gray-400 truncate">
                {config?.content ? 'Click to view or edit →' : 'Not yet configured'}
              </p>
            </button>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Manual test**

1. Open a project's Dashboard.
2. Verify `learning_layer` no longer appears (replaced with `observability_layer`).
3. Verify the health banner shows correct counts (e.g. "2/7 complete · 3 draft · 2 pending").
4. Click "↓ Export ZIP" — browser should download `<slug>-configs.zip` containing 7 YAML files.
5. Open the ZIP — verify all 7 block YAMLs are present.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/Dashboard.jsx
git commit -m "feat: add health banner and export ZIP to Dashboard; fix observability_layer block name"
```

---

## Task 8: `ProjectList.jsx` — Delete Project Button + UI Polish

**Files:**
- Modify: `dev-kit/frontend/src/components/ProjectList.jsx`

- [ ] **Step 1: Replace `ProjectList.jsx` entirely**

```jsx
// dev-kit/frontend/src/components/ProjectList.jsx
import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function ProjectList({ onOpen }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState(null)
  const [deletingSlug, setDeletingSlug] = useState(null)

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]))
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const project = await api.createProject(name.trim(), description.trim())
      setProjects(p => [...p, project])
      setName('')
      setDescription('')
      onOpen(project.slug)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(e, slug) {
    e.stopPropagation()
    if (!window.confirm(`Delete project "${slug}"? This cannot be undone.`)) return
    setDeletingSlug(slug)
    try {
      await api.deleteProject(slug)
      setProjects(p => p.filter(proj => proj.slug !== slug))
    } catch (err) {
      alert(`Failed to delete: ${err.message}`)
    } finally {
      setDeletingSlug(null)
    }
  }

  const phaseLabel = (phase) => phase ? phase.charAt(0).toUpperCase() + phase.slice(1) : 'Not started'

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center pt-16 px-4">
      {/* Hero */}
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold mb-2">DPG Configuration Agent</h1>
        <p className="text-gray-400 max-w-md">
          Generate and validate all 7 DPG block configs through a guided AI conversation.
        </p>
      </div>

      {/* Create form */}
      <div className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-8 shadow-xl">
        <h2 className="text-base font-semibold mb-4 text-gray-200">New Project</h2>
        <form onSubmit={handleCreate} className="flex flex-col gap-3">
          <input
            className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent placeholder-gray-500 transition"
            placeholder="Project name (e.g. Rural Jobs Assistant)"
            value={name}
            onChange={e => setName(e.target.value)}
            required
          />
          <textarea
            className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none placeholder-gray-500 transition"
            placeholder="Brief description of your use case (optional)"
            rows={2}
            value={description}
            onChange={e => setDescription(e.target.value)}
          />
          {error && (
            <p className="text-red-400 text-sm bg-red-950/40 border border-red-800 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={creating || !name.trim()}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-xl py-2.5 font-medium text-sm transition-colors"
          >
            {creating ? 'Creating…' : 'Create & Start Configuration →'}
          </button>
        </form>
      </div>

      {/* Existing projects */}
      {projects.length > 0 && (
        <div className="w-full max-w-lg">
          <h2 className="text-base font-semibold mb-3 text-gray-200">Existing Projects</h2>
          <div className="flex flex-col gap-2">
            {projects.map(p => (
              <div
                key={p.slug}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 hover:border-gray-600 rounded-xl px-4 py-3 transition-colors group cursor-pointer"
                onClick={() => onOpen(p.slug)}
              >
                <div className="min-w-0">
                  <p className="font-medium text-sm truncate">{p.name}</p>
                  {p.description && (
                    <p className="text-gray-400 text-xs truncate mt-0.5">{p.description}</p>
                  )}
                  <p className="text-gray-600 text-xs mt-1">
                    Phase: <span className="text-gray-400">{phaseLabel(p.current_phase)}</span>
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4 shrink-0">
                  <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors">Open →</span>
                  <button
                    onClick={e => handleDelete(e, p.slug)}
                    disabled={deletingSlug === p.slug}
                    className="text-xs text-gray-600 hover:text-red-400 disabled:opacity-50 px-2 py-1 rounded-lg hover:bg-red-950/40 transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete project"
                  >
                    {deletingSlug === p.slug ? '…' : 'Delete'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {projects.length === 0 && (
        <p className="text-gray-600 text-sm">No projects yet. Create one above.</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Manual test**

1. Open the project list page.
2. Verify the hero text and create form render cleanly.
3. Hover over an existing project card — the "Delete" button should appear (opacity-0 → 100 on group hover).
4. Click Delete on a project → confirm dialog → project is removed from the list.
5. Click Delete → dismiss confirm dialog → project remains.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/ProjectList.jsx
git commit -m "feat: add delete-project button to ProjectList; polish project cards and create form"
```

---

## Task 9: Build and Smoke Test

- [ ] **Step 1: Run backend tests**

```bash
cd dev-kit && uv run pytest tests/test_app_endpoints.py tests/test_schema.py -v
```

Expected: All PASS.

- [ ] **Step 2: Build the frontend**

```bash
cd dev-kit/frontend && npm run build
```

Expected: Build completes with no errors. Output written to `dev-kit/dev_kit/agent/static/`.

- [ ] **Step 3: Start the full dev stack locally**

```bash
# Terminal 1 — backend
cd dev-kit && uv run python -m dev_kit.agent.app

# Terminal 2 — frontend dev server (hot reload)
cd dev-kit/frontend && npm run dev
```

- [ ] **Step 4: End-to-end smoke test checklist**

| Check | Pass? |
|---|---|
| Projects page loads, "DPG Configuration Agent" hero visible | |
| Create a new project → opens Chat | |
| Send a message → agent replies, YAML panel reflects any changes | |
| "YAML" button toggles side panel; "Graph" and "YAML" are mutually exclusive | |
| YamlPanel: switch block tabs, content updates | |
| YamlPanel: Edit → change content → Cancel → content reverts | |
| YamlPanel: Edit → change content → Save → status badge updates, editor goes read-only | |
| YamlPanel: Copy → clipboard has YAML content | |
| YamlPanel: "? Guide" → descriptions appear for fields with comments in template | |
| Dashboard health banner shows correct counts (X/7 complete, etc.) | |
| Dashboard "↓ Export ZIP" → ZIP downloads with 7 YAML files | |
| Dashboard block cards show `observability_layer` not `learning_layer` | |
| ConfigEditor: Edit → Cancel → reverts | |
| ConfigEditor: Edit → Save → status badge refreshes | |
| ConfigEditor: Copy and ? Guide buttons work | |
| Checkpoint restore: clicking phase → DiffModal appears | |
| DiffModal: block tabs show which blocks will change (yellow dot) | |
| DiffModal: Cancel → no restore | |
| DiffModal: Restore → configs refresh in panel | |
| ProjectList: hover → Delete button appears; clicking deletes project | |

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final build and smoke test — all UI enhancements complete"
```

---

## Self-Review

**Spec coverage check:**

| Feature | Task |
|---|---|
| Live YAML side-panel in Chat | Task 4 (YamlPanel), Task 5 (Chat integration) |
| Working Edit/Save/Cancel with dirty state | Task 4, Task 6 |
| Status badge refresh after save | Task 4 (onSaved callback), Task 6 |
| Export as ZIP | Task 1a (backend), Task 7 (Dashboard button) |
| Config diff view on checkpoint restore | Task 3 (DiffModal), Task 5 (Chat wiring) |
| Validation summary / health banner | Task 7 (Dashboard) |
| Copy to clipboard | Task 4 (YamlPanel), Task 6 (ConfigEditor) |
| Schema field guide (tooltips) | Task 1c (backend), Task 4, Task 6 |
| Delete project | Task 2 (api), Task 8 (ProjectList) |
| UI polish — neat, user-friendly | Tasks 5, 6, 7, 8 (all rewrites use consistent Tailwind) |
| Fix `learning_layer` → `observability_layer` bug | Task 7 |

**Placeholder scan:** No TBD, TODO, or "implement later" in any step. All code is complete.

**Type consistency:**
- `configs` is always `[{block, status, content}]` — consistent across Chat, YamlPanel, DiffModal.
- `api.exportConfigs(slug)` returns a URL string — used as `href` in Dashboard, not as a fetch. Documented in Task 2 Step 1.
- `onSaved(block, updatedConfig)` in YamlPanel matches `handleConfigSaved(block, updatedConfig)` in Chat.
- `BLOCKS` array in Dashboard.jsx and DiffModal.jsx both use `observability_layer` (not `learning_layer`).
