# Reach Layer React UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-file vanilla JS chat UI with a production-grade React 19 + Tailwind SPA featuring rich Markdown rendering, dark/light theme, latency badges, tool-use indicators, auto-scroll, toast notifications, and all 20 agreed UX features.

**Architecture:** A Vite + React 19 project lives at `reach_layer/web-src/`. `npm run build` outputs to `reach_layer/web/dist/`. FastAPI's `server.py` mounts `dist/assets/` as static files and serves `dist/index.html` at `GET /`. The React app fetches `/app-config` at boot for all domain-specific strings (branding, copy) and calls `/chat` / `/user-history/{user_id}` for all turn data — no hard-coded domain values anywhere.

**Tech Stack:** React 19, Vite 6, Tailwind CSS 3, react-markdown 9, remark-gfm 4, rehype-highlight 7, highlight.js 11, FastAPI StaticFiles

---

## File Map

### New files (React source)
| File | Responsibility |
|------|---------------|
| `reach_layer/web-src/package.json` | npm deps and scripts |
| `reach_layer/web-src/vite.config.js` | Vite config: proxy to `:8005`, build → `../web/dist` |
| `reach_layer/web-src/tailwind.config.js` | Tailwind content paths |
| `reach_layer/web-src/postcss.config.js` | PostCSS autoprefixer |
| `reach_layer/web-src/index.html` | Vite HTML entry |
| `reach_layer/web-src/src/main.jsx` | React 19 `createRoot` mount |
| `reach_layer/web-src/src/styles/index.css` | Tailwind directives + highlight.js theme + custom CSS variables |
| `reach_layer/web-src/src/utils.js` | `generateUUID()` |
| `reach_layer/web-src/src/api.js` | `fetchAppConfig`, `fetchUserHistory`, `sendChat` |
| `reach_layer/web-src/src/hooks/useTheme.js` | dark/light toggle, persisted in localStorage |
| `reach_layer/web-src/src/hooks/useAppConfig.js` | loads `/app-config`, merges with hardcoded defaults |
| `reach_layer/web-src/src/hooks/useChat.js` | message state, send, loadHistory, reset |
| `reach_layer/web-src/src/hooks/useToast.js` | toast queue state + helpers |
| `reach_layer/web-src/src/components/ui/ThemeToggle.jsx` | sun/moon icon button |
| `reach_layer/web-src/src/components/ui/Toast.jsx` | toast container + individual toast |
| `reach_layer/web-src/src/components/markdown/MarkdownRenderer.jsx` | react-markdown with tables, code+copy, blockquotes |
| `reach_layer/web-src/src/components/chat/MessageBubble.jsx` | bubble, badges, timestamps, latency, word-reveal, collapse |
| `reach_layer/web-src/src/components/chat/TypingIndicator.jsx` | three-dot animated indicator |
| `reach_layer/web-src/src/components/chat/MessageList.jsx` | scroll container, auto-scroll with override |
| `reach_layer/web-src/src/components/chat/InputArea.jsx` | textarea, char count, clear, Enter/Shift+Enter |
| `reach_layer/web-src/src/components/chat/ChatHeader.jsx` | app name, user pill, session debug, switch user, theme toggle |
| `reach_layer/web-src/src/components/screens/LoadingScreen.jsx` | spinner + message |
| `reach_layer/web-src/src/components/screens/SetupScreen.jsx` | user-id form card |
| `reach_layer/web-src/src/components/screens/ChatScreen.jsx` | composes header + message list + input |
| `reach_layer/web-src/src/App.jsx` | screen router, boot flow, top-level state |

### Modified files
| File | Change |
|------|--------|
| `reach_layer/server.py` | Mount `web/dist/assets` as StaticFiles; serve `web/dist/index.html` at `GET /` |
| `reach_layer/pyproject.toml` | Add `aiofiles` dependency (required by FastAPI StaticFiles) |

---

## Task 1: Project scaffold

**Files:**
- Create: `reach_layer/web-src/package.json`
- Create: `reach_layer/web-src/vite.config.js`
- Create: `reach_layer/web-src/tailwind.config.js`
- Create: `reach_layer/web-src/postcss.config.js`
- Create: `reach_layer/web-src/index.html`

- [ ] **Step 1: Create `reach_layer/web-src/package.json`**

```json
{
  "name": "dpg-chat-ui",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0",
    "rehype-highlight": "^7.0.1",
    "highlight.js": "^11.10.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.0",
    "tailwindcss": "^3.4.17",
    "postcss": "^8.5.3",
    "autoprefixer": "^10.4.21"
  }
}
```

- [ ] **Step 2: Create `reach_layer/web-src/vite.config.js`**

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/chat': 'http://localhost:8005',
      '/app-config': 'http://localhost:8005',
      '/user-history': 'http://localhost:8005',
      '/health': 'http://localhost:8005',
    },
  },
  build: {
    outDir: '../web/dist',
    emptyOutDir: true,
  },
})
```

- [ ] **Step 3: Create `reach_layer/web-src/tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#1a1d27',
          2: '#242736',
        },
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 4: Create `reach_layer/web-src/postcss.config.js`**

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 5: Create `reach_layer/web-src/index.html`**

```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DPG Chat</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%236366f1'/%3E%3Cpath d='M6 10a3 3 0 0 1 3-3h14a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-4l-4 4-4-4H9a3 3 0 0 1-3-3z' fill='white'/%3E%3C/svg%3E" />
</head>
<body class="bg-gray-950 text-gray-100">
  <div id="root"></div>
  <script type="module" src="/src/main.jsx"></script>
</body>
</html>
```

- [ ] **Step 6: Install dependencies**

```bash
cd reach_layer/web-src && npm install
```

Expected: `node_modules/` created, no errors.

- [ ] **Step 7: Commit**

```bash
git add reach_layer/web-src/
git commit -m "feat(reach-ui): scaffold Vite + React 19 + Tailwind project"
```

---

## Task 2: Utilities and API module

**Files:**
- Create: `reach_layer/web-src/src/utils.js`
- Create: `reach_layer/web-src/src/api.js`

- [ ] **Step 1: Create `reach_layer/web-src/src/utils.js`**

