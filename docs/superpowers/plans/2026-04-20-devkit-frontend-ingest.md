# Dev-Kit Frontend Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a post-deploy IngestDocumentsStep (step 8) to the dev-kit Deploy Wizard, update MandatoryInputsStep with Azure/callback fields, and add API client methods for batch submission and job status polling.

**Architecture:** `IngestDocumentsStep.jsx` is a new React component that lets operators add up to 5 files (each with a mode selector), submit as one batch, and poll per-file status until terminal. `MandatoryInputsStep.jsx` gets 3 new fields. `DeployWizard.jsx` gains step 8. All API calls go through `api.js`. The component reads config values (max files, max size, supported extensions, poll interval) from the backend via a new `GET /api/devkit-config` endpoint (simpler than baking them into the component).

**Tech Stack:** React 18, Vite, Vitest + React Testing Library, JavaScript (matching existing codebase).

**Dependency:** Plan 3 (Dev-Kit Backend) must be complete before end-to-end testing. Frontend components can be developed independently using msw (mock service worker) or inline mocks.

---

## File Map

| Path | Action | Responsibility |
|------|--------|---------------|
| `dev-kit/frontend/src/api.js` | Modify | Add `submitIngestBatch()`, `getJobStatus()`, `getDevKitConfig()` |
| `dev-kit/frontend/src/components/deploy/DeployWizard.jsx` | Modify | Add step 8, bump total from 7 → 8 |
| `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx` | Modify | Add Azure fields (conditional), callback URL, KE internal URL |
| `dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx` | **Create** | File-add UI, batch submit, per-file polling with timeout |
| `dev-kit/dev_kit/agent/app.py` | Modify | Add `GET /api/devkit-config` endpoint |
| `dev-kit/frontend/src/components/deploy/__tests__/IngestDocumentsStep.test.jsx` | **Create** | Unit tests for IngestDocumentsStep |
| `dev-kit/frontend/src/components/deploy/__tests__/MandatoryInputsStep.test.jsx` | **Create** | Tests for new mandatory fields |

---

## Task 1: Add GET /api/devkit-config endpoint (backend)

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`

The frontend needs to read `max_files_per_upload`, `max_file_size_mb`, `supported_extensions`, and `poll_interval_seconds` from the backend without hardcoding them. Add a simple config endpoint.

- [ ] **Step 1: Add endpoint to app.py**

In `dev-kit/dev_kit/agent/app.py`, after the ingest endpoints added in Plan 3, add:

```python
@app.get("/api/devkit-config")
async def get_devkit_config():
    """Return dev-kit operational config values for the frontend.

    Used by IngestDocumentsStep to read upload limits and polling parameters
    without hardcoding them in the frontend bundle.

    Returns:
        Upload limits and polling config from devkit.yaml.
    """
    return {
        "user_id": _DEVKIT_CONFIG.user_id,
        "upload": {
            "max_files_per_upload": _DEVKIT_CONFIG.upload.max_files_per_upload,
            "max_file_size_mb": _DEVKIT_CONFIG.upload.max_file_size_mb,
            "supported_extensions": _DEVKIT_CONFIG.upload.supported_extensions,
        },
        "polling": {
            "poll_interval_seconds": _DEVKIT_CONFIG.polling.poll_interval_seconds,
            "poll_timeout_minutes": _DEVKIT_CONFIG.polling.poll_timeout_minutes,
        },
    }
```

- [ ] **Step 2: Verify endpoint returns expected shape**

```bash
cd dev-kit && uv run python -c "
import asyncio
from dev_kit.agent.app import app
from fastapi.testclient import TestClient
client = TestClient(app)
r = client.get('/api/devkit-config')
import json; print(json.dumps(r.json(), indent=2))
"
```

Expected: JSON with upload and polling sections.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py
git commit -m "feat(devkit): add GET /api/devkit-config endpoint for frontend config"
```

---

## Task 2: api.js additions

**Files:**
- Modify: `dev-kit/frontend/src/api.js`

