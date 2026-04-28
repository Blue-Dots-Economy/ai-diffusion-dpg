// dev-kit/frontend/src/components/deploy/ConfigReviewStep.jsx
import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

const BLOCKS = [
  'agent_core', 'knowledge_engine', 'trust_layer', 'memory_layer',
  'observability_layer', 'action_gateway', 'reach_layer',
]

const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  trust_layer: 'Trust Layer',
  memory_layer: 'Memory Layer',
  observability_layer: 'Observability Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
}

export default function ConfigReviewStep({ slug, onValidationResult }) {
  const [loading, setLoading] = useState(true)
  const [validation, setValidation] = useState(null)
  const [domainConfigs, setDomainConfigs] = useState({})
  const [expandedBlock, setExpandedBlock] = useState(null)
  const [editingBlock, setEditingBlock] = useState(null)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [revalidating, setRevalidating] = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [validationResult, ...configResults] = await Promise.all([
        api.validateDeployConfig(slug),
        ...BLOCKS.map(b => api.getConfig(slug, b).catch(() => ({ content: '' }))),
      ])
      setValidation(validationResult)
      onValidationResult?.(validationResult)
      const configs = {}
      BLOCKS.forEach((b, i) => { configs[b] = configResults[i]?.content || '' })
      setDomainConfigs(configs)
    } catch {
      setValidation(null)
    } finally {
      setLoading(false)
    }
  }, [slug, onValidationResult])

  useEffect(() => { fetchAll() }, [fetchAll])

  const revalidate = useCallback(async () => {
    setRevalidating(true)
    try {
      const result = await api.validateDeployConfig(slug)
      setValidation(result)
      onValidationResult?.(result)
    } finally {
      setRevalidating(false)
    }
  }, [slug, onValidationResult])

  async function handleSave(block) {
    setSaving(true)
    setSaveError('')
    try {
      await api.updateConfig(slug, block, editContent)
      setDomainConfigs(prev => ({ ...prev, [block]: editContent }))
      setEditingBlock(null)
      await revalidate()
    } catch (e) {
      setSaveError(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading configs…</div>
  }

  const blockErrors = validation?.block_errors || {}
  const invariantErrors = validation?.invariant_errors || []
  const mergedConfigs = validation?.merged_configs || {}
  const totalErrors = Object.values(blockErrors).reduce((n, errs) => n + errs.length, 0) + invariantErrors.length

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Config Review</h2>
      <p className="text-sm text-gray-400 mb-4">
        Review and fix the merged configuration for each block. Errors will block deployment.
      </p>

      {totalErrors > 0 ? (
        <div className="mb-4 border border-yellow-700 rounded-xl bg-yellow-950/30 p-3 flex items-center justify-between">
          <p className="text-sm text-yellow-400">
            {totalErrors} error{totalErrors !== 1 ? 's' : ''} found — you can continue but deployment will be blocked until fixed.
          </p>
          <button
            onClick={revalidate}
            disabled={revalidating}
            className="text-xs text-gray-400 hover:text-gray-200 ml-4 shrink-0"
          >
            {revalidating ? 'Checking…' : 'Re-validate'}
          </button>
        </div>
      ) : validation ? (
        <div className="mb-4 flex items-center gap-2 text-xs text-green-400">
          <span>✓</span> All configs valid — ready to deploy.
          <button onClick={revalidate} disabled={revalidating} className="ml-2 text-gray-500 hover:text-gray-300">
            {revalidating ? 'Checking…' : 'Re-validate'}
          </button>
        </div>
      ) : null}

      <div className="space-y-2">
        {BLOCKS.map(block => {
          const errs = blockErrors[block] || []
          const isEditing = editingBlock === block
          const isExpanded = expandedBlock === block

          return (
            <div key={block} className="border border-gray-700 rounded-xl overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 bg-gray-900/50">
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full ${errs.length > 0 ? 'bg-red-400' : 'bg-green-400'}`} />
                  <span className="text-sm font-medium">{BLOCK_LABELS[block]}</span>
                  {errs.length > 0 && (
                    <span className="text-xs bg-red-900/50 text-red-300 px-2 py-0.5 rounded-full">
                      {errs.length} error{errs.length !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      if (isEditing) {
                        setEditingBlock(null)
                        setSaveError('')
                      } else {
                        setEditingBlock(block)
                        setEditContent(domainConfigs[block] || '')
                        setSaveError('')
                        setExpandedBlock(null)
                      }
                    }}
                    className="text-xs text-blue-400 hover:text-blue-300 px-2 py-1 rounded transition-colors"
                  >
                    {isEditing ? 'Cancel' : 'Edit'}
                  </button>
                  {!isEditing && (
                    <button
                      onClick={() => setExpandedBlock(prev => prev === block ? null : block)}
                      className="text-xs text-gray-400 hover:text-gray-200 px-2 py-1 rounded transition-colors"
                    >
                      Merged config {isExpanded ? '▲' : '▼'}
                    </button>
                  )}
                </div>
              </div>

              {errs.length > 0 && !isEditing && (
                <div className="px-4 py-2 border-t border-gray-800 bg-red-950/20">
                  <ul className="space-y-1">
                    {errs.map((e, i) => (
                      <li key={i} className="text-xs text-red-300 font-mono leading-relaxed">• {e}</li>
                    ))}
                  </ul>
                </div>
              )}

              {isEditing && (
                <div className="border-t border-gray-800">
                  <p className="px-4 pt-2 pb-1 text-xs text-gray-500">
                    Editing domain config — DPG defaults are merged on top at runtime.
                  </p>
                  <textarea
                    className="w-full px-4 py-3 text-xs text-gray-200 font-mono bg-gray-950 border-t border-gray-800 resize-y min-h-[200px] focus:outline-none"
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    spellCheck={false}
                  />
                  {saveError && (
                    <p className="px-4 pb-1 text-xs text-red-400">{saveError}</p>
                  )}
                  <div className="flex justify-end gap-2 px-4 pb-3">
                    <button
                      onClick={() => { setEditingBlock(null); setSaveError('') }}
                      className="text-xs text-gray-400 hover:text-gray-200 px-3 py-1.5 rounded"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => handleSave(block)}
                      disabled={saving}
                      className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white px-3 py-1.5 rounded transition-colors"
                    >
                      {saving ? 'Saving…' : 'Save & Re-validate'}
                    </button>
                  </div>
                </div>
              )}

              {isExpanded && !isEditing && (
                <div className="border-t border-gray-800">
                  <div className="px-4 pt-2 pb-1 flex items-center justify-between">
                    <p className="text-xs text-gray-500">
                      Merged config (DPG defaults + domain — what the runtime sees)
                    </p>
                    <p className="text-xs text-gray-600">
                      To change framework defaults, go back to{' '}
                      <button
                        onClick={() => window.dispatchEvent(new CustomEvent('deploy-wizard-go-to-step', { detail: 1 }))}
                        className="text-blue-500 hover:text-blue-400 underline"
                      >
                        Step 1 — DPG Values
                      </button>
                    </p>
                  </div>
                  <pre className="px-4 py-3 text-xs text-gray-300 font-mono overflow-auto max-h-[400px] bg-gray-900/50 leading-relaxed whitespace-pre-wrap">
                    {mergedConfigs[block] || '# No merged config available'}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {invariantErrors.length > 0 && (
        <div className="mt-4 border border-orange-800 rounded-xl bg-orange-950/20 p-4">
          <p className="text-sm font-semibold text-orange-400 mb-2">
            Cross-block errors ({invariantErrors.length})
          </p>
          <ul className="space-y-1">
            {invariantErrors.map((e, i) => (
              <li key={i} className="text-xs text-orange-300 font-mono leading-relaxed">• {e}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
