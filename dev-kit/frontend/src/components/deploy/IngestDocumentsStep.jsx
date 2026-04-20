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
    dispatch({ type: 'UPDATE_ROW', id, patch: { mode, file: null, cloudPath: '', filename: '' } })
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

  const validateBatch = () => {
    if (state.rows.length === 0) return 'Add at least one file.'
    const names = state.rows.map(r => r.filename).filter(Boolean)
    if (new Set(names).size !== names.length) return 'Duplicate filenames in batch.'
    for (const row of state.rows) {
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

    const formData = new FormData()
    formData.append('metadata', JSON.stringify(
      state.rows.map(row => ({
        filename: row.filename,
        mode: row.mode,
        ...(row.cloudPath ? { cloud_path: row.cloudPath } : {}),
      }))
    ))
    for (const row of state.rows) {
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

  const _statusLabel = (row) => {
    switch (row.status) {
      case 'queued': return row.queuePosition ? `Queued (position ${row.queuePosition})` : 'Queued'
      case 'ingesting': return 'Ingesting\u2026'
      case 'ingested': return `\u2713 Ingested \u2014 ${row.chunksAdded} chunks`
      case 'failed': return `\u2717 Failed: ${row.error || 'unknown error'}`
      default: return ''
    }
  }

  const allTerminal = state.rows.length > 0 &&
    state.rows.every(r => r.status === 'ingested' || r.status === 'failed')

  if (!state.config) {
    return <div className="ingest-documents-step"><p>Loading\u2026</p></div>
  }

  return (
    <div className="ingest-documents-step">
      <h2>Ingest Knowledge Documents</h2>
      <p>Upload your documents into the Knowledge Engine vector store.</p>
      <p className="upload-limits">Max {maxFiles} files &middot; Max {maxSizeMb} MB per file</p>

      {state.rows.length > 0 && (
        <table className="file-rows">
          <tbody>
            {state.rows.map(row => (
              <tr key={row.id} className={`row-${row.status}`}>
                <td className="filename-cell">
                  {row.status !== 'pending' ? (
                    <span>{row.filename} <span className="status-label">{_statusLabel(row)}</span></span>
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
                      onChange={e => handleFileChange(row.id, e.target.files?.[0])}
                    />
                  )}
                </td>
                <td className="mode-cell">
                  {row.status === 'pending' ? (
                    <select value={row.mode} onChange={e => handleModeChange(row.id, e.target.value)}>
                      <option value="local_write_ingest">Local file</option>
                      {hasAzure && <option value="cloud_upload_ingest">Upload + push to Azure</option>}
                      {hasAzure && <option value="cloud_fetch_ingest">Fetch from Azure</option>}
                    </select>
                  ) : (
                    <span>{row.mode === 'local_write_ingest' ? 'Local' : 'Azure'}</span>
                  )}
                </td>
                <td>
                  <button
                    title="Remove"
                    onClick={() => handleRemoveRow(row.id)}
                    disabled={row.status !== 'pending'}
                  >&times;</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row-actions">
        <button onClick={handleAddRow} disabled={state.rows.length >= maxFiles}>
          + Add File
        </button>
        <button
          onClick={handleSubmit}
          disabled={state.submitting || state.rows.length === 0}
          className="primary-btn"
        >
          {state.submitting ? 'Uploading\u2026' : 'Upload & Ingest'}
        </button>
      </div>

      {state.submitError && <p className="error-msg">{state.submitError}</p>}

      <div className="step-footer">
        <button onClick={onBack}>&larr; Back</button>
        <button onClick={() => onNext({})}>Skip</button>
        <button
          onClick={() => onNext({})}
          disabled={state.rows.length > 0 && !allTerminal}
          className="primary-btn"
        >
          Done &rarr;
        </button>
      </div>
    </div>
  )
}
