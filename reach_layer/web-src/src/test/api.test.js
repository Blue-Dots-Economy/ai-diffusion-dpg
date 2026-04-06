import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fetchAppConfig, fetchUserHistory, sendChat } from '../api'

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
    expect(fetch).toHaveBeenCalledWith('/user-history/user123')
  })

  it('URL-encodes special characters in user ID', async () => {
    global.fetch = mockFetch({ session_id: null, turns: [] })
    await fetchUserHistory('user@domain.com')
    expect(fetch).toHaveBeenCalledWith('/user-history/user%40domain.com')
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: 's1', user_id: 'u1', message: 'Hi' }),
    })
  })

  it('sends null user_id when userId is falsy', async () => {
    global.fetch = mockFetch({ response_text: 'Ok' })
    await sendChat({ sessionId: 's1', userId: null, message: 'test' })
    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body.user_id).toBeNull()
  })

  it('throws when server returns non-OK status', async () => {
    global.fetch = mockFetch({}, 503)
    await expect(sendChat({ sessionId: 's1', userId: null, message: 'hi' }))
      .rejects.toThrow('/chat responded 503')
  })
})
