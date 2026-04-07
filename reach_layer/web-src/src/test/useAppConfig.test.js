import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useAppConfig } from '../hooks/useAppConfig'
import * as api from '../api'

describe('useAppConfig', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('starts with default config and configLoading true', () => {
    vi.spyOn(api, 'fetchAppConfig').mockResolvedValue({})
    const { result } = renderHook(() => useAppConfig())
    expect(result.current.configLoading).toBe(true)
    expect(result.current.config.app_name).toBe('DPG Chat')
  })

  it('merges server config with defaults when fetch succeeds', async () => {
    vi.spyOn(api, 'fetchAppConfig').mockResolvedValue({
      app_name: 'KKB Assistant',
      app_icon: '🏦',
    })
    const { result } = renderHook(() => useAppConfig())
    await waitFor(() => expect(result.current.configLoading).toBe(false))
    expect(result.current.config.app_name).toBe('KKB Assistant')
    expect(result.current.config.app_icon).toBe('🏦')
    // Defaults still present for keys not overridden
    expect(result.current.config.storage_key).toBe('dpg_user_id')
  })

  it('falls back to defaults silently when fetch fails', async () => {
    vi.spyOn(api, 'fetchAppConfig').mockRejectedValue(new Error('Network error'))
    const { result } = renderHook(() => useAppConfig())
    await waitFor(() => expect(result.current.configLoading).toBe(false))
    expect(result.current.config.app_name).toBe('DPG Chat')
  })

  it('sets configLoading to false after fetch regardless of outcome', async () => {
    vi.spyOn(api, 'fetchAppConfig').mockRejectedValue(new Error('fail'))
    const { result } = renderHook(() => useAppConfig())
    await waitFor(() => expect(result.current.configLoading).toBe(false))
  })

  it('default config has all required UI keys', () => {
    vi.spyOn(api, 'fetchAppConfig').mockResolvedValue({})
    const { result } = renderHook(() => useAppConfig())
    const c = result.current.config
    for (const key of ['app_name', 'app_tagline', 'app_icon', 'agent_avatar',
      'user_avatar', 'setup_heading', 'user_id_placeholder', 'storage_key',
      'start_btn_label', 'new_session_msg', 'returning_user_msg']) {
      expect(c[key]).toBeDefined()
    }
  })
})
