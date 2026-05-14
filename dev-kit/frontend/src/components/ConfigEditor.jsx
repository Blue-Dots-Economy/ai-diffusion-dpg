// dev-kit/frontend/src/components/ConfigEditor.jsx
import React, { useEffect, useRef, useState } from 'react'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'
import { useTheme } from '../ThemeContext'
import ConfirmModal from './ConfirmModal'
import ThemeToggle from './shared/ThemeToggle'

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  incomplete: 'bg-gray-800 text-gray-400 border-gray-700',
}

const DRAFT_BLOCKS = new Set(['trust_layer', 'action_gateway', 'reach_layer'])

export default function ConfigEditor({ slug, block, onBack }) {
  const { theme } = useTheme()
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')
  const editableCompartment = useRef(new Compartment())
  const readOnlyCompartment = useRef(new Compartment())
  const [status, setStatus] = useState('incomplete')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null)
  const [validationModal, setValidationModal] = useState(null)  // null | string[]
  const [copied, setCopied] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})

  useEffect(() => {
    api.getConfig(slug, block).then(({ content, status: s }) => {
      setStatus(s)
      viewRef.current?.destroy()
      if (!editorRef.current) return
      const state = EditorState.create({
        doc: content || '',
        extensions: [
          basicSetup,
          yaml(),
          ...(theme === 'dark' ? [oneDark] : []),
          editableCompartment.current.of(EditorView.editable.of(false)),
          readOnlyCompartment.current.of(EditorState.readOnly.of(true)),
        ],
      })
      viewRef.current = new EditorView({ state, parent: editorRef.current })
    }).catch(() => {})
    return () => { viewRef.current?.destroy(); viewRef.current = null }
  }, [slug, block, theme])

  useEffect(() => {
    api.getSchemaDescriptions(block)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [block])

  function startEdit() {
    if (!viewRef.current) return
    originalRef.current = viewRef.current.state.doc.toString()
    viewRef.current.dispatch({
      effects: [
        editableCompartment.current.reconfigure(EditorView.editable.of(true)),
        readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(false)),
      ],
    })
    setEditing(true)
    setSaveMsg(null)
  }

  function cancelEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: { from: 0, to: viewRef.current.state.doc.length, insert: originalRef.current },
      effects: [
        editableCompartment.current.reconfigure(EditorView.editable.of(false)),
        readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(true)),
      ],
    })
    setEditing(false)
    setSaveMsg(null)
  }

  async function handleSave() {
    if (!viewRef.current) return
    setSaving(true)
    setSaveMsg(null)
    const content = viewRef.current.state.doc.toString()
    try {
      const result = await api.updateConfig(slug, block, content)
      if (result.validation_errors?.length > 0) {
        // Stay in edit mode — show popup, block save until errors are fixed
        setValidationModal(result.validation_errors)
        return
      }
      setStatus(result.status)
      viewRef.current.dispatch({
        effects: [
          editableCompartment.current.reconfigure(EditorView.editable.of(false)),
          readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(true)),
        ],
      })
      setEditing(false)
      setSaveMsg('Saved successfully.')
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleReload() {
    setReloading(true)
    setSaveMsg(null)
    try {
      await api.reloadConfigs(slug)
      // Re-fetch the config content so the editor shows the current disk state
      const { content, status: s } = await api.getConfig(slug, block)
      setStatus(s)
      if (viewRef.current) {
        viewRef.current.dispatch({
          changes: { from: 0, to: viewRef.current.state.doc.length, insert: content || '' },
        })
      }
      setSaveMsg('Reloaded from disk.')
    } catch (err) {
      setSaveMsg(`Reload error: ${err.message}`)
    } finally {
      setReloading(false)
    }
  }

  async function handleCopy() {
    const content = viewRef.current?.state.doc.toString() || ''
    try {
      await navigator.clipboard.writeText(content)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = content
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <>
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
          ← Dashboard
        </button>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-300">{block}.yaml</span>
          <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_PILL[status] || STATUS_PILL.incomplete}`}>
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
          {!editing && (
            <button
              onClick={handleReload}
              disabled={reloading}
              title="Reload config from disk (useful after editing files directly)"
              className="text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-400 hover:text-gray-200 px-2.5 py-1.5 rounded-lg transition-colors"
            >
              {reloading ? '…' : '↺ Reload'}
            </button>
          )}
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
          <ThemeToggle />
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
        <div className="px-4 py-1.5 text-xs border-b shrink-0 bg-green-950 text-green-300 border-green-800">
          {saveMsg}
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

    {validationModal && (
      <ConfirmModal
        title="Validation errors — cannot save"
        message={`Fix the following errors in ${block}.yaml before saving:`}
        bullets={validationModal}
        confirmLabel="OK, I'll fix them"
        confirmClass="bg-blue-600 hover:bg-blue-500 text-white"
        onConfirm={() => setValidationModal(null)}
        onCancel={() => setValidationModal(null)}
      />
    )}
    </>
  )
}
