import React, { useEffect, useState, useRef } from 'react'
import { api } from '../../api'
import { BLOCKS, BLOCK_LABELS } from '../../constants'
import TabBar from '../shared/TabBar'
import useYamlEditor from '../../hooks/useYamlEditor'

function YamlTab({ slug, block, content }) {
  const containerRef = useRef(null)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const { startEdit, cancelEdit, getContent, setReadOnly } = useYamlEditor(containerRef, content, { readOnly: true })

  function handleEdit() {
    setEditing(true)
    startEdit()
  }

  function handleCancel() {
    setEditing(false)
    setSaveError(null)
    cancelEdit()
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      await api.updateDpgValue(slug, block, getContent())
      setEditing(false)
      setReadOnly(true)
    } catch (e) {
      console.error(e)
      const detail = e?.response?.data?.detail || e?.message || 'Failed to save'
      setSaveError(detail)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-300">{BLOCK_LABELS[block]} — Framework Defaults</h3>
        <div className="flex gap-2">
          {editing ? (
            <>
              <button onClick={handleCancel} className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg transition-colors">Cancel</button>
              <button onClick={handleSave} disabled={saving} className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors">
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          ) : (
            <button onClick={handleEdit} className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg transition-colors">Edit</button>
          )}
        </div>
      </div>
      <div ref={containerRef} className="border border-gray-700 rounded-xl overflow-hidden min-h-[300px]" />
      {saveError && (
        <div className="mt-2 p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-sm">
          {saveError}
        </div>
      )}
    </div>
  )
}

export default function DpgValuesStep({ slug }) {
  const [values, setValues] = useState([])
  const [activeTab, setActiveTab] = useState(BLOCKS[0])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getDpgValues(slug).then(data => {
      setValues(data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [slug])

  const tabs = BLOCKS.map(b => ({ key: b, label: BLOCK_LABELS[b] }))
  const activeValue = values.find(v => v.block === activeTab)

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading DPG values…</div>
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">DPG Framework Values</h2>
      <p className="text-sm text-gray-400 mb-4">Review and edit the default configuration for each DPG building block.</p>
      <TabBar tabs={tabs} activeKey={activeTab} onSelect={setActiveTab} />
      <div className="mt-4">
        <YamlTab key={activeTab} slug={slug} block={activeTab} content={activeValue?.content || '# No configuration loaded'} />
      </div>
    </div>
  )
}
