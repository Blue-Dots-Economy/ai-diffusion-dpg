/**
 * Renders the toast stack in the bottom-right corner.
 * Each toast auto-dismisses after 4 seconds (set in useToast).
 */
export function ToastContainer({ toasts, onRemove }) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map(toast => (
        <Toast key={toast.id} toast={toast} onRemove={onRemove} />
      ))}
    </div>
  )
}

function Toast({ toast, onRemove }) {
  const colors = {
    error: 'bg-red-900/90 border-red-700 text-red-100',
    success: 'bg-green-900/90 border-green-700 text-green-100',
    info: 'bg-indigo-900/90 border-indigo-700 text-indigo-100',
  }

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-xl border text-sm shadow-lg animate-[fadeInWord_0.2s_ease] ${colors[toast.type] || colors.error}`}
    >
      <span className="flex-1">{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className="opacity-60 hover:opacity-100 flex-shrink-0 mt-0.5"
        aria-label="Dismiss"
      >
        ✕
      </button>
    </div>
  )
}