```js
/**
 * Generate a UUID v4. Uses crypto.randomUUID when available.
 * @returns {string} UUID string
 */
export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/**
 * Format a timestamp string or Date to HH:MM.
 * @param {string|Date|null} ts
 * @returns {string}
 */
export function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/**
 * Format a timestamp string or Date to full locale string.
 * @param {string|Date|null} ts
 * @returns {string}
 */
export function formatFullTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString()
}
```

- [ ] **Step 2: Create `reach_layer/web-src/src/api.js`**

```js
/**
 * Fetch UI branding config from the server.
 * @returns {Promise<Object>}
 */
export async function fetchAppConfig() {
  const res = await fetch('/app-config')
  if (!res.ok) throw new Error(`/app-config responded ${res.status}`)
  return res.json()
}

/**
 * Fetch active session and chat history for a returning user.
 * @param {string} userId
 * @returns {Promise<{session_id: string|null, turns: Array}>}
 */
export async function fetchUserHistory(userId) {
  const res = await fetch(`/user-history/${encodeURIComponent(userId)}`)
  if (!res.ok) throw new Error(`/user-history responded ${res.status}`)
  return res.json()
}

/**
 * Send a chat turn to Agent Core via the Reach Layer proxy.
 * @param {{sessionId: string, userId: string|null, message: string}} params
 * @returns {Promise<{response_text: string, was_escalated: boolean, was_tool_used: boolean, latency_ms: number}>}
 */
export async function sendChat({ sessionId, userId, message }) {
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      user_id: userId || null,
      message,
    }),
  })
  if (!res.ok) throw new Error(`/chat responded ${res.status}`)
  return res.json()
}
```

- [ ] **Step 3: Commit**

```bash
git add reach_layer/web-src/src/utils.js reach_layer/web-src/src/api.js
git commit -m "feat(reach-ui): add api module and uuid/time utilities"
```

---

## Task 3: Global styles

**Files:**
- Create: `reach_layer/web-src/src/styles/index.css`

- [ ] **Step 1: Create `reach_layer/web-src/src/styles/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* highlight.js theme — GitHub Dark */
@import 'highlight.js/styles/github-dark.css';

/* ------------------------------------------------------------------ */
/* Light mode token overrides (applied when <html> lacks .dark class)  */
/* ------------------------------------------------------------------ */
:root {
  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-2: #f1f5f9;
  --border: #e2e8f0;
  --text: #0f172a;
  --text-muted: #64748b;
  --bubble-agent-bg: #f1f5f9;
  --bubble-agent-border: #e2e8f0;
  --bubble-agent-text: #0f172a;
}

/* ------------------------------------------------------------------ */
/* Dark mode tokens                                                     */
/* ------------------------------------------------------------------ */
.dark {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface-2: #242736;
  --border: #2e3147;
  --text: #e8eaf6;
  --text-muted: #8b91b0;
  --bubble-agent-bg: #242736;
  --bubble-agent-border: #2e3147;
  --bubble-agent-text: #e8eaf6;
}

/* ------------------------------------------------------------------ */
/* Global resets                                                        */
/* ------------------------------------------------------------------ */
*, *::before, *::after {
  box-sizing: border-box;
}

html, body, #root {
  height: 100%;
  margin: 0;
  padding: 0;
}

body {
  background-color: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* ------------------------------------------------------------------ */
/* Scrollbar styling                                                    */
/* ------------------------------------------------------------------ */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* ------------------------------------------------------------------ */
/* Markdown table — fix highlight.js overriding table cell padding      */
/* ------------------------------------------------------------------ */
.prose-agent table {
  border-collapse: collapse;
  width: 100%;
}
.prose-agent th,
.prose-agent td {
  border: 1px solid var(--border);
  padding: 6px 12px;
  text-align: left;
}
.prose-agent thead {
  background: var(--surface-2);
}
.prose-agent tr:nth-child(even) td {
  background: rgba(255,255,255,0.03);
}

/* ------------------------------------------------------------------ */
/* Code block wrapper                                                   */
/* ------------------------------------------------------------------ */
.code-block-wrapper {
  position: relative;
}
.code-block-wrapper .copy-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  opacity: 0;
  transition: opacity 0.15s;
}
.code-block-wrapper:hover .copy-btn {
  opacity: 1;
}

/* ------------------------------------------------------------------ */
/* Word-reveal animation                                                */
/* ------------------------------------------------------------------ */
@keyframes fadeInWord {
  from { opacity: 0; transform: translateY(2px); }
  to   { opacity: 1; transform: translateY(0); }
}
.word-reveal-word {
  display: inline;
  animation: fadeInWord 0.15s ease forwards;
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/styles/
git commit -m "feat(reach-ui): global styles, CSS tokens, dark/light mode, hljs theme"
```

---

## Task 4: Theme and config hooks

**Files:**
- Create: `reach_layer/web-src/src/hooks/useTheme.js`
- Create: `reach_layer/web-src/src/hooks/useAppConfig.js`
- Create: `reach_layer/web-src/src/hooks/useToast.js`

- [ ] **Step 1: Create `reach_layer/web-src/src/hooks/useTheme.js`**

```js
import { useState, useEffect } from 'react'

/**
 * Persist and apply dark/light theme via .dark class on <html>.
 * @returns {{ theme: 'dark'|'light', toggle: () => void }}
 */
export function useTheme() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem('dpg_theme') || 'dark'
  )

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('dpg_theme', theme)
  }, [theme])

  const toggle = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'))

  return { theme, toggle }
}
```

- [ ] **Step 2: Create `reach_layer/web-src/src/hooks/useAppConfig.js`**

```js
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
      .catch(() => {
        // silently fall back to defaults — server may not have ui config
      })
      .finally(() => setConfigLoading(false))
  }, [])

  return { config, configLoading }
}
```

- [ ] **Step 3: Create `reach_layer/web-src/src/hooks/useToast.js`**

```js
import { useState, useCallback } from 'react'
import { generateUUID } from '../utils'

/**
 * Manage a queue of toast notifications.
 * @returns {{ toasts: Array, addToast: (msg, type?) => void, removeToast: (id) => void }}
 */
export function useToast() {
  const [toasts, setToasts] = useState([])

  const removeToast = useCallback(id => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const addToast = useCallback(
    (message, type = 'error') => {
      const id = generateUUID()
      setToasts(prev => [...prev, { id, message, type }])
      setTimeout(() => removeToast(id), 4000)
    },
    [removeToast]
  )

  return { toasts, addToast, removeToast }
}
```

