import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { Sidebar } from '../components/chat/Sidebar'

const config = {
  app_name: 'KKB',
  app_tagline: 'DPG Skill-Jobs AI',
  app_icon: '💼',
  agent_avatar: '🌾',
}

const baseProps = {
  config,
  authEnabled: false,
  collapsed: false,
  onToggleCollapsed: vi.fn(),
  onSignOut: vi.fn(),
}

describe('Sidebar (expanded)', () => {
  it('renders the agent_avatar logo', () => {
    render(<Sidebar {...baseProps} />)
    expect(screen.getByText('🌾')).toBeInTheDocument()
  })

  it('does not render user name, email or initials', () => {
    render(<Sidebar {...baseProps} />)
    expect(screen.queryByText(/local user/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signed in with google/i)).not.toBeInTheDocument()
  })

  it('renders a collapse button that fires onToggleCollapsed', () => {
    const onToggleCollapsed = vi.fn()
    render(<Sidebar {...baseProps} onToggleCollapsed={onToggleCollapsed} />)
    fireEvent.click(screen.getByLabelText('Collapse sidebar'))
    expect(onToggleCollapsed).toHaveBeenCalled()
  })

  it('renders Switch user button when auth disabled', () => {
    render(<Sidebar {...baseProps} />)
    expect(screen.getByRole('button', { name: /switch user/i })).toBeInTheDocument()
  })

  it('renders Sign out button when auth enabled', () => {
    render(<Sidebar {...baseProps} authEnabled={true} />)
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('opens ConfirmDialog and fires onSignOut when confirmed', () => {
    const onSignOut = vi.fn()
    render(<Sidebar {...baseProps} authEnabled={true} onSignOut={onSignOut} />)
    // First click on the footer button opens the dialog (does not sign out).
    fireEvent.click(screen.getByRole('button', { name: /^sign out$/i }))
    expect(onSignOut).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    // Confirm button inside the dialog with the same label.
    fireEvent.click(within(dialog).getByRole('button', { name: /^sign out$/i }))
    expect(onSignOut).toHaveBeenCalled()
  })

  it('skips sign out when ConfirmDialog cancel is clicked', () => {
    const onSignOut = vi.fn()
    render(<Sidebar {...baseProps} authEnabled={true} onSignOut={onSignOut} />)
    fireEvent.click(screen.getByRole('button', { name: /^sign out$/i }))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))
    expect(onSignOut).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders New chat button and triggers onNewChat', () => {
    const onNewChat = vi.fn()
    render(<Sidebar {...baseProps} onNewChat={onNewChat} />)
    fireEvent.click(screen.getByRole('button', { name: /new chat/i }))
    expect(onNewChat).toHaveBeenCalled()
  })

  it('renders conversations list and calls onSelectSession', () => {
    const onSelectSession = vi.fn()
    const sessions = [
      { session_id: 'a', last_accessed: '2026-04-01T10:30:00Z' },
      { session_id: 'b', last_accessed: '2026-03-30T14:00:00Z' },
    ]
    render(<Sidebar {...baseProps} sessions={sessions} onSelectSession={onSelectSession} />)
    const rowButtons = screen.getAllByRole('button').filter(
      (el) => el.getAttribute('title') && el.getAttribute('title').includes('2026')
    )
    expect(rowButtons).toHaveLength(2)
    fireEvent.click(rowButtons[0])
    expect(onSelectSession).toHaveBeenCalledWith('a')
  })

  it('shows "No previous chats" placeholder when sessions empty', () => {
    render(<Sidebar {...baseProps} sessions={[]} />)
    expect(screen.getByText(/no previous chats/i)).toBeInTheDocument()
  })

  it('opens ConfirmDialog and fires onDeleteSession with the right id', () => {
    const onDeleteSession = vi.fn()
    const sessions = [{ session_id: 'a', last_accessed: '2026-04-01T10:30:00Z' }]
    render(<Sidebar {...baseProps} sessions={sessions} onDeleteSession={onDeleteSession} />)
    fireEvent.click(screen.getByLabelText(/^delete conversation /i))
    expect(onDeleteSession).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /delete conversation/i }))
    expect(onDeleteSession).toHaveBeenCalledWith('a')
  })

  it('skips delete when ConfirmDialog cancel is clicked', () => {
    const onDeleteSession = vi.fn()
    const sessions = [{ session_id: 'a', last_accessed: '2026-04-01T10:30:00Z' }]
    render(<Sidebar {...baseProps} sessions={sessions} onDeleteSession={onDeleteSession} />)
    fireEvent.click(screen.getByLabelText(/^delete conversation /i))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))
    expect(onDeleteSession).not.toHaveBeenCalled()
  })

  it('does not render a theme toggle (moved to app bar)', () => {
    render(<Sidebar {...baseProps} />)
    expect(screen.queryByLabelText('Toggle theme')).not.toBeInTheDocument()
  })

  it('does not render a Clear current view button', () => {
    render(<Sidebar {...baseProps} />)
    expect(screen.queryByText(/clear current view/i)).not.toBeInTheDocument()
  })
})

describe('Sidebar (collapsed)', () => {
  it('renders logo as an Expand sidebar button', () => {
    const onToggleCollapsed = vi.fn()
    render(<Sidebar {...baseProps} collapsed={true} onToggleCollapsed={onToggleCollapsed} />)
    const expand = screen.getByLabelText('Expand sidebar')
    expect(expand).toBeInTheDocument()
    fireEvent.click(expand)
    expect(onToggleCollapsed).toHaveBeenCalled()
  })

  it('does not render a separate collapse button when collapsed', () => {
    render(<Sidebar {...baseProps} collapsed={true} />)
    expect(screen.queryByLabelText('Collapse sidebar')).not.toBeInTheDocument()
  })

  it('still renders sign-out button (icon-only) when collapsed', () => {
    const onSignOut = vi.fn()
    render(<Sidebar {...baseProps} collapsed={true} onSignOut={onSignOut} />)
    fireEvent.click(screen.getByLabelText('Switch user'))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /^switch user$/i }))
    expect(onSignOut).toHaveBeenCalled()
  })

  it('falls back to app_icon when agent_avatar missing', () => {
    render(
      <Sidebar
        {...baseProps}
        collapsed={true}
        config={{ ...config, agent_avatar: undefined }}
      />
    )
    expect(screen.getByText('💼')).toBeInTheDocument()
  })
})
