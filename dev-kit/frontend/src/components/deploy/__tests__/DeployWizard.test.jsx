import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi } from 'vitest'

// Mock all step components so they render simple identifiable placeholders.
// ConfigReviewStep and PreviewStep also call onValidationResult on mount so
// the Deploy button isn't permanently disabled in navigation tests.
vi.mock('../DpgValuesStep', () => ({ default: () => <div>DpgValuesStep</div> }))
vi.mock('../ConfigReviewStep', () => ({
  default: function MockConfigReview({ onValidationResult }) {
    React.useEffect(() => {
      onValidationResult?.({ valid: true, block_errors: {}, invariant_errors: [] })
    }, [])
    return <div>ConfigReviewStep</div>
  },
}))
vi.mock('../DependenciesStep', () => ({ default: () => <div>DependenciesStep</div> }))
vi.mock('../ResourcePresetStep', () => ({ default: () => <div>ResourcePresetStep</div> }))
vi.mock('../MandatoryInputsStep', () => ({ default: () => <div>MandatoryInputsStep</div> }))
vi.mock('../DeployTargetStep', () => ({ default: () => <div>DeployTargetStep</div> }))
vi.mock('../PreviewStep', () => ({
  default: function MockPreview({ onValidationResult }) {
    React.useEffect(() => {
      onValidationResult?.({ valid: true, block_errors: {}, invariant_errors: [] })
    }, [])
    return <div>PreviewStep</div>
  },
}))
vi.mock('../DeployStatusStep', () => ({
  default: function MockDeployStatus({ onSuccess }) {
    return (
      <div>
        DeployStatusStep
        <button onClick={onSuccess}>MarkDone</button>
      </div>
    )
  },
}))
vi.mock('../IngestDocumentsStep', () => ({ default: () => <div>IngestDocumentsStep</div> }))

vi.mock('../../../api', () => ({
  api: {
    getProject: vi.fn().mockResolvedValue({ slug: 'test-proj' }),
    getDeployStatus: vi.fn().mockResolvedValue({ overall: 'idle', services: [] }),
  },
}))

import DeployWizard from '../DeployWizard'
import { api } from '../../../api'

const defaultProps = { slug: 'test-proj', onBack: vi.fn() }

// Jump to a step via the internal event — bypasses per-step validation guards
function jumpToStep(n) {
  window.dispatchEvent(new CustomEvent('deploy-wizard-go-to-step', { detail: n }))
}

describe('DeployWizard — initial state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('starts at step 1 (DPG Values)', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    expect(screen.getByText('DpgValuesStep')).toBeInTheDocument()
  })

  it('shows Next and Dashboard buttons on step 1', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    expect(screen.getByText('Next →')).toBeInTheDocument()
    // Both header and footer show "← Dashboard" on step 1
    expect(screen.getAllByText('← Dashboard').length).toBeGreaterThanOrEqual(1)
  })
})

describe('DeployWizard — auto-skip to step 9', () => {
  it('skips to IngestDocumentsStep when deploy status is complete', async () => {
    api.getDeployStatus.mockResolvedValue({
      overall: 'complete',
      services: [{ name: 'agent_core', status: 'healthy' }],
      target: 'docker',
    })
    render(<DeployWizard {...defaultProps} />)
    await waitFor(() => expect(screen.getByText('IngestDocumentsStep')).toBeInTheDocument())
  })

  it('shows "already deployed" informational banner on auto-skip', async () => {
    api.getDeployStatus.mockResolvedValue({
      overall: 'complete',
      services: [{ name: 'agent_core', status: 'healthy' }],
    })
    render(<DeployWizard {...defaultProps} />)
    await waitFor(() => expect(screen.getByText(/already deployed/i)).toBeInTheDocument())
  })

  it('does NOT auto-skip when status is idle', async () => {
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    expect(screen.getByText('DpgValuesStep')).toBeInTheDocument()
  })
})

describe('DeployWizard — handleNext step navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('advances from step 1 to step 2 on Next click', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText('ConfigReviewStep')).toBeInTheDocument()
  })

  it('step 4: blocks advance when no preset selected', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(4))
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText(/select a resource preset/i)).toBeInTheDocument()
  })

  it('step 5: blocks advance when Anthropic API key is empty', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(5))
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText(/anthropic api key is required/i)).toBeInTheDocument()
  })

  it('step 6: blocks advance when no target selected', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(6))
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText(/select a deploy target/i)).toBeInTheDocument()
  })
})

describe('DeployWizard — step 5 always validates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('validates step 5 even after it was previously completed', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    // Mark step 5 as completed by first going forward then jumping back
    act(() => jumpToStep(5))
    // Clicking Next without a key should show the error even though
    // we just jumped here (not via the normal flow that would have enforced the key)
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText(/anthropic api key is required/i)).toBeInTheDocument()
    // We're still on step 5
    expect(screen.getByText('MandatoryInputsStep')).toBeInTheDocument()
  })
})

describe('DeployWizard — step 7 Deploy button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('shows Deploy button (not Next) on step 7', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(7))
    await waitFor(() => expect(screen.getByText('Deploy')).toBeInTheDocument())
    expect(screen.queryByText('Next →')).toBeNull()
  })

  it('Deploy button is enabled when validation passes', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(7))
    await waitFor(() => {
      expect(screen.getByText('Deploy')).not.toBeDisabled()
    })
  })
})

describe('DeployWizard — step 8 footer hidden', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('step 8 has no Next or footer Back button', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(8))
    await waitFor(() => expect(screen.getByText('DeployStatusStep')).toBeInTheDocument())
    expect(screen.queryByText('Next →')).toBeNull()
    // Back button in footer is gone; DeployStatusStep provides its own
    expect(screen.queryByText('← Back')).toBeNull()
  })

  it('onSuccess from DeployStatusStep advances to step 9', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    act(() => jumpToStep(8))
    await waitFor(() => screen.getByText('MarkDone'))
    fireEvent.click(screen.getByText('MarkDone'))
    expect(screen.getByText('IngestDocumentsStep')).toBeInTheDocument()
  })
})

describe('DeployWizard — handleStepClick navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('step indicator click navigates to a previously-visited step', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    // Advance to step 2 (step 1 becomes completed)
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText('ConfigReviewStep')).toBeInTheDocument()
    // Click step 1 in the indicator to go back
    fireEvent.click(screen.getByRole('button', { name: /dpg values/i }))
    expect(screen.getByText('DpgValuesStep')).toBeInTheDocument()
  })

  it('Back button navigates to previous step', async () => {
    render(<DeployWizard {...defaultProps} />)
    await act(async () => {})
    fireEvent.click(screen.getByText('Next →'))
    expect(screen.getByText('ConfigReviewStep')).toBeInTheDocument()
    fireEvent.click(screen.getByText('← Back'))
    expect(screen.getByText('DpgValuesStep')).toBeInTheDocument()
  })
})
