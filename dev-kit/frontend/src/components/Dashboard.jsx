// dev-kit/frontend/src/components/Dashboard.jsx
import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { BLOCKS, BLOCK_LABELS, BLOCK_DESC, STATUS_COLORS } from '../constants'
import StatusBadge from './shared/StatusBadge'
import ThemeToggle from './shared/ThemeToggle'

function HealthBanner({ configs, onDeploy }) {
  const counts = { complete: 0, incomplete: 0 }
  configs.forEach(c => { counts[c.status] = (counts[c.status] || 0) + 1 })
  const total = BLOCKS.length
  const allComplete = counts.complete === total

  return (
    <div className={`rounded-xl border px-4 py-3 mb-6 flex items-center justify-between ${
      allComplete ? 'border-green-700 bg-green-950/40' : 'border-gray-700 bg-gray-900'
    }`}>
      <div className="flex items-center gap-3">
        <span className="text-xl">{allComplete ? '✅' : '🔧'}</span>
        <div>
          <p className="text-sm font-medium">
            {allComplete ? 'All configs complete — ready to deploy' : 'Configuration in progress'}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {counts.complete}/{total} complete
            {counts.incomplete > 0 && ` · ${counts.incomplete} incomplete`}
          </p>
        </div>
      </div>
      {allComplete && onDeploy && (
        <button
          onClick={onDeploy}
          className="text-sm bg-green-700 hover:bg-green-600 text-white px-4 py-2 rounded-xl font-medium transition-colors"
        >
          Deploy →
        </button>
      )}
    </div>
  )
}

export default function Dashboard({ slug, onChat, onEditConfig, onBack, onDeploy }) {
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
          <button onClick={onChat} className="text-gray-400 hover:text-white text-sm mb-2 block transition-colors">
            ← Back to Chat
          </button>
          <h1 className="text-2xl font-bold">{project?.name || slug}</h1>
          {project?.description && (
            <p className="text-gray-400 text-sm mt-1">{project.description}</p>
          )}
        </div>
        <div className="flex gap-2 mt-6">
          <button
            onClick={onBack}
            className="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-2 rounded-xl transition-colors"
          >
            ← Projects
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 px-3 py-2 rounded-xl transition-colors"
          >
            {exporting ? 'Exporting…' : '↓ Export ZIP'}
          </button>
          <ThemeToggle className="px-3 py-2" />
        </div>
      </div>

      <HealthBanner configs={configs} onDeploy={onDeploy} />

      {/* Config grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {BLOCKS.map(block => {
          const config = configs.find(c => c.block === block)
          const status = config?.status || 'incomplete'
          return (
            <button
              key={block}
              onClick={() => onEditConfig(block)}
              className={`border rounded-xl p-4 text-left hover:brightness-110 transition-all ${STATUS_COLORS[status]}`}
            >
              <div className="flex items-start justify-between mb-1.5">
                <span className="font-semibold text-sm">{BLOCK_LABELS[block]}</span>
                <StatusBadge status={status} />
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
