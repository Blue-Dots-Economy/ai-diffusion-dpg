import { useState, useEffect, useCallback } from 'react'

/**
 * Persist sidebar collapsed state in localStorage so it survives reloads.
 *
 * @param {string} storageKey - localStorage key. Should be domain-scoped
 *   (e.g. config.storage_key + '_sidebar') so multiple deployments don't
 *   collide on the same browser.
 * @returns {{ collapsed: boolean, toggle: () => void, setCollapsed: (v: boolean) => void }}
 */
export function useSidebar(storageKey = 'dpg_sidebar_collapsed') {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(storageKey) === '1'
  )

  useEffect(() => {
    localStorage.setItem(storageKey, collapsed ? '1' : '0')
  }, [collapsed, storageKey])

  const toggle = useCallback(() => setCollapsed(c => !c), [])

  return { collapsed, toggle, setCollapsed }
}
