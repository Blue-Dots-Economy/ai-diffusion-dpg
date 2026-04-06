import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from '../hooks/useChat'
import * as api from '../api'
import * as utils from '../utils'

describe('useChat', () => {
  let onError

  beforeEach(() => {
    onError = vi.fn()
    vi.spyOn(utils, 'generateUUID').mockReturnValue('test-uuid')
  })

  afterEach(() => { vi.restoreAllMocks() })

  describe('initial state', () => {
    it('starts with empty messages', () => {
      const { result } = renderHook(() => useChat({ onError }))
      expect(result.current.messages).toEqual([])
    })

    it('starts with isSending false', () => {
      const { result } = renderHook(() => useChat({ onError }))
      expect(result.current.isSending).toBe(false)
    })

    it('starts with null sessionId', () => {
      const { result } = renderHook(() => useChat({ onError }))
      expect(result.current.sessionId).toBeNull()
    })
  })

  describe('loadHistory', () => {
    it('sets sessionId and messages from history', async () => {
      vi.spyOn(api, 'fetchUserHistory').mockResolvedValue({
        session_id: 'sess-abc',
        turns: [{ user_message: 'Hello', system_message: 'Hi there', timestamp: '2024-01-01T00:00:00Z' }],
      })
      const { result } = renderHook(() => useChat({ onError }))
      let ret
      await act(async () => { ret = await result.current.loadHistory('user1') })
      expect(result.current.sessionId).toBe('sess-abc')
      expect(result.current.messages).toHaveLength(2)
      expect(ret).toEqual({ isReturning: true })
    })

    it('returns isReturning false when no history found', async () => {
      vi.spyOn(api, 'fetchUserHistory').mockResolvedValue({ session_id: null, turns: [] })
      const { result } = renderHook(() => useChat({ onError }))
      let ret
      await act(async () => { ret = await result.current.loadHistory('new-user') })
      expect(ret).toEqual({ isReturning: false })
      expect(result.current.sessionId).toBe('test-uuid')
    })

    it('generates fresh sessionId on fetch error', async () => {
      vi.spyOn(api, 'fetchUserHistory').mockRejectedValue(new Error('Network'))
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.loadHistory('user') })
      expect(result.current.sessionId).toBe('test-uuid')
    })

    it('maps turns to user and agent messages', async () => {
      vi.spyOn(api, 'fetchUserHistory').mockResolvedValue({
        session_id: 'sess-1',
        turns: [{ user_message: 'Question', system_message: 'Answer' }],
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.loadHistory('u1') })
      const [userMsg, agentMsg] = result.current.messages
      expect(userMsg.role).toBe('user')
      expect(userMsg.text).toBe('Question')
      expect(agentMsg.role).toBe('agent')
      expect(agentMsg.text).toBe('Answer')
    })
  })

  describe('send', () => {
    it('appends user message immediately', async () => {
      vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Reply', was_escalated: false, was_tool_used: false, latency_ms: 50,
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: 'Hello', userId: 'u1' }) })
      expect(result.current.messages[0].role).toBe('user')
      expect(result.current.messages[0].text).toBe('Hello')
    })

    it('appends agent reply after successful sendChat', async () => {
      vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'World', was_escalated: false, was_tool_used: true, latency_ms: 100,
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: 'Hi', userId: null }) })
      const agentMsg = result.current.messages[1]
      expect(agentMsg.role).toBe('agent')
      expect(agentMsg.text).toBe('World')
      expect(agentMsg.wasToolUsed).toBe(true)
      expect(agentMsg.latencyMs).toBe(100)
    })

    it('calls onError on network failure', async () => {
      vi.spyOn(api, 'sendChat').mockRejectedValue(new Error('timeout'))
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: 'Hi', userId: null }) })
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Connection error'))
    })

    it('sets isSending to false after completion', async () => {
      vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Ok', was_escalated: false, was_tool_used: false, latency_ms: 10,
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: 'Hi', userId: null }) })
      expect(result.current.isSending).toBe(false)
    })

    it('does nothing when text is empty', async () => {
      const spy = vi.spyOn(api, 'sendChat')
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: '   ', userId: null }) })
      expect(spy).not.toHaveBeenCalled()
      expect(result.current.messages).toHaveLength(0)
    })
  })

  describe('reset', () => {
    it('clears messages and generates new sessionId', async () => {
      vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Hi', was_escalated: false, was_tool_used: false, latency_ms: 10,
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.send({ text: 'Message', userId: null }) })
      act(() => { result.current.reset() })
      expect(result.current.messages).toHaveLength(0)
      expect(result.current.sessionId).toBe('test-uuid')
      expect(result.current.newestAgentId).toBeNull()
    })
  })
})
