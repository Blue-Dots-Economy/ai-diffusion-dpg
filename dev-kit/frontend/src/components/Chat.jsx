import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import PhaseBar from './PhaseBar'
import FlowGraph from './FlowGraph'

export default function Chat({ slug, onDashboard, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState('overview')
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [checkpoints, setCheckpoints] = useState([])
  const [showGraph, setShowGraph] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    api.getHistory(slug).then((history) => {
      setMessages(history.map((m) => ({ role: m.role, text: m.content })))
    }).catch(() => {})
    api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
    api.getGraph(slug).then(setGraph).catch(() => {})
  }, [slug])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(e) {
    e.preventDefault()
    if (!input.trim() || loading) return
    const userText = input.trim()
    setInput('')
    setMessages((m) => [...m, { role: 'user', text: userText }])
    setLoading(true)
    try {
      const res = await api.chat(slug, userText)
      setMessages((m) => [...m, { role: 'assistant', text: res.reply }])
      setPhase(res.phase)
      if (res.graph) setGraph(res.graph)
      if (res.checkpoint_created) {
        api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
      }
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: `Error: ${err.message}` }])
    } finally {
      setLoading(false)
    }
  }

  async function handleRestoreCheckpoint(checkpointPhase) {
    if (!window.confirm(`Restore to checkpoint: ${checkpointPhase}? This will clear current conversation history.`)) return
    try {
      await api.restoreCheckpoint(slug, checkpointPhase)
      setMessages([])
      const history = await api.getHistory(slug)
      setMessages(history.map((m) => ({ role: m.role, text: m.content })))
      const project = await api.getProject(slug)
      setPhase(project.current_phase)
      const newGraph = await api.getGraph(slug)
      setGraph(newGraph)
      const newCheckpoints = await api.getCheckpoints(slug)
      setCheckpoints(newCheckpoints)
    } catch (err) {
      alert(`Failed to restore: ${err.message}`)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-800">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm">&larr; Projects</button>
        <span className="font-semibold text-sm">{slug}</span>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGraph((g) => !g)}
            className="text-xs bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded-lg transition-colors"
          >
            {showGraph ? 'Hide Graph' : 'Show Graph'}
          </button>
          <button
            onClick={onDashboard}
            className="text-xs bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded-lg transition-colors"
          >
            Dashboard
          </button>
        </div>
      </div>

      <PhaseBar currentPhase={phase} checkpoints={checkpoints} onRestoreCheckpoint={handleRestoreCheckpoint} />

      <div className="flex flex-1 overflow-hidden">
        <div className={`flex flex-col ${showGraph ? 'w-1/2' : 'w-full'} overflow-hidden`}>
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-center text-sm mt-8">
                Describe your AI agent use case to get started.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={[
                  'max-w-xl rounded-2xl px-4 py-2 text-sm leading-relaxed whitespace-pre-wrap',
                  m.role === 'user' ? 'bg-blue-600 text-white' : '',
                  m.role === 'assistant' ? 'bg-gray-800 text-gray-100' : '',
                  m.role === 'error' ? 'bg-red-900 text-red-200' : '',
                ].filter(Boolean).join(' ')}>
                  {m.text}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-800 rounded-2xl px-4 py-2 text-sm text-gray-400 animate-pulse">
                  Thinking&hellip;
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form onSubmit={send} className="flex gap-2 px-4 py-3 border-t border-gray-800">
            <input
              className="flex-1 bg-gray-800 rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Type your message\u2026"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-xl px-4 py-2 text-sm font-medium transition-colors"
            >
              Send
            </button>
          </form>
        </div>

        {showGraph && (
          <div className="w-1/2 border-l border-gray-800 bg-gray-950">
            <FlowGraph graph={graph} />
          </div>
        )}
      </div>
    </div>
  )
}
