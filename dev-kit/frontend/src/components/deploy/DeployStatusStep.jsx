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

export default function DeployStatusStep({ slug, data, project, onSuccess, onBack, destroyed = false, onDestroyedChange, autoDeployOnMount = false }) {
  const [status, setStatus] = useState({ services: [], overall: 'deploying' })
  const [deployed, setDeployed] = useState(false)
  const [error, setError] = useState(null)
  const [readyToIngest, setReadyToIngest] = useState(false)
  const [restarting, setRestarting] = useState(new Set())
  const [showDestroyConfirm, setShowDestroyConfirm] = useState(false)
  const [removeVolumes, setRemoveVolumes] = useState(false)
  const [destroying, setDestroying] = useState(false)
  const [redeployMissingDataError, setRedeployMissingDataError] = useState(null)
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

        // overall === 'idle' — only deploy when the user explicitly clicked the
        // Deploy button on step 6 (autoDeployOnMount=true). Direct navigation to
        // step 7 via the step indicator must never trigger an auto-deploy.
        if (!autoDeployOnMount) {
          setStatus({ services: [], overall: 'idle' })
          return
        }

        // User clicked Deploy on step 6 — clear destroyed flag and deploy.
        onDestroyedChange?.(false)
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
        if (result.overall === 'complete') {
          clearInterval(pollRef.current)
          pollRef.current = null
          setError(null)
          setReadyToIngest(true)
        } else if (result.overall === 'failed') {
          clearInterval(pollRef.current)
          pollRef.current = null
          setDestroying(false)
        } else if (result.overall === 'idle') {
          // Destroy completed — backend cleared state
          clearInterval(pollRef.current)
          pollRef.current = null
          setDeployed(false)
          setReadyToIngest(false)
          setDestroying(false)
          setStatus({ services: [], overall: 'idle' })
          onDestroyedChange?.(true)
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

  async function handleRedeploy() {
    // Collect missing fields per step
    const missingByStep = {};

    // Step 4 — preset
    if (!data?.preset) {
      missingByStep[4] = ['Resource preset'];
    }

    // Step 5 — secrets
    const missingSecrets = [];
    if (!data?.secrets?.anthropic_api_key) {
      missingSecrets.push('Anthropic API key');
    }
    const requiredToolSecrets = project?.required_secrets || [];
    for (const field of requiredToolSecrets) {
      if (!data?.secrets?.tool_secrets?.[field.env_var]) {
        missingSecrets.push(field.description || field.env_var);
      }
    }
    const channelSecrets = project?.channel_secrets || [];
    for (const field of channelSecrets) {
      if (field.required && !data?.secrets?.channel_secrets?.[field.env_var]) {
        missingSecrets.push(field.label || field.env_var);
      }
    }
    if (missingSecrets.length > 0) {
      missingByStep[5] = missingSecrets;
    }

    // Step 6 — target
    if (!data?.target) {
      missingByStep[6] = ['Deployment target'];
    }

    if (Object.keys(missingByStep).length > 0) {
      setRedeployMissingDataError(missingByStep);
      return;
    }
    setRedeployMissingDataError(null);
    onDestroyedChange?.(false)
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
      setError(e.message || 'Redeploy failed')
    }
  }

  async function handleDestroy() {
    setShowDestroyConfirm(false)
    setDestroying(true)
    setStatus(prev => ({ ...prev, overall: 'destroying' }))
    try {
      await api.destroyProject(slug, removeVolumes)
      startPolling()
    } catch (e) {
      setStatus(prev => ({ ...prev, overall: 'failed' }))
      setError(e.message || 'Destroy failed')
      setDestroying(false)
    }
  }

  const serviceMap = {}
  status.services.forEach(s => { serviceMap[s.name] = s })

  const failedServices = status.services.filter(s => s.status === 'failed')

  const canDestroy = data?.target === 'docker' &&
    (status.overall === 'complete' || status.overall === 'failed')

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={onBack}
          className="text-sm text-gray-400 hover:text-white transition-colors"
        >
          ← Back
        </button>
        <h2 className="text-lg font-semibold">Deployment Status</h2>
      </div>
      <p className="text-sm text-gray-400 mb-4 -mt-2">
        {status.overall === 'complete'
          ? 'All services deployed successfully!'
          : status.overall === 'failed'
          ? 'One or more services failed. Restart individual services below.'
          : status.overall === 'destroying'
          ? 'Tearing down containers…'
          : destroyed
          ? 'Stack has been destroyed. Deploy again when ready.'
          : 'Deploying services…'}
      </p>

      {error && (
        <StatusBanner
          variant="error"
          title="Deployment Error"
          subtitle={error}
          action={
            <div className="flex gap-2">
              <button onClick={handleRetry} className="text-xs bg-red-800 hover:bg-red-700 text-white px-3 py-1.5 rounded-lg transition-colors">
                Retry
              </button>
              {canDestroy && (
                <button
                  onClick={() => setShowDestroyConfirm(true)}
                  disabled={destroying}
                  className="text-xs bg-gray-800 hover:bg-red-900 disabled:opacity-50 text-gray-300 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Destroy
                </button>
              )}
            </div>
          }
        />
      )}

      {status.overall === 'failed' && !error && failedServices.length > 0 && (
        <StatusBanner
          variant="error"
          title={`${failedServices.length} service${failedServices.length > 1 ? 's' : ''} failed to start`}
          subtitle={`${failedServices.map(s => SERVICE_LABELS[s.name] || s.name).join(', ')} — check the error details below, fix the issue, and redeploy.`}
          action={
            <div className="flex gap-2">
              <button
                onClick={handleRedeploy}
                className="text-xs bg-red-800 hover:bg-red-700 text-white px-3 py-1.5 rounded-lg transition-colors"
              >
                Redeploy
              </button>
              {canDestroy && (
                <button
                  onClick={() => setShowDestroyConfirm(true)}
                  disabled={destroying}
                  className="text-xs bg-gray-800 hover:bg-red-900 disabled:opacity-50 text-gray-300 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Destroy
                </button>
              )}
            </div>
          }
        />
      )}

      {status.overall === 'destroying' && (
        <StatusBanner
          variant="warning"
          title="Destroying Stack"
          subtitle="Stopping and removing all containers. This may take a few seconds…"
        />
      )}

      {redeployMissingDataError && (
        <div className="mt-3 p-3 bg-yellow-900/30 border border-yellow-700 rounded-lg text-yellow-300 text-sm">
          <p className="mb-2 font-medium">Deploy configuration is incomplete. Please re-enter the following:</p>
          <ul className="list-disc list-inside mb-3 space-y-0.5">
            {Object.entries(redeployMissingDataError)
              .sort(([a], [b]) => Number(a) - Number(b))
              .flatMap(([, fields]) => fields)
              .map((field, i) => (
                <li key={i} className="text-yellow-200">{field}</li>
              ))}
          </ul>
          <button
            onClick={() => {
              const earliestStep = Math.min(...Object.keys(redeployMissingDataError).map(Number));
              window.dispatchEvent(new CustomEvent('deploy-wizard-go-to-step', { detail: earliestStep }));
            }}
            className="text-yellow-200 underline text-xs hover:text-white"
          >
            Go Back to Step {Math.min(...Object.keys(redeployMissingDataError).map(Number))}
          </button>
        </div>
      )}

      {destroyed && (
        <StatusBanner
          variant="warning"
          title="Stack Destroyed"
          subtitle="All containers have been stopped and removed."
          action={
            <button
              onClick={handleRedeploy}
              className="text-xs bg-blue-700 hover:bg-blue-600 text-white px-3 py-1.5 rounded-lg transition-colors font-medium"
            >
              Redeploy
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
          <div className="flex justify-between mt-4 mb-2">
            <button
              onClick={() => setShowDestroyConfirm(true)}
              disabled={destroying}
              className="text-sm bg-gray-800 hover:bg-red-900 disabled:opacity-50 text-gray-300 px-4 py-2 rounded-xl transition-colors"
            >
              Destroy Stack
            </button>
            <button
              onClick={() => onSuccessRef.current && onSuccessRef.current()}
              className="text-sm bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-xl font-medium transition-colors"
            >
              Ingest Knowledge Documents →
            </button>
          </div>
        </>
      )}

      {/* Destroy confirmation modal */}
      {showDestroyConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-base font-semibold mb-2">Destroy Stack?</h3>
            <p className="text-sm text-gray-400 mb-4">
              This will run <code className="text-red-400 bg-gray-800 px-1 rounded">docker compose down --remove-orphans</code> and
              stop all containers for this project.
            </p>

            <label className="flex items-start gap-3 cursor-pointer select-none mb-5">
              <input
                type="checkbox"
                checked={removeVolumes}
                onChange={e => setRemoveVolumes(e.target.checked)}
                className="mt-0.5 accent-red-500"
              />
              <span className="text-sm">
                <span className="text-white font-medium">Wipe all data</span>
                <span className="block text-gray-400 text-xs mt-0.5">
                  {removeVolumes
                    ? 'All data will be permanently deleted — knowledge base (ChromaDB), user memory (Memgraph), and uploaded documents. This cannot be undone.'
                    : 'Leave unchecked to keep your knowledge base and user memory. You can redeploy without re-ingesting documents.'}
                </span>
              </span>
            </label>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDestroyConfirm(false)}
                className="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-xl transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDestroy}
                className="text-sm bg-red-700 hover:bg-red-600 text-white px-4 py-2 rounded-xl font-medium transition-colors"
              >
                {removeVolumes ? 'Destroy & Wipe Data' : 'Confirm Destroy'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4">
        {!destroyed && PHASES.map(phase => (
          <div key={phase.name} className="border border-gray-700 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-gray-900 border-b border-gray-700">
              <span className="text-xs font-medium text-gray-300">{phase.name}</span>
            </div>
            <div className="divide-y divide-gray-800/50">
              {phase.services.map(svcName => {
                const svc = serviceMap[svcName] || { status: 'queued' }
                const isRestarting = restarting.has(svcName)
                return (
                  <div key={svcName} className={`px-4 py-2.5${svc.status === 'failed' ? ' border-l-2 border-red-500 bg-red-950/20' : ''}`}>
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

