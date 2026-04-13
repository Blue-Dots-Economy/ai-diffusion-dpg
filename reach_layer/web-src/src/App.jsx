import { useState, useCallback, useEffect } from 'react'
import { useTheme } from './hooks/useTheme'
import { useAppConfig } from './hooks/useAppConfig'
import { useChat } from './hooks/useChat'
import { useSessions } from './hooks/useSessions'
import { useToast } from './hooks/useToast'
import { LoadingScreen } from './components/screens/LoadingScreen'
import { SetupScreen } from './components/screens/SetupScreen'
import { LoginScreen } from './components/screens/LoginScreen'
import { ChatScreen } from './components/screens/ChatScreen'
import { ToastContainer } from './components/ui/Toast'
import { fetchCurrentUser, logout } from './api'

/**
 * Root application component.
 *
 * Boot flow (ChatGPT-style multi-session):
 *   - When auth.enabled (server config): loading → login (signed-out) or chat
 *   - When auth.enabled is false: loading → setup (new user) or chat
 *   - On every successful sign-in/setup we land on a fresh "New chat".
 *     Previous sessions are listed in the sidebar; clicking one continues it.
 */
export default function App() {
  const { config, configLoading } = useAppConfig()
  const { theme, toggle: toggleTheme } = useTheme(config.theme_storage_key)
  const { toasts, addToast, removeToast } = useToast()

  const authEnabled = !!config.auth?.enabled

  const [screen, setScreen] = useState('loading') // 'loading' | 'setup' | 'login' | 'chat'
  const [userId, setUserId] = useState(null)
  const [identity, setIdentity] = useState(null)
  const [systemMsg, setSystemMsg] = useState(null)

  const {
    messages,
    isSending,
    sessionId,
    newestAgentId,
    startNewChat,
    loadSession,
    send,
    reset,
  } = useChat({ onError: addToast })

  const { sessions, refresh: refreshSessions, remove: removeSession } = useSessions({
    userId,
    authEnabled,
  })

  const proceedToChat = useCallback(
    (uid, ident = null) => {
      setUserId(uid)
      setIdentity(ident)
      setSystemMsg(config.new_session_msg)
      startNewChat()
      setScreen('chat')
    },
    [startNewChat, config]
  )

  // Boot: run once configLoading resolves
  const [booted, setBooted] = useState(false)
  if (!booted && !configLoading) {
    setBooted(true)
    if (authEnabled) {
      fetchCurrentUser()
        .then((me) => {
          if (me && me.user_id) {
            proceedToChat(me.user_id, me)
          } else {
            setScreen('login')
          }
        })
        .catch(() => setScreen('login'))
    } else {
      const storedId = localStorage.getItem(config.storage_key)
      if (!storedId) {
        setScreen('setup')
      } else {
        proceedToChat(storedId)
      }
    }
  }

  // After every send the session list may have changed (new title, new
  // session). Re-fetch when the most recent agent message arrives.
  useEffect(() => {
    if (newestAgentId && userId) {
      refreshSessions()
    }
  }, [newestAgentId, userId, refreshSessions])

  const handleStart = useCallback(
    (uid) => {
      localStorage.setItem(config.storage_key, uid)
      proceedToChat(uid)
    },
    [config, proceedToChat]
  )

  const handleSignedIn = useCallback(
    (ident) => {
      proceedToChat(ident.user_id, ident)
    },
    [proceedToChat]
  )

  const handleSend = useCallback(
    (text) => {
      send({ text, userId })
    },
    [send, userId]
  )

  const handleNewChat = useCallback(() => {
    startNewChat()
    setSystemMsg(config.new_session_msg)
  }, [startNewChat, config])

  const handleSelectSession = useCallback(
    async (targetSessionId) => {
      if (!targetSessionId || targetSessionId === sessionId) return
      await loadSession(targetSessionId, authEnabled ? null : userId)
      setSystemMsg(null)
    },
    [loadSession, sessionId, authEnabled, userId]
  )

  const handleDeleteSession = useCallback(
    async (targetSessionId) => {
      await removeSession(targetSessionId)
      // If the active session was deleted, drop into a fresh chat.
      if (targetSessionId === sessionId) {
        startNewChat()
        setSystemMsg(config.new_session_msg)
      }
    },
    [removeSession, sessionId, startNewChat, config]
  )

  const handleSwitchUser = useCallback(async () => {
    if (authEnabled) {
      try { await logout() } catch (_) { /* best-effort */ }
      setUserId(null)
      setIdentity(null)
      setSystemMsg(null)
      reset()
      setScreen('login')
    } else {
      localStorage.removeItem(config.storage_key)
      setUserId(null)
      setIdentity(null)
      setSystemMsg(null)
      reset()
      setScreen('setup')
    }
  }, [authEnabled, config, reset])

  return (
    <div className="h-full">
      {screen === 'loading' && <LoadingScreen message="Loading…" />}
      {screen === 'setup' && (
        <SetupScreen config={config} onStart={handleStart} />
      )}
      {screen === 'login' && (
        <LoginScreen
          config={config}
          onSignedIn={handleSignedIn}
          onError={addToast}
        />
      )}
      {screen === 'chat' && (
        <ChatScreen
          config={config}
          identity={identity}
          authEnabled={authEnabled}
          userId={userId}
          sessionId={sessionId}
          messages={messages}
          isSending={isSending}
          newestAgentId={newestAgentId}
          systemMsg={systemMsg}
          theme={theme}
          onToggleTheme={toggleTheme}
          onSend={handleSend}
          onSwitchUser={handleSwitchUser}
          sessions={sessions}
          onNewChat={handleNewChat}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
        />
      )}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </div>
  )
}
