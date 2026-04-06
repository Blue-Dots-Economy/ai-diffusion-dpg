import { useState } from 'react'
import { MarkdownRenderer } from '../markdown/MarkdownRenderer'
import { formatTime, formatFullTime } from '../../utils'

const COLLAPSE_LINE_THRESHOLD = 15

/**
 * Single message bubble — supports user and agent roles.
 * Agent bubbles render Markdown; user bubbles render plain text.
 * Features: latency badge, tool-use badge, escalation style,
 * timestamps (hover full), collapsible long responses, fade-in on new messages.
 *
 * @param {{
 *   message: Object,
 *   isNew: boolean,
 *   agentAvatar: string,
 *   userAvatar: string,
 * }} props
 */
export function MessageBubble({ message, isNew, agentAvatar, userAvatar }) {
  const { role, text, timestamp, latencyMs, wasToolUsed, wasEscalated } = message
  const isAgent = role === 'agent'

  const lineCount = text.split('\n').length
  const wordCount = text.split(/\s+/).length
  const isLong = lineCount > COLLAPSE_LINE_THRESHOLD || wordCount > 200
  const [expanded, setExpanded] = useState(false)
  const [showFullTime, setShowFullTime] = useState(false)

  const bubbleBase = 'px-3.5 py-2.5 rounded-2xl text-sm leading-relaxed break-words'
  const agentBubbleStyle = wasEscalated
    ? `${bubbleBase} bg-orange-900/30 border border-orange-600 text-orange-100 rounded-bl-sm`
    : `${bubbleBase} bg-[var(--bubble-agent-bg)] border border-[var(--bubble-agent-border)] text-[var(--bubble-agent-text)] rounded-bl-sm`
  const userBubbleStyle = `${bubbleBase} bg-indigo-600 text-white rounded-br-sm`

  return (
    <div className={`flex mb-3 items-end gap-2 ${isAgent ? 'justify-start' : 'justify-end'}`}>
      {/* Agent avatar */}
      {isAgent && (
        <div className="w-7 h-7 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-sm flex-shrink-0 self-end">
          {agentAvatar}
        </div>
      )}

      <div className={`flex flex-col ${isAgent ? 'items-start' : 'items-end'} max-w-[78%] sm:max-w-[72%]`}>
        {/* Badges row (agent only) */}
        {isAgent && (wasToolUsed || wasEscalated) && (
          <div className="flex gap-1.5 mb-1 flex-wrap">
            {wasToolUsed && (
              <span className="text-[10px] bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded-full border border-blue-700/60">
                🔧 tool used
              </span>
            )}
            {wasEscalated && (
              <span className="text-[10px] bg-orange-900/50 text-orange-300 px-2 py-0.5 rounded-full border border-orange-700/60">
                ⚡ escalated
              </span>
            )}
          </div>
        )}

        {/* Bubble */}
        <div className={isAgent ? agentBubbleStyle : userBubbleStyle}>
          {isAgent ? (
            <>
              <div className={isLong && !expanded ? 'max-h-52 overflow-hidden relative' : ''}>
                <div className={isNew ? 'message-new' : ''}>
                  <MarkdownRenderer text={text} />
                </div>
                {isLong && !expanded && (
                  <div className="absolute bottom-0 left-0 right-0 h-14 bg-gradient-to-t from-[var(--bubble-agent-bg)] to-transparent pointer-events-none" />
                )}
              </div>
              {isLong && (
                <button
                  onClick={() => setExpanded(e => !e)}
                  className="mt-2 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  {expanded ? '↑ Show less' : '↓ Show more'}
                </button>
              )}
            </>
          ) : (
            <span className="whitespace-pre-wrap">{text}</span>
          )}
        </div>

        {/* Time + latency row */}
        <div className={`flex items-center gap-2 mt-1 ${isAgent ? '' : 'flex-row-reverse'}`}>
          <span
            className="text-[10px] text-gray-500 cursor-default select-none"
            onMouseEnter={() => setShowFullTime(true)}
            onMouseLeave={() => setShowFullTime(false)}
          >
            {showFullTime ? formatFullTime(timestamp) : formatTime(timestamp)}
          </span>
          {isAgent && latencyMs != null && (
            <span className="text-[10px] text-gray-600 bg-gray-900 px-1.5 py-0.5 rounded-full border border-gray-800">
              {latencyMs}ms
            </span>
          )}
        </div>
      </div>

      {/* User avatar */}
      {!isAgent && (
        <div className="w-7 h-7 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-sm flex-shrink-0 self-end">
          {userAvatar}
        </div>
      )}
    </div>
  )
}
