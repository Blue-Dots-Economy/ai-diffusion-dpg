// dev-kit/frontend/src/components/ConfirmModal.jsx
import React from 'react'

/**
 * Generic confirmation popup.
 *
 * Props:
 *   title       string   — bold heading
 *   message     string   — body text (or a React node)
 *   bullets     string[] — optional warning bullet list
 *   confirmLabel string  — confirm button label (default "Confirm")
 *   confirmClass string  — Tailwind classes for confirm button (default red)
 *   onConfirm   fn
 *   onCancel    fn
 */
export default function ConfirmModal({
  title,
  message,
  bullets,
  confirmLabel = 'Confirm',
  confirmClass = 'bg-red-600 hover:bg-red-500 text-white',
  onConfirm,
  onCancel,
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm px-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-start justify-between px-6 pt-5 pb-3">
          <h2 className="font-semibold text-base text-white">{title}</h2>
          <button onClick={onCancel} className="text-gray-500 hover:text-white text-2xl leading-none ml-4 transition-colors">&times;</button>
        </div>

        {/* Body */}
        <div className="px-6 pb-4">
          {message && <p className="text-gray-300 text-sm mb-3">{message}</p>}
          {bullets && bullets.length > 0 && (
            <ul className="text-red-400 text-xs space-y-1 list-disc pl-4">
              {bullets.map((b, i) => <li key={i}>{b}</li>)}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-xl font-medium transition-colors ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
