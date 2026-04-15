import { useEffect, useRef } from 'react'

/**
 * Themed confirmation modal.
 *
 * Controlled component — parent owns open/close state. Renders nothing
 * when `open` is false. Closes on: Cancel button, Esc key, or overlay
 * click. The confirm button supports a `danger` variant (red) for
 * destructive actions such as Delete or Sign out.
 *
 * @param {{
 *   open: boolean,
 *   title?: string,
 *   message: string,
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 *   danger?: boolean,
 *   onConfirm: () => void,
 *   onCancel: () => void,
 * }} props
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  onConfirm,
  onCancel,
}) {
  const confirmBtnRef = useRef(null)

  // Esc closes, initial focus on the confirm button.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') onCancel()
      if (e.key === 'Enter') onConfirm()
    }
    window.addEventListener('keydown', onKey)
    confirmBtnRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel, onConfirm])

  if (!open) return null

  const confirmClasses = danger
    ? 'bg-red-600 hover:bg-red-500 focus:ring-red-400 text-white'
    : 'bg-indigo-600 hover:bg-indigo-500 focus:ring-indigo-400 text-white'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title || 'Confirm'}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onCancel}
        aria-hidden="true"
      />

      {/* Card */}
      <div
        className="relative w-full max-w-sm rounded-2xl bg-[var(--surface)] border border-[var(--border)] shadow-2xl p-5 animate-pop-in"
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <h2 className="text-[15px] font-semibold text-[var(--text)] mb-1.5">
            {title}
          </h2>
        )}
        <p className="text-[13px] text-[var(--text-muted)] leading-relaxed whitespace-pre-line">
          {message}
        </p>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-3.5 py-1.5 rounded-lg text-[12px] font-semibold text-[var(--text)] bg-[var(--surface-2)] hover:bg-black/5 dark:hover:bg-white/5 border border-[var(--border)] focus:outline-none focus:ring-2 focus:ring-[var(--border)] transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            onClick={onConfirm}
            className={`px-3.5 py-1.5 rounded-lg text-[12px] font-semibold focus:outline-none focus:ring-2 transition-colors ${confirmClasses}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
