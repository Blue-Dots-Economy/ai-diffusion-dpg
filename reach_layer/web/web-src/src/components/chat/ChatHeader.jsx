import { useState, useRef, useEffect } from 'react'
import { ThemeToggle } from '../ui/ThemeToggle'
import { ConfirmDialog } from '../ui/ConfirmDialog'

function LogoutIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M6 12.5a.5.5 0 0 0 .5.5h8a.5.5 0 0 0 .5-.5v-9a.5.5 0 0 0-.5-.5h-8a.5.5 0 0 0-.5.5v2a.5.5 0 0 1-1 0v-2A1.5 1.5 0 0 1 6.5 2h8A1.5 1.5 0 0 1 16 3.5v9a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 5 12.5v-2a.5.5 0 0 1 1 0z" />
      <path d="M.146 8.354a.5.5 0 0 1 0-.708l3-3a.5.5 0 1 1 .708.708L1.707 7.5H10.5a.5.5 0 0 1 0 1H1.707l2.147 2.146a.5.5 0 0 1-.708.708z" />
    </svg>
  )
}

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
 *   onSignOut?: () => void,
 * }} props
 */
export function ChatHeader({ config, identity, userId, authEnabled, theme, onToggleTheme, onSignOut }) {
  const [imgFailed, setImgFailed] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const menuRef = useRef(null)

  const displayName = identity?.display_name || userId || ''
  const email = identity?.email || ''
  const picture = identity?.picture || ''
  const initials = initialsOf(identity?.display_name, userId)

  const signOutLabel = authEnabled ? 'Sign out' : 'Switch user'
  const signOutConfirm =
    (authEnabled ? config.sign_out_confirm : config.switch_user_confirm) ||
    (authEnabled ? 'Sign out of your account?' : 'Switch user? Your current session will be closed.')
  const cancelLabel = config.cancel_label || 'Cancel'

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!menuOpen) return
    const handleClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [menuOpen])

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
          <div className="relative flex items-center gap-2.5 flex-shrink-0 min-w-0 max-w-[60%]" ref={menuRef}>
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
            <button
              onClick={() => setMenuOpen((v) => !v)}
              title="Profile menu"
              aria-label="Profile menu"
              aria-expanded={menuOpen}
              className="rounded-full flex-shrink-0 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1"
            >
              {picture && !imgFailed ? (
                <img
                  src={picture}
                  alt=""
                  referrerPolicy="no-referrer"
                  onError={() => setImgFailed(true)}
                  className="w-9 h-9 rounded-full border border-[var(--border)] object-cover"
                />
              ) : (
                <div className="w-9 h-9 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold">
                  {initials}
                </div>
              )}
            </button>

            {/* Profile dropdown */}
            {menuOpen && (
              <div className="absolute right-0 top-full mt-2 w-44 rounded-lg bg-[var(--surface)] border border-[var(--border)] shadow-lg z-50 py-1">
                <button
                  onClick={() => {
                    setMenuOpen(false)
                    setConfirmOpen(true)
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-[12px] font-semibold text-red-400 hover:text-red-300 hover:bg-red-500/10 transition-colors"
                >
                  <LogoutIcon />
                  <span>{signOutLabel}</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={signOutLabel}
        message={signOutConfirm}
        confirmLabel={signOutLabel}
        cancelLabel={cancelLabel}
        danger
        onConfirm={() => {
          setConfirmOpen(false)
          onSignOut && onSignOut()
        }}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  )
}
