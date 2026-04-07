// dev-kit/frontend/src/components/YamlPanel.jsx
import React, { useEffect, useRef, useState } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const BLOCKS = ['agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer', 'action_gateway', 'reach_layer', 'observability_layer']
const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability',
}
const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}
const STATUS_DOT = {
  complete: 'bg-green-400',
  draft: 'bg-yellow-400',
  stale: 'bg-red-400',
  pending: 'bg-gray-600',
}

export default function YamlPanel({ slug, configs, onSaved }) {
  const [activeBlock, setActiveBlock] = useState('agent_core')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)
  const [copied, setCopied] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')

  const activeConfig = configs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }

  useEffect(() => {
    if (!editorRef.current) return
    if (editing) return
    viewRef.current?.destroy()
    const state = EditorState.create({
      doc: activeConfig.content || '',
      extensions: [basicSetup, yaml(), oneDark, EditorView.editable.of(false)],
    })
    viewRef.current = new EditorView({ state, parent: editorRef.current })
    return () => { viewRef.current?.destroy(); viewRef.current = null }
  }, [activeBlock, configs, editing])

  useEffect(() => {
    setDescriptions({})
    api.getSchemaDescriptions(activeBlock)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [activeBlock])

  function handleTabChange(block) {
    if (editing) {
      if (!window.confirm('You have unsaved changes. Switch block and discard them?')) return
      cancelEdit()
    }
    setActiveBlock(block)
    setValidationErrors([])
    setSaveMsg(null)
  }

  function startEdit() {
    originalRef.current = viewRef.current?.state.doc.toString() || ''
    viewRef.current?.dispatch({
      effects: EditorView.editable.reconfigure(EditorView.editable.of(true)),
    })
    setEditing(true)
    setSaveMsg(null)
    setValidationErrors([])
  }

  function cancelEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: { from: 0, to: viewRef.current.state.doc.length, insert: originalRef.current },
      effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
    })
    setEditing(false)
    setValidationErrors([])
    setSaveMsg(null)
  }

  async function handleSave() {
    if (!viewRef.current) return
    setSaving(true)
    setValidationErrors([])
    setSaveMsg(null)
    const content = viewRef.current.state.doc.toString()
    try {
      const result = await api.updateConfig(slug, activeBlock, content)
      viewRef.current.dispatch({
        effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
      })
      setEditing(false)
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved successfully.')
      onSaved?.(activeBlock, { block: activeBlock, status: result.status, content })
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleCopy() {
    const content = viewRef.current?.state.doc.toString() || ''
    await navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="flex flex-col h-full bg-gray-950 border-l border-gray-800">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-900 border-b border-gray-800 shrink-0">
        <span className="text-xs font-semibold text-gray-300 uppercase tracking-wide">YAML Preview</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setShowGuide(g => !g)}
            title="Toggle field guide"
            className={`text-xs px-2 py-1 rounded-lg transition-colors ${showGuide ? 'bg-blue-800 text-blue-200' : 'bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700'}`}
          >
            ? Guide
          </button>
          <button
            onClick={handleCopy}
            title="Copy to clipboard"
            className="text-xs px-2 py-1 bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 rounded-lg transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* Block tabs */}
      <div className="flex overflow-x-auto border-b border-gray-800 bg-gray-900 shrink-0 scrollbar-hide">
        {BLOCKS.map(block => {
          const st = (configs.find(c => c.block === block) || {}).status || 'pending'
          const isActive = block === activeBlock
          return (
            <button
              key={block}
              onClick={() => handleTabChange(block)}
              className={[
                'flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors shrink-0',
                isActive ? 'border-blue-500 text-white bg-gray-800' : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800/60',
              ].join(' ')}
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[st] || STATUS_DOT.pending}`} />
              {BLOCK_LABELS[block]}
            </button>
          )
        })}
      </div>

      {/* Status + action row */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-gray-500">{activeBlock}.yaml</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full border ${STATUS_PILL[activeConfig.status] || STATUS_PILL.pending}`}>
            {activeConfig.status}
          </span>
        </div>
        <div className="flex gap-1.5">
          {!editing ? (
            <button
              onClick={startEdit}
              className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
            >
              Edit
            </button>
          ) : (
            <>
              <button
                onClick={cancelEdit}
                className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2.5 py-1 rounded-lg transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Save feedback */}
      {saveMsg && (
        <div className={`px-3 py-1.5 text-xs border-b shrink-0 ${
          validationErrors.length > 0 ? 'bg-red-950 text-red-300 border-red-800' : 'bg-green-950 text-green-300 border-green-800'
        }`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5 pl-2">• {e}</div>)}
        </div>
      )}

      {/* CodeMirror editor */}
      <div ref={editorRef} className="flex-1 overflow-auto text-xs min-h-0" />

      {/* Field guide */}
      {showGuide && Object.keys(descriptions).length > 0 && (
        <div className="border-t border-gray-800 bg-gray-900 max-h-48 overflow-y-auto shrink-0">
          <p className="px-3 pt-2 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide">Field Guide</p>
          {Object.entries(descriptions).map(([key, desc]) => (
            <div key={key} className="px-3 py-1 flex gap-2 text-xs border-b border-gray-800/40">
              <span className="font-mono text-blue-400 shrink-0 w-36 truncate">{key}</span>
              <span className="text-gray-400">{desc}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
