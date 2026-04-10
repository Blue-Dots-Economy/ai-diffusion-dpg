import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import { BLOCK_LABELS } from '../../constants'

const TIER_META = {
  low: { label: 'Low', desc: 'Minimal resources for local development', icon: '🧪' },
  medium: { label: 'Medium', desc: 'Balanced resources for staging/testing', icon: '⚖️' },
  high: { label: 'High', desc: 'Production-grade resources for deployment', icon: '🚀' },
}

const INFRA_LABELS = {
  redis: 'Redis',
  memgraph: 'Memgraph',
  otel_collector: 'OTel Collector',
  jaeger: 'Jaeger',
  prometheus: 'Prometheus',
  loki: 'Loki',
  grafana: 'Grafana',
}

/** Parse a K8s resource string to a numeric value for summation. */
function parseCpu(val) {
  if (!val) return 0
  const s = String(val)
  if (s.endsWith('m')) return parseInt(s, 10)
  return parseFloat(s) * 1000 // e.g. "1" → 1000m
}

function parseMem(val) {
  if (!val) return 0
  const s = String(val)
  if (s.endsWith('Gi')) return parseFloat(s) * 1024
  if (s.endsWith('Mi')) return parseFloat(s)
  if (s.endsWith('Ki')) return parseFloat(s) / 1024
  return parseFloat(s) // assume Mi
}

function formatCpu(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}` : `${m}m`
}

function formatMem(mi) {
  return mi >= 1024 ? `${(mi / 1024).toFixed(1)}Gi` : `${mi}Mi`
}

export default function ResourcePresetStep({ slug, data, updateData }) {
  const [presets, setPresets] = useState({})
  const [loading, setLoading] = useState(true)
  const [applying, setApplying] = useState(false)
  const [infraResources, setInfraResources] = useState({})
  const selectedTier = data.preset

  useEffect(() => {
    Promise.all([
      api.getResourcePresets(slug),
      api.getDependencies(slug),
    ]).then(([p, deps]) => {
      setPresets(p)
      // Extract resources from infra service defaults
      const infra = {}
      for (const [name, svc] of Object.entries(deps)) {
        const res = svc?.defaults?.resources
        if (res) {
          infra[name] = res
        }
      }
      setInfraResources(infra)
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

  // Compute totals across DPG + infra
  function computeTotals() {
    let cpuReq = 0, cpuLim = 0, memReq = 0, memLim = 0
    // DPG resources
    if (data.resources) {
      for (const res of Object.values(data.resources)) {
        cpuReq += parseCpu(res?.requests?.cpu)
        cpuLim += parseCpu(res?.limits?.cpu)
        memReq += parseMem(res?.requests?.memory)
        memLim += parseMem(res?.limits?.memory)
      }
    }
    // Infra resources
    for (const res of Object.values(infraResources)) {
      cpuReq += parseCpu(res?.requests?.cpu)
      cpuLim += parseCpu(res?.limits?.cpu)
      memReq += parseMem(res?.requests?.memory)
      memLim += parseMem(res?.limits?.memory)
    }
    return {
      cpuReq: formatCpu(cpuReq),
      cpuLim: formatCpu(cpuLim),
      memReq: formatMem(memReq),
      memLim: formatMem(memLim),
    }
  }

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading presets…</div>
  }

  const totals = (selectedTier && data.resources) ? computeTotals() : null

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
        <>
          {/* DPG Services Table */}
          <div className="border border-gray-700 rounded-xl overflow-hidden mb-4">
            <div className="px-4 py-2 bg-gray-900 border-b border-gray-700">
              <span className="text-xs font-medium text-gray-300">DPG Services — {TIER_META[selectedTier].label}</span>
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

          {/* Infrastructure Services Table */}
          {Object.keys(infraResources).length > 0 && (
            <div className="border border-gray-700 rounded-xl overflow-hidden mb-4">
              <div className="px-4 py-2 bg-gray-900 border-b border-gray-700">
                <span className="text-xs font-medium text-gray-300">Infrastructure Services — Fixed Defaults</span>
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
                  {Object.entries(infraResources).map(([name, res]) => (
                    <tr key={name} className="border-b border-gray-800/50">
                      <td className="px-4 py-2 font-medium">{INFRA_LABELS[name] || name}</td>
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

          {/* Total Resources */}
          {totals && (
            <div className="border border-blue-800/50 rounded-xl overflow-hidden bg-blue-950/20">
              <div className="px-4 py-2 border-b border-blue-800/50">
                <span className="text-xs font-medium text-blue-300">Total Resources — All 14 Services</span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-blue-800/30 text-gray-400">
                    <th className="text-left px-4 py-2">Metric</th>
                    <th className="text-left px-4 py-2">Requests</th>
                    <th className="text-left px-4 py-2">Limits</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-blue-800/30">
                    <td className="px-4 py-2 font-medium text-blue-200">CPU</td>
                    <td className="px-4 py-2 text-blue-300 font-mono">{totals.cpuReq}</td>
                    <td className="px-4 py-2 text-blue-300 font-mono">{totals.cpuLim}</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2 font-medium text-blue-200">Memory</td>
                    <td className="px-4 py-2 text-blue-300 font-mono">{totals.memReq}</td>
                    <td className="px-4 py-2 text-blue-300 font-mono">{totals.memLim}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
