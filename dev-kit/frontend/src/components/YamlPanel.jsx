// dev-kit/frontend/src/components/YamlPanel.jsx
import React, { useEffect, useRef, useState } from 'react'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'
import { useTheme } from '../ThemeContext'
import ConfirmModal from './ConfirmModal'
import { BLOCKS, BLOCK_LABELS, STATUS_DOT } from '../constants'
import StatusBadge from './shared/StatusBadge'

export default function YamlPanel({ slug, configs, onSaved }) {
  const { theme } = useTheme()
  const [activeBlock, setActiveBlock] = useState('agent_core')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)
  const [validationModal, setValidationModal] = useState(null)  // null | string[]
  const [copied, setCopied] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [descriptions, setDescriptions] = useState({})
  const [validationByBlock, setValidationByBlock] = useState({})
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const originalRef = useRef('')
  const editableCompartment = useRef(new Compartment())
  const readOnlyCompartment = useRef(new Compartment())
  const editingRef = useRef(false)  // mirrors editing state; used in effect cleanup

  const activeConfig = configs.find(c => c.block === activeBlock) || { content: '', status: 'incomplete' }
  const blockValidation = validationByBlock[activeBlock]

  // Rebuild editor when block/configs/theme change — but never while editing.
  // We use editingRef (not the editing state) in the cleanup so that toggling
  // edit mode does NOT trigger cleanup and destroy the live editor.
  useEffect(() => {
    if (!editorRef.current) return
    if (editingRef.current) return  // configs changed mid-edit — preserve editor
    viewRef.current?.destroy()
    const compartment = editableCompartment.current
    const state = EditorState.create({
      doc: activeConfig.content || '',
      extensions: [
        basicSetup,
        yaml(),
        ...(theme === 'dark' ? [oneDark] : []),
        compartment.of(EditorView.editable.of(false)),
        readOnlyCompartment.current.of(EditorState.readOnly.of(true)),
      ],
    })
    viewRef.current = new EditorView({ state, parent: editorRef.current })
    return () => {
      // Only destroy when NOT editing — prevents destroying the live editor
      // when deps change while the user is mid-edit.
      if (!editingRef.current) {
        viewRef.current?.destroy()
        viewRef.current = null
      }
    }
  }, [activeBlock, configs, theme])  // `editing` intentionally excluded

  // Fetch schema descriptions on tab change
  useEffect(() => {
    setDescriptions({})
    api.getSchemaDescriptions(activeBlock)
      .then(data => setDescriptions(data.descriptions || {}))
      .catch(() => {})
  }, [activeBlock])

  // Fetch validation status for all blocks on mount
  useEffect(() => {
    api.validateConfigs(slug)
      .then(results => setValidationByBlock(results))
      .catch(() => {})
  }, [slug, configs])

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
    editingRef.current = true
    viewRef.current?.dispatch({
      effects: [
        editableCompartment.current.reconfigure(EditorView.editable.of(true)),
        readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(false)),
      ],
    })
    setEditing(true)
    setSaveMsg(null)
    setValidationErrors([])
  }

  function cancelEdit() {
    if (!viewRef.current) return
    editingRef.current = false
    viewRef.current.dispatch({
      changes: { from: 0, to: viewRef.current.state.doc.length, insert: originalRef.current },
      effects: [
        editableCompartment.current.reconfigure(EditorView.editable.of(false)),
        readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(true)),
      ],
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
      if (result.validation_errors?.length > 0) {
        // Stay in edit mode — show popup, block save until errors are fixed
        setValidationModal(result.validation_errors)
        return
      }
      editingRef.current = false
      viewRef.current.dispatch({
        effects: [
          editableCompartment.current.reconfigure(EditorView.editable.of(false)),
          readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(true)),
        ],
      })
      setEditing(false)
      setValidationErrors([])
      setSaveMsg('Saved successfully.')
      onSaved?.(activeBlock, { block: activeBlock, status: result.status, content })
      // Refresh validation status after save
      api.validateConfigs(slug)
        .then(results => setValidationByBlock(results))
        .catch(() => {})
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
      const freshConfigs = await api.getConfigs(slug)
      freshConfigs.forEach(c => onSaved?.(c.block, c))
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
      // Fallback for non-HTTPS / browsers without clipboard API
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
          const st = (configs.find(c => c.block === block) || {}).status || 'incomplete'
          const isActive = block === activeBlock
          const bv = validationByBlock[block]
          return (
            <button
              key={block}
              onClick={() => handleTabChange(block)}
              className={[
                'flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors shrink-0',
                isActive ? 'border-blue-500 text-white bg-gray-800' : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800/60',
              ].join(' ')}
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[st] || STATUS_DOT.incomplete}`} />
              {BLOCK_LABELS[block]}
              {bv && (
                <span className={`ml-0.5 text-[10px] font-bold ${bv.valid ? 'text-green-400' : 'text-red-400'}`}>
                  {bv.valid ? '✓' : '✗'}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Status + validation + action row */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-xs text-gray-500">{activeBlock}.yaml</span>
          <StatusBadge status={activeConfig.status} />
          {blockValidation && (
            <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${
              blockValidation.valid
                ? 'bg-green-900 text-green-300 border-green-700'
                : 'bg-red-900 text-red-300 border-red-700'
            }`}>
              {blockValidation.valid ? '✓ Valid' : `✗ ${blockValidation.errors.length} error${blockValidation.errors.length !== 1 ? 's' : ''}`}
            </span>
          )}
        </div>
        <div className="flex gap-1.5">
          {!editing ? (
            <>
              <button
                onClick={handleReload}
                disabled={reloading}
                title="Reload config from disk (picks up manual file edits)"
                className="text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
              >
                {reloading ? '…' : '↺ Reload'}
              </button>
              <button
                onClick={startEdit}
                className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2.5 py-1 rounded-lg transition-colors"
              >
                Edit
              </button>
            </>
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

      {/* Validation error details */}
      {blockValidation && !blockValidation.valid && blockValidation.errors.length > 0 && !saveMsg && (
        <div className="px-3 py-1.5 text-xs bg-red-950 text-red-300 border-b border-red-800 shrink-0">
          {blockValidation.errors.map((e, i) => <div key={i} className="mt-0.5">• {e}</div>)}
        </div>
      )}

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

    {validationModal && (
      <ConfirmModal
        title="Validation errors — cannot save"
        message={`Fix the following errors in ${activeBlock}.yaml before saving:`}
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
