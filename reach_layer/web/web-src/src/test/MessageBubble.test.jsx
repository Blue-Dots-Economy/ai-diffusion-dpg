import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MessageBubble } from '../components/chat/MessageBubble'

// Silence MarkdownRenderer child-component render issues in tests
vi.mock('../components/markdown/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ text }) => <div data-testid="markdown">{text}</div>,
}))

const userMsg = {
  id: 'u1',
  role: 'user',
  text: 'Hello there',
  timestamp: '2024-01-15T10:00:00Z',
  latencyMs: null,
  wasToolUsed: false,
  wasEscalated: false,
}

const agentMsg = {
  id: 'a1',
  role: 'agent',
  text: 'Hi! How can I help?',
  timestamp: '2024-01-15T10:00:01Z',
  latencyMs: 342,
  wasToolUsed: false,
  wasEscalated: false,
}

describe('MessageBubble — user role', () => {
  it('renders user message text', () => {
    render(<MessageBubble message={userMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText('Hello there')).toBeInTheDocument()
  })

  it('shows user avatar', () => {
    render(<MessageBubble message={userMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText('👤')).toBeInTheDocument()
  })

  it('does not show latency badge for user messages', () => {
    render(<MessageBubble message={userMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.queryByText(/ms/)).not.toBeInTheDocument()
  })
})

describe('MessageBubble — agent role', () => {
  it('renders agent message via MarkdownRenderer', () => {
    render(<MessageBubble message={agentMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByTestId('markdown')).toBeInTheDocument()
  })

  it('shows agent avatar', () => {
    render(<MessageBubble message={agentMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText('🤖')).toBeInTheDocument()
  })

  it('shows latency badge', () => {
    render(<MessageBubble message={agentMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText('342ms')).toBeInTheDocument()
  })

  it('does not show latency badge when latencyMs is null', () => {
    const msg = { ...agentMsg, latencyMs: null }
    render(<MessageBubble message={msg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument()
  })

  it('shows tool-use badge when wasToolUsed is true', () => {
    const msg = { ...agentMsg, wasToolUsed: true }
    render(<MessageBubble message={msg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText(/tool used/i)).toBeInTheDocument()
  })

  it('shows escalated badge when wasEscalated is true', () => {
    const msg = { ...agentMsg, wasEscalated: true }
    render(<MessageBubble message={msg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText(/escalated/i)).toBeInTheDocument()
  })

  it('shows "Show more" button for long messages (>200 words)', () => {
    const longText = Array(201).fill('word').join(' ')
    const msg = { ...agentMsg, text: longText }
    render(<MessageBubble message={msg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.getByText('↓ Show more')).toBeInTheDocument()
  })

  it('expands collapsed long message on "Show more" click', () => {
    const longText = Array(201).fill('word').join(' ')
    const msg = { ...agentMsg, text: longText }
    render(<MessageBubble message={msg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    fireEvent.click(screen.getByText('↓ Show more'))
    expect(screen.getByText('↑ Show less')).toBeInTheDocument()
  })

  it('does not show Show more for short messages', () => {
    render(<MessageBubble message={agentMsg} isNew={false} agentAvatar="🤖" userAvatar="👤" />)
    expect(screen.queryByText(/show more/i)).not.toBeInTheDocument()
  })
})
