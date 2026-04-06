/**
 * Full-screen spinner shown during boot / session restore.
 *
 * @param {{ message: string }} props
 */
export function LoadingScreen({ message }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 bg-[var(--bg)]">
      <div
        className="w-10 h-10 rounded-full border-gray-700 border-t-indigo-500"
        style={{ border: '3px solid #374151', borderTopColor: '#6366f1', animation: 'spin 0.75s linear infinite' }}
      />
      <p className="text-sm text-gray-500">{message || 'Loading…'}</p>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
