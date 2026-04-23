import { ChatHeader } from '../chat/ChatHeader'
import { MessageList } from '../chat/MessageList'
import { InputArea } from '../chat/InputArea'
import { Sidebar } from '../chat/Sidebar'
import { useSidebar } from '../../hooks/useSidebar'

/**
 * Full chat screen — composes a collapsible sidebar with the chat pane
 * (header + message list + input area).
 *
 * @param {{
 *   config: Object,
 *   identity: { user_id: string, display_name?: string, email?: string, picture?: string }|null,
 *   userId: string|null,
 *   sessionId: string|null,
 *   authEnabled: boolean,
 *   messages: Array,
 *   isSending: boolean,
 *   newestAgentId: string|null,
 *   systemMsg: string|null,
 *   theme: 'dark'|'light',
 *   onToggleTheme: () => void,
 *   onSend: (text: string) => void,
 *   onSwitchUser: () => void,
 * }} props
 */
export function ChatScreen({
  config,
  identity,
  userId,
  sessionId,
  authEnabled,
  messages,
  isSending,
  newestAgentId,
  systemMsg,
  theme,
  onToggleTheme,
  onSend,
  onSwitchUser,
  sessions = [],
  onNewChat,
  onSelectSession,
  onDeleteSession,
}) {
  const sidebarKey = (config.storage_key || 'dpg') + '_sidebar_collapsed'
  const { collapsed, toggle } = useSidebar(sidebarKey)

  return (
    <div className="flex h-full bg-[var(--bg)]">
      <Sidebar
        config={config}
        collapsed={collapsed}
        onToggleCollapsed={toggle}
        sessions={sessions}
        activeSessionId={sessionId}
        onNewChat={onNewChat}
        onSelectSession={onSelectSession}
        onDeleteSession={onDeleteSession}
      />
      <div className="flex flex-col flex-1 min-w-0">
        <ChatHeader
          config={config}
          identity={identity}
          userId={userId}
          authEnabled={authEnabled}
          theme={theme}
          onToggleTheme={onToggleTheme}
          onSignOut={onSwitchUser}
        />
        <MessageList
          messages={messages}
          isSending={isSending}
          newestAgentId={newestAgentId}
          agentAvatar={config.agent_avatar}
          userAvatar={config.user_avatar}
          systemMsg={systemMsg}
        />
        <InputArea
          onSend={onSend}
          disabled={isSending}
          placeholder={`Message ${config.app_name}…`}
        />
      </div>
    </div>
  )
}