- [ ] **Step 4: Commit**

```bash
git add reach_layer/web-src/src/hooks/
git commit -m "feat(reach-ui): theme, app-config, and toast hooks"
```

---

## Task 5: UI primitives — ThemeToggle and Toast

**Files:**
- Create: `reach_layer/web-src/src/components/ui/ThemeToggle.jsx`
- Create: `reach_layer/web-src/src/components/ui/Toast.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/ui/ThemeToggle.jsx`**

```jsx
/**
 * Sun/moon icon button that calls toggle() from useTheme.
 */
export function ThemeToggle({ theme, onToggle }) {
  return (
    <button
      onClick={onToggle}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-white/10 dark:hover:bg-white/10 transition-colors"
      aria-label="Toggle theme"
    >
      {theme === 'dark' ? (
        /* Sun icon */
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
          <path d="M8 11a3 3 0 1 1 0-6 3 3 0 0 1 0 6m0 1a4 4 0 1 0 0-8 4 4 0 0 0 0 8M8 0a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-1 0v-2A.5.5 0 0 1 8 0m0 13a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-1 0v-2A.5.5 0 0 1 8 13m8-5a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1 0-1h2a.5.5 0 0 1 .5.5M3 8a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1 0-1h2A.5.5 0 0 1 3 8m10.657-5.657a.5.5 0 0 1 0 .707l-1.414 1.415a.5.5 0 1 1-.707-.708l1.414-1.414a.5.5 0 0 1 .707 0m-9.193 9.193a.5.5 0 0 1 0 .707L3.05 13.657a.5.5 0 0 1-.707-.707l1.414-1.414a.5.5 0 0 1 .707 0m9.193 2.121a.5.5 0 0 1-.707 0l-1.414-1.414a.5.5 0 0 1 .707-.707l1.414 1.414a.5.5 0 0 1 0 .707M4.464 4.465a.5.5 0 0 1-.707 0L2.343 3.05a.5.5 0 1 1 .707-.707l1.414 1.414a.5.5 0 0 1 0 .708"/>
        </svg>
      ) : (
        /* Moon icon */
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
          <path d="M6 .278a.77.77 0 0 1 .08.858 7.2 7.2 0 0 0-.878 3.46c0 4.021 3.278 7.277 7.318 7.277q.792-.001 1.533-.16a.79.79 0 0 1 .81.316.73.73 0 0 1-.031.893A8.35 8.35 0 0 1 8.344 16C3.734 16 0 12.286 0 7.71 0 4.266 2.114 1.312 5.124.06A.75.75 0 0 1 6 .278"/>
        </svg>
      )}
    </button>
  )
}
```

- [ ] **Step 2: Create `reach_layer/web-src/src/components/ui/Toast.jsx`**

```jsx
/**
 * Renders the toast stack in the bottom-right corner.
 * Each toast auto-dismisses after 4 seconds (set in useToast).
 */
export function ToastContainer({ toasts, onRemove }) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map(toast => (
        <Toast key={toast.id} toast={toast} onRemove={onRemove} />
      ))}
    </div>
  )
}

function Toast({ toast, onRemove }) {
  const colors = {
    error: 'bg-red-900/90 border-red-700 text-red-100',
    success: 'bg-green-900/90 border-green-700 text-green-100',
    info: 'bg-indigo-900/90 border-indigo-700 text-indigo-100',
  }

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-xl border text-sm shadow-lg animate-[fadeInWord_0.2s_ease] ${colors[toast.type] || colors.error}`}
    >
      <span className="flex-1">{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className="opacity-60 hover:opacity-100 flex-shrink-0 mt-0.5"
        aria-label="Dismiss"
      >
        ✕
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add reach_layer/web-src/src/components/ui/
git commit -m "feat(reach-ui): ThemeToggle and Toast components"
```

---

## Task 6: Markdown renderer

**Files:**
- Create: `reach_layer/web-src/src/components/markdown/MarkdownRenderer.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/markdown/MarkdownRenderer.jsx`**

```jsx
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

/**
 * Code block with a hover-reveal copy button.
 */
function CodeBlock({ children, className }) {
  const [copied, setCopied] = useState(false)
  const code = String(children).replace(/\n$/, '')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may be unavailable in insecure contexts
    }
  }

  return (
    <div className="code-block-wrapper rounded-lg overflow-hidden my-2 text-sm">
      <button
        onClick={handleCopy}
        className="copy-btn text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 px-2 py-1 rounded transition-colors"
        aria-label="Copy code"
      >
        {copied ? '✓ Copied' : 'Copy'}
      </button>
      <code className={className}>{children}</code>
    </div>
  )
}

/**
 * Render agent response text as rich Markdown.
 * Supports: tables, fenced code with syntax highlighting + copy,
 * blockquotes, lists, bold/italic, horizontal rules, inline code.
 *
 * @param {{ text: string }} props
 */
