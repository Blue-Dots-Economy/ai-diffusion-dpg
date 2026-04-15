import { useEffect, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'

/**
 * Scrollable message container.
 * Auto-scrolls to the bottom on new messages.
 * If the user scrolls up, auto-scroll is paused until they return to bottom.
 * Shows a "scroll to bottom" button when paused.
 *
 * @param {{
 *   messages: Array,
 *   isSending: boolean,
 *   newestAgentId: string|null,
 *   agentAvatar: string,
 *   userAvatar: string,
 *   systemMsg: string|null,
 * }} props
 */
export function MessageList({
  messages,
  isSending,
  newestAgentId,
  agentAvatar,
  userAvatar,
  systemMsg,
}) {
  const containerRef = useRef(null)
  const bottomRef = useRef(null)
  const [userScrolled, setUserScrolled] = useState(false)

  // Auto-scroll to bottom when new messages arrive (if user hasn't scrolled up)
  useEffect(() => {
    if (!userScrolled) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isSending, userScrolled])

  // Detect manual scroll
  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setUserScrolled(!atBottom)
  }

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    setUserScrolled(false)
  }

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto px-4 py-5 sm:px-6"
      >
        {/* System / welcome message */}
        {systemMsg && (
          <div className="flex justify-center mb-4">
            <span className="text-xs text-[var(--text-muted)] bg-[var(--surface-2)] px-3 py-1.5 rounded-full border border-[var(--border)]">
              {systemMsg}
            </span>
          </div>
        )}

        {/* Message bubbles */}
        {messages.map(msg => (
          <MessageBubble
            key={msg.id}
            message={msg}
            isNew={msg.id === newestAgentId}
            agentAvatar={agentAvatar}
            userAvatar={userAvatar}
          />
        ))}

        {/* Typing indicator */}
        {isSending && <TypingIndicator agentAvatar={agentAvatar} />}

        {/* Scroll anchor */}
        <div ref={bottomRef} />
      </div>

      {/* Scroll-to-bottom FAB */}
      {userScrolled && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-3 py-1.5 rounded-full shadow-lg flex items-center gap-1.5 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="currentColor" viewBox="0 0 16 16">
            <path d="M8 4a.5.5 0 0 1 .5.5v5.793l2.146-2.147a.5.5 0 0 1 .708.708l-3 3a.5.5 0 0 1-.708 0l-3-3a.5.5 0 1 1 .708-.708L7.5 10.293V4.5A.5.5 0 0 1 8 4"/>
          </svg>
          Latest message
        </button>
      )}
    </div>
  )
}
