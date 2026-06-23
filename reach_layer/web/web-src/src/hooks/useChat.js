import { useState, useCallback } from 'react'
import { sendChat, fetchSessionHistory } from '../api'
import { generateUUID } from '../utils'

/**
 * Manages all chat state: messages, in-flight status, session ID.
 *
 * The hook supports the ChatGPT-style multi-session model:
 *   - startNewChat() begins a fresh conversation (new session_id, no
 *     adoption of prior session state on the next turn).
 *   - loadSession(sessionId) hydrates messages from the server for an
 *     existing session and continues it on the next send.
 *
 * @param {{ onError: (msg: string) => void }} options
 */
export function useChat({ onError }) {
  const [messages, setMessages] = useState([])
  const [isSending, setIsSending] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  // Track the id of the most recently arrived agent message for word-reveal
  const [newestAgentId, setNewestAgentId] = useState(null)
  // When true the next /chat call will carry fresh=true so Memory Layer
  // does not adopt state from the user's previous active session.
  const [freshOnNextSend, setFreshOnNextSend] = useState(false)

  /**
   * Begin a brand-new conversation. Generates a new session_id and arms
   * the next send with fresh=true so Memory Layer treats it as a clean
   * slate (only persistent profile facts carry over).
   */
  const startNewChat = useCallback(() => {
    setMessages([])
    setSessionId(generateUUID())
    setNewestAgentId(null)
    setFreshOnNextSend(true)
  }, [])

  /**
   * Hydrate state for an existing session so the user can continue it.
   * Falls back to a fresh chat on failure.
   *
   * @param {string} targetSessionId Session to load.
   * @param {string|null} userId Used only when auth is disabled.
   */
  const loadSession = useCallback(async (targetSessionId, userId = null) => {
    if (!targetSessionId) return
    try {
      const data = await fetchSessionHistory(targetSessionId, userId)
      const turns = data.turns || []
      const historyMsgs = turns.flatMap(turn => {
        const msgs = []
        if (turn.user_message) {
          msgs.push({
            id: generateUUID(),
            role: 'user',
            text: turn.user_message,
            timestamp: turn.timestamp || new Date().toISOString(),
          })
        }
        if (turn.system_message) {
          msgs.push({
            id: generateUUID(),
            role: 'agent',
            text: turn.system_message,
            timestamp: turn.timestamp || new Date().toISOString(),
            latencyMs: null,
            wasToolUsed: false,
            wasEscalated: false,
          })
        }
        return msgs
      })
      setMessages(historyMsgs)
      setSessionId(targetSessionId)
      setNewestAgentId(null)
      setFreshOnNextSend(false)
    } catch (err) {
      console.warn('[useChat] loadSession failed:', err)
      onError && onError('Could not load that conversation.')
    }
  }, [onError])

  /**
   * Send a user message and append the agent reply.
   */
  const send = useCallback(
    async ({ text, userId }) => {
      if (isSending || !text.trim()) return

      const sid = sessionId || generateUUID()
      if (!sessionId) setSessionId(sid)
      const sendingFresh = freshOnNextSend

      const userMsg = {
        id: generateUUID(),
        role: 'user',
        text: text.trim(),
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => [...prev, userMsg])
      setIsSending(true)

      try {
        const data = await sendChat({
          sessionId: sid,
          userId,
          message: text.trim(),
          fresh: sendingFresh,
        })
        if (data.error_type) {
          const errorMsg = {
            id: generateUUID(),
            role: 'system_error',
            text: data.error_message || 'An error occurred.',
            timestamp: new Date().toISOString(),
          }
          setMessages(prev => [...prev, errorMsg])
          if (sendingFresh) setFreshOnNextSend(false)
          return
        }
        const agentId = generateUUID()
        const agentMsg = {
          id: agentId,
          role: 'agent',
          text: data.response_text || '(no response)',
          timestamp: new Date().toISOString(),
          latencyMs: data.latency_ms ?? null,
          wasToolUsed: !!data.was_tool_used,
          wasEscalated: !!data.was_escalated,
        }
        setMessages(prev => [...prev, agentMsg])
        setNewestAgentId(agentId)
        if (sendingFresh) setFreshOnNextSend(false)
      } catch (err) {
        const errorMsg = {
          id: generateUUID(),
          role: 'system_error',
          text: 'Connection error. Please check your network and try again.',
          timestamp: new Date().toISOString(),
        }
        setMessages(prev => [...prev, errorMsg])
        onError('Connection error. Please check your network and try again.')
      } finally {
        setIsSending(false)
      }
    },
    [isSending, sessionId, freshOnNextSend, onError]
  )

  /**
   * Clear messages and generate a new session ID (local reset only).
   * Equivalent to startNewChat — kept for API compatibility.
   */
  const reset = useCallback(() => {
    startNewChat()
  }, [startNewChat])

  return {
    messages,
    isSending,
    sessionId,
    newestAgentId,
    startNewChat,
    loadSession,
    send,
    reset,
  }
}
