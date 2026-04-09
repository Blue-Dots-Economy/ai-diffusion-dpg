import React, { useEffect, useState, useRef } from 'react'
import { api } from '../../api'
import useYamlEditor from '../../hooks/useYamlEditor'

const DATA_SERVICES = ['redis', 'memgraph']
const OBS_SERVICES = ['otel_collector', 'jaeger', 'prometheus', 'loki', 'grafana']

const SERVICE_LABELS = {
  redis: 'Redis', memgraph: 'Memgraph', otel_collector: 'OTel Collector',
  jaeger: 'Jaeger', prometheus: 'Prometheus', loki: 'Loki', grafana: 'Grafana',
}

function ServiceCard({ slug, name, config }) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [mounted, setMounted] = useState(false)
  const containerRef = useRef(null)
  const yamlContent = typeof config === 'string' ? config : (config?.config || '')
  const { startEdit, cancelEdit, getContent, setReadOnly } = useYamlEditor(
    containerRef, mounted ? yamlContent : null, { readOnly: true }
  )

  useEffect(() => {
    if (expanded) {
      // Delay so the ref div is rendered before the editor initializes
      const t = setTimeout(() => setMounted(true), 50)
      return () => clearTimeout(t)
    } else {
      setMounted(false)
    }
  }, [expanded])

  function handleEdit() {
    setEditing(true)
    startEdit()
  }
  function handleCancel() {
    setEditing(false)
    cancelEdit()
  }
  async function handleSave() {
    setSaving(true)
    try {
      await api.updateDependency(slug, name, getContent())
      setEditing(false)
      setReadOnly(true)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border border-gray-700 rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-800/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className={`text-xs transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
          <span className="text-sm font-medium">{SERVICE_LABELS[name]}</span>
        </div>
        <span className="text-xs text-gray-500">{config?.image || name}</span>
      </button>
      {expanded && (
        <div className="border-t border-gray-700 p-4">
          <div className="flex items-center justify-end gap-2 mb-3">
            {editing ? (
              <>
                <button onClick={handleCancel} className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg transition-colors">Cancel</button>
                <button onClick={handleSave} disabled={saving} className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors">
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </>
            ) : (
              <button onClick={handleEdit} className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg transition-colors">Edit</button>
            )}
          </div>
          <div ref={containerRef} className="border border-gray-700 rounded-lg overflow-hidden min-h-[200px]" />
        </div>
      )}
    </div>
  )
}

export default function DependenciesStep({ slug }) {
  const [deps, setDeps] = useState({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getDependencies(slug).then(data => {
      setDeps(data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [slug])

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading dependencies…</div>
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Infrastructure Dependencies</h2>
      <p className="text-sm text-gray-400 mb-4">Configure the infrastructure services that DPG layers depend on.</p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-medium text-gray-300 mb-3">Data Services</h3>
          <div className="flex flex-col gap-2">
            {DATA_SERVICES.map(s => (
              <ServiceCard key={s} slug={slug} name={s} config={deps[s] || {}} />
            ))}
          </div>
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-300 mb-3">Observability Stack</h3>
          <div className="flex flex-col gap-2">
            {OBS_SERVICES.map(s => (
              <ServiceCard key={s} slug={slug} name={s} config={deps[s] || {}} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
