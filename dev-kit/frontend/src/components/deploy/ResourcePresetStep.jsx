import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import { BLOCK_LABELS } from '../../constants'

const TIER_META = {
  low: { label: 'Low', desc: 'Minimal resources for local development', icon: '🧪' },
  medium: { label: 'Medium', desc: 'Balanced resources for staging/testing', icon: '⚖️' },
  high: { label: 'High', desc: 'Production-grade resources for deployment', icon: '🚀' },
}

export default function ResourcePresetStep({ slug, data, updateData }) {
  const [presets, setPresets] = useState({})
  const [loading, setLoading] = useState(true)
  const [applying, setApplying] = useState(false)
  const selectedTier = data.preset

  useEffect(() => {
    api.getResourcePresets(slug).then(p => {
      setPresets(p)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [slug])

  async function selectPreset(tier) {
    setApplying(true)
    try {
      const resources = await api.applyResourcePreset(slug, tier)
      updateData('preset', tier)
      updateData('resources', resources)
    } catch (e) {
      console.error(e)
    } finally {
      setApplying(false)
    }
  }

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading presets…</div>
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Resource Presets</h2>
      <p className="text-sm text-gray-400 mb-4">Choose a resource tier for the 7 DPG services. Infrastructure services use fixed defaults.</p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        {Object.entries(TIER_META).map(([tier, meta]) => (
          <button
            key={tier}
            onClick={() => selectPreset(tier)}
            disabled={applying}
            className={`border rounded-xl p-4 text-left transition-all ${
              selectedTier === tier
                ? 'border-blue-500 bg-blue-950/30 ring-1 ring-blue-500/50'
                : 'border-gray-700 bg-gray-900 hover:border-gray-600'
            }`}
          >
            <div className="text-2xl mb-2">{meta.icon}</div>
            <div className="font-semibold text-sm mb-1">{meta.label}</div>
            <p className="text-xs text-gray-400">{meta.desc}</p>
          </button>
        ))}
      </div>

      {selectedTier && data.resources && (
        <div className="border border-gray-700 rounded-xl overflow-hidden">
          <div className="px-4 py-2 bg-gray-900 border-b border-gray-700">
            <span className="text-xs font-medium text-gray-300">Resource Summary — {TIER_META[selectedTier].label}</span>
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400">
                <th className="text-left px-4 py-2">Service</th>
                <th className="text-left px-4 py-2">CPU Request</th>
                <th className="text-left px-4 py-2">CPU Limit</th>
                <th className="text-left px-4 py-2">Memory Request</th>
                <th className="text-left px-4 py-2">Memory Limit</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.resources).map(([block, res]) => (
                <tr key={block} className="border-b border-gray-800/50">
                  <td className="px-4 py-2 font-medium">{BLOCK_LABELS[block] || block}</td>
                  <td className="px-4 py-2 text-gray-400">{res?.requests?.cpu || '—'}</td>
                  <td className="px-4 py-2 text-gray-400">{res?.limits?.cpu || '—'}</td>
                  <td className="px-4 py-2 text-gray-400">{res?.requests?.memory || '—'}</td>
                  <td className="px-4 py-2 text-gray-400">{res?.limits?.memory || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
