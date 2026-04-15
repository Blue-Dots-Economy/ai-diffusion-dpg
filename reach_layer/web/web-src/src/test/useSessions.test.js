import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useSessions } from '../hooks/useSessions'
import * as api from '../api'

describe('useSessions', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('starts empty when userId is null', async () => {
    const spy = vi.spyOn(api, 'fetchSessions').mockResolvedValue({ sessions: [] })
    const { result } = renderHook(() => useSessions({ userId: null, authEnabled: false }))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.sessions).toEqual([])
    expect(spy).not.toHaveBeenCalled()
  })

  it('fetches sessions on mount when userId is set', async () => {
    const fixture = [
      { session_id: 'a', last_accessed: '2026-04-01', title: 'A' },
      { session_id: 'b', last_accessed: '2026-03-31', title: 'B' },
    ]
    vi.spyOn(api, 'fetchSessions').mockResolvedValue({ sessions: fixture })
    const { result } = renderHook(() =>
      useSessions({ userId: 'alice', authEnabled: false })
    )
    await waitFor(() => expect(result.current.sessions).toHaveLength(2))
    expect(result.current.sessions[0].title).toBe('A')
  })

  it('passes null userId to api when auth is enabled (cookie-bound)', async () => {
    const spy = vi.spyOn(api, 'fetchSessions').mockResolvedValue({ sessions: [] })
    renderHook(() => useSessions({ userId: 'alice', authEnabled: true }))
    await waitFor(() => expect(spy).toHaveBeenCalled())
    expect(spy).toHaveBeenCalledWith(null)
  })

  it('remove() optimistically drops the session from local state', async () => {
    const fixture = [
      { session_id: 'a', last_accessed: '2026-04-01', title: 'A' },
      { session_id: 'b', last_accessed: '2026-03-31', title: 'B' },
    ]
    vi.spyOn(api, 'fetchSessions').mockResolvedValue({ sessions: fixture })
    const delSpy = vi.spyOn(api, 'deleteSession').mockResolvedValue({ ok: true, session_id: 'a' })
    const { result } = renderHook(() =>
      useSessions({ userId: 'alice', authEnabled: false })
    )
    await waitFor(() => expect(result.current.sessions).toHaveLength(2))
    await act(async () => { await result.current.remove('a') })
    expect(delSpy).toHaveBeenCalledWith('a', 'alice')
    expect(result.current.sessions.map((s) => s.session_id)).toEqual(['b'])
  })

  it('refresh() re-pulls and replaces the list', async () => {
    const spy = vi
      .spyOn(api, 'fetchSessions')
      .mockResolvedValueOnce({ sessions: [{ session_id: 'a', title: 'A' }] })
      .mockResolvedValueOnce({ sessions: [{ session_id: 'a', title: 'A' }, { session_id: 'b', title: 'B' }] })
    const { result } = renderHook(() =>
      useSessions({ userId: 'alice', authEnabled: false })
    )
    await waitFor(() => expect(result.current.sessions).toHaveLength(1))
    await act(async () => { await result.current.refresh() })
    expect(result.current.sessions).toHaveLength(2)
    expect(spy).toHaveBeenCalledTimes(2)
  })

  it('handles fetch errors gracefully', async () => {
    vi.spyOn(api, 'fetchSessions').mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() =>
      useSessions({ userId: 'alice', authEnabled: false })
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.sessions).toEqual([])
  })
})