- [ ] **Step 1: Read current api.js to understand the request() helper pattern**

```bash
cat dev-kit/frontend/src/api.js
```

- [ ] **Step 2: Add three new API methods**

In `dev-kit/frontend/src/api.js`, add the following to the `api` export object:

```javascript
  // Ingest endpoints
  getDevKitConfig: () =>
    request('GET', '/devkit-config'),

  submitIngestBatch: (slug, formData) =>
    // formData is a FormData object containing metadata + file parts
    fetch(`/api/ingest/submit`, {
      method: 'POST',
      body: formData,
      // Do not set Content-Type — browser sets it with correct boundary for multipart
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      return res.json()
    }),

  getJobStatus: (jobId) =>
    request('GET', `/ingest/job/${jobId}`),
```

> **Note:** `submitIngestBatch` uses raw `fetch` instead of the `request()` helper because multipart FormData requires the browser to set the `Content-Type` boundary automatically. Do not pass `Content-Type: multipart/form-data` manually.

- [ ] **Step 3: Verify api.js is syntactically valid**

```bash
cd dev-kit/frontend && node --input-type=module < src/api.js 2>&1 | head -5
```

Expected: no syntax errors (or just import warnings that are acceptable).

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/api.js
git commit -m "feat(devkit-frontend): add submitIngestBatch, getJobStatus, getDevKitConfig API methods"
```

---

## Task 3: MandatoryInputsStep.jsx — new fields

**Files:**
- Modify: `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx`

- [ ] **Step 1: Read current MandatoryInputsStep.jsx**

```bash
cat dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx
```

Note the current fields and the `data.secrets` state shape.

- [ ] **Step 2: Write failing test**

Create `dev-kit/frontend/src/components/deploy/__tests__/MandatoryInputsStep.test.jsx`:

```jsx
import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import MandatoryInputsStep from '../MandatoryInputsStep'

const defaultProps = {
  data: {
    secrets: {
      anthropic_api_key: '',
      memgraph_password: '',
      redis_password: '',
      grafana_admin_password: 'admin',
      devkit_callback_url: '',
      ke_internal_url: '',
    },
  },
  project: { slug: 'test', azure_storage: null },
  onUpdate: vi.fn(),
  onNext: vi.fn(),
  onBack: vi.fn(),
}

