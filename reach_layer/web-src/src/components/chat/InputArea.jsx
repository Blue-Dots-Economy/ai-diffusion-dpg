import { useRef, useState, useEffect } from 'react'

/**
 * Chat input area.
 * - Send on Enter, newline on Shift+Enter
 * - Character count display
 * - Clear / reset conversation button
 * - Auto-growing textarea (up to 6 lines)
 * - Disabled while sending
 *
 * @param {{
 *   onSend: (text: string) => void,
 *   onClear: () => void,
 *   disabled: boolean,
 *   placeholder: string,
 * }} props
 */
export function InputArea({ onSend, onClear, disabled, placeholder }) {
  const [text, setText] = useState('')
  const textareaRef = useRef(null)

  // Restore focus when the textarea becomes enabled again (after send completes)
  useEffect(() => {
    if (!disabled) {
      textareaRef.current?.focus()
    }
  }, [disabled])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = e => {
    setText(e.target.value)
    // Auto-resize
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 144) + 'px' // max ~6 lines
  }

  const charCount = text.length
  const charLimit = 2000
  const nearLimit = charCount > charLimit * 0.8

  return (
    <div className="bg-[var(--surface)] border-t border-[var(--border)] px-4 py-3 sm:px-6">
      {/* Char count + clear row */}
      <div className="flex items-center justify-between mb-1.5 px-1">
        <button
          onClick={onClear}
          disabled={disabled}
          title="Clear conversation (local only)"
          className="text-[11px] text-gray-500 hover:text-gray-300 disabled:opacity-40 transition-colors"
        >
          ↺ Clear chat
        </button>
        <span className={`text-[11px] ${nearLimit ? 'text-orange-400' : 'text-gray-600'}`}>
          {charCount}/{charLimit}
        </span>
      </div>

      {/* Input row */}
      <div className="flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          value={text}
          onInput={handleInput}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder || 'Type your message…'}
          maxLength={charLimit}
          rows={1}
          className="
            flex-1 resize-none bg-[var(--surface-2)] border border-[var(--border)]
            rounded-2xl px-4 py-2.5 text-sm text-[var(--text)] placeholder-gray-500
            outline-none focus:border-indigo-500 transition-colors
            disabled:opacity-50 disabled:cursor-not-allowed
            leading-relaxed max-h-36
          "
        />
        <button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          title="Send (Enter)"
          className="
            w-10 h-10 rounded-full bg-indigo-600 hover:bg-indigo-500
            disabled:opacity-40 disabled:cursor-not-allowed
            flex items-center justify-center flex-shrink-0
            transition-all active:scale-90
          "
          aria-label="Send"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="white" viewBox="0 0 16 16">
            <path d="M15.854.146a.5.5 0 0 1 .11.54l-5.819 14.547a.75.75 0 0 1-1.329.124l-3.178-4.995L.643 7.184a.75.75 0 0 1 .124-1.33L15.314.037a.5.5 0 0 1 .54.11ZM6.636 10.07l2.761 4.338L14.13 2.576zm6.787-8.201L1.591 6.602l4.339 2.76z"/>
          </svg>
        </button>
      </div>

      <p className="text-[10px] text-gray-600 mt-1.5 px-1">
        Enter to send · Shift+Enter for new line
      </p>
    </div>
  )
}
