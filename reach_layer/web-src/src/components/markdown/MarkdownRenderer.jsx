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
