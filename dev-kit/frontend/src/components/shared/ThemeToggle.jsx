import React from 'react'
import { useTheme } from '../../ThemeContext'

/**
 * Theme switcher button. Rendered into every top-level view's header so the
 * user can flip dark↔light from anywhere in the flow (chat, dashboard,
 * config editor, deploy wizard, project list). Uses the same emoji + button
 * styling everywhere so it reads as a single global control.
 */
export default function ThemeToggle({ className = '' }) {
  const { theme, toggle } = useTheme()
  return (
    <button
      onClick={toggle}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className={`text-xs px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors ${className}`}
    >
      {theme === 'dark' ? '☀' : '☾'}
    </button>
  )
}
