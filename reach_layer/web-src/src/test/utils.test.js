import { describe, it, expect, vi, beforeEach } from 'vitest'
import { generateUUID, formatTime, formatFullTime } from '../utils'

describe('generateUUID', () => {
  it('returns a string in UUID v4 format', () => {
    const id = generateUUID()
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  })

  it('returns unique values on successive calls', () => {
    const ids = new Set(Array.from({ length: 20 }, generateUUID))
    expect(ids.size).toBe(20)
  })

  it('falls back to Math.random when crypto.randomUUID is unavailable', () => {
    const original = crypto.randomUUID
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true })
    const id = generateUUID()
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
    Object.defineProperty(crypto, 'randomUUID', { value: original, configurable: true })
  })
})

describe('formatTime', () => {
  it('returns empty string for null', () => {
    expect(formatTime(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatTime(undefined)).toBe('')
  })

  it('formats a valid ISO timestamp to HH:MM', () => {
    const result = formatTime('2024-01-15T14:30:00.000Z')
    // Just verify it returns a non-empty string with colon (locale-dependent)
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it('accepts a Date object', () => {
    const d = new Date('2024-06-01T08:05:00Z')
    expect(formatTime(d)).toMatch(/\d{1,2}:\d{2}/)
  })
})

describe('formatFullTime', () => {
  it('returns empty string for null', () => {
    expect(formatFullTime(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatFullTime(undefined)).toBe('')
  })

  it('returns a non-empty string for a valid ISO timestamp', () => {
    const result = formatFullTime('2024-01-15T14:30:00.000Z')
    expect(result.length).toBeGreaterThan(0)
  })

  it('returns more detail than formatTime', () => {
    const ts = '2024-01-15T14:30:00.000Z'
    expect(formatFullTime(ts).length).toBeGreaterThanOrEqual(formatTime(ts).length)
  })
})
