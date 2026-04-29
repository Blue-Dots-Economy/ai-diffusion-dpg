import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import StepIndicator from '../StepIndicator'

describe('StepIndicator', () => {
  it('renders all 9 step labels', () => {
    render(<StepIndicator currentStep={1} completedSteps={[]} onStepClick={vi.fn()} />)
    expect(screen.getByText('DPG Values')).toBeInTheDocument()
    expect(screen.getByText('Config Review')).toBeInTheDocument()
    expect(screen.getByText('Ingest')).toBeInTheDocument()
  })

  it('shows checkmark for completed steps', () => {
    render(<StepIndicator currentStep={3} completedSteps={[1, 2]} onStepClick={vi.fn()} />)
    const checks = screen.getAllByText('✓')
    expect(checks).toHaveLength(2)
  })

  it('calls onStepClick when a completed step is clicked', () => {
    const onStepClick = vi.fn()
    render(<StepIndicator currentStep={3} completedSteps={[1, 2]} onStepClick={onStepClick} />)
    fireEvent.click(screen.getByRole('button', { name: /dpg values/i }))
    expect(onStepClick).toHaveBeenCalledWith(1)
  })

  it('calls onStepClick for the immediate next step after a completed one', () => {
    const onStepClick = vi.fn()
    render(<StepIndicator currentStep={1} completedSteps={[1]} onStepClick={onStepClick} />)
    // Step 2 is next after step 1 which is completed — should be reachable
    fireEvent.click(screen.getByRole('button', { name: /config review/i }))
    expect(onStepClick).toHaveBeenCalledWith(2)
  })

  it('does not render unreachable steps as buttons', () => {
    render(<StepIndicator currentStep={1} completedSteps={[1]} onStepClick={vi.fn()} />)
    // Step 5 is not reachable from step 1 with only step 1 completed
    expect(screen.queryByRole('button', { name: /inputs/i })).toBeNull()
  })

  it('active step is not a button', () => {
    render(<StepIndicator currentStep={1} completedSteps={[]} onStepClick={vi.fn()} />)
    // "DPG Values" is the active step — should be a div, not a button
    expect(screen.queryByRole('button', { name: /dpg values/i })).toBeNull()
  })

  it('renders 9 step numbers when none are completed', () => {
    render(<StepIndicator currentStep={1} completedSteps={[]} onStepClick={vi.fn()} />)
    // Numbers 2-9 should appear (step 1 is active so it shows "1" in the circle)
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('9')).toBeInTheDocument()
  })
})
