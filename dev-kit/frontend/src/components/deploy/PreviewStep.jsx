import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import TabBar from '../shared/TabBar'
import StatusBanner from '../shared/StatusBanner'

export default function PreviewStep({ slug, data }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState(null)

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
      if (result.services?.length > 0) {
        setActiveTab(result.services[0].name)
      } else if (result.compose) {
        setActiveTab('compose')
      }
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
  const tabs = isDocker
    ? [{ key: 'compose', label: 'docker-compose.yml' }]
    : (preview?.services || []).map(s => ({ key: s.name, label: s.name }))

  const activeContent = isDocker
    ? preview?.compose || ''
    : preview?.services?.find(s => s.name === activeTab)?.template || ''

  const serviceCount = isDocker ? 14 : (preview?.services?.length || 0)

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deployment Preview</h2>
      <p className="text-sm text-gray-400 mb-4">Review the generated deployment configuration before deploying.</p>

      <StatusBanner
        variant="info"
        title={`${serviceCount} services · ${data.target === 'kubernetes' ? 'Kubernetes (Helm)' : 'Docker Compose'}`}
        subtitle="This is a read-only preview of the rendered deployment templates."
      />

      <TabBar tabs={tabs} activeKey={activeTab} onSelect={setActiveTab} />

      <div className="mt-4 border border-gray-700 rounded-xl overflow-hidden">
        <pre className="p-4 text-xs text-gray-300 font-mono overflow-auto max-h-[500px] bg-gray-900/50 leading-relaxed whitespace-pre-wrap">
          {activeContent || '# No content available'}
        </pre>
      </div>
    </div>
  )
}
