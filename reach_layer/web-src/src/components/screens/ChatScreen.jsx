import { ChatHeader } from '../chat/ChatHeader'
import { MessageList } from '../chat/MessageList'
import { InputArea } from '../chat/InputArea'

/**
 * Full chat screen — composes header, message list, and input area.
 *
 * @param {{
 *   config: Object,
 *   userId: string|null,
 *   sessionId: string|null,
 *   messages: Array,
 *   isSending: boolean,
 *   newestAgentId: string|null,
 *   systemMsg: string|null,
 *   theme: 'dark'|'light',
 *   onToggleTheme: () => void,
 *   onSend: (text: string) => void,
 *   onClear: () => void,
 *   onSwitchUser: () => void,
 * }} props
 */
export function ChatScreen({
  config,
  userId,
  sessionId,
  messages,
  isSending,
  newestAgentId,
  systemMsg,
  theme,
  onToggleTheme,
  onSend,
  onClear,
  onSwitchUser,
}) {
  return (
    <div className="flex flex-col h-full bg-[var(--bg)]">
      <ChatHeader
        config={config}
        userId={userId}
        sessionId={sessionId}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onSwitchUser={onSwitchUser}
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
        onClear={onClear}
        disabled={isSending}
        placeholder={`Message ${config.app_name}…`}
      />
    </div>
  )
}
