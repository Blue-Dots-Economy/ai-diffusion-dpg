import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useToast } from '../hooks/useToast'

afterEach(() => { vi.useRealTimers() })

describe('useToast', () => {
  it('starts with an empty toast list', () => {
    const { result } = renderHook(() => useToast())
    expect(result.current.toasts).toEqual([])
  })

  it('addToast appends a toast with correct message and type', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => { result.current.addToast('Something went wrong', 'error') })
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0].message).toBe('Something went wrong')
    expect(result.current.toasts[0].type).toBe('error')
    expect(result.current.toasts[0].id).toBeDefined()
  })

  it('addToast defaults type to error', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => { result.current.addToast('Oops') })
    expect(result.current.toasts[0].type).toBe('error')
  })

  it('addToast supports info and success types', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => { result.current.addToast('Done', 'success') })
    expect(result.current.toasts[0].type).toBe('success')
  })

  it('removeToast removes the correct toast by id', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => {
      result.current.addToast('First')
      result.current.addToast('Second')
    })
    const idToRemove = result.current.toasts[0].id
    act(() => { result.current.removeToast(idToRemove) })
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0].message).toBe('Second')
  })

  it('toast auto-dismisses after 4000ms', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => { result.current.addToast('Bye') })
    expect(result.current.toasts).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(4000) })
    expect(result.current.toasts).toHaveLength(0)
  })

  it('does not dismiss toast before 4000ms', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => { result.current.addToast('Still here') })
    act(() => { vi.advanceTimersByTime(3999) })
    expect(result.current.toasts).toHaveLength(1)
  })

  it('multiple toasts each have unique ids', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useToast())
    act(() => {
      result.current.addToast('A')
      result.current.addToast('B')
      result.current.addToast('C')
    })
    const ids = result.current.toasts.map(t => t.id)
    expect(new Set(ids).size).toBe(3)
  })
})
