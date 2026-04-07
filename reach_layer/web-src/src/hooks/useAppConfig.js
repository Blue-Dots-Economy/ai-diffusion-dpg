import { useState, useEffect } from 'react'
import { fetchAppConfig } from '../api'

const DEFAULTS = {
  app_name: 'DPG Chat',
  app_tagline: 'AI Assistant',
  app_icon: '💬',
  agent_avatar: '🤖',
  user_avatar: '👤',
  setup_heading: 'Start a session',
  setup_subtitle:
    'Enter your user ID to begin. Returning users will have their previous conversation restored automatically.',
  user_id_placeholder: 'Enter your user ID',
  user_id_hint: 'Use the same ID across sessions to restore your conversation history.',
  start_btn_label: 'Start chatting →',
  new_session_msg: 'New session started. How can I help you today?',
  returning_user_msg: 'Welcome back! Continuing your previous conversation.',
  storage_key: 'dpg_user_id',
  theme_storage_key: 'dpg_theme',
}

/**
 * Load /app-config at boot and merge with hardcoded defaults.
 * Returns defaults immediately; updates once the fetch resolves.
 * @returns {{ config: Object, configLoading: boolean }}
 */
export function useAppConfig() {
  const [config, setConfig] = useState(DEFAULTS)
  const [configLoading, setConfigLoading] = useState(true)

  useEffect(() => {
    fetchAppConfig()
      .then(data => setConfig(prev => ({ ...prev, ...data })))
      .catch(err => {
        console.warn('[useAppConfig] /app-config fetch failed, using defaults:', err)
      })
      .finally(() => setConfigLoading(false))
  }, [])

  return { config, configLoading }
}
