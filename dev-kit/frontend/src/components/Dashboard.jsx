import React, { useEffect, useState } from 'react'
import { api } from '../api'

const STATUS_COLORS = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

const BLOCKS = ['agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer', 'action_gateway', 'reach_layer', 'learning_layer']
const BLOCK_LABELS = {
  agent_core: 'Agent Core', knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer', trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway', reach_layer: 'Reach Layer',
  learning_layer: 'Learning Layer',
}

export default function Dashboard({ slug, onChat, onEditConfig, onBack }) {
  const [configs, setConfigs] = useState([])
  const [project, setProject] = useState(null)

  useEffect(() => {
    api.getConfigs(slug).then(setConfigs).catch(() => {})
    api.getProject(slug).then(setProject).catch(() => {})
  }, [slug])

  return (
    <div className="min-h-screen px-6 py-8 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <button onClick={onBack} className="text-gray-400 hover:text-white text-sm mb-2 block">&larr; Projects</button>
          <h1 className="text-2xl font-bold">{project?.name || slug}</h1>
          <p className="text-gray-400 text-sm">{project?.description}</p>
        </div>
        <button
          onClick={onChat}
          className="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded-xl text-sm font-medium transition-colors"
        >
          Continue Configuration
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {BLOCKS.map((block) => {
          const config = configs.find((c) => c.block === block)
          const status = config?.status || 'pending'
          return (
            <button
              key={block}
              onClick={() => onEditConfig(block)}
              className={`border rounded-xl p-4 text-left hover:opacity-90 transition-opacity ${STATUS_COLORS[status] || STATUS_COLORS.pending}`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-sm">{BLOCK_LABELS[block]}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS[status]}`}>{status}</span>
              </div>
              <p className="text-xs opacity-70 truncate">
                {config?.content ? 'Click to view or edit' : 'Not yet configured'}
              </p>
            </button>
          )
        })}
      </div>
    </div>
  )
}
