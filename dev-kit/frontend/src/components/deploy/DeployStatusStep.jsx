import React, { useEffect, useState, useRef } from 'react'
import { api } from '../../api'
import StatusBanner from '../shared/StatusBanner'
import StatusBadge from '../shared/StatusBadge'

const PHASES = [
  { name: 'Data', services: ['redis', 'memgraph'] },
  { name: 'Observability', services: ['jaeger', 'prometheus', 'loki', 'grafana'] },
  { name: 'Telemetry', services: ['otel_collector'] },
  { name: 'DPG Backend', services: ['memory_layer', 'trust_layer', 'action_gateway', 'knowledge_engine'] },
  { name: 'DPG Core', services: ['observability_layer', 'agent_core'] },
  { name: 'DPG Frontend', services: ['reach_layer'] },
]

const STATUS_ICONS = {
  queued: '⏳',
  starting: '🔄',
  running: '✅',
  failed: '❌',
  healthy: '💚',
}

const SERVICE_LABELS = {
  redis: 'Redis', memgraph: 'Memgraph', otel_collector: 'OTel Collector',
  jaeger: 'Jaeger', prometheus: 'Prometheus', loki: 'Loki', grafana: 'Grafana',
  agent_core: 'Agent Core', knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer', trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway', reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

export default function DeployStatusStep({ slug, data }) {
  const [status, setStatus] = useState({ services: [], overall: 'deploying' })
  const [deployed, setDeployed] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => {
    // Start deployment on mount
    const options = {
      target: data.target,
      secrets: data.secrets,
      preset: data.preset,
      resources: data.resources,
      kubeconfig: data.target === 'kubernetes' ? data.kubeconfig : undefined,
    }

    api.executeDeploy(slug, options)
      .then(() => {
        setDeployed(true)
        startPolling()
      })
      .catch(e => setError(e.message || 'Deployment failed to start'))

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  function startPolling() {
    pollRef.current = setInterval(async () => {
      try {
        const result = await api.getDeployStatus(slug)
        setStatus(result)
        if (result.overall === 'complete' || result.overall === 'failed') {
          clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch (e) {
        console.error('Status poll error:', e)
      }
    }, 3000)
  }

  async function handleRetry() {
    setError(null)
    setStatus({ services: [], overall: 'deploying' })
    try {
      await api.executeDeploy(slug, {
        target: data.target,
        secrets: data.secrets,
        preset: data.preset,
        resources: data.resources,
        kubeconfig: data.target === 'kubernetes' ? data.kubeconfig : undefined,
      })
      setDeployed(true)
      startPolling()
    } catch (e) {
      setError(e.message || 'Retry failed')
    }
  }

  const serviceMap = {}
  status.services.forEach(s => { serviceMap[s.name] = s })

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deployment Status</h2>
      <p className="text-sm text-gray-400 mb-4">
        {status.overall === 'complete'
          ? 'All services deployed successfully!'
          : status.overall === 'failed'
          ? 'Deployment encountered errors.'
          : 'Deploying services…'}
      </p>

      {error && (
        <StatusBanner
          variant="error"
          title="Deployment Error"
          subtitle={error}
          action={
            <button onClick={handleRetry} className="text-xs bg-red-800 hover:bg-red-700 text-white px-3 py-1.5 rounded-lg transition-colors">
              Retry
            </button>
          }
        />
      )}

      {status.overall === 'complete' && (
        <StatusBanner
          variant="success"
          title="Deployment Complete"
          subtitle={`All ${status.services.length} services are running. ${data.target === 'docker' ? 'Access the Reach Layer UI at http://localhost:8005' : 'Services deployed to your Kubernetes cluster.'}`}
        />
      )}

      <div className="flex flex-col gap-4">
        {PHASES.map(phase => (
          <div key={phase.name} className="border border-gray-700 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-gray-900 border-b border-gray-700">
              <span className="text-xs font-medium text-gray-300">{phase.name}</span>
            </div>
            <div className="divide-y divide-gray-800/50">
              {phase.services.map(svcName => {
                const svc = serviceMap[svcName] || { status: 'queued' }
                return (
                  <div key={svcName} className="flex items-center justify-between px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="text-sm">{STATUS_ICONS[svc.status] || STATUS_ICONS.queued}</span>
                      <span className="text-sm">{SERVICE_LABELS[svcName] || svcName}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {svc.error && <span className="text-xs text-red-400 max-w-[200px] truncate">{svc.error}</span>}
                      <StatusBadge status={svc.status || 'queued'} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
