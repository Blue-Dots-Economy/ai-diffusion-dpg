import { useState, useCallback } from 'react'
import { useTheme } from './hooks/useTheme'
import { useAppConfig } from './hooks/useAppConfig'
import { useChat } from './hooks/useChat'
import { useToast } from './hooks/useToast'
import { LoadingScreen } from './components/screens/LoadingScreen'
import { SetupScreen } from './components/screens/SetupScreen'
import { ChatScreen } from './components/screens/ChatScreen'
import { ToastContainer } from './components/ui/Toast'

/**
 * Root application component.
 * Manages boot flow: loading → setup (new user) or chat (returning user).
 * Holds all shared state: userId, screen, system message.
 */
export default function App() {
  const { config, configLoading } = useAppConfig()
  const { theme, toggle: toggleTheme } = useTheme(config.theme_storage_key)
  const { toasts, addToast, removeToast } = useToast()

  const [screen, setScreen] = useState('loading') // 'loading' | 'setup' | 'chat'
  const [loadingMsg, setLoadingMsg] = useState('Loading…')
  const [userId, setUserId] = useState(null)
  const [systemMsg, setSystemMsg] = useState(null)

  const { messages, isSending, sessionId, newestAgentId, loadHistory, send, reset } =
    useChat({ onError: addToast })

  // Boot: run once configLoading resolves
  const [booted, setBooted] = useState(false)
  if (!booted && !configLoading) {
    setBooted(true)
    const storedId = localStorage.getItem(config.storage_key)
    if (!storedId) {
      setScreen('setup')
    } else {
      setLoadingMsg('Restoring your session…')
      setScreen('loading')
      loadHistory(storedId).then(({ isReturning }) => {
        setUserId(storedId)
        setSystemMsg(
          isReturning ? config.returning_user_msg : config.new_session_msg
        )
        setScreen('chat')
      })
    }
  }

  const handleStart = useCallback(
    uid => {
      localStorage.setItem(config.storage_key, uid)
      setUserId(uid)
      setLoadingMsg('Setting up your session…')
      setScreen('loading')
      loadHistory(uid).then(({ isReturning }) => {
        setSystemMsg(
          isReturning ? config.returning_user_msg : config.new_session_msg
        )
        setScreen('chat')
      })
    },
    [config, loadHistory]
  )

  const handleSend = useCallback(
    text => {
      send({ text, userId })
    },
    [send, userId]
  )

  const handleClear = useCallback(() => {
    reset()
    setSystemMsg(config.new_session_msg)
  }, [reset, config])

  const handleSwitchUser = useCallback(() => {
    localStorage.removeItem(config.storage_key)
    setUserId(null)
    setSystemMsg(null)
    reset()
    setScreen('setup')
  }, [config, reset])

  return (
    <div className="h-full">
      {screen === 'loading' && <LoadingScreen message={loadingMsg} />}
      {screen === 'setup' && (
        <SetupScreen config={config} onStart={handleStart} />
      )}
      {screen === 'chat' && (
        <ChatScreen
          config={config}
          userId={userId}
          sessionId={sessionId}
          messages={messages}
          isSending={isSending}
          newestAgentId={newestAgentId}
          systemMsg={systemMsg}
          theme={theme}
          onToggleTheme={toggleTheme}
          onSend={handleSend}
          onClear={handleClear}
          onSwitchUser={handleSwitchUser}
        />
      )}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </div>
  )
}
