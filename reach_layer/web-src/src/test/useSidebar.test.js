import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSidebar } from '../hooks/useSidebar'

beforeEach(() => { localStorage.clear() })
afterEach(() => { localStorage.clear() })

describe('useSidebar', () => {
  it('defaults to expanded when no stored value', () => {
    const { result } = renderHook(() => useSidebar('k'))
    expect(result.current.collapsed).toBe(false)
  })

  it('reads collapsed state from localStorage', () => {
    localStorage.setItem('k', '1')
    const { result } = renderHook(() => useSidebar('k'))
    expect(result.current.collapsed).toBe(true)
  })

  it('toggle flips collapsed state', () => {
    const { result } = renderHook(() => useSidebar('k'))
    act(() => { result.current.toggle() })
    expect(result.current.collapsed).toBe(true)
    act(() => { result.current.toggle() })
    expect(result.current.collapsed).toBe(false)
  })

  it('persists collapsed state to localStorage', () => {
    const { result } = renderHook(() => useSidebar('k'))
    act(() => { result.current.toggle() })
    expect(localStorage.getItem('k')).toBe('1')
    act(() => { result.current.toggle() })
    expect(localStorage.getItem('k')).toBe('0')
  })

  it('setCollapsed forces state to a specific value', () => {
    const { result } = renderHook(() => useSidebar('k'))
    act(() => { result.current.setCollapsed(true) })
    expect(result.current.collapsed).toBe(true)
    act(() => { result.current.setCollapsed(false) })
    expect(result.current.collapsed).toBe(false)
  })

  it('uses different storage keys for different deployments', () => {
    localStorage.setItem('a', '1')
    const { result: a } = renderHook(() => useSidebar('a'))
    const { result: b } = renderHook(() => useSidebar('b'))
    expect(a.current.collapsed).toBe(true)
    expect(b.current.collapsed).toBe(false)
  })
})
