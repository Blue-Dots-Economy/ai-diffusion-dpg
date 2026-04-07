// dev-kit/frontend/src/components/Chat.jsx
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import PhaseBar from './PhaseBar'
import FlowGraph from './FlowGraph'
import YamlPanel from './YamlPanel'
import DiffModal from './DiffModal'

export default function Chat({ slug, onDashboard, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState('overview')
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [checkpoints, setCheckpoints] = useState([])
  const [configs, setConfigs] = useState([])
  const [showGraph, setShowGraph] = useState(false)
  const [showYaml, setShowYaml] = useState(false)
  const [diffModal, setDiffModal] = useState(null)  // null | {phase, currentConfigs, previewConfigs}
  const bottomRef = useRef(null)

  useEffect(() => {
    api.getHistory(slug).then(history => {
      setMessages(history.map(m => ({ role: m.role, text: m.content })))
    }).catch(() => {})
    api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
    api.getGraph(slug).then(setGraph).catch(() => {})
    api.getConfigs(slug).then(setConfigs).catch(() => {})
  }, [slug])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(e) {
    e.preventDefault()
    if (!input.trim() || loading) return
    const userText = input.trim()
    setInput('')
    setMessages(m => [...m, { role: 'user', text: userText }])
    setLoading(true)
    try {
      const res = await api.chat(slug, userText)
      setMessages(m => [...m, { role: 'assistant', text: res.reply }])
      setPhase(res.phase)
      if (res.graph) setGraph(res.graph)
      if (res.checkpoint_created) {
        api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
      }
      // Refresh configs after every agent turn
      api.getConfigs(slug).then(setConfigs).catch(() => {})
    } catch (err) {
      setMessages(m => [...m, { role: 'error', text: `Error: ${err.message}` }])
    } finally {
      setLoading(false)
    }
  }

  async function handleRestoreCheckpoint(checkpointPhase) {
    try {
      const [currentConfigs, previewConfigs] = await Promise.all([
        api.getConfigs(slug),
        api.getCheckpointPreview(slug, checkpointPhase),
      ])
      setDiffModal({ phase: checkpointPhase, currentConfigs, previewConfigs })
    } catch (err) {
      alert(`Failed to load checkpoint preview: ${err.message}`)
    }
  }

  async function confirmRestore() {
    if (!diffModal) return
    const checkpointPhase = diffModal.phase
    setDiffModal(null)
    try {
      await api.restoreCheckpoint(slug, checkpointPhase)
      const [history, project, newGraph, newCheckpoints, newConfigs] = await Promise.all([
        api.getHistory(slug),
        api.getProject(slug),
        api.getGraph(slug),
        api.getCheckpoints(slug),
        api.getConfigs(slug),
      ])
      setMessages(history.map(m => ({ role: m.role, text: m.content })))
      setPhase(project.current_phase)
      setGraph(newGraph)
      setCheckpoints(newCheckpoints)
      setConfigs(newConfigs)
    } catch (err) {
      alert(`Failed to restore: ${err.message}`)
    }
  }

  function handleConfigSaved(block, updatedConfig) {
    setConfigs(prev => prev.map(c => c.block === block ? updatedConfig : c))
  }

  // Graph and YAML are mutually exclusive side panels
  function toggleGraph() {
    setShowGraph(g => !g)
    if (!showGraph) setShowYaml(false)
  }
  function toggleYaml() {
    setShowYaml(y => !y)
    if (!showYaml) setShowGraph(false)
  }

  const showSidePanel = showGraph || showYaml

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
          ← Projects
        </button>
        <span className="font-semibold text-sm text-gray-300">{slug}</span>
        <div className="flex gap-2">
          <button
            onClick={toggleGraph}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${showGraph ? 'bg-indigo-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-300'}`}
          >
            {showGraph ? 'Hide Graph' : 'Graph'}
          </button>
          <button
            onClick={toggleYaml}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${showYaml ? 'bg-indigo-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-300'}`}
          >
            {showYaml ? 'Hide YAML' : 'YAML'}
          </button>
          <button
            onClick={onDashboard}
            className="text-xs bg-blue-600 hover:bg-blue-500 px-3 py-1.5 rounded-lg transition-colors font-medium"
          >
            Dashboard
          </button>
        </div>
      </div>

      <PhaseBar currentPhase={phase} checkpoints={checkpoints} onRestoreCheckpoint={handleRestoreCheckpoint} />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Chat column */}
        <div className={`flex flex-col ${showSidePanel ? 'w-1/2' : 'w-full'} overflow-hidden`}>
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-center text-sm mt-12">
                Describe your AI agent use case to get started.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={[
                  'max-w-xl rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap',
                  m.role === 'user' ? 'bg-blue-600 text-white' : '',
                  m.role === 'assistant' ? 'bg-gray-800 text-gray-100' : '',
                  m.role === 'error' ? 'bg-red-900/60 text-red-200 border border-red-700' : '',
                ].filter(Boolean).join(' ')}>
                  {m.text}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-800 rounded-2xl px-4 py-2.5 text-sm text-gray-400">
                  <span className="inline-flex gap-1">
                    <span className="animate-bounce" style={{ animationDelay: '0ms' }}>●</span>
                    <span className="animate-bounce" style={{ animationDelay: '150ms' }}>●</span>
                    <span className="animate-bounce" style={{ animationDelay: '300ms' }}>●</span>
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form onSubmit={send} className="flex gap-2 px-4 py-3 border-t border-gray-800 bg-gray-900 shrink-0">
            <input
              className="flex-1 bg-gray-800 rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
              placeholder="Type your message…"
              value={input}
              onChange={e => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-xl px-4 py-2 text-sm font-medium transition-colors"
            >
              Send
            </button>
          </form>
        </div>

        {/* Side panels (mutually exclusive) */}
        {showGraph && (
          <div className="w-1/2 border-l border-gray-800 bg-gray-950">
            <FlowGraph graph={graph} />
          </div>
        )}
        {showYaml && (
          <div className="w-1/2 border-l border-gray-800 min-h-0 flex flex-col">
            <YamlPanel slug={slug} configs={configs} onSaved={handleConfigSaved} />
          </div>
        )}
      </div>

      {/* Diff modal — shown before checkpoint restore */}
      {diffModal && (
        <DiffModal
          currentConfigs={diffModal.currentConfigs}
          previewConfigs={diffModal.previewConfigs}
          checkpointPhase={diffModal.phase}
          onConfirm={confirmRestore}
          onCancel={() => setDiffModal(null)}
        />
      )}
    </div>
  )
}
