import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatHeader } from '../components/chat/ChatHeader'

const config = {
  app_name: 'KKB Assistant',
  app_icon: '🏦',
}

beforeEach(() => {
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
})

describe('ChatHeader', () => {
  it('renders app name', () => {
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    expect(screen.getByText('KKB Assistant')).toBeInTheDocument()
  })

  it('shows Connected status indicator', () => {
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('shows userId in pill', () => {
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('does not show user ID pill when userId is null', () => {
    render(
      <ChatHeader config={config} userId={null} sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    // "alice" text should not appear; userId-related pill absent
    expect(screen.queryByTitle(/click to copy user id/i)).not.toBeInTheDocument()
  })

  it('calls onSwitchUser when Switch button clicked', () => {
    const onSwitchUser = vi.fn()
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={onSwitchUser} />
    )
    fireEvent.click(screen.getByTitle(/switch user/i))
    expect(onSwitchUser).toHaveBeenCalled()
  })

  it('calls onToggleTheme when ThemeToggle clicked', () => {
    const onToggleTheme = vi.fn()
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={onToggleTheme} onSwitchUser={vi.fn()} />
    )
    fireEvent.click(screen.getByLabelText('Toggle theme'))
    expect(onToggleTheme).toHaveBeenCalled()
  })

  it('toggles debug panel open and closed', () => {
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-1"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    const debugBtn = screen.getByTitle(/session debug/i)
    fireEvent.click(debugBtn)
    expect(screen.getByText(/User ID/)).toBeInTheDocument()
    expect(screen.getByText(/Session/)).toBeInTheDocument()
    fireEvent.click(debugBtn)
    expect(screen.queryByText('User ID')).not.toBeInTheDocument()
  })

  it('shows session ID in debug panel', () => {
    render(
      <ChatHeader config={config} userId="alice" sessionId="sess-xyz"
        theme="dark" onToggleTheme={vi.fn()} onSwitchUser={vi.fn()} />
    )
    fireEvent.click(screen.getByTitle(/session debug/i))
    expect(screen.getByText('sess-xyz')).toBeInTheDocument()
  })
})
