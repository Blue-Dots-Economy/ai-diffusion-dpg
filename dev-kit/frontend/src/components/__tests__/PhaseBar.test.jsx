import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import PhaseBar from '../PhaseBar'

const PHASE_LABELS = {
  tier: 'Agent Type', overview: 'Overview', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', user_state: 'User State', trust: 'Trust', tools: 'Tools',
  workflow: 'Workflow', observability: 'Observability', reach: 'Reach Layer', review: 'Review',
}

describe('PhaseBar', () => {
  it('renders in expanded state by default showing all phase labels', () => {
    render(<PhaseBar currentPhase="overview" />)
    Object.values(PHASE_LABELS).forEach(label => {
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  })

  it('collapses when toggle button is clicked and hides labels', () => {
    render(<PhaseBar currentPhase="overview" />)
    // Click the collapse toggle (‹)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // Labels should no longer be rendered as text elements
    expect(screen.queryByText('Overview')).toBeNull()
    expect(screen.queryByText('Language')).toBeNull()
  })

  it('shows expand arrow title when collapsed', () => {
    render(<PhaseBar currentPhase="overview" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    expect(screen.getByTitle('Expand phases')).toBeInTheDocument()
  })

  it('re-expands when toggle clicked again', () => {
    render(<PhaseBar currentPhase="overview" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    fireEvent.click(screen.getByTitle('Expand phases'))
    expect(screen.getByText('Overview')).toBeInTheDocument()
  })

  it('marks phases before current as done (✓)', () => {
    render(<PhaseBar currentPhase="knowledge" />)
    // overview and language are before knowledge
    const overviewRow = screen.getByText('Overview').closest('div[title]')
    const languageRow = screen.getByText('Language').closest('div[title]')
    expect(overviewRow.textContent).toContain('✓')
    expect(languageRow.textContent).toContain('✓')
  })

  it('marks current phase with ●', () => {
    render(<PhaseBar currentPhase="memory" />)
    const memoryRow = screen.getByText('Memory').closest('div[title]')
    expect(memoryRow.textContent).toContain('●')
  })

  it('renders dots in collapsed mode for each phase', () => {
    render(<PhaseBar currentPhase="overview" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // 12 phases = 12 dot spans; they have title attributes
    const dots = screen.getAllByTitle(/Agent Type|Overview|Language|Knowledge|Memory|User State|Trust|Tools|Workflow|Observability|Reach Layer|Review/)
    expect(dots).toHaveLength(12)
  })
})