describe('MandatoryInputsStep — new fields', () => {
  it('renders Dev-Kit Callback URL field', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.getByLabelText(/dev-kit callback url/i)).toBeInTheDocument()
  })

  it('renders KE Internal Service URL field', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.getByLabelText(/ke internal service url/i)).toBeInTheDocument()
  })

  it('does NOT show Azure fields when azure_storage is null', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.queryByLabelText(/azure account name/i)).not.toBeInTheDocument()
  })

  it('shows Azure fields when azure_storage is present', () => {
    const props = {
      ...defaultProps,
      project: {
        slug: 'test',
        azure_storage: {
          account_name: 'myaccount',
          account_key: '***KEY',
          container_name: 'kb-docs',
        },
      },
      data: {
        secrets: {
          ...defaultProps.data.secrets,
          azure_account_name: 'myaccount',
          azure_account_key: '',
          azure_container_name: 'kb-docs',
        },
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.getByLabelText(/azure account name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/azure account key/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/azure container name/i)).toBeInTheDocument()
  })

  it('pre-fills Azure account name from project.azure_storage', () => {
    const props = {
      ...defaultProps,
      project: {
        slug: 'test',
        azure_storage: { account_name: 'prefilledacct', account_key: '***XYZ', container_name: 'docs' },
      },
      data: {
        secrets: { ...defaultProps.data.secrets, azure_account_name: 'prefilledacct' },
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.getByDisplayValue('prefilledacct')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd dev-kit/frontend && npx vitest run src/components/deploy/__tests__/MandatoryInputsStep.test.jsx
```

Expected: FAIL — new fields not rendered.

- [ ] **Step 4: Update MandatoryInputsStep.jsx**

Find `MandatoryInputsStep.jsx` and add the three new field groups. The component receives `project` (from parent, contains `azure_storage`) and `data.secrets` (form state). Add before the existing submit/next button:

```jsx
{/* Dev-Kit Callback URL — always shown */}
<div className="field-group">
  <label htmlFor="devkit_callback_url">Dev-Kit Callback URL</label>
  <input
    id="devkit_callback_url"
    type="url"
    placeholder="https://devkit.your-vm.example.com"
    value={data.secrets.devkit_callback_url || ''}
    onChange={e => onUpdate({ secrets: { ...data.secrets, devkit_callback_url: e.target.value } })}
  />
  <p className="field-hint">
    The URL of this Dev-Kit instance, reachable from inside the Kubernetes cluster.
    The Knowledge Engine uses this to notify when ingestion completes.
  </p>
</div>

{/* KE Internal Service URL — always shown */}
<div className="field-group">
  <label htmlFor="ke_internal_url">KE Internal Service URL</label>
  <input
    id="ke_internal_url"
    type="url"
    placeholder="http://knowledge-engine.dpg.svc.cluster.local:8001"
    value={data.secrets.ke_internal_url || ''}
    onChange={e => onUpdate({ secrets: { ...data.secrets, ke_internal_url: e.target.value } })}
  />
  <p className="field-hint">
    Internal Kubernetes service URL for KE. Used by Reach Layer to proxy upload requests.
  </p>
</div>

{/* Azure Blob Storage — conditional on project.azure_storage */}
{project?.azure_storage && (
  <fieldset className="field-group azure-creds">
    <legend>Azure Blob Storage</legend>
    <div>
      <label htmlFor="azure_account_name">Azure Account Name</label>
      <input
        id="azure_account_name"
        type="text"
        value={data.secrets.azure_account_name || project.azure_storage.account_name || ''}
        onChange={e => onUpdate({ secrets: { ...data.secrets, azure_account_name: e.target.value } })}
      />
    </div>
    <div>
      <label htmlFor="azure_account_key">Azure Account Key</label>
      <input
        id="azure_account_key"
        type="password"
        placeholder="Paste your Azure storage account key"
        value={data.secrets.azure_account_key || ''}
        onChange={e => onUpdate({ secrets: { ...data.secrets, azure_account_key: e.target.value } })}
      />
      <p className="field-hint">Enter the full key. It will be stored encrypted.</p>
    </div>
    <div>
      <label htmlFor="azure_container_name">Azure Container Name</label>
      <input
        id="azure_container_name"
        type="text"
        value={data.secrets.azure_container_name || project.azure_storage.container_name || ''}
        onChange={e => onUpdate({ secrets: { ...data.secrets, azure_container_name: e.target.value } })}
      />
    </div>
  </fieldset>
)}
```

Update the `data.secrets` initial shape in `DeployWizard.jsx` (see Task 4) to include the new fields.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd dev-kit/frontend && npx vitest run src/components/deploy/__tests__/MandatoryInputsStep.test.jsx
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx
git add dev-kit/frontend/src/components/deploy/__tests__/MandatoryInputsStep.test.jsx
git commit -m "feat(devkit-frontend): add Azure, callback URL, KE internal URL to MandatoryInputsStep"
```

---

## Task 4: IngestDocumentsStep.jsx (new)

**Files:**
- Create: `dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/__tests__/IngestDocumentsStep.test.jsx`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/frontend/src/components/deploy/__tests__/IngestDocumentsStep.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi } from 'vitest'
import IngestDocumentsStep from '../IngestDocumentsStep'

// Mock the api module
vi.mock('../../../../api', () => ({
  api: {
    getDevKitConfig: vi.fn().mockResolvedValue({
      upload: { max_files_per_upload: 5, max_file_size_mb: 30, supported_extensions: ['.pdf', '.txt'] },
      polling: { poll_interval_seconds: 5, poll_timeout_minutes: 15 },
    }),
    submitIngestBatch: vi.fn(),
    getJobStatus: vi.fn(),
  },
}))

const defaultProps = {
  slug: 'test-project',
  project: { azure_storage: null },
  onNext: vi.fn(),
  onBack: vi.fn(),
}

describe('IngestDocumentsStep', () => {
  it('renders empty file list with Add File button', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => expect(screen.getByText(/\+ Add File/i)).toBeInTheDocument())
    expect(screen.queryByRole('row')).not.toBeInTheDocument()
  })

  it('adds a row when Add File is clicked', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    // A row should appear with a mode selector
    expect(screen.getByRole('combobox')).toBeInTheDocument()
  })

  it('removes a row when × is clicked', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const removeBtn = screen.getByTitle(/remove/i)
    fireEvent.click(removeBtn)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('disables Add File when max_files_per_upload reached', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    // Click 5 times
    for (let i = 0; i < 5; i++) {
      fireEvent.click(screen.getByText(/\+ Add File/i))
    }
    expect(screen.getByText(/\+ Add File/i).closest('button')).toBeDisabled()
  })

  it('does not show Azure modes when project has no azure_storage', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const modeSelect = screen.getByRole('combobox')
    const options = Array.from(modeSelect.options).map(o => o.value)
    expect(options).not.toContain('cloud_fetch_ingest')
    expect(options).not.toContain('cloud_upload_ingest')
  })

  it('shows Azure modes when project has azure_storage', async () => {
    const props = {
      ...defaultProps,
      project: { azure_storage: { account_name: 'a', account_key: 'k', container_name: 'c' } },
    }
    render(<IngestDocumentsStep {...props} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const modeSelect = screen.getByRole('combobox')
    const options = Array.from(modeSelect.options).map(o => o.value)
    expect(options).toContain('cloud_fetch_ingest')
  })

  it('shows skip and done buttons', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/skip/i))
    expect(screen.getByText(/skip/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit/frontend && npx vitest run src/components/deploy/__tests__/IngestDocumentsStep.test.jsx
```

Expected: FAIL — component not created yet.

- [ ] **Step 3: Implement IngestDocumentsStep.jsx**

Create `dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx`:

```jsx
/**
 * dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx
 *
 * Step 8 of the Deploy Wizard — post-deploy KB document ingestion.
 *
 * Operator adds up to max_files_per_upload files (each with a mode selector),
 * clicks "Upload & Ingest" to submit as one batch, then polls per-file status
 * until terminal (ingested | failed) or timeout.
 */

import { useEffect, useReducer, useRef, useState, useCallback } from 'react'
import { api } from '../../../api'

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

const INITIAL_STATE = {
  config: null,          // DevKitConfig from backend
  rows: [],              // Array of FileRow
  submitting: false,
  submitError: null,
}

// FileRow shape:
// {
//   id: string (local UUID),
//   mode: 'local_write_ingest' | 'cloud_fetch_ingest' | 'cloud_upload_ingest',
//   file: File | null,             // present for local/upload modes
//   cloudPath: string,             // present for cloud_fetch_ingest
//   filename: string,              // display name
//   jobId: string | null,          // set after submission
//   status: 'pending' | 'queued' | 'ingesting' | 'ingested' | 'failed',
//   queuePosition: number | null,
//   chunksAdded: number | null,
//   error: string | null,
// }

function _rowId() {
  return Math.random().toString(36).slice(2)
}

function _makeRow(hasAzure) {
  return {
    id: _rowId(),
    mode: 'local_write_ingest',
    file: null,
    cloudPath: '',
    filename: '',
    jobId: null,
    status: 'pending',
    queuePosition: null,
    chunksAdded: null,
    error: null,
  }
}

function reducer(state, action) {
  switch (action.type) {
    case 'SET_CONFIG':
      return { ...state, config: action.config }

    case 'ADD_ROW':
      return { ...state, rows: [...state.rows, action.row] }

    case 'REMOVE_ROW':
      return { ...state, rows: state.rows.filter(r => r.id !== action.id) }

    case 'UPDATE_ROW':
      return {
        ...state,
        rows: state.rows.map(r => r.id === action.id ? { ...r, ...action.patch } : r),
      }

    case 'SET_SUBMITTING':
      return { ...state, submitting: action.value, submitError: null }

    case 'SET_SUBMIT_ERROR':
      return { ...state, submitting: false, submitError: action.error }

    case 'ASSIGN_JOB_IDS':
      // action.assignments: [{filename, job_id}]
      return {
        ...state,
        submitting: false,
        rows: state.rows.map(row => {
          const match = action.assignments.find(a => a.filename === row.filename)
          return match ? { ...row, jobId: match.job_id, status: 'queued' } : row
        }),
      }

    case 'UPDATE_JOB_STATUS':
      return {
        ...state,
        rows: state.rows.map(r =>
          r.jobId === action.jobId
            ? {
                ...r,
                status: action.status,
                queuePosition: action.queuePosition ?? r.queuePosition,
                chunksAdded: action.chunksAdded ?? r.chunksAdded,
                error: action.error ?? r.error,
              }
            : r
        ),
      }

    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function IngestDocumentsStep({ slug, project, onNext, onBack }) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE)
  const pollingRef = useRef({})   // jobId → intervalId
  const timeoutRef = useRef({})   // jobId → timeoutId
  const hasAzure = Boolean(project?.azure_storage)

  // Load config on mount
  useEffect(() => {
    api.getDevKitConfig().then(cfg => {
      dispatch({ type: 'SET_CONFIG', config: cfg })
    }).catch(() => {
      // Use sensible defaults if endpoint fails
      dispatch({
        type: 'SET_CONFIG',
        config: {
          upload: { max_files_per_upload: 5, max_file_size_mb: 30, supported_extensions: ['.pdf', '.txt', '.md', '.csv', '.docx', '.html'] },
          polling: { poll_interval_seconds: 5, poll_timeout_minutes: 15 },
        },
      })
    })
    return () => {
      // Cleanup all polling on unmount
      Object.values(pollingRef.current).forEach(clearInterval)
      Object.values(timeoutRef.current).forEach(clearTimeout)
    }
  }, [])

  const maxFiles = state.config?.upload?.max_files_per_upload ?? 5
  const maxSizeMb = state.config?.upload?.max_file_size_mb ?? 30
  const supportedExtensions = state.config?.upload?.supported_extensions ?? ['.pdf', '.txt', '.md', '.csv', '.docx', '.html']
  const pollIntervalMs = (state.config?.polling?.poll_interval_seconds ?? 5) * 1000
  const pollTimeoutMs = (state.config?.polling?.poll_timeout_minutes ?? 15) * 60 * 1000

  // ---------------------------------------------------------------------------
  // File row management
  // ---------------------------------------------------------------------------

  const handleAddRow = () => {
    if (state.rows.length >= maxFiles) return
    dispatch({ type: 'ADD_ROW', row: _makeRow(hasAzure) })
  }

  const handleRemoveRow = (id) => {
    dispatch({ type: 'REMOVE_ROW', id })
  }

  const handleModeChange = (id, mode) => {
    dispatch({ type: 'UPDATE_ROW', id, patch: { mode, file: null, cloudPath: '', filename: '' } })
  }

  const handleFileChange = (id, file) => {
    if (!file) return
    // Validate extension
    const ext = '.' + file.name.split('.').pop().toLowerCase()
    if (!supportedExtensions.includes(ext)) {
      alert(`Unsupported file type: ${ext}. Supported: ${supportedExtensions.join(', ')}`)
      return
    }
    // Validate size
    if (file.size > maxSizeMb * 1024 * 1024) {
      alert(`${file.name} exceeds the ${maxSizeMb} MB limit.`)
      return
    }
    dispatch({ type: 'UPDATE_ROW', id, patch: { file, filename: file.name } })
  }

  const handleCloudPathChange = (id, cloudPath) => {
    const filename = cloudPath.split('/').pop() || cloudPath
    dispatch({ type: 'UPDATE_ROW', id, patch: { cloudPath, filename } })
  }

  // ---------------------------------------------------------------------------
  // Validate before submission
  // ---------------------------------------------------------------------------

  const validateBatch = () => {
    if (state.rows.length === 0) return 'Add at least one file.'

    // Duplicate filenames
    const names = state.rows.map(r => r.filename).filter(Boolean)
    if (new Set(names).size !== names.length) return 'Duplicate filenames in batch.'

    for (const row of state.rows) {
      if (!row.filename) return 'All rows must have a filename or cloud path.'
      if (row.mode !== 'cloud_fetch_ingest' && !row.file) return `Select a file for: ${row.filename}`
      if (row.mode === 'cloud_fetch_ingest' && !row.cloudPath) return 'Enter a cloud path for Azure fetch rows.'
    }
    return null
  }

  // ---------------------------------------------------------------------------
  // Submission
  // ---------------------------------------------------------------------------

  const handleSubmit = async () => {
    const validationError = validateBatch()
    if (validationError) {
      dispatch({ type: 'SET_SUBMIT_ERROR', error: validationError })
      return
    }

    dispatch({ type: 'SET_SUBMITTING', value: true })

    const formData = new FormData()

    const metadataEntries = state.rows.map(row => ({
      filename: row.filename,
      mode: row.mode,
      ...(row.cloudPath ? { cloud_path: row.cloudPath } : {}),
    }))
    formData.append('metadata', JSON.stringify(metadataEntries))

    for (const row of state.rows) {
      if (row.file) {
        formData.append('files', row.file, row.filename)
      }
    }

    try {
      const result = await api.submitIngestBatch(slug, formData)
      dispatch({ type: 'ASSIGN_JOB_IDS', assignments: result.jobs })
      // Start polling for each job
      result.jobs.forEach(({ job_id }) => _startPolling(job_id))
    } catch (err) {
      dispatch({ type: 'SET_SUBMIT_ERROR', error: err.message || 'Submission failed.' })
    }
  }

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------

  const _startPolling = useCallback((jobId) => {
    const startTime = Date.now()

    const poll = async () => {
      try {
        const data = await api.getJobStatus(jobId)
        dispatch({
          type: 'UPDATE_JOB_STATUS',
          jobId,
          status: data.status,
          queuePosition: data.queue_position,
          chunksAdded: data.chunks_added,
          error: data.error,
        })

        if (data.status === 'ingested' || data.status === 'failed') {
          clearInterval(pollingRef.current[jobId])
          clearTimeout(timeoutRef.current[jobId])
          delete pollingRef.current[jobId]
          delete timeoutRef.current[jobId]
        }
      } catch (err) {
        // API error — KE may be down
        const elapsed = Date.now() - startTime
        if (elapsed >= pollTimeoutMs) {
          clearInterval(pollingRef.current[jobId])
          clearTimeout(timeoutRef.current[jobId])
          dispatch({
            type: 'UPDATE_JOB_STATUS',
            jobId,
            status: 'failed',
            error: 'Knowledge Engine may be unavailable. Re-select your files and try again.',
          })
        }
      }
    }

    pollingRef.current[jobId] = setInterval(poll, pollIntervalMs)

    // Timeout: stop polling after poll_timeout_minutes
    timeoutRef.current[jobId] = setTimeout(() => {
      clearInterval(pollingRef.current[jobId])
      delete pollingRef.current[jobId]
      // If still ingesting, show a message but don't mark as failed
      dispatch((prev) => {
        const row = state.rows.find(r => r.jobId === jobId)
        if (row && row.status === 'ingesting') {
          return {
            ...prev,
            rows: prev.rows.map(r =>
              r.jobId === jobId
                ? { ...r, error: 'Ingestion is taking longer than expected. It may complete soon.' }
                : r
            ),
          }
        }
        return prev
      })
    }, pollTimeoutMs)
  }, [pollIntervalMs, pollTimeoutMs])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  const _statusLabel = (row) => {
    switch (row.status) {
      case 'pending': return ''
      case 'queued': return row.queuePosition ? `Queued (position ${row.queuePosition})` : 'Queued'
      case 'ingesting': return 'Ingesting…'
      case 'ingested': return `✓ Ingested — ${row.chunksAdded} chunks`
      case 'failed': return `✗ Failed: ${row.error || 'unknown error'}`
      default: return row.status
    }
  }

  const allTerminal = state.rows.length > 0 &&
    state.rows.every(r => r.status === 'ingested' || r.status === 'failed')

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="ingest-documents-step">
      <h2>Ingest Knowledge Documents</h2>
      <p>Upload your documents into the Knowledge Engine. They will be ingested into the vector store immediately.</p>

      <p className="upload-limits">
        ⚠ Max {maxFiles} files · Max {maxSizeMb} MB per file
      </p>

      {state.rows.length > 0 && (
        <table className="file-rows">
          <tbody>
            {state.rows.map(row => (
              <tr key={row.id} className={`row-${row.status}`}>
                <td className="filename-cell">
                  {row.status !== 'pending' ? (
                    <span>{row.filename}</span>
                  ) : row.mode === 'cloud_fetch_ingest' ? (
                    <input
                      type="text"
                      placeholder="e.g. docs/guide.pdf"
                      value={row.cloudPath}
                      onChange={e => handleCloudPathChange(row.id, e.target.value)}
                    />
                  ) : (
                    <input
                      type="file"
                      accept={supportedExtensions.join(',')}
                      onChange={e => handleFileChange(row.id, e.target.files[0])}
                    />
                  )}
                  {row.status !== 'pending' && (
                    <span className="status-label">{_statusLabel(row)}</span>
                  )}
                </td>
                <td className="mode-cell">
                  {row.status === 'pending' ? (
                    <select
                      value={row.mode}
                      onChange={e => handleModeChange(row.id, e.target.value)}
                    >
                      <option value="local_write_ingest">Local file</option>
                      {hasAzure && (
                        <option value="cloud_upload_ingest">Upload local + push to Azure</option>
                      )}
                      {hasAzure && (
                        <option value="cloud_fetch_ingest">Fetch from Azure</option>
                      )}
                    </select>
                  ) : (
                    <span>{row.mode === 'local_write_ingest' ? 'Local' : 'Azure'}</span>
                  )}
                </td>
                <td className="action-cell">
                  <button
                    title="Remove"
                    onClick={() => handleRemoveRow(row.id)}
                    disabled={row.status !== 'pending'}
                  >×</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row-actions">
        <button
          onClick={handleAddRow}
          disabled={state.rows.length >= maxFiles}
        >
          + Add File
        </button>
        <button
          onClick={handleSubmit}
          disabled={state.submitting || state.rows.length === 0}
          className="primary-btn"
        >
          {state.submitting ? 'Uploading…' : 'Upload & Ingest'}
        </button>
      </div>

      {state.submitError && (
        <p className="error-msg">{state.submitError}</p>
      )}

      <div className="step-footer">
        <button onClick={onBack}>← Back</button>
        <button onClick={() => onNext({})}>Skip</button>
        <button
          onClick={() => onNext({})}
          disabled={state.rows.length > 0 && !allTerminal}
          className="primary-btn"
        >
          Done →
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dev-kit/frontend && npx vitest run src/components/deploy/__tests__/IngestDocumentsStep.test.jsx
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx
git add dev-kit/frontend/src/components/deploy/__tests__/IngestDocumentsStep.test.jsx
git commit -m "feat(devkit-frontend): add IngestDocumentsStep component"
```

---

## Task 5: Update DeployWizard.jsx — add step 8

**Files:**
- Modify: `dev-kit/frontend/src/components/deploy/DeployWizard.jsx`

- [ ] **Step 1: Read current DeployWizard.jsx**

```bash
cat dev-kit/frontend/src/components/deploy/DeployWizard.jsx
```

Note: current steps are 1–7 and `data` has a `secrets` shape. Update accordingly.

- [ ] **Step 2: Add step 8 and update secrets shape**

In `DeployWizard.jsx`:

1. Add `IngestDocumentsStep` import:
```jsx
import IngestDocumentsStep from './IngestDocumentsStep'
```

2. Update `data` initial state's `secrets` to include new fields:
```jsx
const [data, setData] = useState({
  dpgValues: {},
  dependencies: {},
  preset: null,
  resources: {},
  secrets: {
    anthropic_api_key: '',
    namespace_prefix: 'dpg',
    memgraph_password: '',
    redis_password: '',
    grafana_admin_password: 'admin',
    devkit_callback_url: '',      // new
    ke_internal_url: '',           // new
    azure_account_name: '',        // new (conditional)
    azure_account_key: '',         // new (conditional)
    azure_container_name: '',      // new (conditional)
  },
  target: null,
  kubeconfig: '',
  clusterInfo: null,
})
```

3. Change `useState(1)` max check from `7` to `8` (if the wizard has a maxStep guard):
```jsx
// If there's a guard like: if (step > 7) return null
// Change to: if (step > 8) return null
```

4. Add step 8 to the steps object:
```jsx
const steps = {
  1: <DpgValuesStep {...stepProps} />,
  2: <DependenciesStep {...stepProps} />,
  3: <ResourcePresetStep {...stepProps} />,
  4: <MandatoryInputsStep {...stepProps} project={project} />,
  5: <DeployTargetStep {...stepProps} />,
  6: <PreviewStep {...stepProps} />,
  7: <DeployStatusStep {...stepProps} onSuccess={() => setStep(8)} />,
  8: <IngestDocumentsStep slug={slug} project={project} onNext={handleNext} onBack={() => setStep(7)} />,
}
```

Note: `DeployStatusStep` should call `onSuccess` (or `onNext`) to advance to step 8 after all services are healthy. Check existing `DeployStatusStep.jsx` — if it already calls `onNext`, step 7 → 8 will flow automatically. If it uses a different callback, wire accordingly.

5. Update `StepIndicator` (if it shows step count) to use `totalSteps={8}`.

- [ ] **Step 3: Run full frontend test suite**

```bash
cd dev-kit/frontend && npx vitest run
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/DeployWizard.jsx
git commit -m "feat(devkit-frontend): add step 8 IngestDocumentsStep to DeployWizard"
```

---

## Self-Review Checklist

- [x] `submitIngestBatch` uses raw `fetch` (not `request()`) so browser sets multipart boundary correctly
- [x] `getJobStatus` polls `GET /api/ingest/job/{jobId}` via the dev-kit backend (not KE directly)
- [x] `IngestDocumentsStep` reads config from `GET /api/devkit-config` — no hardcoded limits
- [x] Azure mode options only shown when `project.azure_storage` is present
- [x] `cloud_fetch_ingest` rows show text input (cloud_path), not file picker
- [x] Duplicate filename check happens before submission (client-side)
- [x] File size check happens when file is selected (client-side)
- [x] Extension check happens when file is selected (client-side)
- [x] "Upload & Ingest" re-enables after submission completes (new batch allowed)
- [x] "Done →" disabled until all submitted files reach terminal state
- [x] "Skip" always available — `onNext({})` regardless of state
- [x] Poll timeout: if KE unreachable (API error) → mark failed after poll_timeout_minutes
- [x] Poll timeout: if still `ingesting` → show warning, don't mark failed
- [x] `DeployWizard.jsx` `secrets` shape updated with 5 new fields
- [x] `MandatoryInputsStep` receives `project` prop for Azure pre-fill
- [x] Step 7 → 8 transition fires after DeployStatusStep reports healthy services
