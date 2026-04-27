import React, { useEffect, useState, useRef } from 'react'
import { api } from '../../api'
import StatusBanner from '../shared/StatusBanner'
import StatusBadge from '../shared/StatusBadge'
import { buildSecretsPayload } from '../../crypto.js'

const PHASES = [
  { name: 'Data', services: ['redis', 'memgraph'] },
  { name: 'Observability', services: ['jaeger', 'prometheus', 'loki', 'grafana'] },
  { name: 'Telemetry', services: ['otel_collector'] },
  { name: 'DPG Backend', services: ['memory_layer', 'trust_layer', 'action_gateway', 'knowledge_engine'] },
  { name: 'DPG Core', services: ['observability_layer', 'agent_core'] },
  { name: 'DPG Frontend', services: ['reach_layer'] },
]

const STATUS_ICONS = {
  queued:   { icon: '○', cls: 'text-gray-500' },
  starting: { icon: '◌', cls: 'text-blue-400' },
  running:  { icon: '◑', cls: 'text-yellow-400' },
  healthy:  { icon: '✓', cls: 'text-green-400' },
  failed:   { icon: '✗', cls: 'text-red-400' },
}

const SERVICE_LABELS = {
  redis: 'Redis', memgraph: 'Memgraph', otel_collector: 'OTel Collector',
  jaeger: 'Jaeger', prometheus: 'Prometheus', loki: 'Loki', grafana: 'Grafana',
  agent_core: 'Agent Core', knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer', trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway', reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

function ServiceIcon({ status }) {
  const s = STATUS_ICONS[status] || STATUS_ICONS.queued
  return <span className={`text-sm font-mono ${s.cls}`}>{s.icon}</span>
}

export default function DeployStatusStep({ slug, data, onSuccess }) {
  const [status, setStatus] = useState({ services: [], overall: 'deploying' })
  const [deployed, setDeployed] = useState(false)
  const [error, setError] = useState(null)
  const [readyToIngest, setReadyToIngest] = useState(false)
  const [restarting, setRestarting] = useState(new Set())
  const pollRef = useRef(null)
  const onSuccessRef = useRef(onSuccess)
  onSuccessRef.current = onSuccess

  useEffect(() => {
    // Probe before deploying. Mounting this step must not trigger a fresh
    // `docker compose up` if a deployment already exists for this slug.
    // Only kick a deploy when overall === 'idle'.
    let cancelled = false
    async function init() {
      try {
        const initial = await api.getDeployStatus(slug)
        if (cancelled) return
        setStatus(initial)

        if (initial.overall === 'complete') {
          setDeployed(true)
          setReadyToIngest(true)
          return
        }
        if (initial.overall === 'deploying' || initial.overall === 'failed') {
          setDeployed(true)
          startPolling()
          return
        }

        // overall === 'idle' (or unknown) — first deploy for this slug
        const options = {
          target: data.target,
          ...(await buildSecretsPayload(data.secrets)),
          preset: data.preset,
          resources: data.resources,
          kubeconfig: data.target === 'kubernetes' ? data.kubeconfig : undefined,
        }
        await api.executeDeploy(slug, options)
        if (cancelled) return
        setDeployed(true)
        startPolling()
      } catch (e) {
        if (!cancelled) setError(e.message || 'Deployment failed to start')
      }
    }
    init()

    return () => {
      cancelled = true
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  function startPolling() {
    if (pollRef.current) return // already polling
    pollRef.current = setInterval(async () => {
      try {
        const result = await api.getDeployStatus(slug)
        setStatus(result)
        if (result.overall === 'complete' || result.overall === 'failed') {
          clearInterval(pollRef.current)
          pollRef.current = null
          if (result.overall === 'complete') setReadyToIngest(true)
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
        ...(await buildSecretsPayload(data.secrets)),
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

  async function handleServiceRestart(svcName) {
    setRestarting(prev => new Set(prev).add(svcName))
    setStatus(prev => ({
      ...prev,
      overall: prev.overall === 'failed' ? 'deploying' : prev.overall,
      services: prev.services.map(s =>
        s.name === svcName ? { ...s, status: 'starting', error: '' } : s
      ),
    }))
    try {
      await api.restartService(slug, svcName)
      startPolling()
    } catch (e) {
      setStatus(prev => ({
        ...prev,
        overall: 'failed',
        services: prev.services.map(s =>
          s.name === svcName ? { ...s, status: 'failed', error: e.message || 'Restart failed' } : s
        ),
      }))
    } finally {
      setRestarting(prev => { const n = new Set(prev); n.delete(svcName); return n })
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
          ? 'One or more services failed. Restart individual services below.'
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
        <>
          <StatusBanner
            variant="success"
            title="Deployment Complete"
            subtitle={`All ${status.services.length} services are running. ${data.target === 'docker' ? 'Access the Reach Layer UI at http://localhost:8005' : 'Services deployed to your Kubernetes cluster.'}`}
          />
          <div className="flex justify-end mt-4 mb-2">
            <button
              onClick={() => onSuccessRef.current && onSuccessRef.current()}
              className="text-sm bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-xl font-medium transition-colors"
            >
              Ingest Knowledge Documents →
            </button>
          </div>
        </>
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
                const isRestarting = restarting.has(svcName)
                return (
                  <div key={svcName} className="px-4 py-2.5">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <ServiceIcon status={svc.status || 'queued'} />
                        <span className="text-sm">{SERVICE_LABELS[svcName] || svcName}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {svc.status === 'failed' && data.target === 'docker' && (
                          <button
                            onClick={() => handleServiceRestart(svcName)}
                            disabled={isRestarting}
                            className="text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 px-2 py-1 rounded transition-colors"
                          >
                            {isRestarting ? '…' : '↺ Restart'}
                          </button>
                        )}
                        <StatusBadge status={svc.status || 'queued'} />
                      </div>
                    </div>
                    {svc.error && (
                      <details className="mt-1 ml-5">
                        <summary className="text-xs text-red-400 cursor-pointer select-none">Show error</summary>
                        <pre className="mt-1 text-xs text-red-300 bg-red-950/40 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words max-h-32 overflow-y-auto">{svc.error}</pre>
                      </details>
                    )}
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
