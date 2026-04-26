/**
 * dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx
 *
 * Step 8 of the Deploy Wizard — post-deploy KB document ingestion.
 *
 * Operator adds up to max_files_per_upload files (each with a mode selector),
 * clicks "Upload & Ingest" to submit as one batch, then polls per-file status
 * until terminal (ingested | failed) or timeout.
 */

import { useEffect, useReducer, useRef, useCallback } from 'react'
import { api } from '../../api'

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

const INITIAL_STATE = {
  config: null,
  docTypes: [],
  defaultDocType: '',
  rows: [],
  submitting: false,
  submitError: null,
  servicesReady: false,
}

function _rowId() {
  return Math.random().toString(36).slice(2)
}

function _makeRow(defaultDocType = '') {
  return {
    id: _rowId(),
    mode: 'local_write_ingest',
    file: null,
    cloudPath: '',
    filename: '',
    docType: defaultDocType,
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
    case 'SET_DOC_TYPES':
      return {
        ...state,
        docTypes: action.docTypes || [],
        defaultDocType: action.defaultDocType || '',
      }
    case 'SET_SERVICES_READY':
      return { ...state, servicesReady: action.value }
    case 'ADD_ROW':
      return { ...state, rows: [...state.rows, action.row] }
    case 'REMOVE_ROW':
      return { ...state, rows: state.rows.filter(r => r.id !== action.id) }
    case 'UPDATE_ROW':
      return { ...state, rows: state.rows.map(r => r.id === action.id ? { ...r, ...action.patch } : r) }
    case 'SET_SUBMITTING':
      return { ...state, submitting: action.value, submitError: null }
    case 'SET_SUBMIT_ERROR':
      return { ...state, submitting: false, submitError: action.error }
    case 'ASSIGN_JOB_IDS':
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
// Helpers
// ---------------------------------------------------------------------------

function statusBadge(row) {
  switch (row.status) {
    case 'queued':
      return (
        <span className="text-xs text-yellow-400">
          {row.queuePosition ? `Queued (pos ${row.queuePosition})` : 'Queued'}
        </span>
      )
    case 'ingesting':
      return <span className="text-xs text-blue-400">Ingesting…</span>
    case 'ingested':
      return <span className="text-xs text-green-400">✓ Ingested — {row.chunksAdded} chunks</span>
    case 'failed':
      return <span className="text-xs text-red-400">✗ Failed: {row.error || 'unknown error'}</span>
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function IngestDocumentsStep({ slug, project, onNext, onBack }) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE)
  const pollingRef = useRef({})
  const timeoutRef = useRef({})
  // Backend always returns azure_storage as `{ needed: bool }`, so we have to
  // check the flag — `Boolean(project?.azure_storage)` would be true even when
  // Azure isn't configured.
  const hasAzure = Boolean(project?.azure_storage?.needed)

  const maxFiles = state.config?.upload?.max_files_per_upload ?? 5
  const maxSizeMb = state.config?.upload?.max_file_size_mb ?? 30
  const supportedExtensions = state.config?.upload?.supported_extensions ?? ['.pdf', '.txt', '.md', '.csv', '.docx', '.html']
  const pollIntervalMs = (state.config?.polling?.poll_interval_seconds ?? 5) * 1000
  const pollTimeoutMs = (state.config?.polling?.poll_timeout_minutes ?? 15) * 60 * 1000

  useEffect(() => {
    api.getDevKitConfig().then(cfg => {
      dispatch({ type: 'SET_CONFIG', config: cfg })
    }).catch(() => {
      dispatch({
        type: 'SET_CONFIG',
        config: {
          upload: { max_files_per_upload: 5, max_file_size_mb: 30, supported_extensions: ['.pdf', '.txt', '.md', '.csv', '.docx', '.html'] },
          polling: { poll_interval_seconds: 5, poll_timeout_minutes: 15 },
        },
      })
    })
    if (slug) {
      api.getProjectDocTypes(slug).then(payload => {
        dispatch({
          type: 'SET_DOC_TYPES',
          docTypes: payload.doc_types || [],
          defaultDocType: payload.default_doc_type || '',
        })
      }).catch(() => {
        // Non-fatal — operator can still type a doc_type manually.
      })

      // Poll deploy status until reach_layer is healthy
      const checkServices = async () => {
        try {
          const status = await api.getDeployStatus(slug)
          const reachSvc = (status.services || []).find(
            s => s.name === 'reach_layer' || s.name === 'reach_layer_web'
          )
          const keSvc = (status.services || []).find(s => s.name === 'knowledge_engine')
          if (
            (reachSvc && reachSvc.status === 'healthy') &&
            (keSvc && keSvc.status === 'healthy')
          ) {
            dispatch({ type: 'SET_SERVICES_READY', value: true })
            clearInterval(svcPollId)
          }
        } catch (_err) { /* retry on next interval */ }
      }
      checkServices() // immediate check
      const svcPollId = setInterval(checkServices, 5000)
      // Store for cleanup
      pollingRef.current._svcPoll = svcPollId
    }
    return () => {
      Object.values(pollingRef.current).forEach(id => clearInterval(id))
      Object.values(timeoutRef.current).forEach(clearTimeout)
    }
  }, [slug])

  // ---------------------------------------------------------------------------
  // Row management
  // ---------------------------------------------------------------------------

  const handleAddRow = () => {
    if (state.rows.length >= maxFiles) return
    // Pre-select the first doc type when no explicit default is configured,
    // so the dropdown never needs an empty placeholder option.
    const fallbackDocType = state.defaultDocType || state.docTypes[0] || ''
    dispatch({ type: 'ADD_ROW', row: _makeRow(fallbackDocType) })
  }

  const handleDocTypeChange = (id, docType) => {
    dispatch({ type: 'UPDATE_ROW', id, patch: { docType } })
  }

  const handleRemoveRow = (id) => {
    dispatch({ type: 'REMOVE_ROW', id })
  }

  const handleModeChange = (id, mode) => {
    // Only clear file/filename when switching to cloud_fetch_ingest (text input, no file)
    // or away from it. Switching between the two upload modes keeps the selected file.
    const row = state.rows.find(r => r.id === id)
    const wasCloudFetch = row?.mode === 'cloud_fetch_ingest'
    const isCloudFetch = mode === 'cloud_fetch_ingest'
    if (wasCloudFetch || isCloudFetch) {
      dispatch({ type: 'UPDATE_ROW', id, patch: { mode, file: null, cloudPath: '', filename: '' } })
    } else {
      dispatch({ type: 'UPDATE_ROW', id, patch: { mode } })
    }
  }

  const handleFileChange = (id, file) => {
    if (!file) return
    const ext = '.' + file.name.split('.').pop().toLowerCase()
    if (!supportedExtensions.includes(ext)) {
      alert(`Unsupported file type: ${ext}. Supported: ${supportedExtensions.join(', ')}`)
      return
    }
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
  // Submission
  // ---------------------------------------------------------------------------

  const handleRetry = (id) => {
    dispatch({ type: 'UPDATE_ROW', id, patch: { status: 'pending', jobId: null, error: null, chunksAdded: null } })
  }

  const validateBatch = () => {
    const pending = state.rows.filter(r => r.status === 'pending')
    if (pending.length === 0) return 'No pending files to upload.'
    const names = pending.map(r => r.filename).filter(Boolean)
    if (new Set(names).size !== names.length) return 'Duplicate filenames in batch.'
    for (const row of pending) {
      if (!row.filename) return 'All rows must have a filename or cloud path.'
      if (row.mode !== 'cloud_fetch_ingest' && !row.file) return `Select a file for: ${row.filename}`
      if (row.mode === 'cloud_fetch_ingest' && !row.cloudPath) return 'Enter a cloud path for Azure fetch rows.'
    }
    return null
  }

  const handleSubmit = async () => {
    const validationError = validateBatch()
    if (validationError) {
      dispatch({ type: 'SET_SUBMIT_ERROR', error: validationError })
      return
    }
    dispatch({ type: 'SET_SUBMITTING', value: true })

    // Only submit pending rows — skip already ingested/failed rows
    const pending = state.rows.filter(r => r.status === 'pending')
    const formData = new FormData()
    formData.append('metadata', JSON.stringify(
      pending.map(row => ({
        filename: row.filename,
        mode: row.mode,
        ...(row.cloudPath ? { cloud_path: row.cloudPath } : {}),
        ...(row.docType ? { doc_type: row.docType } : {}),
      }))
    ))
    for (const row of pending) {
      if (row.file) formData.append('files', row.file, row.filename)
    }

    try {
      const result = await api.submitIngestBatch(slug, formData)
      dispatch({ type: 'ASSIGN_JOB_IDS', assignments: result.jobs })
      result.jobs.forEach(({ job_id }) => _startPolling(job_id))
    } catch (err) {
      dispatch({ type: 'SET_SUBMIT_ERROR', error: err.message || 'Submission failed.' })
    }
  }

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------

  const _startPolling = useCallback((jobId) => {
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
      } catch (_err) {
        // Polling error — timeout will handle it
      }
    }

    pollingRef.current[jobId] = setInterval(poll, pollIntervalMs)
    timeoutRef.current[jobId] = setTimeout(() => {
      clearInterval(pollingRef.current[jobId])
      delete pollingRef.current[jobId]
      dispatch({
        type: 'UPDATE_JOB_STATUS',
        jobId,
        status: 'failed',
        error: 'Ingestion timed out. Check KE logs.',
      })
    }, pollTimeoutMs)
  }, [pollIntervalMs, pollTimeoutMs])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const pendingRows = state.rows.filter(r => r.status === 'pending')
  const hasPending = pendingRows.length > 0
  const allTerminal = state.rows.length > 0 &&
    state.rows.every(r => r.status === 'ingested' || r.status === 'failed')

  if (!state.config) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
        Loading…
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Ingest Knowledge Documents</h2>
      <p className="text-sm text-gray-400 mb-1">
        Upload your documents into the Knowledge Engine vector store.
      </p>
      <p className="text-xs text-gray-500 mb-6">
        Max {maxFiles} files · Max {maxSizeMb} MB per file
      </p>

      {!state.servicesReady && (
        <div className="mb-4 px-4 py-3 bg-yellow-900/30 border border-yellow-700 rounded-xl text-sm text-yellow-300 flex items-center gap-2">
          <span className="animate-spin text-xs">⏳</span>
          Waiting for services to become healthy (Knowledge Engine + Reach Layer)…
        </div>
      )}

      {/* File rows */}
      {state.rows.length > 0 && (
        <div className="flex flex-col gap-2 mb-4">
          {state.rows.map(row => (
            <div
              key={row.id}
              className="flex items-center gap-3 bg-gray-900 border border-gray-700 rounded-xl px-4 py-3"
            >
              {/* File input / cloud path / status */}
              <div className="flex-1 min-w-0">
                {row.status !== 'pending' ? (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-gray-200 truncate">{row.filename}</span>
                    {statusBadge(row)}
                  </div>
                ) : row.mode === 'cloud_fetch_ingest' ? (
                  <input
                    type="text"
                    placeholder="e.g. docs/guide.pdf"
                    value={row.cloudPath}
                    onChange={e => handleCloudPathChange(row.id, e.target.value)}
                    className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-1.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                  />
                ) : (
                  <input
                    type="file"
                    accept={supportedExtensions.join(',')}
                    onChange={e => handleFileChange(row.id, e.target.files?.[0])}
                    className="text-sm text-gray-300 file:mr-3 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:bg-gray-700 file:text-gray-200 hover:file:bg-gray-600 cursor-pointer"
                  />
                )}
              </div>

              {/* Mode selector — only rendered when Azure is configured.
                  Without Azure the only valid mode is local_write_ingest, so
                  showing a single-option dropdown is just visual noise. */}
              {hasAzure && (
                <div className="shrink-0">
                  {row.status === 'pending' ? (
                    <select
                      value={row.mode}
                      onChange={e => handleModeChange(row.id, e.target.value)}
                      className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                    >
                      <option value="local_write_ingest">Local file</option>
                      <option value="cloud_upload_ingest">Upload + push to Azure</option>
                      <option value="cloud_fetch_ingest">Fetch from Azure</option>
                    </select>
                  ) : (
                    <span className="text-xs text-gray-500">
                      {row.mode === 'local_write_ingest' ? 'Local' : 'Azure'}
                    </span>
                  )}
                </div>
              )}

              {/* Doc type selector */}
              <div className="shrink-0">
                {row.status === 'pending' ? (
                  state.docTypes.length > 0 ? (
                    <select
                      value={row.docType || state.docTypes[0]}
                      onChange={e => handleDocTypeChange(row.id, e.target.value)}
                      title="doc_type (matched against intent_filters)"
                      className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                    >
                      {state.docTypes.map(dt => (
                        <option key={dt} value={dt}>{dt}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      placeholder="doc_type"
                      value={row.docType}
                      onChange={e => handleDocTypeChange(row.id, e.target.value)}
                      title="Optional doc_type tag (matches intent_filters)"
                      className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5 text-xs text-gray-200 w-32 focus:outline-none focus:border-blue-500"
                    />
                  )
                ) : row.docType ? (
                  <span className="text-xs text-gray-500">{row.docType}</span>
                ) : null}
              </div>

              {/* Retry button for failed rows */}
              {row.status === 'failed' && (
                <button
                  onClick={() => handleRetry(row.id)}
                  title="Retry"
                  className="shrink-0 text-xs text-yellow-400 hover:text-yellow-300 px-2 py-1 rounded-lg border border-yellow-700 hover:border-yellow-600 transition-colors"
                >
                  Retry
                </button>
              )}

              {/* Remove button — hidden for successfully ingested rows */}
              {row.status !== 'ingested' && (
                <button
                  onClick={() => handleRemoveRow(row.id)}
                  disabled={row.status !== 'pending' && row.status !== 'failed'}
                  title="Remove"
                  className="shrink-0 text-gray-500 hover:text-red-400 disabled:opacity-30 disabled:cursor-not-allowed text-lg leading-none transition-colors"
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add file + Upload actions */}
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handleAddRow}
          disabled={state.rows.length >= maxFiles}
          className="text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300 px-4 py-2 rounded-xl border border-gray-700 transition-colors"
        >
          + Add File
        </button>
        <button
          onClick={handleSubmit}
          disabled={state.submitting || !hasPending || !state.servicesReady}
          className="text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-2 rounded-xl font-medium transition-colors"
        >
          {!state.servicesReady ? 'Waiting for services…' : state.submitting ? 'Uploading…' : hasPending ? `Upload & Ingest (${pendingRows.length})` : 'Upload & Ingest'}
        </button>
      </div>

      {/* Validation / submission error */}
      {state.submitError && (
        <div className="mb-4 px-4 py-3 bg-red-900/40 border border-red-700 rounded-xl text-sm text-red-300">
          {state.submitError}
        </div>
      )}

      {/* Footer nav */}
      <div className="flex items-center justify-between pt-4 border-t border-gray-800 mt-4">
        <button
          onClick={onBack}
          className="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-xl transition-colors"
        >
          ← Back
        </button>
        <div className="flex gap-2">
          <button
            onClick={() => onNext({})}
            className="text-sm text-gray-400 hover:text-gray-200 px-4 py-2 rounded-xl transition-colors"
          >
            Skip
          </button>
          {allTerminal && (
            <a
              href={`http://${window.location.hostname}:8005`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-xl font-medium transition-colors inline-flex items-center gap-1"
            >
              Open Agent Chat ↗
            </a>
          )}
          <button
            onClick={() => onNext({})}
            disabled={state.rows.length > 0 && !allTerminal}
            className="text-sm bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-2 rounded-xl font-medium transition-colors"
          >
            Done →
          </button>
        </div>
      </div>
    </div>
  )
}
