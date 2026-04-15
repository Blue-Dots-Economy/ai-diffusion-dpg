import { useState } from 'react'
import { ThemeToggle } from '../ui/ThemeToggle'

/**
 * Compute display initials from a name/user id.
 * @param {string|null} name
 * @param {string|null} fallback
 * @returns {string}
 */
function initialsOf(name, fallback) {
  const src = (name || fallback || '?').trim()
  if (!src) return '?'
  const parts = src.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return src.slice(0, 2).toUpperCase()
}

/**
 * Chat header.
 *
 * Left: app name + Connected status.
 * Right: signed-in user card (avatar + name + email / caption). The
 * sidebar expand control has moved to the sidebar itself (clicking the
 * logo when collapsed expands it), so no hamburger lives here.
 *
 * @param {{
 *   config: Object,
 *   identity: { user_id: string, display_name?: string, email?: string, picture?: string }|null,
 *   userId: string|null,
 *   authEnabled: boolean,
 *   theme?: 'dark'|'light',
 *   onToggleTheme?: () => void,
 * }} props
 */
export function ChatHeader({ config, identity, userId, authEnabled, theme, onToggleTheme }) {
  const [imgFailed, setImgFailed] = useState(false)

  const displayName = identity?.display_name || userId || ''
  const email = identity?.email || ''
  const picture = identity?.picture || ''
  const initials = initialsOf(identity?.display_name, userId)

  return (
    <div className="bg-[var(--surface)] border-b border-[var(--border)] flex-shrink-0 h-16">
      <div className="flex items-center gap-3 px-4 sm:px-6 h-full">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-[var(--text)] text-sm truncate">
            {config.app_name}
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)] mt-0.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
            Connected
          </div>
        </div>
        {onToggleTheme && (
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        )}
        {(identity || userId) && (
          <div className="flex items-center gap-2.5 flex-shrink-0 min-w-0 max-w-[60%]">
            <div className="hidden sm:block text-right min-w-0">
              <div className="text-sm font-semibold text-[var(--text)] truncate">
                {displayName || '—'}
              </div>
              {authEnabled ? (
                email && (
                  <div className="text-[11px] text-[var(--text-muted)] truncate">
                    {email}
                  </div>
                )
              ) : (
                <div className="text-[11px] text-[var(--text-muted)] truncate">
                  Local user
                </div>
              )}
            </div>
            {picture && !imgFailed ? (
              <img
                src={picture}
                alt=""
                referrerPolicy="no-referrer"
                onError={() => setImgFailed(true)}
                className="w-9 h-9 rounded-full flex-shrink-0 border border-[var(--border)] object-cover"
              />
            ) : (
              <div className="w-9 h-9 rounded-full flex-shrink-0 bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold">
                {initials}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
