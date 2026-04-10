import React from 'react'

const VARIANTS = {
  success: 'border-green-700 bg-green-950/40',
  warning: 'border-yellow-700 bg-yellow-950/30',
  error: 'border-red-700 bg-red-950/30',
  info: 'border-gray-700 bg-gray-900',
}

const ICONS = {
  success: '✅',
  warning: '⚠️',
  error: '❌',
  info: '🔧',
}

export default function StatusBanner({ variant = 'info', title, subtitle, action }) {
  return (
    <div className={`rounded-xl border px-4 py-3 mb-6 flex items-center justify-between ${VARIANTS[variant]}`}>
      <div className="flex items-center gap-3">
        <span className="text-xl">{ICONS[variant]}</span>
        <div>
          <p className="text-sm font-medium">{title}</p>
          {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}
