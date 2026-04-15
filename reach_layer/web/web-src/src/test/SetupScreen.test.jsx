import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SetupScreen } from '../components/screens/SetupScreen'

const config = {
  app_name: 'Test App',
  app_tagline: 'Tagline',
  app_icon: '🤖',
  setup_heading: 'Start a session',
  setup_subtitle: 'Enter your user ID to begin.',
  user_id_placeholder: 'User ID',
  user_id_hint: 'Same ID = restored history.',
  start_btn_label: 'Start chatting →',
}

describe('SetupScreen', () => {
  it('renders app name, heading, and start button', () => {
    render(<SetupScreen config={config} onStart={vi.fn()} />)
    expect(screen.getByText('Test App')).toBeInTheDocument()
    expect(screen.getByText('Start a session')).toBeInTheDocument()
    expect(screen.getByText('Start chatting →')).toBeInTheDocument()
  })

  it('calls onStart with typed user ID on button click', async () => {
    const onStart = vi.fn()
    render(<SetupScreen config={config} onStart={onStart} />)
    await userEvent.type(screen.getByPlaceholderText('User ID'), 'alice')
    fireEvent.click(screen.getByText('Start chatting →'))
    expect(onStart).toHaveBeenCalledWith('alice')
  })

  it('calls onStart on Enter key press', async () => {
    const onStart = vi.fn()
    render(<SetupScreen config={config} onStart={onStart} />)
    const input = screen.getByPlaceholderText('User ID')
    await userEvent.type(input, 'bob')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onStart).toHaveBeenCalledWith('bob')
  })

  it('generates guest ID when field is blank', () => {
    const onStart = vi.fn()
    render(<SetupScreen config={config} onStart={onStart} />)
    fireEvent.click(screen.getByText('Start chatting →'))
    expect(onStart).toHaveBeenCalledWith(expect.stringMatching(/^guest_/))
  })

  it('trims whitespace from entered user ID', async () => {
    const onStart = vi.fn()
    render(<SetupScreen config={config} onStart={onStart} />)
    const input = screen.getByPlaceholderText('User ID')
    await userEvent.type(input, '  carol  ')
    fireEvent.click(screen.getByText('Start chatting →'))
    expect(onStart).toHaveBeenCalledWith('carol')
  })

  it('shows subtitle and hint text', () => {
    render(<SetupScreen config={config} onStart={vi.fn()} />)
    expect(screen.getByText('Enter your user ID to begin.')).toBeInTheDocument()
    expect(screen.getByText('Same ID = restored history.')).toBeInTheDocument()
  })
})
