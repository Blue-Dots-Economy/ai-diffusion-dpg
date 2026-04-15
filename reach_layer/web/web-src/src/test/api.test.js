import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  fetchAppConfig,
  fetchUserHistory,
  sendChat,
  exchangeGoogleCredential,
  fetchCurrentUser,
  logout,
  fetchSessions,
  fetchSessionHistory,
  deleteSession,
} from '../api'

function mockFetch(body, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  })
}

describe('fetchAppConfig', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('returns parsed JSON on success', async () => {
    global.fetch = mockFetch({ app_name: 'Test', app_icon: '🤖' })
    const result = await fetchAppConfig()
    expect(result).toEqual({ app_name: 'Test', app_icon: '🤖' })
    expect(fetch).toHaveBeenCalledWith('/app-config')
  })

  it('throws when server returns non-OK status', async () => {
    global.fetch = mockFetch({}, 500)
    await expect(fetchAppConfig()).rejects.toThrow('/app-config responded 500')
  })
})

describe('fetchUserHistory', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('fetches user history with encoded user ID', async () => {
    const history = { session_id: 'sess-1', turns: [] }
    global.fetch = mockFetch(history)
    const result = await fetchUserHistory('user123')
    expect(result).toEqual(history)
    expect(fetch).toHaveBeenCalledWith('/user-history/user123', { credentials: 'include' })
  })

  it('URL-encodes special characters in user ID', async () => {
    global.fetch = mockFetch({ session_id: null, turns: [] })
    await fetchUserHistory('user@domain.com')
    expect(fetch).toHaveBeenCalledWith('/user-history/user%40domain.com', { credentials: 'include' })
  })

  it('throws when server returns non-OK status', async () => {
    global.fetch = mockFetch({}, 404)
    await expect(fetchUserHistory('ghost')).rejects.toThrow('/user-history responded 404')
  })
})

describe('sendChat', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('sends POST with correct JSON body', async () => {
    const reply = { response_text: 'Hello', was_escalated: false, latency_ms: 120 }
    global.fetch = mockFetch(reply)
    const result = await sendChat({ sessionId: 's1', userId: 'u1', message: 'Hi' })
    expect(result).toEqual(reply)
    expect(fetch).toHaveBeenCalledWith('/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: 's1', user_id: 'u1', message: 'Hi', fresh: false }),
    })
  })

  it('sends fresh=true when starting a new chat', async () => {
    global.fetch = mockFetch({ response_text: 'Ok' })
    await sendChat({ sessionId: 's1', userId: 'u1', message: 'Hi', fresh: true })
    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body.fresh).toBe(true)
  })

  it('sends null user_id when userId is falsy', async () => {
    global.fetch = mockFetch({ response_text: 'Ok' })
    await sendChat({ sessionId: 's1', userId: null, message: 'test' })
    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body.user_id).toBeNull()
    expect(body.fresh).toBe(false)
  })

  it('throws when server returns non-OK status', async () => {
    global.fetch = mockFetch({}, 503)
    await expect(sendChat({ sessionId: 's1', userId: null, message: 'hi' }))
      .rejects.toThrow('/chat responded 503')
  })
})

describe('exchangeGoogleCredential', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('POSTs the credential and returns identity', async () => {
    const identity = { user_id: 'google:1', display_name: 'Alice', email: 'a@x', picture: '' }
    global.fetch = mockFetch(identity)
    const result = await exchangeGoogleCredential('jwt-token')
    expect(result).toEqual(identity)
    expect(fetch).toHaveBeenCalledWith('/auth/google', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: 'jwt-token' }),
    })
  })

  it('throws on non-OK response', async () => {
    global.fetch = mockFetch({}, 401)
    await expect(exchangeGoogleCredential('bad')).rejects.toThrow('/auth/google responded 401')
  })
})

describe('fetchCurrentUser', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('returns identity on 200', async () => {
    const me = { user_id: 'google:1', display_name: 'Alice' }
    global.fetch = mockFetch(me)
    const result = await fetchCurrentUser()
    expect(result).toEqual(me)
    expect(fetch).toHaveBeenCalledWith('/auth/me', { credentials: 'include' })
  })

  it('returns null on 401', async () => {
    global.fetch = mockFetch({}, 401)
    const result = await fetchCurrentUser()
    expect(result).toBeNull()
  })

  it('throws on other error statuses', async () => {
    global.fetch = mockFetch({}, 500)
    await expect(fetchCurrentUser()).rejects.toThrow('/auth/me responded 500')
  })
})

describe('fetchSessions', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('GETs /sessions without query when userId is null', async () => {
    global.fetch = mockFetch({ sessions: [] })
    await fetchSessions(null)
    expect(fetch).toHaveBeenCalledWith('/sessions', { credentials: 'include' })
  })

  it('GETs /sessions?user_id=... when userId provided', async () => {
    global.fetch = mockFetch({ sessions: [{ session_id: 's1', title: 't' }] })
    const out = await fetchSessions('u@x')
    expect(out.sessions).toHaveLength(1)
    expect(fetch).toHaveBeenCalledWith('/sessions?user_id=u%40x', { credentials: 'include' })
  })

  it('throws on non-OK', async () => {
    global.fetch = mockFetch({}, 500)
    await expect(fetchSessions(null)).rejects.toThrow(/sessions responded 500/)
  })
})

describe('fetchSessionHistory', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('GETs the per-session history endpoint', async () => {
    global.fetch = mockFetch({ session_id: 's1', turns: [] })
    await fetchSessionHistory('s1')
    expect(fetch).toHaveBeenCalledWith('/sessions/s1/history', { credentials: 'include' })
  })

  it('appends user_id query when provided', async () => {
    global.fetch = mockFetch({ session_id: 's1', turns: [] })
    await fetchSessionHistory('s1', 'alice')
    expect(fetch).toHaveBeenCalledWith('/sessions/s1/history?user_id=alice', { credentials: 'include' })
  })

  it('throws on non-OK', async () => {
    global.fetch = mockFetch({}, 404)
    await expect(fetchSessionHistory('s1')).rejects.toThrow(/responded 404/)
  })
})

describe('deleteSession', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('DELETEs the session endpoint', async () => {
    global.fetch = mockFetch({ ok: true, session_id: 's1' })
    const out = await deleteSession('s1')
    expect(out.ok).toBe(true)
    expect(fetch).toHaveBeenCalledWith('/sessions/s1', {
      method: 'DELETE',
      credentials: 'include',
    })
  })

  it('appends user_id query when provided', async () => {
    global.fetch = mockFetch({ ok: true, session_id: 's1' })
    await deleteSession('s1', 'alice')
    expect(fetch).toHaveBeenCalledWith('/sessions/s1?user_id=alice', {
      method: 'DELETE',
      credentials: 'include',
    })
  })

  it('throws on non-OK', async () => {
    global.fetch = mockFetch({}, 500)
    await expect(deleteSession('s1')).rejects.toThrow(/DELETE responded 500/)
  })
})

describe('logout', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('POSTs to /auth/logout with credentials', async () => {
    global.fetch = mockFetch({ ok: true })
    await logout()
    expect(fetch).toHaveBeenCalledWith('/auth/logout', {
      method: 'POST',
      credentials: 'include',
    })
  })
})
