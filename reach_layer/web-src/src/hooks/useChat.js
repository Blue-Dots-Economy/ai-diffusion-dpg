import { useState, useCallback } from 'react'
import { sendChat, fetchUserHistory } from '../api'
import { generateUUID } from '../utils'

/**
 * Manages all chat state: messages, in-flight status, session ID.
 *
 * @param {{ onError: (msg: string) => void }} options
 * @returns {{
 *   messages: Array,
 *   isSending: boolean,
 *   sessionId: string|null,
 *   newestAgentId: string|null,
 *   loadHistory: (userId: string) => Promise<{isReturning: boolean}>,
 *   send: ({text: string, userId: string|null}) => Promise<void>,
 *   reset: () => void,
 * }}
 */
export function useChat({ onError }) {
  const [messages, setMessages] = useState([])
  const [isSending, setIsSending] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  // Track the id of the most recently arrived agent message for word-reveal
  const [newestAgentId, setNewestAgentId] = useState(null)

  /**
   * Load history for a returning user. Sets sessionId and messages.
   * Returns { isReturning: true } if history was found.
   */
  const loadHistory = useCallback(async userId => {
    try {
      const data = await fetchUserHistory(userId)
      if (data.session_id) {
        setSessionId(data.session_id)
        const historyMsgs = (data.turns || []).flatMap(turn => {
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
        return { isReturning: historyMsgs.length > 0 }
      }
    } catch (err) {
      console.warn('[useChat] loadHistory failed, starting fresh session:', err)
    }
    setSessionId(generateUUID())
    return { isReturning: false }
  }, [])

  /**
   * Send a user message and append the agent reply.
   */
  const send = useCallback(
    async ({ text, userId }) => {
      if (isSending || !text.trim()) return

      const sid = sessionId || generateUUID()
      if (!sessionId) setSessionId(sid)

      const userMsg = {
        id: generateUUID(),
        role: 'user',
        text: text.trim(),
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => [...prev, userMsg])
      setIsSending(true)

      try {
        const data = await sendChat({ sessionId: sid, userId, message: text.trim() })
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
      } catch {
        onError('Connection error. Please check your network and try again.')
      } finally {
        setIsSending(false)
      }
    },
    [isSending, sessionId, onError]
  )

  /**
   * Clear messages and generate a new session ID (local reset only).
   */
  const reset = useCallback(() => {
    setMessages([])
    setSessionId(generateUUID())
    setNewestAgentId(null)
  }, [])

  return { messages, isSending, sessionId, newestAgentId, loadHistory, send, reset }
}
