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
  rows: [],
  submitting: false,
  submitError: null,
}

function _rowId() {
  return Math.random().toString(36).slice(2)
}

function _makeRow() {
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
  const hasAzure = Boolean(project?.azure_storage)

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
    return () => {
      Object.values(pollingRef.current).forEach(clearInterval)
      Object.values(timeoutRef.current).forEach(clearTimeout)
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Row management
  // ---------------------------------------------------------------------------

  const handleAddRow = () => {
    if (state.rows.length >= maxFiles) return
    dispatch({ type: 'ADD_ROW', row: _makeRow() })
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

              {/* Mode selector */}
              <div className="shrink-0">
                {row.status === 'pending' ? (
                  <select
                    value={row.mode}
                    onChange={e => handleModeChange(row.id, e.target.value)}
                    className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                  >
                    <option value="local_write_ingest">Local file</option>
                    {hasAzure && <option value="cloud_upload_ingest">Upload + push to Azure</option>}
                    {hasAzure && <option value="cloud_fetch_ingest">Fetch from Azure</option>}
                  </select>
                ) : (
                  <span className="text-xs text-gray-500">
                    {row.mode === 'local_write_ingest' ? 'Local' : 'Azure'}
                  </span>
                )}
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
          disabled={state.submitting || !hasPending}
          className="text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-2 rounded-xl font-medium transition-colors"
        >
          {state.submitting ? 'Uploading…' : hasPending ? `Upload & Ingest (${pendingRows.length})` : 'Upload & Ingest'}
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
