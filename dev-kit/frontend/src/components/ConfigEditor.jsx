// dev-kit/frontend/src/components/ConfigEditor.jsx
import React, { useEffect, useRef, useState } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

const DRAFT_BLOCKS = new Set(['trust_layer', 'action_gateway', 'reach_layer'])

export default function ConfigEditor({ slug, block, onBack }) {
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')
  const [status, setStatus] = useState('pending')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)
  const [copied, setCopied] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})

  useEffect(() => {
    api.getConfig(slug, block).then(({ content, status: s }) => {
      setStatus(s)
      if (viewRef.current) viewRef.current.destroy()
      if (!editorRef.current) return
      const state = EditorState.create({
        doc: content || '',
        extensions: [basicSetup, yaml(), oneDark, EditorView.editable.of(false)],
      })
      viewRef.current = new EditorView({ state, parent: editorRef.current })
    }).catch(() => {})
    return () => { viewRef.current?.destroy(); viewRef.current = null }
  }, [slug, block])

  useEffect(() => {
    api.getSchemaDescriptions(block)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [block])

  function startEdit() {
    if (!viewRef.current) return
    originalRef.current = viewRef.current.state.doc.toString()
    viewRef.current.dispatch({
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
      const result = await api.updateConfig(slug, block, content)
      setStatus(result.status)
      viewRef.current.dispatch({
        effects: EditorView.editable.reconfigure(EditorView.editable.of(false)),
      })
      setEditing(false)
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved successfully.')
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
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
          ← Dashboard
        </button>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-300">{block}.yaml</span>
          <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_PILL[status] || STATUS_PILL.pending}`}>
            {status}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGuide(g => !g)}
            className={`text-xs px-2.5 py-1.5 rounded-lg transition-colors ${showGuide ? 'bg-indigo-700 text-indigo-200' : 'bg-gray-800 hover:bg-gray-700 text-gray-400'}`}
          >
            ? Guide
          </button>
          <button
            onClick={handleCopy}
            className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 px-2.5 py-1.5 rounded-lg transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
          {!editing ? (
            <button
              onClick={startEdit}
              className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded-lg transition-colors"
            >
              Edit
            </button>
          ) : (
            <>
              <button
                onClick={cancelEdit}
                className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2.5 py-1.5 rounded-lg transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      </div>

      {DRAFT_BLOCKS.has(block) && (
        <div className="px-4 py-1.5 bg-yellow-900/40 border-b border-yellow-800 text-yellow-400 text-xs shrink-0">
          This config block is a draft — the block template is not yet finalised.
        </div>
      )}

      {editing && (
        <div className="px-4 py-1.5 bg-indigo-900/30 border-b border-indigo-800 text-indigo-300 text-xs shrink-0">
          Editing — click Save to persist or Cancel to discard changes.
        </div>
      )}

      {saveMsg && (
        <div className={`px-4 py-1.5 text-xs border-b shrink-0 ${
          validationErrors.length > 0 ? 'bg-red-950 text-red-300 border-red-800' : 'bg-green-950 text-green-300 border-green-800'
        }`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5 pl-2">• {e}</div>)}
        </div>
      )}

      <div ref={editorRef} className="flex-1 overflow-auto text-sm min-h-0" />

      {/* Field guide */}
      {showGuide && Object.keys(descriptions).length > 0 && (
        <div className="border-t border-gray-800 bg-gray-900 max-h-52 overflow-y-auto shrink-0">
          <p className="px-4 pt-2 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide">Field Guide</p>
          {Object.entries(descriptions).map(([key, desc]) => (
            <div key={key} className="px-4 py-1 flex gap-3 text-xs border-b border-gray-800/50">
              <span className="font-mono text-blue-400 shrink-0 w-40">{key}</span>
              <span className="text-gray-400">{desc}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
