import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function ImportModal({ onImport, onClose }) {
  const [importable, setImportable] = useState([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listImportableProjects()
      .then(setImportable)
      .catch(() => setImportable([]))
      .finally(() => setLoading(false))
  }, [])

  async function handleImport(slug) {
    setImporting(slug)
    setError(null)
    try {
      const project = await api.importProject(slug)
      onImport(project)
    } catch (err) {
      setError(err.message)
    } finally {
      setImporting(null)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-800">
          <h2 className="text-base font-semibold text-gray-100">Import Existing Config Folder</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl leading-none">&times;</button>
        </div>

        <div className="px-6 py-4 max-h-96 overflow-y-auto">
          {loading && (
            <p className="text-gray-500 text-sm">Loading importable folders…</p>
          )}
          {!loading && importable.length === 0 && (
            <p className="text-gray-500 text-sm">
              No importable folders found. A folder is importable when it contains
              block YAML files but no <code className="text-gray-400">_meta/project.json</code>.
            </p>
          )}
          {!loading && importable.map(item => {
            const hasErrors = Object.keys(item.validation_errors || {}).length > 0
            const blockCount = item.detected_blocks.length
            return (
              <div
                key={item.slug}
                data-testid="importable-row"
                onClick={() => !importing && handleImport(item.slug)}
                className="flex items-center justify-between bg-gray-800 hover:bg-gray-750 border border-gray-700 hover:border-gray-500 rounded-xl px-4 py-3 mb-2 cursor-pointer transition-colors"
              >
                <div>
                  <p className="font-medium text-sm text-gray-100">{item.slug}</p>
                  <p className="text-gray-500 text-xs mt-0.5">
                    {blockCount} {blockCount === 1 ? 'block' : 'blocks'} detected
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4 shrink-0">
                  {hasErrors && (
                    <span className="text-xs text-yellow-400 bg-yellow-950/40 border border-yellow-800 rounded-lg px-2 py-0.5">
                      Validation issues
                    </span>
                  )}
                  {importing === item.slug
                    ? <span className="text-xs text-gray-400">Importing…</span>
                    : <span className="text-xs text-gray-500">Import →</span>
                  }
                </div>
              </div>
            )
          })}
        </div>

        <div className="px-6 pb-5">
          {error && (
            <p className="text-red-400 text-sm bg-red-950/40 border border-red-800 rounded-lg px-3 py-2 mb-3">
              {error}
            </p>
          )}
          <button
            onClick={onClose}
            className="w-full text-sm text-gray-400 hover:text-gray-200 py-2 rounded-xl hover:bg-gray-800 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