export function MarkdownRenderer({ text }) {
  return (
    <div className="prose-agent">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          /* Inline code */
          code({ node, inline, className, children, ...props }) {
            if (inline) {
              return (
                <code
                  className="bg-gray-800 dark:bg-gray-900 text-indigo-300 px-1.5 py-0.5 rounded text-[0.82em] font-mono"
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return <CodeBlock className={className}>{children}</CodeBlock>
          },

          /* Tables */
          table({ children }) {
            return (
              <div className="overflow-x-auto my-3 rounded-lg border border-gray-700 dark:border-gray-700">
                <table className="min-w-full text-sm">{children}</table>
              </div>
            )
          },
          thead({ children }) {
            return (
              <thead className="bg-gray-800 dark:bg-gray-900 text-gray-300">{children}</thead>
            )
          },
          th({ children }) {
            return (
              <th className="px-3 py-2 text-left font-semibold border-b border-gray-700 whitespace-nowrap">
                {children}
              </th>
            )
          },
          td({ children }) {
            return (
              <td className="px-3 py-2 border-b border-gray-800 dark:border-gray-800/60">
                {children}
              </td>
            )
          },
          tr({ children }) {
            return (
              <tr className="even:bg-gray-800/30 hover:bg-indigo-900/10 transition-colors">
                {children}
              </tr>
            )
          },

          /* Blockquote */
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-indigo-500 pl-3 my-2 text-gray-400 dark:text-gray-400 italic">
                {children}
              </blockquote>
            )
          },

          /* Links */
          a({ children, href }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-400 underline underline-offset-2 hover:text-indigo-300 transition-colors"
              >
                {children}
              </a>
            )
          },

          /* Headings */
          h1({ children }) {
            return <h1 className="text-lg font-bold mt-3 mb-1">{children}</h1>
          },
          h2({ children }) {
            return <h2 className="text-base font-bold mt-3 mb-1">{children}</h2>
          },
          h3({ children }) {
            return (
              <h3 className="text-sm font-semibold mt-2 mb-1 text-gray-400 uppercase tracking-wide">
                {children}
              </h3>
            )
          },

          /* Lists */
          ul({ children }) {
            return <ul className="list-disc list-outside ml-4 my-1 space-y-0.5">{children}</ul>
          },
          ol({ children }) {
            return <ol className="list-decimal list-outside ml-4 my-1 space-y-0.5">{children}</ol>
          },
          li({ children }) {
            return <li className="leading-relaxed">{children}</li>
          },

          /* Paragraph */
          p({ children }) {
            return <p className="my-1.5 leading-relaxed">{children}</p>
          },

          /* Horizontal rule */
          hr() {
            return <hr className="border-gray-700 my-3" />
          },

          /* Strong / Em */
          strong({ children }) {
            return <strong className="font-semibold text-gray-100 dark:text-gray-100">{children}</strong>
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/components/markdown/
git commit -m "feat(reach-ui): MarkdownRenderer with tables, code+copy, GFM"
```

---

## Task 7: useChat hook

**Files:**
- Create: `reach_layer/web-src/src/hooks/useChat.js`

- [ ] **Step 1: Create `reach_layer/web-src/src/hooks/useChat.js`**

```js
import { useState, useCallback } from 'react'
import { sendChat, fetchUserHistory } from '../api'
import { generateUUID } from '../utils'

/**
 * Manages all chat state: messages, in-flight status, session ID.
 *
 * @param {{ onError: (msg: string) => void }} options
 * @returns {{
 *   messages: Array,
 *   isSending: boolean,
 *   sessionId: string|null,
 *   newestAgentId: string|null,
 *   loadHistory: (userId: string) => Promise<{isReturning: boolean}>,
 *   send: ({text: string, userId: string|null}) => Promise<void>,
 *   reset: () => void,
 * }}
 */
export function useChat({ onError }) {
  const [messages, setMessages] = useState([])
  const [isSending, setIsSending] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  // Track the id of the most recently arrived agent message for word-reveal
  const [newestAgentId, setNewestAgentId] = useState(null)

  /**
   * Load history for a returning user. Sets sessionId and messages.
   * Returns { isReturning: true } if history was found.
   */
  const loadHistory = useCallback(async userId => {
    try {
      const data = await fetchUserHistory(userId)
      if (data.session_id) {
        setSessionId(data.session_id)
        const historyMsgs = (data.turns || []).flatMap(turn => {
          const msgs = []
          if (turn.user_message) {
            msgs.push({
              id: generateUUID(),
              role: 'user',
              text: turn.user_message,
              timestamp: turn.timestamp || new Date().toISOString(),
            })
          }
          if (turn.system_message) {
            msgs.push({
              id: generateUUID(),
              role: 'agent',
              text: turn.system_message,
              timestamp: turn.timestamp || new Date().toISOString(),
              latencyMs: null,
              wasToolUsed: false,
              wasEscalated: false,
            })
          }
          return msgs
        })
        setMessages(historyMsgs)
        return { isReturning: historyMsgs.length > 0 }
      }
    } catch {
      // ignore — fall through to fresh session
    }
    setSessionId(generateUUID())
    return { isReturning: false }
  }, [])

  /**
   * Send a user message and append the agent reply.
   */
  const send = useCallback(
    async ({ text, userId }) => {
      if (isSending || !text.trim()) return

      const sid = sessionId || generateUUID()
      if (!sessionId) setSessionId(sid)

      const userMsg = {
        id: generateUUID(),
        role: 'user',
        text: text.trim(),
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => [...prev, userMsg])
      setIsSending(true)

      try {
        const data = await sendChat({ sessionId: sid, userId, message: text.trim() })
        const agentId = generateUUID()
        const agentMsg = {
          id: agentId,
          role: 'agent',
          text: data.response_text || '(no response)',
          timestamp: new Date().toISOString(),
          latencyMs: data.latency_ms ?? null,
          wasToolUsed: !!data.was_tool_used,
          wasEscalated: !!data.was_escalated,
        }
        setMessages(prev => [...prev, agentMsg])
        setNewestAgentId(agentId)
      } catch {
        onError('Connection error. Please check your network and try again.')
      } finally {
        setIsSending(false)
      }
    },
    [isSending, sessionId, onError]
  )

  /**
   * Clear messages and generate a new session ID (local reset only).
   */
  const reset = useCallback(() => {
    setMessages([])
    setSessionId(generateUUID())
    setNewestAgentId(null)
  }, [])

  return { messages, isSending, sessionId, newestAgentId, loadHistory, send, reset }
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/hooks/useChat.js
git commit -m "feat(reach-ui): useChat hook — message state, send, history, reset"
```

---

## Task 8: MessageBubble and TypingIndicator

**Files:**
- Create: `reach_layer/web-src/src/components/chat/TypingIndicator.jsx`
- Create: `reach_layer/web-src/src/components/chat/MessageBubble.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/chat/TypingIndicator.jsx`**

```jsx
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
```

- [ ] **Step 2: Create `reach_layer/web-src/src/components/chat/MessageBubble.jsx`**

```jsx
import { useState, useEffect, useRef } from 'react'
import { MarkdownRenderer } from '../markdown/MarkdownRenderer'
import { formatTime, formatFullTime } from '../../utils'

const COLLAPSE_LINE_THRESHOLD = 15

/**
 * Render individual words of text with a fade-in animation.
 * Used only for the newest agent message (isNew=true).
 */
function WordReveal({ text }) {
  const words = text.split(' ')
  return (
    <>
      {words.map((word, i) => (
        <span
          key={i}
          className="word-reveal-word"
          style={{ animationDelay: `${i * 18}ms` }}
        >
          {word}{i < words.length - 1 ? ' ' : ''}
        </span>
      ))}
    </>
  )
}

/**
 * Single message bubble — supports user and agent roles.
 * Agent bubbles render Markdown; user bubbles render plain text.
 * Features: latency badge, tool-use badge, escalation style,
 * timestamps (hover full), collapsible long responses, word-reveal on new messages.
 *
 * @param {{
 *   message: Object,
 *   isNew: boolean,
 *   agentAvatar: string,
 *   userAvatar: string,
 * }} props
 */
export function MessageBubble({ message, isNew, agentAvatar, userAvatar }) {
  const { role, text, timestamp, latencyMs, wasToolUsed, wasEscalated } = message
  const isAgent = role === 'agent'

  const lineCount = text.split('\n').length
  const wordCount = text.split(/\s+/).length
  const isLong = lineCount > COLLAPSE_LINE_THRESHOLD || wordCount > 200
  const [expanded, setExpanded] = useState(false)
  const [showFullTime, setShowFullTime] = useState(false)
  const [revealed, setRevealed] = useState(!isNew)
  const revealTimerRef = useRef(null)

  // After word-reveal animation completes, switch to normal MarkdownRenderer
  useEffect(() => {
    if (!isNew) { setRevealed(true); return }
    const wordCount = text.split(' ').length
    const duration = wordCount * 18 + 400
    revealTimerRef.current = setTimeout(() => setRevealed(true), duration)
    return () => clearTimeout(revealTimerRef.current)
  }, [isNew, text])

  const bubbleBase = 'px-3.5 py-2.5 rounded-2xl text-sm leading-relaxed break-words'
  const agentBubbleStyle = wasEscalated
    ? `${bubbleBase} bg-orange-900/30 border border-orange-600 text-orange-100 rounded-bl-sm`
    : `${bubbleBase} bg-[var(--bubble-agent-bg)] border border-[var(--bubble-agent-border)] text-[var(--bubble-agent-text)] rounded-bl-sm`
  const userBubbleStyle = `${bubbleBase} bg-indigo-600 text-white rounded-br-sm`

  return (
    <div className={`flex mb-3 items-end gap-2 ${isAgent ? 'justify-start' : 'justify-end'}`}>
      {/* Agent avatar */}
      {isAgent && (
        <div className="w-7 h-7 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-sm flex-shrink-0 self-end">
          {agentAvatar}
        </div>
      )}

      <div className={`flex flex-col ${isAgent ? 'items-start' : 'items-end'} max-w-[78%] sm:max-w-[72%]`}>
        {/* Badges row (agent only) */}
        {isAgent && (wasToolUsed || wasEscalated) && (
          <div className="flex gap-1.5 mb-1 flex-wrap">
            {wasToolUsed && (
              <span className="text-[10px] bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded-full border border-blue-700/60">
                🔧 tool used
              </span>
            )}
            {wasEscalated && (
              <span className="text-[10px] bg-orange-900/50 text-orange-300 px-2 py-0.5 rounded-full border border-orange-700/60">
                ⚡ escalated
              </span>
            )}
          </div>
        )}

        {/* Bubble */}
        <div className={isAgent ? agentBubbleStyle : userBubbleStyle}>
          {isAgent ? (
            <>
              <div className={isLong && !expanded ? 'max-h-52 overflow-hidden relative' : ''}>
                {isNew && !revealed ? (
                  <div className="leading-relaxed">
                    <WordReveal text={text} />
                  </div>
                ) : (
                  <MarkdownRenderer text={text} />
                )}
                {isLong && !expanded && (
                  <div className="absolute bottom-0 left-0 right-0 h-14 bg-gradient-to-t from-[var(--bubble-agent-bg)] to-transparent pointer-events-none" />
                )}
              </div>
              {isLong && (
                <button
                  onClick={() => setExpanded(e => !e)}
                  className="mt-2 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  {expanded ? '↑ Show less' : '↓ Show more'}
                </button>
              )}
            </>
          ) : (
            <span className="whitespace-pre-wrap">{text}</span>
          )}
        </div>

        {/* Time + latency row */}
        <div className={`flex items-center gap-2 mt-1 ${isAgent ? '' : 'flex-row-reverse'}`}>
          <span
            className="text-[10px] text-gray-500 cursor-default select-none"
            onMouseEnter={() => setShowFullTime(true)}
            onMouseLeave={() => setShowFullTime(false)}
          >
            {showFullTime ? formatFullTime(timestamp) : formatTime(timestamp)}
          </span>
          {isAgent && latencyMs != null && (
            <span className="text-[10px] text-gray-600 bg-gray-900 px-1.5 py-0.5 rounded-full border border-gray-800">
              {latencyMs}ms
            </span>
          )}
        </div>
      </div>

      {/* User avatar */}
      {!isAgent && (
        <div className="w-7 h-7 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-sm flex-shrink-0 self-end">
          {userAvatar}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add reach_layer/web-src/src/components/chat/TypingIndicator.jsx reach_layer/web-src/src/components/chat/MessageBubble.jsx
git commit -m "feat(reach-ui): MessageBubble (badges, collapse, word-reveal) and TypingIndicator"
```

---

## Task 9: MessageList with auto-scroll override

**Files:**
- Create: `reach_layer/web-src/src/components/chat/MessageList.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/chat/MessageList.jsx`**

```jsx
import { useEffect, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'

/**
 * Scrollable message container.
 * Auto-scrolls to the bottom on new messages.
 * If the user scrolls up, auto-scroll is paused until they return to bottom.
 * Shows a "scroll to bottom" button when paused.
 *
 * @param {{
 *   messages: Array,
 *   isSending: boolean,
 *   newestAgentId: string|null,
 *   agentAvatar: string,
 *   userAvatar: string,
 *   systemMsg: string|null,
 * }} props
 */
export function MessageList({
  messages,
  isSending,
  newestAgentId,
  agentAvatar,
  userAvatar,
  systemMsg,
}) {
  const containerRef = useRef(null)
  const bottomRef = useRef(null)
  const [userScrolled, setUserScrolled] = useState(false)

  // Auto-scroll to bottom when new messages arrive (if user hasn't scrolled up)
  useEffect(() => {
    if (!userScrolled) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isSending, userScrolled])

  // Detect manual scroll
  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setUserScrolled(!atBottom)
  }

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    setUserScrolled(false)
  }

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto px-4 py-5 sm:px-6"
      >
        {/* System / welcome message */}
        {systemMsg && (
          <div className="flex justify-center mb-4">
            <span className="text-xs text-gray-500 bg-gray-900/60 px-3 py-1.5 rounded-full border border-gray-800">
              {systemMsg}
            </span>
          </div>
        )}

        {/* Message bubbles */}
        {messages.map(msg => (
          <MessageBubble
            key={msg.id}
            message={msg}
            isNew={msg.id === newestAgentId}
            agentAvatar={agentAvatar}
            userAvatar={userAvatar}
          />
        ))}

        {/* Typing indicator */}
        {isSending && <TypingIndicator agentAvatar={agentAvatar} />}

        {/* Scroll anchor */}
        <div ref={bottomRef} />
      </div>

      {/* Scroll-to-bottom FAB */}
      {userScrolled && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-3 py-1.5 rounded-full shadow-lg flex items-center gap-1.5 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="currentColor" viewBox="0 0 16 16">
            <path d="M8 4a.5.5 0 0 1 .5.5v5.793l2.146-2.147a.5.5 0 0 1 .708.708l-3 3a.5.5 0 0 1-.708 0l-3-3a.5.5 0 1 1 .708-.708L7.5 10.293V4.5A.5.5 0 0 1 8 4"/>
          </svg>
          Latest message
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/components/chat/MessageList.jsx
git commit -m "feat(reach-ui): MessageList with auto-scroll and scroll-override detection"
```

---

## Task 10: InputArea

**Files:**
- Create: `reach_layer/web-src/src/components/chat/InputArea.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/chat/InputArea.jsx`**

```jsx
import { useRef, useState } from 'react'

/**
 * Chat input area.
 * - Send on Enter, newline on Shift+Enter
 * - Character count display
 * - Clear / reset conversation button
 * - Auto-growing textarea (up to 6 lines)
 * - Disabled while sending
 *
 * @param {{
 *   onSend: (text: string) => void,
 *   onClear: () => void,
 *   disabled: boolean,
 *   placeholder: string,
 * }} props
 */
export function InputArea({ onSend, onClear, disabled, placeholder }) {
  const [text, setText] = useState('')
  const textareaRef = useRef(null)

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = e => {
    setText(e.target.value)
    // Auto-resize
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 144) + 'px' // max ~6 lines
  }

  const charCount = text.length
  const charLimit = 2000
  const nearLimit = charCount > charLimit * 0.8

  return (
    <div className="bg-[var(--surface)] border-t border-[var(--border)] px-4 py-3 sm:px-6">
      {/* Char count + clear row */}
      <div className="flex items-center justify-between mb-1.5 px-1">
        <button
          onClick={onClear}
          disabled={disabled}
          title="Clear conversation (local only)"
          className="text-[11px] text-gray-500 hover:text-gray-300 disabled:opacity-40 transition-colors"
        >
          ↺ Clear chat
        </button>
        <span className={`text-[11px] ${nearLimit ? 'text-orange-400' : 'text-gray-600'}`}>
          {charCount}/{charLimit}
        </span>
      </div>

      {/* Input row */}
      <div className="flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          value={text}
          onInput={handleInput}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder || 'Type your message…'}
          maxLength={charLimit}
          rows={1}
          className="
            flex-1 resize-none bg-[var(--surface-2)] border border-[var(--border)]
            rounded-2xl px-4 py-2.5 text-sm text-[var(--text)] placeholder-gray-500
            outline-none focus:border-indigo-500 transition-colors
            disabled:opacity-50 disabled:cursor-not-allowed
            leading-relaxed max-h-36
          "
        />
        <button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          title="Send (Enter)"
          className="
            w-10 h-10 rounded-full bg-indigo-600 hover:bg-indigo-500
            disabled:opacity-40 disabled:cursor-not-allowed
            flex items-center justify-center flex-shrink-0
            transition-all active:scale-90
          "
          aria-label="Send"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="white" viewBox="0 0 16 16">
            <path d="M15.854.146a.5.5 0 0 1 .11.54l-5.819 14.547a.75.75 0 0 1-1.329.124l-3.178-4.995L.643 7.184a.75.75 0 0 1 .124-1.33L15.314.037a.5.5 0 0 1 .54.11ZM6.636 10.07l2.761 4.338L14.13 2.576zm6.787-8.201L1.591 6.602l4.339 2.76z"/>
          </svg>
        </button>
      </div>

      <p className="text-[10px] text-gray-600 mt-1.5 px-1">
        Enter to send · Shift+Enter for new line
      </p>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/components/chat/InputArea.jsx
git commit -m "feat(reach-ui): InputArea with char count, clear, Enter/Shift+Enter"
```

---

## Task 11: ChatHeader

**Files:**
- Create: `reach_layer/web-src/src/components/chat/ChatHeader.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/chat/ChatHeader.jsx`**

```jsx
import { useState } from 'react'
import { ThemeToggle } from '../ui/ThemeToggle'

/**
 * Chat screen header.
 * Shows app name/icon, connected status, user ID pill (click to copy),
 * collapsible session debug section, switch-user button, and theme toggle.
 *
 * @param {{
 *   config: Object,
 *   userId: string|null,
 *   sessionId: string|null,
 *   theme: 'dark'|'light',
 *   onToggleTheme: () => void,
 *   onSwitchUser: () => void,
 * }} props
 */
export function ChatHeader({ config, userId, sessionId, theme, onToggleTheme, onSwitchUser }) {
  const [debugOpen, setDebugOpen] = useState(false)
  const [userIdCopied, setUserIdCopied] = useState(false)
  const [sidCopied, setSidCopied] = useState(false)

  const copyText = async (text, setCopied) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard unavailable
    }
  }

  return (
    <div className="bg-[var(--surface)] border-b border-[var(--border)] flex-shrink-0">
      {/* Main header row */}
      <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
        {/* App icon + name */}
        <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-base flex-shrink-0">
          {config.app_icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-[var(--text)] text-sm truncate">{config.app_name}</div>
          <div className="flex items-center gap-1.5 text-[11px] text-gray-500 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
            Connected
          </div>
        </div>

        {/* User ID pill */}
        {userId && (
          <button
            onClick={() => copyText(userId, setUserIdCopied)}
            title="Click to copy user ID"
            className="hidden sm:flex items-center gap-1.5 text-[11px] bg-gray-800 border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 px-2.5 py-1 rounded-full transition-colors max-w-[140px]"
          >
            <span className="truncate">{userIdCopied ? 'Copied!' : userId}</span>
          </button>
        )}

        {/* Debug toggle */}
        <button
          onClick={() => setDebugOpen(o => !o)}
          title="Session debug info"
          className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/10 transition-colors text-[11px]"
        >
          {debugOpen ? '▲' : '▼'} debug
        </button>

        {/* Theme toggle */}
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />

        {/* Switch user */}
        <button
          onClick={onSwitchUser}
          title="Switch user"
          className="text-[11px] text-gray-500 hover:text-gray-300 border border-gray-700 hover:border-gray-500 px-2.5 py-1 rounded-lg transition-colors whitespace-nowrap"
        >
          ← Switch
        </button>
      </div>

      {/* Debug panel */}
      {debugOpen && (
        <div className="px-4 pb-3 sm:px-6 border-t border-[var(--border)] bg-gray-950/50">
          <div className="mt-2 space-y-1.5 text-[11px] font-mono text-gray-500">
            <div className="flex items-center gap-2">
              <span className="text-gray-600 w-20 flex-shrink-0">User ID</span>
              <span className="text-gray-400 truncate">{userId || '—'}</span>
              {userId && (
                <button
                  onClick={() => copyText(userId, setUserIdCopied)}
                  className="text-indigo-500 hover:text-indigo-400 flex-shrink-0"
                >
                  {userIdCopied ? '✓' : 'copy'}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-gray-600 w-20 flex-shrink-0">Session</span>
              <span className="text-gray-400 truncate">{sessionId || '—'}</span>
              {sessionId && (
                <button
                  onClick={() => copyText(sessionId, setSidCopied)}
                  className="text-indigo-500 hover:text-indigo-400 flex-shrink-0"
                >
                  {sidCopied ? '✓' : 'copy'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add reach_layer/web-src/src/components/chat/ChatHeader.jsx
git commit -m "feat(reach-ui): ChatHeader with user pill, session debug, theme toggle"
```

---

## Task 12: Screen components (Loading, Setup, Chat)

**Files:**
- Create: `reach_layer/web-src/src/components/screens/LoadingScreen.jsx`
- Create: `reach_layer/web-src/src/components/screens/SetupScreen.jsx`
- Create: `reach_layer/web-src/src/components/screens/ChatScreen.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/components/screens/LoadingScreen.jsx`**

```jsx
/**
 * Full-screen spinner shown during boot / session restore.
 *
 * @param {{ message: string }} props
 */
export function LoadingScreen({ message }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 bg-[var(--bg)]">
      <div className="w-10 h-10 border-3 border-gray-700 border-t-indigo-500 rounded-full animate-spin" />
      <p className="text-sm text-gray-500">{message || 'Loading…'}</p>

      <style>{`
        .border-3 { border-width: 3px; }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 2: Create `reach_layer/web-src/src/components/screens/SetupScreen.jsx`**

```jsx
import { useState } from 'react'

/**
 * User ID entry form shown to new visitors.
 * Generates a guest ID if the field is left blank.
 *
 * @param {{
 *   config: Object,
 *   onStart: (userId: string) => void,
 * }} props
 */
export function SetupScreen({ config, onStart }) {
  const [value, setValue] = useState('')

  const handleStart = () => {
    const uid = value.trim() || `guest_${Math.random().toString(36).slice(2, 8)}`
    onStart(uid)
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter') handleStart()
  }

  return (
    <div className="flex items-center justify-center h-full bg-[var(--bg)] px-6">
      <div className="w-full max-w-sm bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-8 shadow-2xl">
        {/* Logo */}
        <div className="flex items-center gap-3 mb-7">
          <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center text-lg">
            {config.app_icon}
          </div>
          <div>
            <div className="font-bold text-[var(--text)] leading-tight">{config.app_name}</div>
            <div className="text-[11px] text-gray-500 mt-0.5">{config.app_tagline}</div>
          </div>
        </div>

        <h1 className="text-xl font-bold text-[var(--text)] mb-1.5">{config.setup_heading}</h1>
        <p className="text-sm text-gray-500 mb-6 leading-relaxed">{config.setup_subtitle}</p>

        {/* User ID field */}
        <label className="block mb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
          User ID
        </label>
        <input
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={config.user_id_placeholder}
          autoComplete="username"
          className="
            w-full bg-[var(--surface-2)] border border-[var(--border)]
            rounded-xl px-4 py-2.5 text-sm text-[var(--text)]
            placeholder-gray-500 outline-none
            focus:border-indigo-500 transition-colors mb-1.5
          "
        />
        <p className="text-[11px] text-gray-500 mb-5">{config.user_id_hint}</p>

        <button
          onClick={handleStart}
          className="
            w-full bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98]
            text-white font-semibold text-sm rounded-xl py-3
            transition-all
          "
        >
          {config.start_btn_label}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create `reach_layer/web-src/src/components/screens/ChatScreen.jsx`**

```jsx
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
```

- [ ] **Step 4: Commit**

```bash
git add reach_layer/web-src/src/components/screens/
git commit -m "feat(reach-ui): Loading, Setup, and Chat screen components"
```

---

## Task 13: App.jsx and main.jsx

**Files:**
- Create: `reach_layer/web-src/src/App.jsx`
- Create: `reach_layer/web-src/src/main.jsx`

- [ ] **Step 1: Create `reach_layer/web-src/src/App.jsx`**

```jsx
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
  const { theme, toggle: toggleTheme } = useTheme()
  const { config, configLoading } = useAppConfig()
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
```

- [ ] **Step 2: Create `reach_layer/web-src/src/main.jsx`**

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/index.css'
import App from './App'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>
)
```

- [ ] **Step 3: Commit**

```bash
git add reach_layer/web-src/src/App.jsx reach_layer/web-src/src/main.jsx
git commit -m "feat(reach-ui): App root — boot flow, screen routing, all features wired"
```

---

## Task 14: Vite build verification

- [ ] **Step 1: Run the dev server (smoke test)**

```bash
cd reach_layer/web-src && npm run dev
```

Expected output:
```
  ➜  Local:   http://localhost:5174/
  ➜  Network: ...
```

Open `http://localhost:5174` in a browser. Verify:
- Setup screen renders with branded copy (or Loading → Chat if `dpg_user_id` is in localStorage)
- No console errors on load

Stop the dev server with Ctrl+C.

- [ ] **Step 2: Build for production**

```bash
cd reach_layer/web-src && npm run build
```

Expected output:
```
dist/index.html        x.xx kB
dist/assets/index-*.js  xxx kB
dist/assets/index-*.css  xx kB
```

Verify `reach_layer/web/dist/` exists and contains `index.html` and `assets/`.

- [ ] **Step 3: Commit**

```bash
git add reach_layer/web/dist/
git commit -m "feat(reach-ui): initial production build"
```

---

## Task 15: FastAPI server.py — serve the React build

**Files:**
- Modify: `reach_layer/server.py`
- Modify: `reach_layer/pyproject.toml`

- [ ] **Step 1: Add `aiofiles` to `reach_layer/pyproject.toml`**

Find the `dependencies` list in `reach_layer/pyproject.toml` and add `"aiofiles>=23.0.0"`.

The dependencies section should include:
```toml
dependencies = [
  ...existing deps...,
  "aiofiles>=23.0.0",
]
```

- [ ] **Step 2: Install the new dependency**

```bash
cd reach_layer && uv add aiofiles
```

Expected: lock file updated, no errors.

- [ ] **Step 3: Update `reach_layer/server.py`**

Add `StaticFiles` import at the top alongside existing imports:

```python
from fastapi.staticfiles import StaticFiles
```

Replace the existing `GET /` route and add the assets mount inside `create_app`, after `FastAPIInstrumentor.instrument_app(app)`:

```python
    # Paths to the React production build
    _dist = Path(__file__).parent / "web" / "dist"
    _assets = _dist / "assets"

    # Mount /assets — serves JS/CSS bundles built by Vite
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        """Serve the React SPA entry point."""
        return FileResponse(str(_dist / "index.html"), media_type="text/html")
```

Remove the old `GET /` route that pointed to `web/index.html`.

The final `GET /` route in `create_app` should be exactly:

```python
    @app.get("/")
    def index() -> FileResponse:
        """Serve the React SPA entry point."""
        return FileResponse(str(_dist / "index.html"), media_type="text/html")
```

- [ ] **Step 4: Verify server starts**

```bash
cd reach_layer && uv run uvicorn server:app --host 0.0.0.0 --port 8005 --reload
```

Expected log lines:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8005
```

Open `http://localhost:8005` — the React app should load (the setup screen or chat screen).

- [ ] **Step 5: Commit**

```bash
cd reach_layer
git add server.py pyproject.toml uv.lock
git commit -m "feat(reach-ui): FastAPI serves React dist — mount assets, serve index.html"
```

---

## Task 16: .gitignore — exclude node_modules and dist

- [ ] **Step 1: Verify root `.gitignore` covers web-src**

Check that `reach_layer/web-src/node_modules/` is covered by `.gitignore`. If there is no entry, add:

```
reach_layer/web-src/node_modules/
reach_layer/web/dist/
```

to the root `.gitignore`.

> Note: `web/dist/` should only be committed if the project has no CI build step. In a CI pipeline, the build step runs before deployment and the dist folder is not tracked in git. If you want to track the dist (for simple deployments without a build step), remove `reach_layer/web/dist/` from the ignore list.

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore reach-layer node_modules and dist"
```

---

## Task 17: End-to-end smoke test

- [ ] **Step 1: Start the full stack**

```bash
cd automation/docker
docker compose -f docker-compose.dev.yml up -d
```

Wait for all services to report healthy (or start individual services manually for local dev).

- [ ] **Step 2: Open the UI and run through the consent + chat flow**

1. Open `http://localhost:8005`
2. Verify the Setup screen shows the KKB branding (`app_name: "KKB Assistant"`, icon `🏦`)
3. Enter `user001` and click Start
4. Verify the consent prompt appears as the first agent bubble
5. Reply `yes` — verify it does NOT loop (the fix from earlier is in place)
6. Send a message that will produce a table response (e.g., if the domain supports it)
7. Verify: table renders with borders, code blocks have copy buttons, latency badge shows on agent bubbles
8. Toggle dark/light mode — verify CSS variables switch correctly
9. Click debug ▼ — verify user ID and session ID are shown
10. Click "← Switch" — verify you return to the Setup screen

- [ ] **Step 3: Build a final clean production build and commit**

```bash
cd reach_layer/web-src && npm run build
cd ../..
git add reach_layer/web/dist/
git commit -m "feat(reach-ui): production build — React 19 chat UI complete"
```

---

## Feature → Task mapping

| Feature | Task |
|---------|------|
| 1. Rich table rendering | Task 6 (MarkdownRenderer — `table`/`th`/`td` components) |
| 2. Code block + copy button | Task 6 (CodeBlock component) |
| 3. Collapsible long responses | Task 8 (MessageBubble — `isLong` + expand toggle) |
| 4. Hover timestamps | Task 8 (MessageBubble — `showFullTime` state) |
| 5. Latency badge | Task 8 (MessageBubble — `latencyMs` pill) |
| 6. Tool-use indicator | Task 8 (MessageBubble — `wasToolUsed` badge) |
| 7. Escalation visual | Task 8 (MessageBubble — `wasEscalated` orange bubble) |
| 8. Auto-scroll with override | Task 9 (MessageList — `userScrolled` + FAB) |
| 9. Word-reveal animation | Task 8 (MessageBubble — `WordReveal` + CSS keyframe) |
| 10. User ID pill | Task 11 (ChatHeader) |
| 11. Session ID debug | Task 11 (ChatHeader — debug panel) |
| 12. Switch user | Task 11 + Task 13 (ChatHeader + App.jsx) |
| 13. Enter/Shift+Enter | Task 10 (InputArea) |
| 14. Char count | Task 10 (InputArea) |
| 15. Clear button | Task 10 (InputArea) |
| 16. Dark/light toggle | Task 4 (useTheme) + Task 5 (ThemeToggle) |
| 17. Config-driven theming | Task 4 (useAppConfig) + Task 3 (CSS variables) |
| 18. Responsive layout | Tasks 8–12 (Tailwind `sm:` breakpoints throughout) |
| 19. Toast notifications | Task 4 (useToast) + Task 5 (Toast) |
| 20. Vite build → dist | Tasks 1, 14, 15 |
