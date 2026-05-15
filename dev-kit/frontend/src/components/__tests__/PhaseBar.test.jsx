import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import PhaseBar from '../PhaseBar'

const PHASE_LABELS = {
  tier: 'Intake', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', user_state: 'User State', trust: 'Trust', tools: 'Tools',
  workflow: 'Workflow', observability: 'Observability', reach: 'Reach', review: 'Review',
}

describe('PhaseBar', () => {
  it('renders in expanded state by default showing all phase labels', () => {
    render(<PhaseBar currentPhase="tier" />)
    Object.values(PHASE_LABELS).forEach(label => {
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  })

  it('does not render "overview" or "Agent Type" phases', () => {
    render(<PhaseBar currentPhase="tier" />)
    expect(screen.queryByText('Overview')).toBeNull()
    expect(screen.queryByText('Agent Type')).toBeNull()
  })

  it('renders tier phase as "Intake"', () => {
    render(<PhaseBar currentPhase="tier" />)
    expect(screen.getByText('Intake')).toBeInTheDocument()
  })

  it('collapses when toggle button is clicked and hides labels', () => {
    render(<PhaseBar currentPhase="tier" />)
    // Click the collapse toggle (‹)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // Labels should no longer be rendered as text elements
    expect(screen.queryByText('Intake')).toBeNull()
    expect(screen.queryByText('Language')).toBeNull()
  })

  it('shows expand arrow title when collapsed', () => {
    render(<PhaseBar currentPhase="tier" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    expect(screen.getByTitle('Expand phases')).toBeInTheDocument()
  })

  it('re-expands when toggle clicked again', () => {
    render(<PhaseBar currentPhase="tier" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    fireEvent.click(screen.getByTitle('Expand phases'))
    expect(screen.getByText('Intake')).toBeInTheDocument()
  })

  it('marks phases before current as done (✓)', () => {
    render(<PhaseBar currentPhase="knowledge" />)
    // tier and language are before knowledge
    const tierRow = screen.getByText('Intake').closest('div[title]')
    const languageRow = screen.getByText('Language').closest('div[title]')
    expect(tierRow.textContent).toContain('✓')
    expect(languageRow.textContent).toContain('✓')
  })

  it('marks current phase with ●', () => {
    render(<PhaseBar currentPhase="memory" />)
    const memoryRow = screen.getByText('Memory').closest('div[title]')
    expect(memoryRow.textContent).toContain('●')
  })

  it('renders dots in collapsed mode for each of the 11 phases', () => {
    render(<PhaseBar currentPhase="tier" />)
    fireEvent.click(screen.getByTitle('Collapse phases'))
    // 11 phases = 11 dot spans; they have title attributes
    const dots = screen.getAllByTitle(/Intake|Language|Knowledge|Memory|User State|Trust|Tools|Workflow|Observability|Reach|Review/)
    expect(dots).toHaveLength(11)
  })
})
