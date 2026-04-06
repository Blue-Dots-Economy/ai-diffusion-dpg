import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToastContainer } from '../components/ui/Toast'

const makeToast = (overrides = {}) => ({
  id: 'toast-1',
  message: 'Something went wrong',
  type: 'error',
  ...overrides,
})

describe('ToastContainer', () => {
  it('renders nothing when toasts array is empty', () => {
    const { container } = render(<ToastContainer toasts={[]} onRemove={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders toast messages', () => {
    const toasts = [makeToast({ message: 'Error occurred' })]
    render(<ToastContainer toasts={toasts} onRemove={vi.fn()} />)
    expect(screen.getByText('Error occurred')).toBeInTheDocument()
  })

  it('renders multiple toasts', () => {
    const toasts = [
      makeToast({ id: '1', message: 'First' }),
      makeToast({ id: '2', message: 'Second', type: 'success' }),
    ]
    render(<ToastContainer toasts={toasts} onRemove={vi.fn()} />)
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
  })

  it('calls onRemove with correct id when dismiss button clicked', () => {
    const onRemove = vi.fn()
    const toasts = [makeToast({ id: 'abc', message: 'Dismissible' })]
    render(<ToastContainer toasts={toasts} onRemove={onRemove} />)
    fireEvent.click(screen.getByLabelText('Dismiss'))
    expect(onRemove).toHaveBeenCalledWith('abc')
  })
})

describe('ThemeToggle', () => {
  it('shows toggle theme button', async () => {
    const { ThemeToggle } = await import('../components/ui/ThemeToggle')
    render(<ThemeToggle theme="dark" onToggle={vi.fn()} />)
    expect(screen.getByLabelText('Toggle theme')).toBeInTheDocument()
  })

  it('title says switch to light mode when dark', async () => {
    const { ThemeToggle } = await import('../components/ui/ThemeToggle')
    render(<ThemeToggle theme="dark" onToggle={vi.fn()} />)
    expect(screen.getByTitle('Switch to light mode')).toBeInTheDocument()
  })

  it('title says switch to dark mode when light', async () => {
    const { ThemeToggle } = await import('../components/ui/ThemeToggle')
    render(<ThemeToggle theme="light" onToggle={vi.fn()} />)
    expect(screen.getByTitle('Switch to dark mode')).toBeInTheDocument()
  })

  it('calls onToggle on click', async () => {
    const { ThemeToggle } = await import('../components/ui/ThemeToggle')
    const onToggle = vi.fn()
    render(<ThemeToggle theme="dark" onToggle={onToggle} />)
    fireEvent.click(screen.getByLabelText('Toggle theme'))
    expect(onToggle).toHaveBeenCalled()
  })
})
