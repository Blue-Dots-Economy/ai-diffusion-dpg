import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InputArea } from '../components/chat/InputArea'

describe('InputArea', () => {
  it('renders textarea and send button', () => {
    render(<InputArea onSend={vi.fn()} onClear={vi.fn()} disabled={false} placeholder="Type..." />)
    expect(screen.getByPlaceholderText('Type...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()
  })

  it('calls onSend with trimmed text on button click', async () => {
    const onSend = vi.fn()
    render(<InputArea onSend={onSend} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    const textarea = screen.getByPlaceholderText('Type…')
    await userEvent.type(textarea, '  hello  ')
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    expect(onSend).toHaveBeenCalledWith('hello')
  })

  it('calls onSend on Enter key, not Shift+Enter', async () => {
    const onSend = vi.fn()
    render(<InputArea onSend={onSend} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    const textarea = screen.getByPlaceholderText('Type…')
    await userEvent.type(textarea, 'message')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })
    expect(onSend).toHaveBeenCalled()
  })

  it('does not call onSend on Shift+Enter', async () => {
    const onSend = vi.fn()
    render(<InputArea onSend={onSend} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    const textarea = screen.getByPlaceholderText('Type…')
    await userEvent.type(textarea, 'text')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('does not call onSend when text is blank', () => {
    const onSend = vi.fn()
    render(<InputArea onSend={onSend} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    expect(onSend).not.toHaveBeenCalled()
  })

  it('clears textarea after successful send', async () => {
    render(<InputArea onSend={vi.fn()} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    const textarea = screen.getByPlaceholderText('Type…')
    await userEvent.type(textarea, 'hello')
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    expect(textarea.value).toBe('')
  })

  it('disables textarea and send button when disabled prop is true', () => {
    render(<InputArea onSend={vi.fn()} onClear={vi.fn()} disabled={true} placeholder="Type…" />)
    expect(screen.getByPlaceholderText('Type…')).toBeDisabled()
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
  })

  it('calls onClear when clear button clicked', () => {
    const onClear = vi.fn()
    render(<InputArea onSend={vi.fn()} onClear={onClear} disabled={false} placeholder="Type…" />)
    fireEvent.click(screen.getByTitle(/clear conversation/i))
    expect(onClear).toHaveBeenCalled()
  })

  it('shows character count', async () => {
    render(<InputArea onSend={vi.fn()} onClear={vi.fn()} disabled={false} placeholder="Type…" />)
    const textarea = screen.getByPlaceholderText('Type…')
    await userEvent.type(textarea, 'abc')
    expect(screen.getByText('3/2000')).toBeInTheDocument()
  })

  it('uses default placeholder when none provided', () => {
    render(<InputArea onSend={vi.fn()} onClear={vi.fn()} disabled={false} />)
    expect(screen.getByPlaceholderText('Type your message…')).toBeInTheDocument()
  })
})
