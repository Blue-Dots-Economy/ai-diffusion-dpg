import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import StatusBanner from '../shared/StatusBanner'

const INFRA_LABELS = {
  redis: 'Redis',
  memgraph: 'Memgraph',
  otel_collector: 'OTel Collector',
  jaeger: 'Jaeger',
  prometheus: 'Prometheus',
  loki: 'Loki',
  grafana: 'Grafana',
}

const DPG_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

const ALL_LABELS = { ...INFRA_LABELS, ...DPG_LABELS }

export default function PreviewStep({ slug, data }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    const options = {
      target: data.target,
      secrets: data.secrets,
      preset: data.preset,
      resources: data.resources,
      kubeconfig: data.target === 'kubernetes' ? data.kubeconfig : undefined,
    }
    api.getDeployPreview(slug, options).then(result => {
      setPreview(result)
      setLoading(false)
    }).catch(e => {
      setError(e.message || 'Failed to generate preview')
      setLoading(false)
    })
  }, [slug])

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Generating deployment preview…</div>
  }

  if (error) {
    return <StatusBanner variant="error" title="Preview failed" subtitle={error} />
  }

  const isDocker = data.target === 'docker'
  const previewData = preview?.preview || {}
  const serviceKeys = Object.keys(previewData)

  // Group services for K8s view
  const infraKeys = serviceKeys.filter(k => k in INFRA_LABELS)
  const dpgKeys = serviceKeys.filter(k => k in DPG_LABELS)
  // Catch any unlabeled keys
  const otherKeys = serviceKeys.filter(k => !(k in ALL_LABELS))

  function toggleExpand(key) {
    setExpanded(prev => prev === key ? null : key)
  }

  if (isDocker) {
    // Docker: single compose file view
    const content = previewData['docker-compose.yml'] || Object.values(previewData)[0] || ''
    return (
      <div>
        <h2 className="text-lg font-semibold mb-1">Deployment Preview</h2>
        <p className="text-sm text-gray-400 mb-4">Review the generated Docker Compose configuration.</p>
        <StatusBanner
          variant="info"
          title="14 services · Docker Compose"
          subtitle="Read-only preview of the rendered deployment template."
        />
        <div className="mt-4 border border-gray-700 rounded-xl overflow-hidden">
          <pre className="p-4 text-xs text-gray-300 font-mono overflow-auto max-h-[500px] bg-gray-900/50 leading-relaxed whitespace-pre-wrap">
            {content || '# No content available'}
          </pre>
        </div>
      </div>
    )
  }

  // Kubernetes: expandable per-service sections
  function renderServiceGroup(title, keys) {
    if (keys.length === 0) return null
    return (
      <div className="mb-4">
        <h3 className="text-sm font-medium text-gray-400 mb-2 uppercase tracking-wide">{title}</h3>
        <div className="space-y-1">
          {keys.map(key => {
            const isOpen = expanded === key
            const content = previewData[key] || ''
            const hasError = content.startsWith('# Error:')
            return (
              <div key={key} className="border border-gray-700 rounded-lg overflow-hidden">
                <button
                  onClick={() => toggleExpand(key)}
                  className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-gray-800/50 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${hasError ? 'bg-red-400' : 'bg-green-400'}`} />
                    <span className="text-sm font-medium">{ALL_LABELS[key] || key}</span>
                  </div>
                  <span className="text-xs text-gray-500">{isOpen ? '▲' : '▼'}</span>
                </button>
                {isOpen && (
                  <pre className="px-4 py-3 text-xs text-gray-300 font-mono overflow-auto max-h-[400px] bg-gray-900/50 leading-relaxed whitespace-pre-wrap border-t border-gray-700">
                    {content || '# No content available'}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deployment Preview</h2>
      <p className="text-sm text-gray-400 mb-4">Click a service to view its rendered Helm template.</p>

      <StatusBanner
        variant="info"
        title={`${serviceKeys.length} services · Kubernetes (Helm)`}
        subtitle="Read-only preview of helm template output for each chart."
      />

      <div className="mt-4">
        {renderServiceGroup('Infrastructure Services', infraKeys)}
        {renderServiceGroup('DPG Application Services', dpgKeys)}
        {renderServiceGroup('Other', otherKeys)}
      </div>
    </div>
  )
}
