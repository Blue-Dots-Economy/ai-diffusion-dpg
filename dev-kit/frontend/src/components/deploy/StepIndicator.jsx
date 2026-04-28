// dev-kit/frontend/src/components/deploy/StepIndicator.jsx
import React from 'react'

const STEPS = [
  { key: 1, label: 'DPG Values' },
  { key: 2, label: 'Config Review' },
  { key: 3, label: 'Dependencies' },
  { key: 4, label: 'Resources' },
  { key: 5, label: 'Inputs' },
  { key: 6, label: 'Target' },
  { key: 7, label: 'Preview' },
  { key: 8, label: 'Status' },
  { key: 9, label: 'Ingest' },
]

export default function StepIndicator({ currentStep, completedSteps, onStepClick }) {
  return (
    <div className="flex items-center gap-1 px-4 py-3 bg-gray-900 border-b border-gray-800 overflow-x-auto">
      {STEPS.map((step, i) => {
        const isActive = step.key === currentStep
        const isDone = completedSteps.includes(step.key)
        // A step is reachable if it's been completed, is the active step,
        // or is the immediate next step after a completed one.
        const isReachable =
          isActive ||
          isDone ||
          completedSteps.includes(step.key - 1)
        const clickable = !!onStepClick && isReachable && !isActive
        const Wrapper = clickable ? 'button' : 'div'
        return (
          <React.Fragment key={step.key}>
            {i > 0 && <div className={`h-px flex-1 min-w-[12px] ${isDone ? 'bg-green-700' : 'bg-gray-700'}`} />}
            <Wrapper
              type={clickable ? 'button' : undefined}
              onClick={clickable ? () => onStepClick(step.key) : undefined}
              className={`flex items-center gap-1.5 shrink-0 ${clickable ? 'cursor-pointer hover:opacity-80' : ''}`}
            >
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium border ${
                isActive ? 'border-blue-500 bg-blue-600 text-white' :
                isDone ? 'border-green-700 bg-green-900 text-green-300' :
                'border-gray-700 bg-gray-800 text-gray-500'
              }`}>
                {isDone ? '✓' : step.key}
              </div>
              <span className={`text-xs whitespace-nowrap ${
                isActive ? 'text-white font-medium' : 'text-gray-500'
              }`}>
                {step.label}
              </span>
            </Wrapper>
          </React.Fragment>
        )
      })}
    </div>
  )
}
