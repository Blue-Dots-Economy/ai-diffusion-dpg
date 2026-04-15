import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
})
