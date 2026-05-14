// dev-kit/frontend/src/components/Chat.jsx
import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import PhaseBar from './PhaseBar'
import FlowGraph from './FlowGraph'
import YamlPanel from './YamlPanel'
import ThemeToggle from './shared/ThemeToggle'

export default function Chat({ slug, onDashboard, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState('tier')
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [configs, setConfigs] = useState([])
  const [fieldStatus, setFieldStatus] = useState({})
  const [showGraph, setShowGraph] = useState(false)
  const [showYaml, setShowYaml] = useState(false)
  const bottomRef = useRef(null)
  const textareaRef = useRef(null)

  // Side-panel resizer. YAML and Graph panels share the same width state since
  // they are mutually exclusive. The handle on the panel's left edge starts a
  // drag; movement updates yamlWidth, which is clamped between MIN_PANEL_WIDTH
  // and (container width − MIN_CHAT_WIDTH) so neither column collapses. The
  // last value is persisted so the user's preference survives reloads.
  const PANEL_WIDTH_STORAGE_KEY = 'devkit:chat_side_panel_width'
  const MIN_PANEL_WIDTH = 320
  const MIN_CHAT_WIDTH = 320
  const layoutRef = useRef(null)
  const [panelWidth, setPanelWidth] = useState(() => {
    try {
      const saved = parseInt(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY) || '', 10)
      if (Number.isFinite(saved) && saved >= MIN_PANEL_WIDTH) return saved
    } catch {}
    if (typeof window !== 'undefined') {
      return Math.max(MIN_PANEL_WIDTH, Math.floor(window.innerWidth * 0.45))
    }
    return 600
  })
  const [resizing, setResizing] = useState(false)

  useEffect(() => {
    try { localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(panelWidth)) } catch {}
  }, [panelWidth])

  function startPanelResize(e) {
    if (!layoutRef.current) return
    e.preventDefault()
    const containerWidth = layoutRef.current.getBoundingClientRect().width
    const startX = e.clientX
    const startWidth = panelWidth
    const maxWidth = Math.max(MIN_PANEL_WIDTH, containerWidth - MIN_CHAT_WIDTH)

    function onMove(ev) {
      // Panel sits on the right; dragging left grows it.
      const dx = startX - ev.clientX
      const next = Math.min(Math.max(startWidth + dx, MIN_PANEL_WIDTH), maxWidth)
      setPanelWidth(next)
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      setResizing(false)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    setResizing(true)
  }

  useEffect(() => {
    api.getProject(slug).then(p => setPhase(p.current_phase || 'tier')).catch(() => {})
    api.getHistory(slug).then(history => {
      setMessages(history.map(m => ({ role: m.role, text: m.content })))
    }).catch(() => {})
    api.getGraph(slug).then(setGraph).catch(() => {})
    api.getConfigs(slug).then(setConfigs).catch(() => {})
    api.getFieldStatus(slug).then(setFieldStatus).catch(() => {})
  }, [slug])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(e) {
    e.preventDefault()
    if (!input.trim() || loading) return
    const userText = input.trim()
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.focus()
    }
    setMessages(m => [...m, { role: 'user', text: userText }])
    setLoading(true)
    try {
      const res = await api.chat(slug, userText)
      if (res.reply) {
        setMessages(m => [...m, { role: 'assistant', text: res.reply }])
      }
      setPhase(res.phase)
      if (res.graph) setGraph(res.graph)
      // Refresh configs after every agent turn
      api.getConfigs(slug).then(setConfigs).catch(() => {})
    } catch (err) {
      setMessages(m => [...m, { role: 'error', text: `Error: ${err.message}` }])
    } finally {
      setLoading(false)
      setTimeout(() => textareaRef.current?.focus(), 0)
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

  async function attachFile(e) {
    const file = e.target.files?.[0]
    if (!file || loading) return

    // Reset the input so the same file can be re-selected
    e.target.value = ''

    const MAX_BYTES = 500 * 1024  // 500 KB
    if (file.size > MAX_BYTES) {
      alert(`File "${file.name}" is too large (${(file.size / 1024).toFixed(0)} KB). Maximum is 500 KB.`)
      return
    }

    const reader = new FileReader()
    reader.onload = async (ev) => {
      const content = ev.target.result
      const message = `[Attached: ${file.name}]\n\n${content}`
      setMessages(m => [...m, { role: 'user', text: message }])
      setLoading(true)
      try {
        const res = await api.chat(slug, message)
        if (res.reply) {
          setMessages(m => [...m, { role: 'assistant', text: res.reply }])
        }
        setPhase(res.phase)
        if (res.graph) setGraph(res.graph)
        api.getConfigs(slug).then(setConfigs).catch(() => {})
      } catch (err) {
        setMessages(m => [...m, { role: 'error', text: `Error: ${err.message}` }])
      } finally {
        setLoading(false)
        setTimeout(() => textareaRef.current?.focus(), 0)
      }
    }
    reader.onerror = () => {
      setMessages(m => [...m, { role: 'error', text: `Error: Failed to read file "${file.name}".` }])
    }
    reader.readAsText(file)
  }

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
          <ThemeToggle />
          <button
            onClick={onDashboard}
            className="text-xs bg-blue-600 hover:bg-blue-500 px-3 py-1.5 rounded-lg transition-colors font-medium text-white"
          >
            Dashboard
          </button>
        </div>
      </div>

      <div ref={layoutRef} className="flex flex-1 overflow-hidden min-h-0">
        {/* Phase sidebar */}
        <PhaseBar currentPhase={phase} />

        {/* Chat column — always flexes to fill space the side panel doesn't take. */}
        <div className="flex flex-col flex-1 overflow-hidden min-w-0">
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-center text-sm mt-12">
                Describe your AI agent use case to get started.
              </p>
            )}
            {messages.map((m, i) => {
              // Detect file attachments and show filename badge instead of raw content
              const isAttachment = m.role === 'user' && m.text.startsWith('[Attached: ')
              const attachedName = isAttachment ? m.text.match(/^\[Attached: (.+?)\]/)?.[1] : null

              return (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={[
                  'max-w-xl rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
                  m.role === 'user' ? 'bg-blue-600 text-white whitespace-pre-wrap' : '',
                  m.role === 'assistant' ? 'bg-gray-800 text-gray-100' : '',
                  m.role === 'error' ? 'bg-red-900/60 text-red-200 border border-red-700 whitespace-pre-wrap' : '',
                ].filter(Boolean).join(' ')}>
                  {isAttachment ? (
                    <span className="flex items-center gap-2">📎 {attachedName}</span>
                  ) : m.role === 'assistant' ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                        ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{children}</ol>,
                        li: ({ children }) => <li className="text-sm">{children}</li>,
                        strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                        em: ({ children }) => <em className="italic text-gray-300">{children}</em>,
                        pre: ({ children }) => <pre className="bg-gray-900 rounded-lg p-3 mt-1 mb-2 overflow-x-auto text-xs font-mono text-green-300 whitespace-pre">{children}</pre>,
                        code: ({ children }) => <code className="bg-gray-700 text-green-300 px-1 py-0.5 rounded text-xs font-mono">{children}</code>,
                        h1: ({ children }) => <h1 className="text-base font-bold mb-1 mt-2">{children}</h1>,
                        h2: ({ children }) => <h2 className="text-sm font-bold mb-1 mt-2">{children}</h2>,
                        h3: ({ children }) => <h3 className="text-sm font-semibold mb-1 mt-1.5">{children}</h3>,
                        hr: () => <hr className="border-gray-600 my-2" />,
                        blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-500 pl-3 text-gray-300 italic my-2">{children}</blockquote>,
                        table: ({ children }) => <div className="overflow-x-auto my-2"><table className="text-xs border-collapse w-full">{children}</table></div>,
                        thead: ({ children }) => <thead className="bg-gray-700">{children}</thead>,
                        th: ({ children }) => <th className="border border-gray-600 px-2 py-1 text-left font-semibold">{children}</th>,
                        td: ({ children }) => <td className="border border-gray-600 px-2 py-1">{children}</td>,
                        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 underline hover:text-blue-300">{children}</a>,
                      }}
                    >
                      {m.text}
                    </ReactMarkdown>
                  ) : m.text}
                </div>
              </div>
              )
            })}
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

          {/* Field-status summary line — reads from field_status.json (Task 11.3) */}
          {Object.keys(fieldStatus).length > 0 && (() => {
            const counts = { pending: 0, answered: 0, needs_re_asking: 0, not_applicable: 0 }
            Object.values(fieldStatus).forEach(v => { if (counts[v] !== undefined) counts[v]++ })
            return (
              <div className="px-4 py-1.5 border-t border-gray-800 bg-gray-900 text-xs text-gray-500 shrink-0 flex gap-3">
                {counts.answered > 0 && <span className="text-green-500">{counts.answered} answered</span>}
                {counts.pending > 0 && <span className="text-yellow-500">{counts.pending} pending</span>}
                {counts.needs_re_asking > 0 && <span className="text-orange-400">{counts.needs_re_asking} needs re-asking</span>}
              </div>
            )
          })()}

          {/* Completion banner */}
          {phase === 'review' && messages.length > 0 && (
            <div className="px-4 py-2.5 bg-green-950/40 border-t border-green-800 text-sm text-green-300 text-center shrink-0">
              All config YAMLs have been generated. Head to the Dashboard to deploy.
            </div>
          )}

          <form onSubmit={send} className="flex gap-2 px-4 py-3 border-t border-gray-800 bg-gray-900 shrink-0">
            {/* File input — only shown during tools phase */}
            {phase === 'tools' && (
              <>
                <input
                  type="file"
                  accept=".yaml,.yml,.json"
                  className="hidden"
                  id="spec-file-input"
                  onChange={attachFile}
                  disabled={loading}
                />
                <label
                  htmlFor="spec-file-input"
                  title="Attach spec file (.yaml, .yml, .json)"
                  className={`flex items-center justify-center w-9 h-9 rounded-xl cursor-pointer transition-colors self-end shrink-0 ${
                    loading ? 'text-gray-600 cursor-not-allowed' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700'
                  }`}
                >
                  📎
                </label>
              </>
            )}
            <textarea
              ref={textareaRef}
              rows={1}
              className="flex-1 bg-gray-800 rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500 resize-none overflow-hidden"
              style={{ lineHeight: '1.5rem' }}
              placeholder="Type your message… (Shift+Enter for new line)"
              value={input}
              onChange={e => {
                setInput(e.target.value)
                e.target.style.height = 'auto'
                e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
              }}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send(e)
                }
              }}
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-xl px-4 py-2 text-sm font-medium transition-colors text-white self-end"
            >
              Send
            </button>
          </form>
        </div>

        {/* Side panels (mutually exclusive). Both share the same draggable
            width so the user's resize preference carries between Graph and
            YAML views. */}
        {(showGraph || showYaml) && (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize side panel"
              onMouseDown={startPanelResize}
              className={`w-1 shrink-0 cursor-col-resize bg-gray-800 hover:bg-blue-500/60 active:bg-blue-500 transition-colors ${
                resizing ? 'bg-blue-500' : ''
              }`}
            />
            {showGraph && (
              <div
                style={{ width: panelWidth }}
                className="border-l border-gray-800 bg-gray-950 shrink-0 min-h-0"
              >
                <FlowGraph graph={graph} />
              </div>
            )}
            {showYaml && (
              <div
                style={{ width: panelWidth }}
                className="border-l border-gray-800 min-h-0 flex flex-col shrink-0"
              >
                <YamlPanel slug={slug} configs={configs} onSaved={handleConfigSaved} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
