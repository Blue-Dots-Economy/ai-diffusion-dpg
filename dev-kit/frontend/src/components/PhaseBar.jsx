import React, { useState } from 'react'

const PHASES = ['overview', 'language', 'knowledge', 'memory', 'trust', 'connectors', 'workflow', 'observability', 'reach', 'review']
const PHASE_LABELS = {
  overview: 'Overview', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', trust: 'Trust', connectors: 'Connectors',
  workflow: 'Workflow', observability: 'Observability', reach: 'Reach Layer', review: 'Review',
}

export default function PhaseBar({ currentPhase, checkpoints, onRestoreCheckpoint }) {
  const [collapsed, setCollapsed] = useState(false)
  const currentIdx = PHASES.indexOf(currentPhase)

  return (
    <div className={`flex flex-col bg-gray-900 border-r border-gray-800 shrink-0 transition-all duration-200 ${collapsed ? 'w-8' : 'w-44'}`}>
      {/* Toggle button */}
      <button
        onClick={() => setCollapsed(c => !c)}
        className="flex items-center justify-center h-8 text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors shrink-0"
        title={collapsed ? 'Expand phases' : 'Collapse phases'}
      >
        <span className="text-xs">{collapsed ? '›' : '‹'}</span>
      </button>

      {!collapsed && (
        <div className="flex flex-col gap-0.5 px-2 pb-3 overflow-y-auto">
          {PHASES.map((phase, i) => {
            const isDone = i < currentIdx
            const isCurrent = phase === currentPhase
            const checkpoint = checkpoints?.find((cp) => cp.phase.endsWith(phase))
            const hasCheckpoint = !!checkpoint

            return (
              <button
                key={phase}
                onClick={() => hasCheckpoint && onRestoreCheckpoint && onRestoreCheckpoint(checkpoint.phase)}
                disabled={!hasCheckpoint}
                title={hasCheckpoint ? `Restore to ${PHASE_LABELS[phase]} checkpoint` : PHASE_LABELS[phase]}
                className={[
                  'flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium text-left whitespace-nowrap transition-colors w-full',
                  isCurrent ? 'bg-blue-600 text-white' : '',
                  isDone && !isCurrent ? 'text-gray-300 hover:bg-gray-800 cursor-pointer' : '',
                  !isDone && !isCurrent ? 'text-gray-600 cursor-default' : '',
                ].filter(Boolean).join(' ')}
              >
                <span className="shrink-0 w-3.5 text-center text-[10px]">
                  {isDone ? '✓' : isCurrent ? '●' : '○'}
                </span>
                {PHASE_LABELS[phase]}
              </button>
            )
          })}
        </div>
      )}

      {collapsed && (
        <div className="flex flex-col gap-2.5 px-1 pb-3 items-center overflow-y-auto">
          {PHASES.map((phase, i) => {
            const isDone = i < currentIdx
            const isCurrent = phase === currentPhase
            return (
              <span
                key={phase}
                title={PHASE_LABELS[phase]}
                className={`w-2 h-2 rounded-full shrink-0 ${isCurrent ? 'bg-blue-500' : isDone ? 'bg-green-600' : 'bg-gray-700'}`}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
