import { useState } from 'react'
import { ConfirmDialog } from '../ui/ConfirmDialog'

/**
 * Format a session's last_accessed ISO timestamp for the sidebar.
 *   - today       -> "Today, 3:41 PM"
 *   - yesterday   -> "Yesterday, 3:41 PM"
 *   - this year   -> "Mar 4, 3:41 PM"
 *   - older       -> "Mar 4, 2025"
 * Falls back to "Conversation" when the timestamp is missing or unparseable.
 * @param {string|null|undefined} iso
 * @returns {string}
 */
function formatSessionTimestamp(iso) {
  if (!iso) return 'Conversation'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Conversation'
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const isYesterday = d.toDateString() === yesterday.toDateString()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (sameDay) return `Today, ${time}`
  if (isYesterday) return `Yesterday, ${time}`
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
  }
  return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * Chevron icon used by the collapse toggle (expanded sidebar only).
 */
function ChevronIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M11.354 1.646a.5.5 0 0 1 0 .708L5.707 8l5.647 5.646a.5.5 0 0 1-.708.708l-6-6a.5.5 0 0 1 0-.708l6-6a.5.5 0 0 1 .708 0" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 2a.5.5 0 0 1 .5.5v5h5a.5.5 0 0 1 0 1h-5v5a.5.5 0 0 1-1 0v-5h-5a.5.5 0 0 1 0-1h5v-5A.5.5 0 0 1 8 2" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0z" />
      <path d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4zM2.5 3h11V2h-11z" />
    </svg>
  )
}

/**
 * Collapsible left sidebar for the chat screen.
 *
 * Sections (top → bottom):
 *   1. Logo row — `agent_avatar` from config. When collapsed the logo
 *      itself acts as the expand button. When expanded a chevron on the
 *      right collapses the sidebar.
 *   2. New chat button + Conversations list
 *
 * @param {{
 *   config: Object,
 *   collapsed: boolean,
 *   onToggleCollapsed: () => void,
 * }} props
 */
export function Sidebar({
  config,
  collapsed,
  onToggleCollapsed,
  sessions = [],
  activeSessionId = null,
  onNewChat,
  onSelectSession,
  onDeleteSession,
}) {
  // UI copy — served from /app-config (merge of dpg.yaml + domain.yaml).
  // Fall back to English defaults only so the component remains render-safe
  // if the config endpoint is slow/empty.
  const newChatLabel = config.new_chat_label || 'New chat'
  const conversationsHeading = config.conversations_heading || 'Conversations'
  const noConversationsMsg = config.no_conversations_msg || 'No previous chats'
  const deleteConversationConfirm =
    config.delete_conversation_confirm || 'Delete this conversation? This cannot be undone.'
  const deleteConversationTooltip = config.delete_conversation_tooltip || 'Delete conversation'

  const widthClass = collapsed ? 'w-14' : 'w-64'
  const cancelLabel = config.cancel_label || 'Cancel'
  const logoEmoji = config.agent_avatar || config.app_icon || '🤖'

  // Pending delete confirmation. null = closed.
  const [pending, setPending] = useState(null)

  const closeDialog = () => setPending(null)
  const confirmPending = () => {
    if (!pending) return
    onDeleteSession && onDeleteSession(pending.sessionId)
    setPending(null)
  }

  return (
    <aside
      className={`${widthClass} flex-shrink-0 bg-[var(--surface)] border-r border-[var(--border)] flex flex-col transition-[width] duration-200 ease-out h-full`}
      aria-label="Sidebar"
    >
      {/* Logo row (doubles as expand control when collapsed) */}
      <div className={`px-3 border-b border-[var(--border)] h-16 flex items-center ${collapsed ? 'justify-center' : ''}`}>
        <div className={`flex items-center gap-2 ${collapsed ? '' : 'w-full'}`}>
          {collapsed ? (
            <button
              onClick={onToggleCollapsed}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              className="w-9 h-9 rounded-lg bg-indigo-600 hover:bg-indigo-500 flex items-center justify-center text-lg flex-shrink-0 transition-colors"
            >
              <span aria-hidden="true">{logoEmoji}</span>
            </button>
          ) : (
            <>
              <div className="w-9 h-9 rounded-lg bg-indigo-600 flex items-center justify-center text-lg flex-shrink-0" aria-hidden="true">
                {logoEmoji}
              </div>
              <div className="flex-1" />
              <button
                onClick={onToggleCollapsed}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
                className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-white/10 dark:hover:bg-white/10 transition-colors flex-shrink-0"
              >
                <ChevronIcon />
              </button>
            </>
          )}
        </div>
      </div>

      {/* New chat + conversations list */}
      <div className="px-3 py-3 flex-1 overflow-y-auto flex flex-col min-h-0">
        <button
          onClick={onNewChat}
          title={newChatLabel}
          aria-label={newChatLabel}
          className={`w-full flex items-center gap-2 ${collapsed ? 'justify-center' : ''} px-2.5 py-2 rounded-lg text-[12px] font-semibold text-indigo-300 hover:text-indigo-200 hover:bg-indigo-500/10 border border-indigo-500/20 hover:border-indigo-500/40 transition-colors mb-3 flex-shrink-0`}
        >
          <PlusIcon />
          {!collapsed && <span>{newChatLabel}</span>}
        </button>

        {!collapsed && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1.5 px-1 flex-shrink-0">
              {conversationsHeading}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto -mx-1 px-1 space-y-0.5">
              {sessions.length === 0 ? (
                <div className="text-[11px] text-[var(--text-muted)] italic px-1.5 py-1">
                  {noConversationsMsg}
                </div>
              ) : (
                sessions.map((s) => {
                  const isActive = s.session_id === activeSessionId
                  const label = formatSessionTimestamp(s.last_accessed)
                  return (
                    <div
                      key={s.session_id}
                      className={`group flex items-center gap-1 rounded-lg px-1.5 py-1.5 text-[12px] cursor-pointer transition-colors ${isActive
                          ? 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-200'
                          : 'text-[var(--text)] hover:bg-black/5 dark:hover:bg-white/5'
                        }`}
                      onClick={() => onSelectSession && onSelectSession(s.session_id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onSelectSession && onSelectSession(s.session_id)
                        }
                      }}
                      title={s.last_accessed || s.session_id}
                    >
                      <span className="truncate flex-1">{label}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setPending({ kind: 'delete', sessionId: s.session_id })
                        }}
                        title={deleteConversationTooltip}
                        aria-label={`${deleteConversationTooltip} ${label}`}
                        className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded text-[var(--text-muted)] hover:text-red-500 dark:hover:text-red-400 hover:bg-red-500/10 transition-opacity"
                      >
                        <TrashIcon />
                      </button>
                    </div>
                  )
                })
              )}
            </div>
          </>
        )}
      </div>


      <ConfirmDialog
        open={!!pending}
        title={deleteConversationTooltip}
        message={deleteConversationConfirm}
        confirmLabel={deleteConversationTooltip}
        cancelLabel={cancelLabel}
        danger
        onConfirm={confirmPending}
        onCancel={closeDialog}
      />
    </aside>
  )
}
