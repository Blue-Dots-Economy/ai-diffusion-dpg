import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    validateDeployConfig: vi.fn().mockResolvedValue({ valid: true, block_errors: {}, invariant_errors: [] }),
    getDeployPreview: vi.fn().mockResolvedValue({
      preview: {
        'docker-compose.yml': 'version: "3"\nservices:\n  agent_core:\n    image: dpg/agent-core:latest',
        agent_core: 'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: agent-core',
        redis: 'apiVersion: apps/v1\nkind: StatefulSet\nmetadata:\n  name: redis',
      },
    }),
  },
}))

vi.mock('../../../crypto.js', () => ({
  buildSecretsPayload: vi.fn().mockResolvedValue({ encrypted: 'mock-payload' }),
}))

vi.mock('../../shared/StatusBanner', () => ({
  default: ({ title, subtitle }) => <div>{title}{subtitle && <span>{subtitle}</span>}</div>,
}))

import PreviewStep from '../PreviewStep'
import { api } from '../../../api'

const defaultData = {
  target: 'docker',
  preset: 'low',
  resources: {},
  secrets: { anthropic_api_key: 'sk-ant-test' },
  kubeconfig: '',
}

describe('PreviewStep', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows loading state initially', () => {
    render(<PreviewStep slug="test-proj" data={defaultData} onValidationResult={vi.fn()} />)
    expect(screen.getByText(/generating deployment preview/i)).toBeInTheDocument()
  })

  it('calls onValidationResult with valid result', async () => {
    const onValidationResult = vi.fn()
    render(<PreviewStep slug="test-proj" data={defaultData} onValidationResult={onValidationResult} />)
    await waitFor(() =>
      expect(onValidationResult).toHaveBeenCalledWith(expect.objectContaining({ valid: true }))
    )
  })

  it('shows validation-passed message after load', async () => {
    render(<PreviewStep slug="test-proj" data={defaultData} onValidationResult={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByText(/config validation passed/i)).toBeInTheDocument()
    )
  })

  it('shows service entries from preview data (kubernetes)', async () => {
    const k8sData = { ...defaultData, target: 'kubernetes' }
    render(<PreviewStep slug="test-proj" data={k8sData} onValidationResult={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByText(/agent core/i)).toBeInTheDocument()
    )
  })

  it('calls onValidationResult with invalid result when config has errors', async () => {
    api.validateDeployConfig.mockResolvedValue({
      valid: false,
      block_errors: { agent_core: ['missing persona'] },
      invariant_errors: [],
    })
    const onValidationResult = vi.fn()
    render(<PreviewStep slug="test-proj" data={defaultData} onValidationResult={onValidationResult} />)
    await waitFor(() =>
      expect(onValidationResult).toHaveBeenCalledWith(expect.objectContaining({ valid: false }))
    )
  })

  it('shows error state when getDeployPreview fails', async () => {
    api.getDeployPreview.mockRejectedValueOnce(new Error('preview error'))
    render(<PreviewStep slug="test-proj" data={defaultData} onValidationResult={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/preview failed/i)).toBeInTheDocument())
  })
})
