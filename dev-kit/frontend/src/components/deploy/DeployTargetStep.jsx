import React, { useState, useCallback } from 'react'
import { api } from '../../api'
import StatusBanner from '../shared/StatusBanner'

export default function DeployTargetStep({ slug, data, updateData }) {
  const [inputMode, setInputMode] = useState('upload') // 'upload' | 'paste'
  const [pasteContent, setPasteContent] = useState('')
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState(null)
  const target = data.target
  const clusterInfo = data.clusterInfo

  function selectTarget(t) {
    updateData('target', t)
    if (t === 'docker') {
      updateData('kubeconfig', '')
      updateData('clusterInfo', null)
    }
  }

  const handleFileDrop = useCallback((e) => {
    e.preventDefault()
    const file = e.dataTransfer?.files[0] || e.target?.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const content = ev.target.result
      updateData('kubeconfig', content)
      validateKC(content)
    }
    reader.readAsText(file)
  }, [slug])

  async function validateKC(content) {
    setValidating(true)
    setError(null)
    try {
      const result = await api.validateKubeconfig(slug, content)
      updateData('clusterInfo', result)
    } catch (e) {
      setError(e.message || 'Validation failed')
    } finally {
      setValidating(false)
    }
  }

  function handlePasteSubmit() {
    updateData('kubeconfig', pasteContent)
    validateKC(pasteContent)
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deploy Target</h2>
      <p className="text-sm text-gray-400 mb-4">Choose where to deploy your DPG stack.</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
        <button
          onClick={() => selectTarget('docker')}
          className={`border rounded-xl p-5 text-left transition-all ${
            target === 'docker'
              ? 'border-blue-500 bg-blue-950/30 ring-1 ring-blue-500/50'
              : 'border-gray-700 bg-gray-900 hover:border-gray-600'
          }`}
        >
          <div className="text-2xl mb-2">🐳</div>
          <div className="font-semibold text-sm mb-1">Docker Compose</div>
          <p className="text-xs text-gray-400">Deploy locally using Docker Compose. Best for development and testing.</p>
        </button>
        <button
          onClick={() => selectTarget('kubernetes')}
          className={`border rounded-xl p-5 text-left transition-all ${
            target === 'kubernetes'
              ? 'border-blue-500 bg-blue-950/30 ring-1 ring-blue-500/50'
              : 'border-gray-700 bg-gray-900 hover:border-gray-600'
          }`}
        >
          <div className="text-2xl mb-2">☸️</div>
          <div className="font-semibold text-sm mb-1">Kubernetes</div>
          <p className="text-xs text-gray-400">Deploy to a Kubernetes cluster using Helm charts. Requires a valid kubeconfig.</p>
        </button>
      </div>

      {target === 'docker' && (
        <StatusBanner variant="success" title="Docker Compose selected" subtitle="A docker-compose.yml will be generated with all 14 services configured." />
      )}

      {target === 'kubernetes' && (
        <div className="border border-gray-700 rounded-xl p-4 bg-gray-900">
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setInputMode('upload')}
              className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                inputMode === 'upload' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
              }`}
            >Upload File</button>
            <button
              onClick={() => setInputMode('paste')}
              className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                inputMode === 'paste' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
              }`}
            >Paste</button>
          </div>

          {inputMode === 'upload' && (
            <div
              onDragOver={e => e.preventDefault()}
              onDrop={handleFileDrop}
              className="border-2 border-dashed border-gray-600 rounded-xl p-8 text-center hover:border-gray-500 transition-colors cursor-pointer"
              onClick={() => document.getElementById('kc-file-input')?.click()}
            >
              <input id="kc-file-input" type="file" accept=".yaml,.yml,.conf" className="hidden" onChange={handleFileDrop} />
              <p className="text-sm text-gray-400 mb-1">Drag & drop your kubeconfig file here</p>
              <p className="text-xs text-gray-500">or click to browse</p>
            </div>
          )}

          {inputMode === 'paste' && (
            <div>
              <textarea
                value={pasteContent}
                onChange={e => setPasteContent(e.target.value)}
                placeholder="Paste your kubeconfig YAML here..."
                rows={10}
                className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 font-mono resize-y transition-colors"
              />
              <button
                onClick={handlePasteSubmit}
                disabled={!pasteContent.trim() || validating}
                className="mt-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg transition-colors"
              >
                {validating ? 'Validating…' : 'Validate Kubeconfig'}
              </button>
            </div>
          )}

          {error && (
            <div className="mt-3">
              <StatusBanner variant="error" title="Validation failed" subtitle={error} />
            </div>
          )}

          {clusterInfo && (
            <div className="mt-3 border border-gray-700 rounded-lg p-3 bg-gray-800/50">
              <div className="text-xs font-medium text-green-400 mb-2">✓ Cluster Connected</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-gray-500">Cluster:</span> <span className="text-gray-200">{clusterInfo.cluster_name}</span></div>
                <div><span className="text-gray-500">Server:</span> <span className="text-gray-200">{clusterInfo.server}</span></div>
                <div><span className="text-gray-500">Context:</span> <span className="text-gray-200">{clusterInfo.current_context}</span></div>
                {clusterInfo.node_count !== undefined && (
                  <div><span className="text-gray-500">Nodes:</span> <span className="text-gray-200">{clusterInfo.node_count}</span></div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
