import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import DiffModal from '../DiffModal'

const makeConfig = (block, content, status = 'complete') => ({ block, content, status })

const currentConfigs = [
  makeConfig('agent_core', 'primary_model: claude-haiku\n'),
  makeConfig('knowledge_engine', 'rag: disabled\n'),
]
const previewConfigs = [
  makeConfig('agent_core', 'primary_model: claude-sonnet\n'),
  makeConfig('knowledge_engine', 'rag: disabled\n'),
]

describe('DiffModal', () => {
  const baseProps = {
    currentConfigs,
    previewConfigs,
    checkpointPhase: '02_language',
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  }

  it('renders the modal title', () => {
    render(<DiffModal {...baseProps} />)
    expect(screen.getByRole('heading', { name: 'Restore Checkpoint' })).toBeInTheDocument()
  })

  it('shows the checkpoint phase name', () => {
    render(<DiffModal {...baseProps} />)
    expect(screen.getByText('02_language')).toBeInTheDocument()
  })

  it('shows count of changed blocks', () => {
    render(<DiffModal {...baseProps} />)
    // agent_core changed, knowledge_engine unchanged → 1 block will change
    expect(screen.getByText(/1 block will change/)).toBeInTheDocument()
  })

  it('shows "no config changes" when nothing changed', () => {
    render(<DiffModal {...baseProps} currentConfigs={previewConfigs} />)
    expect(screen.getByText(/no config changes/)).toBeInTheDocument()
  })

  it('renders all 7 block tabs', () => {
    render(<DiffModal {...baseProps} />)
    expect(screen.getByText('Agent Core')).toBeInTheDocument()
    expect(screen.getByText('Knowledge Engine')).toBeInTheDocument()
    expect(screen.getByText('Memory Layer')).toBeInTheDocument()
  })

  it('shows diff lines for changed block', () => {
    render(<DiffModal {...baseProps} />)
    // agent_core is active by default; it changed
    expect(screen.getByText(/primary_model: claude-haiku/)).toBeInTheDocument()
    expect(screen.getByText(/primary_model: claude-sonnet/)).toBeInTheDocument()
  })

  it('shows "No changes to this block" for unchanged block', () => {
    render(<DiffModal {...baseProps} />)
    // Switch to knowledge_engine tab (unchanged)
    fireEvent.click(screen.getByText('Knowledge Engine'))
    expect(screen.getByText('No changes to this block.')).toBeInTheDocument()
  })

  it('calls onCancel when Cancel button is clicked', () => {
    const onCancel = vi.fn()
    render(<DiffModal {...baseProps} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('calls onCancel when × button is clicked', () => {
    const onCancel = vi.fn()
    render(<DiffModal {...baseProps} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: '×' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('does NOT call onConfirm directly when "Restore Checkpoint" is clicked — shows confirmation popup first', () => {
    const onConfirm = vi.fn()
    render(<DiffModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.click(screen.getByRole('button', { name: 'Restore Checkpoint' }))
    expect(onConfirm).not.toHaveBeenCalled()
    // Confirmation popup should appear
    expect(screen.getByText('Restore checkpoint?')).toBeInTheDocument()
  })

  it('calls onConfirm after confirming in the popup', () => {
    const onConfirm = vi.fn()
    render(<DiffModal {...baseProps} onConfirm={onConfirm} />)
    fireEvent.click(screen.getByRole('button', { name: 'Restore Checkpoint' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, restore and lose progress' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('dismisses confirmation popup when "Cancel" in popup is clicked', () => {
    render(<DiffModal {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: 'Restore Checkpoint' }))
    expect(screen.getByText('Restore checkpoint?')).toBeInTheDocument()
    // Two Cancel buttons exist (DiffModal footer + ConfirmModal) — click the last one (ConfirmModal's)
    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' })
    fireEvent.click(cancelButtons[cancelButtons.length - 1])
    expect(screen.queryByText('Restore checkpoint?')).toBeNull()
  })

  it('confirmation popup lists warning bullets', () => {
    render(<DiffModal {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: 'Restore Checkpoint' }))
    expect(screen.getByText(/will be overwritten with checkpoint values/)).toBeInTheDocument()
    expect(screen.getByText(/conversation history will be rolled back/)).toBeInTheDocument()
    expect(screen.getByText(/permanently lost/)).toBeInTheDocument()
  })
})
