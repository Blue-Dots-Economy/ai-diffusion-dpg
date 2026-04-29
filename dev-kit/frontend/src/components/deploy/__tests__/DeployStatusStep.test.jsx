import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    getDeployStatus: vi.fn(),
    executeDeploy: vi.fn().mockResolvedValue({}),
    restartService: vi.fn().mockResolvedValue({}),
    destroyProject: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../../../crypto.js', () => ({
  buildSecretsPayload: vi.fn().mockResolvedValue({ encrypted: 'mock' }),
}))

vi.mock('../../shared/StatusBanner', () => ({
  default: ({ title, subtitle, action }) => (
    <div>
      <span>{title}</span>
      {subtitle && <span>{subtitle}</span>}
      {action}
    </div>
  ),
}))

vi.mock('../../shared/StatusBadge', () => ({
  default: ({ status }) => <span data-testid="status-badge">{status}</span>,
}))

import DeployStatusStep from '../DeployStatusStep'
import { api } from '../../../api'

const defaultProps = {
  slug: 'test-proj',
  data: { target: 'docker', preset: 'low', resources: {}, secrets: { anthropic_api_key: 'sk-test' } },
  onSuccess: vi.fn(),
  onBack: vi.fn(),
  onDestroyedChange: vi.fn(),
  autoDeployOnMount: false,
}

describe('DeployStatusStep — idle, autoDeployOnMount=false', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
  })

  it('does NOT call executeDeploy when autoDeployOnMount is false', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await act(async () => {})
    expect(api.executeDeploy).not.toHaveBeenCalled()
  })

  it('renders the Deployment Status heading', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await act(async () => {})
    expect(screen.getByText('Deployment Status')).toBeInTheDocument()
  })
})

describe('DeployStatusStep — autoDeployOnMount=true', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({ overall: 'idle', services: [] })
    api.executeDeploy.mockResolvedValue({})
  })

  it('calls executeDeploy when autoDeployOnMount=true and status is idle', async () => {
    render(<DeployStatusStep {...defaultProps} autoDeployOnMount={true} />)
    await waitFor(() => expect(api.executeDeploy).toHaveBeenCalledWith('test-proj', expect.any(Object)))
  })
})

describe('DeployStatusStep — already complete on mount', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({
      overall: 'complete',
      services: [{ name: 'agent_core', status: 'healthy' }],
    })
  })

  it('does NOT call executeDeploy when status is already complete', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await act(async () => {})
    expect(api.executeDeploy).not.toHaveBeenCalled()
  })

  it('shows Ingest Knowledge Documents button when complete', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /ingest knowledge documents/i })).toBeInTheDocument()
    )
  })

  it('shows Destroy Stack button when complete', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /destroy stack/i })).toBeInTheDocument()
    )
  })
})

describe('DeployStatusStep — polling leads to complete', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows Ingest button when polling returns complete', async () => {
    // First call (on mount probe): deploying → starts polling
    // Second call (poll tick): complete → sets readyToIngest
    api.getDeployStatus
      .mockResolvedValueOnce({ overall: 'deploying', services: [] })
      .mockResolvedValueOnce({ overall: 'complete', services: [{ name: 'agent_core', status: 'healthy' }] })

    render(<DeployStatusStep {...defaultProps} />)

    // Let mount probe settle
    await act(async () => {})

    // Advance the 3s polling interval once
    await act(async () => { vi.advanceTimersByTime(3000) })
    await act(async () => {})

    expect(
      screen.getByRole('button', { name: /ingest knowledge documents/i })
    ).toBeInTheDocument()
  })
})

describe('DeployStatusStep — destroy flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDeployStatus.mockResolvedValue({
      overall: 'complete',
      services: [{ name: 'agent_core', status: 'healthy' }],
    })
  })

  it('shows destroy confirmation dialog when Destroy Stack is clicked', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await waitFor(() => screen.getByRole('button', { name: /destroy stack/i }))
    fireEvent.click(screen.getByRole('button', { name: /destroy stack/i }))
    expect(screen.getByText('Destroy Stack?')).toBeInTheDocument()
  })

  it('calls destroyProject when Confirm Destroy is clicked', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await waitFor(() => screen.getByRole('button', { name: /destroy stack/i }))
    fireEvent.click(screen.getByRole('button', { name: /destroy stack/i }))
    const confirmBtn = screen.getByRole('button', { name: /confirm destroy/i })
    fireEvent.click(confirmBtn)
    await waitFor(() => expect(api.destroyProject).toHaveBeenCalledWith('test-proj', false))
  })

  it('cancels the destroy dialog when Cancel is clicked', async () => {
    render(<DeployStatusStep {...defaultProps} />)
    await waitFor(() => screen.getByRole('button', { name: /destroy stack/i }))
    fireEvent.click(screen.getByRole('button', { name: /destroy stack/i }))
    expect(screen.getByText('Destroy Stack?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByText('Destroy Stack?')).not.toBeInTheDocument()
  })
})
