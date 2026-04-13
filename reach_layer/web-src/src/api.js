/**
 * Fetch UI branding + public auth config from the server.
 * @returns {Promise<Object>}
 */
export async function fetchAppConfig() {
  const res = await fetch('/app-config')
  if (!res.ok) throw new Error(`/app-config responded ${res.status}`)
  return res.json()
}

/**
 * Fetch active session and chat history for a returning user.
 * Sends cookies so the server can authorise the request.
 * @param {string} userId
 * @returns {Promise<{session_id: string|null, turns: Array}>}
 */
export async function fetchUserHistory(userId) {
  const res = await fetch(`/user-history/${encodeURIComponent(userId)}`, {
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`/user-history responded ${res.status}`)
  return res.json()
}

/**
 * Send a chat turn to Agent Core via the Reach Layer proxy.
 * Sends cookies so the server can authorise the request.
 * @param {{sessionId: string, userId: string|null, message: string}} params
 * @returns {Promise<{response_text: string, was_escalated: boolean, was_tool_used: boolean, latency_ms: number}>}
 */
export async function sendChat({ sessionId, userId, message, fresh = false }) {
  const res = await fetch('/chat', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      user_id: userId || null,
      message,
      fresh: !!fresh,
    }),
  })
  if (!res.ok) throw new Error(`/chat responded ${res.status}`)
  return res.json()
}

/**
 * List the current user's recent sessions (most recent first, capped at 25).
 * @param {string|null} userId Used only when auth is disabled.
 * @returns {Promise<{sessions: Array<{session_id: string, last_accessed: string, title: string}>}>}
 */
export async function fetchSessions(userId = null) {
  const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  const res = await fetch(`/sessions${qs}`, { credentials: 'include' })
  if (!res.ok) throw new Error(`/sessions responded ${res.status}`)
  return res.json()
}

/**
 * Fetch the chat history for a specific session owned by the caller.
 * @param {string} sessionId
 * @param {string|null} userId Used only when auth is disabled.
 * @returns {Promise<{session_id: string, turns: Array}>}
 */
export async function fetchSessionHistory(sessionId, userId = null) {
  const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  const res = await fetch(`/sessions/${encodeURIComponent(sessionId)}/history${qs}`, {
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`/sessions/${sessionId}/history responded ${res.status}`)
  return res.json()
}

/**
 * Delete a session (Redis state + SQLite audit).
 * @param {string} sessionId
 * @param {string|null} userId Used only when auth is disabled.
 * @returns {Promise<{ok: boolean, session_id: string}>}
 */
export async function deleteSession(sessionId, userId = null) {
  const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  const res = await fetch(`/sessions/${encodeURIComponent(sessionId)}${qs}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`/sessions/${sessionId} DELETE responded ${res.status}`)
  return res.json()
}

/**
 * Exchange a Google ID token (from GIS) for a server session cookie.
 * @param {string} credential GIS-issued ID token.
 * @returns {Promise<{user_id: string, display_name: string, email: string, picture: string}>}
 */
export async function exchangeGoogleCredential(credential) {
  const res = await fetch('/auth/google', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential }),
  })
  if (!res.ok) throw new Error(`/auth/google responded ${res.status}`)
  return res.json()
}

/**
 * Return the current authenticated identity, or null when no/expired cookie.
 * @returns {Promise<{user_id: string, display_name: string} | null>}
 */
export async function fetchCurrentUser() {
  const res = await fetch('/auth/me', { credentials: 'include' })
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`/auth/me responded ${res.status}`)
  return res.json()
}

/**
 * Clear the server session cookie.
 * @returns {Promise<void>}
 */
export async function logout() {
  await fetch('/auth/logout', { method: 'POST', credentials: 'include' })
}
