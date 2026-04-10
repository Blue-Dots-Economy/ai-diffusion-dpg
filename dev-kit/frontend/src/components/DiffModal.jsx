// dev-kit/frontend/src/components/DiffModal.jsx
import React, { useState } from 'react'
import ConfirmModal from './ConfirmModal'

const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

const STATUS_PILL = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

function lineDiff(oldText, newText) {
  const a = (oldText || '').split('\n')
  const b = (newText || '').split('\n')
  const result = []
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i >= a.length) {
      result.push({ type: 'add', text: b[j++] })
    } else if (j >= b.length) {
      result.push({ type: 'remove', text: a[i++] })
    } else if (a[i] === b[j]) {
      result.push({ type: 'same', text: a[i] })
      i++; j++
    } else {
      const aNext = a.indexOf(b[j], i + 1)
      const bNext = b.indexOf(a[i], j + 1)
      if (aNext === -1 && bNext === -1) {
        result.push({ type: 'remove', text: a[i++] })
        result.push({ type: 'add', text: b[j++] })
      } else if (aNext !== -1 && (bNext === -1 || aNext - i <= bNext - j)) {
        result.push({ type: 'add', text: b[j++] })
      } else {
        result.push({ type: 'remove', text: a[i++] })
      }
    }
  }
  return result
}

export default function DiffModal({ currentConfigs, previewConfigs, checkpointPhase, onConfirm, onCancel }) {
  const [activeBlock, setActiveBlock] = useState('agent_core')
  const [confirming, setConfirming] = useState(false)

  const current = currentConfigs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }
  const preview = previewConfigs.find(c => c.block === activeBlock) || { content: '', status: 'pending' }
  const diff = lineDiff(current.content, preview.content)
  const hasChanges = diff.some(d => d.type !== 'same')

  const changedBlocks = new Set(
    (previewConfigs || [])
      .filter(p => {
        const c = (currentConfigs || []).find(x => x.block === p.block)
        return c?.content !== p.content
      })
      .map(p => p.block)
  )

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-start bg-black/70 backdrop-blur-sm overflow-y-auto py-8 px-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-4xl flex flex-col" style={{ maxHeight: 'calc(100vh - 4rem)' }}>

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div>
            <h2 className="font-semibold text-base">Restore Checkpoint</h2>
            <p className="text-gray-400 text-xs mt-0.5">
              Phase: <span className="font-mono text-blue-400">{checkpointPhase}</span>
              {' — '}
              {changedBlocks.size === 0
                ? 'no config changes'
                : `${changedBlocks.size} block${changedBlocks.size !== 1 ? 's' : ''} will change`}
            </p>
          </div>
          <button onClick={onCancel} className="text-gray-500 hover:text-white text-2xl leading-none transition-colors">&times;</button>
        </div>

        {/* Block tabs */}
        <div className="flex overflow-x-auto border-b border-gray-800 bg-gray-900 shrink-0">
          {Object.keys(BLOCK_LABELS).map(block => {
            const isChanged = changedBlocks.has(block)
            const isActive = block === activeBlock
            return (
              <button
                key={block}
                onClick={() => setActiveBlock(block)}
                className={[
                  'px-3 py-2.5 text-xs font-medium whitespace-nowrap border-b-2 transition-colors shrink-0',
                  isActive ? 'border-blue-500 text-white bg-gray-800' : 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-gray-800/50',
                  isChanged && !isActive ? 'text-yellow-400' : '',
                ].filter(Boolean).join(' ')}
              >
                {BLOCK_LABELS[block]}
                {isChanged && <span className="ml-1 text-yellow-400">●</span>}
              </button>
            )
          })}
        </div>

        {/* Status row */}
        <div className="flex items-center gap-3 px-6 py-2 bg-gray-950 text-xs border-b border-gray-800 shrink-0">
          <span className="text-gray-500">Current:</span>
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_PILL[current.status] || STATUS_PILL.pending}`}>
            {current.status}
          </span>
          <span className="text-gray-600 mx-1">→</span>
          <span className="text-gray-500">After restore:</span>
          <span className={`px-2 py-0.5 rounded-full border ${STATUS_PILL[preview.status] || STATUS_PILL.pending}`}>
            {preview.status}
          </span>
        </div>

        {/* Diff body */}
        <div className="flex-1 overflow-y-auto font-mono text-xs bg-gray-950 min-h-0">
          {!hasChanges ? (
            <p className="text-gray-500 text-center py-12 text-sm">No changes to this block.</p>
          ) : (
            <div className="py-2">
              {diff.map((line, i) => (
                <div
                  key={i}
                  className={[
                    'px-4 py-0.5 whitespace-pre leading-5 select-text',
                    line.type === 'add' ? 'bg-green-950 text-green-300' : '',
                    line.type === 'remove' ? 'bg-red-950 text-red-300' : '',
                    line.type === 'same' ? 'text-gray-500' : '',
                  ].filter(Boolean).join(' ')}
                >
                  {line.type === 'add' ? '+ ' : line.type === 'remove' ? '- ' : '  '}
                  {line.text}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => setConfirming(true)}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 rounded-xl font-medium transition-colors"
          >
            Restore Checkpoint
          </button>
        </div>

        {/* Confirmation popup */}
        {confirming && (
          <ConfirmModal
            title="Restore checkpoint?"
            message={`This will roll back your project to the "${checkpointPhase}" checkpoint.`}
            bullets={[
              `${changedBlocks.size} config block${changedBlocks.size !== 1 ? 's' : ''} will be overwritten with checkpoint values`,
              'Your entire conversation history will be rolled back to this point',
              'All work done after this checkpoint will be permanently lost',
            ]}
            confirmLabel="Yes, restore and lose progress"
            onConfirm={onConfirm}
            onCancel={() => setConfirming(false)}
          />
        )}
      </div>
    </div>
  )
}
