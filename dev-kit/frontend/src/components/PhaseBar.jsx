import React from 'react'

const PHASES = ['overview', 'language', 'knowledge', 'memory', 'trust', 'connectors', 'workflow', 'review']
const PHASE_LABELS = {
  overview: 'Overview', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', trust: 'Trust', connectors: 'Connectors',
  workflow: 'Workflow', review: 'Review',
}

export default function PhaseBar({ currentPhase, checkpoints, onRestoreCheckpoint }) {
  const currentIdx = PHASES.indexOf(currentPhase)

  return (
    <div className="flex items-center gap-1 px-4 py-2 bg-gray-900 border-b border-gray-800 overflow-x-auto">
      {PHASES.map((phase, i) => {
        const isDone = i < currentIdx
        const isCurrent = phase === currentPhase
        const hasCheckpoint = checkpoints?.some((cp) => cp.phase.endsWith(phase))

        return (
          <button
            key={phase}
            onClick={() => hasCheckpoint && onRestoreCheckpoint && onRestoreCheckpoint(
              checkpoints.find((cp) => cp.phase.endsWith(phase))?.phase
            )}
            disabled={!hasCheckpoint}
            title={hasCheckpoint ? `Restore to ${PHASE_LABELS[phase]} checkpoint` : PHASE_LABELS[phase]}
            className={[
              'flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors',
              isCurrent ? 'bg-blue-600 text-white' : '',
              isDone && !isCurrent ? 'bg-gray-700 text-gray-300 hover:bg-gray-600 cursor-pointer' : '',
              !isDone && !isCurrent ? 'text-gray-600' : '',
            ].filter(Boolean).join(' ')}
          >
            {isDone && <span>&#10003;</span>}
            {PHASE_LABELS[phase]}
          </button>
        )
      })}
    </div>
  )
}
