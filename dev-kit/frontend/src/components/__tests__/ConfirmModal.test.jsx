import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import ConfirmModal from '../ConfirmModal'

describe('ConfirmModal', () => {
  const baseProps = {
    title: 'Delete item?',
    message: 'This will be removed.',
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  }

  it('renders title and message', () => {
    render(<ConfirmModal {...baseProps} />)
    expect(screen.getByText('Delete item?')).toBeInTheDocument()
    expect(screen.getByText('This will be removed.')).toBeInTheDocument()
  })

  it('renders bullet list when provided', () => {
    render(
      <ConfirmModal
        {...baseProps}
        bullets={['First warning', 'Second warning']}
      />
    )
    expect(screen.getByText('First warning')).toBeInTheDocument()
    expect(screen.getByText('Second warning')).toBeInTheDocument()
  })

  it('renders default confirm label when not provided', () => {
    render(<ConfirmModal {...baseProps} />)
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  })

  it('renders custom confirm label', () => {
    render(<ConfirmModal {...baseProps} confirmLabel="Yes, delete it" />)
    expect(screen.getByRole('button', { name: 'Yes, delete it' })).toBeInTheDocument()
  })

  it('calls onConfirm when confirm button is clicked', () => {
    const onConfirm = vi.fn()
    render(<ConfirmModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('calls onCancel when Cancel button is clicked', () => {
    const onCancel = vi.fn()
    render(<ConfirmModal {...baseProps} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('calls onCancel when × close button is clicked', () => {
    const onCancel = vi.fn()
    render(<ConfirmModal {...baseProps} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: '×' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('renders without bullets when not provided', () => {
    const { container } = render(<ConfirmModal {...baseProps} />)
    expect(container.querySelector('ul')).toBeNull()
  })

  it('renders without message when not provided', () => {
    render(<ConfirmModal title="Are you sure?" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.queryByRole('paragraph')).toBeNull()
  })
})
