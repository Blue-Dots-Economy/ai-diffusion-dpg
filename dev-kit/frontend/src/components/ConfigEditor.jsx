import React, { useEffect, useRef, useState } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const DRAFT_BLOCKS = new Set(['trust_layer', 'action_gateway', 'reach_layer', 'learning_layer'])

export default function ConfigEditor({ slug, block, onBack }) {
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const [status, setStatus] = useState('pending')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)

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
    return () => viewRef.current?.destroy()
  }, [slug, block])

  function enableEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: EditorView.editable.reconfigure(EditorView.editable.of(true)),
    })
    setEditing(true)
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
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved.')
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-800">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm">&larr; Dashboard</button>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{block}.yaml</span>
          <span className={`text-xs px-2 py-0.5 rounded-full border ${
            status === 'complete' ? 'bg-green-900 text-green-300 border-green-700' :
            status === 'draft' ? 'bg-yellow-900 text-yellow-300 border-yellow-700' :
            status === 'stale' ? 'bg-red-900 text-red-300 border-red-700' :
            'bg-gray-800 text-gray-400 border-gray-700'
          }`}>{status}</span>
        </div>
        <div className="flex gap-2">
          {!editing && (
            <button onClick={enableEdit} className="text-xs bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-lg">
              Edit
            </button>
          )}
          {editing && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1 rounded-lg"
            >
              {saving ? 'Saving\u2026' : 'Save'}
            </button>
          )}
        </div>
      </div>

      {DRAFT_BLOCKS.has(block) && (
        <div className="px-4 py-2 bg-yellow-900 border-b border-yellow-700 text-yellow-300 text-xs">
          This config is a draft &mdash; the block template is not yet finalised.
        </div>
      )}

      {saveMsg && (
        <div className={`px-4 py-2 text-xs border-b ${validationErrors.length > 0 ? 'bg-red-900 text-red-300 border-red-700' : 'bg-green-900 text-green-300 border-green-700'}`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5">&bull; {e}</div>)}
        </div>
      )}

      <div ref={editorRef} className="flex-1 overflow-auto text-sm" />
    </div>
  )
}
