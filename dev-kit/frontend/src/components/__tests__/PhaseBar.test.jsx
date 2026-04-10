import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import PhaseBar from '../PhaseBar'

const PHASE_LABELS = {
  overview: 'Overview', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', trust: 'Trust', connectors: 'Connectors',
  workflow: 'Workflow', observability: 'Observability', reach: 'Reach Layer', review: 'Review',
}

describe('PhaseBar', () => {
  it('renders in expanded state by default showing all phase labels', () => {
    render(<PhaseBar currentPhase="overview" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    Object.values(PHASE_LABELS).forEach(label => {
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  })

  it('collapses when toggle button is clicked and hides labels', () => {
    render(<PhaseBar currentPhase="overview" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    // Click the collapse toggle (‹)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // Labels should no longer be rendered as text elements
    expect(screen.queryByText('Overview')).toBeNull()
    expect(screen.queryByText('Language')).toBeNull()
  })

  it('shows expand arrow title when collapsed', () => {
    render(<PhaseBar currentPhase="overview" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    expect(screen.getByTitle('Expand phases')).toBeInTheDocument()
  })

  it('re-expands when toggle clicked again', () => {
    render(<PhaseBar currentPhase="overview" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    fireEvent.click(screen.getByTitle('Expand phases'))
    expect(screen.getByText('Overview')).toBeInTheDocument()
  })

  it('marks phases before current as done (✓)', () => {
    render(<PhaseBar currentPhase="knowledge" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    // overview and language are before knowledge
    const buttons = screen.getAllByRole('button')
    const phaseButtons = buttons.filter(b => b.textContent.includes('Overview') || b.textContent.includes('Language'))
    phaseButtons.forEach(btn => {
      expect(btn.textContent).toContain('✓')
    })
  })

  it('marks current phase with ●', () => {
    render(<PhaseBar currentPhase="memory" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    const memoryBtn = screen.getByText('Memory').closest('button')
    expect(memoryBtn.textContent).toContain('●')
  })

  it('disables phases with no checkpoint', () => {
    render(<PhaseBar currentPhase="memory" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    const overviewBtn = screen.getByText('Overview').closest('button')
    expect(overviewBtn).toBeDisabled()
  })

  it('enables phases that have a checkpoint', () => {
    const checkpoints = [{ phase: '01_overview', created_at: '2024-01-01' }]
    render(<PhaseBar currentPhase="memory" checkpoints={checkpoints} onRestoreCheckpoint={vi.fn()} />)
    const overviewBtn = screen.getByText('Overview').closest('button')
    expect(overviewBtn).not.toBeDisabled()
  })

  it('calls onRestoreCheckpoint with checkpoint phase when enabled phase is clicked', () => {
    const onRestore = vi.fn()
    const checkpoints = [{ phase: '01_overview' }]
    render(<PhaseBar currentPhase="memory" checkpoints={checkpoints} onRestoreCheckpoint={onRestore} />)
    fireEvent.click(screen.getByText('Overview').closest('button'))
    expect(onRestore).toHaveBeenCalledWith('01_overview')
  })

  it('does not call onRestoreCheckpoint when disabled phase is clicked', () => {
    const onRestore = vi.fn()
    render(<PhaseBar currentPhase="memory" checkpoints={[]} onRestoreCheckpoint={onRestore} />)
    fireEvent.click(screen.getByText('Overview').closest('button'))
    expect(onRestore).not.toHaveBeenCalled()
  })

  it('renders dots in collapsed mode for each phase', () => {
    render(<PhaseBar currentPhase="overview" checkpoints={[]} onRestoreCheckpoint={vi.fn()} />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // 10 phases = 10 dot spans; they have title attributes
    const dots = screen.getAllByTitle(/Overview|Language|Knowledge|Memory|Trust|Connectors|Workflow|Observability|Reach Layer|Review/)
    expect(dots).toHaveLength(10)
  })
})
