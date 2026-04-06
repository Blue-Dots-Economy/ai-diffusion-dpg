import { useState, useEffect } from 'react'

/**
 * Persist and apply dark/light theme via .dark class on <html>.
 * @returns {{ theme: 'dark'|'light', toggle: () => void }}
 */
export function useTheme() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem('dpg_theme') || 'dark'
  )

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('dpg_theme', theme)
  }, [theme])

  const toggle = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'))

  return { theme, toggle }
}
