import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
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

  describe('startNewChat', () => {
    it('clears messages and assigns a new sessionId', () => {
      const { result } = renderHook(() => useChat({ onError }))
      act(() => { result.current.startNewChat() })
      expect(result.current.messages).toEqual([])
      expect(result.current.sessionId).toBe('test-uuid')
    })

    it('arms the next send with fresh=true', async () => {
      const sendSpy = vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Ok', was_escalated: false, was_tool_used: false, latency_ms: 1,
      })
      const { result } = renderHook(() => useChat({ onError }))
      act(() => { result.current.startNewChat() })
      await act(async () => { await result.current.send({ text: 'Hi', userId: 'u1' }) })
      expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ fresh: true }))
    })

    it('subsequent sends in the same chat are not fresh', async () => {
      const sendSpy = vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Ok', was_escalated: false, was_tool_used: false, latency_ms: 1,
      })
      const { result } = renderHook(() => useChat({ onError }))
      act(() => { result.current.startNewChat() })
      await act(async () => { await result.current.send({ text: 'Hi', userId: 'u1' }) })
      await act(async () => { await result.current.send({ text: 'Again', userId: 'u1' }) })
      expect(sendSpy).toHaveBeenNthCalledWith(2, expect.objectContaining({ fresh: false }))
    })
  })

  describe('loadSession', () => {
    it('hydrates messages and sessionId from server', async () => {
      vi.spyOn(api, 'fetchSessionHistory').mockResolvedValue({
        session_id: 'sess-abc',
        turns: [{ user_message: 'Hello', system_message: 'Hi there' }],
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.loadSession('sess-abc') })
      expect(result.current.sessionId).toBe('sess-abc')
      expect(result.current.messages).toHaveLength(2)
      expect(result.current.messages[0].text).toBe('Hello')
      expect(result.current.messages[1].text).toBe('Hi there')
    })

    it('reports an error toast on failure', async () => {
      vi.spyOn(api, 'fetchSessionHistory').mockRejectedValue(new Error('boom'))
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.loadSession('s') })
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Could not load'))
    })

    it('next send after loadSession is not fresh', async () => {
      vi.spyOn(api, 'fetchSessionHistory').mockResolvedValue({
        session_id: 'sess-abc', turns: [],
      })
      const sendSpy = vi.spyOn(api, 'sendChat').mockResolvedValue({
        response_text: 'Ok', was_escalated: false, was_tool_used: false, latency_ms: 1,
      })
      const { result } = renderHook(() => useChat({ onError }))
      await act(async () => { await result.current.loadSession('sess-abc') })
      await act(async () => { await result.current.send({ text: 'continue', userId: 'u1' }) })
      expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({
        fresh: false,
        sessionId: 'sess-abc',
      }))
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
