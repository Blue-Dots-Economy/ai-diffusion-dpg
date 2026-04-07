/**
 * Fetch UI branding config from the server.
 * @returns {Promise<Object>}
 */
export async function fetchAppConfig() {
  const res = await fetch('/app-config')
  if (!res.ok) throw new Error(`/app-config responded ${res.status}`)
  return res.json()
}

/**
 * Fetch active session and chat history for a returning user.
 * @param {string} userId
 * @returns {Promise<{session_id: string|null, turns: Array}>}
 */
export async function fetchUserHistory(userId) {
  const res = await fetch(`/user-history/${encodeURIComponent(userId)}`)
  if (!res.ok) throw new Error(`/user-history responded ${res.status}`)
  return res.json()
}

/**
 * Send a chat turn to Agent Core via the Reach Layer proxy.
 * @param {{sessionId: string, userId: string|null, message: string}} params
 * @returns {Promise<{response_text: string, was_escalated: boolean, was_tool_used: boolean, latency_ms: number}>}
 */
export async function sendChat({ sessionId, userId, message }) {
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      user_id: userId || null,
      message,
    }),
  })
  if (!res.ok) throw new Error(`/chat responded ${res.status}`)
  return res.json()
}
