/**
 * Three-dot animated typing indicator shown while the agent is processing.
 *
 * @param {{ agentAvatar: string }} props
 */
export function TypingIndicator({ agentAvatar }) {
  return (
    <div className="flex items-end gap-2 mb-3">
      <div className="w-7 h-7 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-sm flex-shrink-0">
        {agentAvatar}
      </div>
      <div className="px-3.5 py-3 rounded-2xl rounded-bl-sm bg-[var(--bubble-agent-bg)] border border-[var(--bubble-agent-border)]">
        <div className="flex items-center gap-1">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-gray-400"
              style={{
                animation: 'bounce 1.2s infinite',
                animationDelay: `${i * 0.2}s`,
              }}
            />
          ))}
        </div>
      </div>

      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-5px); }
        }
      `}</style>
    </div>
  )
}
