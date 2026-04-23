import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { ChatHeader } from '../components/chat/ChatHeader'

const config = {
  app_name: 'KKB Assistant',
  app_icon: '🏦',
}

describe('ChatHeader', () => {
  it('renders app name and Connected status', () => {
    render(<ChatHeader config={config} />)
    expect(screen.getByText('KKB Assistant')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('renders user display name and email on the right when auth enabled', () => {
    const identity = {
      user_id: 'google:1',
      display_name: 'Alice Wonderland',
      email: 'alice@example.com',
      picture: '',
    }
    render(
      <ChatHeader
        config={config}
        identity={identity}
        userId={identity.user_id}
        authEnabled={true}
      />
    )
    expect(screen.getByText('Alice Wonderland')).toBeInTheDocument()
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()
  })

  it('renders Local user caption when auth disabled', () => {
    render(
      <ChatHeader
        config={config}
        identity={null}
        userId="alice"
        authEnabled={false}
      />
    )
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('Local user')).toBeInTheDocument()
  })

  it('renders profile picture when provided', () => {
    const identity = {
      user_id: 'google:1',
      display_name: 'Alice',
      email: 'alice@example.com',
      picture: 'https://example.com/a.png',
    }
    render(
      <ChatHeader
        config={config}
        identity={identity}
        userId={identity.user_id}
        authEnabled={true}
      />
    )
    const img = document.querySelector('img')
    expect(img).not.toBeNull()
    expect(img.getAttribute('src')).toBe('https://example.com/a.png')
  })

  it('falls back to initials when picture missing', () => {
    const identity = {
      user_id: 'google:1',
      display_name: 'Alice Wonderland',
      email: 'alice@example.com',
      picture: '',
    }
    render(
      <ChatHeader
        config={config}
        identity={identity}
        userId={identity.user_id}
        authEnabled={true}
      />
    )
    expect(screen.getByText('AW')).toBeInTheDocument()
  })

  it('does not render a hamburger/expand button (moved to sidebar logo)', () => {
    render(<ChatHeader config={config} identity={null} userId={null} authEnabled={false} />)
    expect(screen.queryByLabelText('Expand sidebar')).not.toBeInTheDocument()
  })

  it('renders theme toggle when onToggleTheme is provided', () => {
    const onToggleTheme = vi.fn()
    render(
      <ChatHeader
        config={config}
        identity={null}
        userId={null}
        authEnabled={false}
        theme="dark"
        onToggleTheme={onToggleTheme}
      />
    )
    fireEvent.click(screen.getByLabelText('Toggle theme'))
    expect(onToggleTheme).toHaveBeenCalled()
  })

  it('does not render theme toggle when onToggleTheme is absent', () => {
    render(<ChatHeader config={config} identity={null} userId={null} authEnabled={false} />)
    expect(screen.queryByLabelText('Toggle theme')).not.toBeInTheDocument()
  })

  it('opens profile menu dropdown on avatar click', () => {
    const identity = { user_id: 'u1', display_name: 'Alice', email: '', picture: '' }
    render(
      <ChatHeader config={config} identity={identity} userId="u1" authEnabled={true} onSignOut={vi.fn()} />
    )
    fireEvent.click(screen.getByLabelText('Profile menu'))
    expect(screen.getByText('Sign out')).toBeInTheDocument()
  })

  it('shows "Switch user" in profile menu when auth disabled', () => {
    render(
      <ChatHeader config={config} identity={null} userId="alice" authEnabled={false} onSignOut={vi.fn()} />
    )
    fireEvent.click(screen.getByLabelText('Profile menu'))
    expect(screen.getByText('Switch user')).toBeInTheDocument()
  })

  it('opens ConfirmDialog and fires onSignOut when confirmed', () => {
    const onSignOut = vi.fn()
    const identity = { user_id: 'u1', display_name: 'Alice', email: '', picture: '' }
    render(
      <ChatHeader config={config} identity={identity} userId="u1" authEnabled={true} onSignOut={onSignOut} />
    )
    fireEvent.click(screen.getByLabelText('Profile menu'))
    fireEvent.click(screen.getByText('Sign out'))
    expect(onSignOut).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /^sign out$/i }))
    expect(onSignOut).toHaveBeenCalled()
  })

  it('skips sign out when ConfirmDialog cancel is clicked', () => {
    const onSignOut = vi.fn()
    const identity = { user_id: 'u1', display_name: 'Alice', email: '', picture: '' }
    render(
      <ChatHeader config={config} identity={identity} userId="u1" authEnabled={true} onSignOut={onSignOut} />
    )
    fireEvent.click(screen.getByLabelText('Profile menu'))
    fireEvent.click(screen.getByText('Sign out'))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))
    expect(onSignOut).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
