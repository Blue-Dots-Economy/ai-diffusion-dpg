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
      {hasStale && (
        <span className="text-xs text-red-400 bg-red-950 border border-red-800 px-2 py-1 rounded-lg">
          Fix stale configs
        </span>
      )}
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
            className="text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 px-3 py-2 rounded-xl transition-colors"
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
