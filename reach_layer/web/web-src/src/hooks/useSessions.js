import { useCallback, useEffect, useState } from 'react'
import { fetchSessions, deleteSession as apiDeleteSession } from '../api'

/**
 * Tracks the user's previous sessions for the sidebar conversations list.
 *
 * Calls /sessions on mount (and when userId changes). Exposes a refresh()
 * to re-pull after sending a turn or starting a new chat, and remove() to
 * delete a single session both server-side and from local state.
 *
 * @param {{ userId: string|null, authEnabled: boolean }} options
 * @returns {{
 *   sessions: Array,
 *   loading: boolean,
 *   refresh: () => Promise<void>,
 *   remove: (sessionId: string) => Promise<void>,
 * }}
 */
export function useSessions({ userId, authEnabled }) {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!userId) {
      setSessions([])
      return
    }
    setLoading(true)
    try {
      // When auth is enabled the cookie is authoritative; pass null so the
      // server uses the cookie identity. When disabled we must pass userId.
      const data = await fetchSessions(authEnabled ? null : userId)
      setSessions(Array.isArray(data?.sessions) ? data.sessions : [])
    } catch (err) {
      console.warn('[useSessions] refresh failed:', err)
      setSessions([])
    } finally {
      setLoading(false)
    }
  }, [userId, authEnabled])

  const remove = useCallback(
    async (sessionId) => {
      if (!sessionId) return
      try {
        await apiDeleteSession(sessionId, authEnabled ? null : userId)
        setSessions((prev) => prev.filter((s) => s.session_id !== sessionId))
      } catch (err) {
        console.warn('[useSessions] delete failed:', err)
      }
    },
    [userId, authEnabled]
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  return { sessions, loading, refresh, remove }
}
