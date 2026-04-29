import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    validateDeployConfig: vi.fn(),
    getConfig: vi.fn().mockResolvedValue({ content: 'agent_core: {}' }),
    updateConfig: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../../../hooks/useYamlEditor', () => ({
  default: () => ({
    startEdit: vi.fn(),
    cancelEdit: vi.fn(),
    getContent: vi.fn().mockReturnValue('edited: true'),
    setReadOnly: vi.fn(),
  }),
}))

import ConfigReviewStep from '../ConfigReviewStep'
import { api } from '../../../api'

const validResult = { valid: true, block_errors: {}, invariant_errors: [] }
const invalidResult = {
  valid: false,
  block_errors: { agent_core: ['missing required field: persona'] },
  invariant_errors: [],
}

describe('ConfigReviewStep', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.validateDeployConfig.mockResolvedValue(validResult)
  })

  it('shows loading state initially', () => {
    render(<ConfigReviewStep slug="test-proj" />)
    expect(screen.getByText(/loading configs/i)).toBeInTheDocument()
  })

  it('shows "All configs valid" after successful validation', async () => {
    render(<ConfigReviewStep slug="test-proj" />)
    await waitFor(() => expect(screen.getByText(/all configs valid/i)).toBeInTheDocument())
  })

  it('calls onValidationResult with the valid result', async () => {
    const onValidationResult = vi.fn()
    render(<ConfigReviewStep slug="test-proj" onValidationResult={onValidationResult} />)
    await waitFor(() => expect(onValidationResult).toHaveBeenCalledWith(validResult))
  })

  it('shows error count when config has errors', async () => {
    api.validateDeployConfig.mockResolvedValue(invalidResult)
    render(<ConfigReviewStep slug="test-proj" />)
    await waitFor(() => expect(screen.getByText(/1 error.*found/i)).toBeInTheDocument())
  })

  it('shows specific error message content', async () => {
    api.validateDeployConfig.mockResolvedValue(invalidResult)
    render(<ConfigReviewStep slug="test-proj" />)
    await waitFor(() => expect(screen.getByText(/missing required field: persona/i)).toBeInTheDocument())
  })

  it('calls onValidationResult with invalid result when config has errors', async () => {
    api.validateDeployConfig.mockResolvedValue(invalidResult)
    const onValidationResult = vi.fn()
    render(<ConfigReviewStep slug="test-proj" onValidationResult={onValidationResult} />)
    await waitFor(() => expect(onValidationResult).toHaveBeenCalledWith(invalidResult))
  })

  it('fetches all 7 block configs on mount', async () => {
    render(<ConfigReviewStep slug="test-proj" />)
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledTimes(7))
  })
})
