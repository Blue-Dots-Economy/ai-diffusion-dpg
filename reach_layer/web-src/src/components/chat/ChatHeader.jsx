import { useState } from 'react'
import { ThemeToggle } from '../ui/ThemeToggle'

/**
 * Chat screen header.
 * Shows app name/icon, connected status, user ID pill (click to copy),
 * collapsible session debug section, switch-user button, and theme toggle.
 *
 * @param {{
 *   config: Object,
 *   userId: string|null,
 *   sessionId: string|null,
 *   theme: 'dark'|'light',
 *   onToggleTheme: () => void,
 *   onSwitchUser: () => void,
 * }} props
 */
export function ChatHeader({ config, userId, sessionId, theme, onToggleTheme, onSwitchUser }) {
  const [debugOpen, setDebugOpen] = useState(false)
  const [userIdCopied, setUserIdCopied] = useState(false)
  const [sidCopied, setSidCopied] = useState(false)

  const copyText = async (text, setCopied) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard unavailable
    }
  }

  return (
    <div className="bg-[var(--surface)] border-b border-[var(--border)] flex-shrink-0">
      {/* Main header row */}
      <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
        {/* App icon + name */}
        <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-base flex-shrink-0">
          {config.app_icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-[var(--text)] text-sm truncate">{config.app_name}</div>
          <div className="flex items-center gap-1.5 text-[11px] text-gray-500 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
            Connected
          </div>
        </div>

        {/* User ID pill */}
        {userId && (
          <button
            onClick={() => copyText(userId, setUserIdCopied)}
            title="Click to copy user ID"
            className="hidden sm:flex items-center gap-1.5 text-[11px] bg-gray-800 border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 px-2.5 py-1 rounded-full transition-colors max-w-[140px]"
          >
            <span className="truncate">{userIdCopied ? 'Copied!' : userId}</span>
          </button>
        )}

        {/* Debug toggle */}
        <button
          onClick={() => setDebugOpen(o => !o)}
          title="Session debug info"
          className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/10 transition-colors text-[11px]"
        >
          {debugOpen ? '▲' : '▼'} debug
        </button>

        {/* Theme toggle */}
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />

        {/* Switch user */}
        <button
          onClick={onSwitchUser}
          title="Switch user"
          className="text-[11px] text-gray-500 hover:text-gray-300 border border-gray-700 hover:border-gray-500 px-2.5 py-1 rounded-lg transition-colors whitespace-nowrap"
        >
          ← Switch
        </button>
      </div>

      {/* Debug panel */}
      {debugOpen && (
        <div className="px-4 pb-3 sm:px-6 border-t border-[var(--border)] bg-gray-950/50">
          <div className="mt-2 space-y-1.5 text-[11px] font-mono text-gray-500">
            <div className="flex items-center gap-2">
              <span className="text-gray-600 w-20 flex-shrink-0">User ID</span>
              <span className="text-gray-400 truncate">{userId || '—'}</span>
              {userId && (
                <button
                  onClick={() => copyText(userId, setUserIdCopied)}
                  className="text-indigo-500 hover:text-indigo-400 flex-shrink-0"
                >
                  {userIdCopied ? '✓' : 'copy'}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-gray-600 w-20 flex-shrink-0">Session</span>
              <span className="text-gray-400 truncate">{sessionId || '—'}</span>
              {sessionId && (
                <button
                  onClick={() => copyText(sessionId, setSidCopied)}
                  className="text-indigo-500 hover:text-indigo-400 flex-shrink-0"
                >
                  {sidCopied ? '✓' : 'copy'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
