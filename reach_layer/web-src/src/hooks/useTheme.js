import { useState, useEffect } from 'react'

/**
 * Persist and apply dark/light theme via .dark class on <html>.
 * The localStorage key is read from the domain config so it can vary
 * per deployment without touching source code.
 *
 * @param {string} storageKey - localStorage key sourced from /app-config
 *   (config.theme_storage_key). Defaults to 'dpg_theme' only as a
 *   last-resort fallback when config has not yet loaded.
 * @returns {{ theme: 'dark'|'light', toggle: () => void }}
 */
export function useTheme(storageKey = 'dpg_theme') {
  const [theme, setTheme] = useState(
    () => localStorage.getItem(storageKey) || 'dark'
  )

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem(storageKey, theme)
  }, [theme, storageKey])

  const toggle = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'))

  return { theme, toggle }
}
