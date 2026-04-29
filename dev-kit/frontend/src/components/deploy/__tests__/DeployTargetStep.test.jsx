import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    validateKubeconfig: vi.fn().mockResolvedValue({
      valid: true,
      cluster_info: { name: 'my-cluster', server: 'https://k8s.example.com' },
    }),
  },
}))

vi.mock('../../shared/StatusBanner', () => ({ default: ({ title, subtitle }) => <div>{title}{subtitle && <span>{subtitle}</span>}</div> }))

import DeployTargetStep from '../DeployTargetStep'
import { api } from '../../../api'

const defaultProps = {
  slug: 'test-proj',
  data: { target: null, kubeconfig: '' },
  updateData: vi.fn(),
}

describe('DeployTargetStep', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders Docker Compose and Kubernetes options', () => {
    render(<DeployTargetStep {...defaultProps} />)
    expect(screen.getByText('Docker Compose')).toBeInTheDocument()
    expect(screen.getByText('Kubernetes')).toBeInTheDocument()
  })

  it('calls updateData with "docker" when Docker Compose is selected', () => {
    const updateData = vi.fn()
    render(<DeployTargetStep {...defaultProps} updateData={updateData} />)
    fireEvent.click(screen.getByText('Docker Compose').closest('button'))
    expect(updateData).toHaveBeenCalledWith('target', 'docker')
  })

  it('shows success banner when docker target is selected', () => {
    render(<DeployTargetStep {...defaultProps} data={{ target: 'docker', kubeconfig: '' }} />)
    expect(screen.getByText('Docker Compose selected')).toBeInTheDocument()
  })

  it('calls updateData with "kubernetes" when Kubernetes is selected', () => {
    const updateData = vi.fn()
    render(<DeployTargetStep {...defaultProps} updateData={updateData} />)
    fireEvent.click(screen.getByText('Kubernetes').closest('button'))
    expect(updateData).toHaveBeenCalledWith('target', 'kubernetes')
  })

  it('shows kubeconfig textarea after switching to Paste mode', () => {
    render(<DeployTargetStep {...defaultProps} data={{ target: 'kubernetes', kubeconfig: '' }} />)
    fireEvent.click(screen.getByRole('button', { name: /paste/i }))
    expect(screen.getByPlaceholderText(/paste your kubeconfig yaml/i)).toBeInTheDocument()
  })

  it('calls validateKubeconfig and shows cluster info on valid kubeconfig', async () => {
    api.validateKubeconfig.mockResolvedValue({
      valid: true, cluster_name: 'my-cluster', server: 'https://k8s.example.com', current_context: 'default',
    })
    const updateData = vi.fn()
    render(<DeployTargetStep {...defaultProps} data={{ target: 'kubernetes', kubeconfig: '', clusterInfo: null }} updateData={updateData} />)
    fireEvent.click(screen.getByRole('button', { name: /paste/i }))
    const textarea = screen.getByPlaceholderText(/paste your kubeconfig yaml/i)
    fireEvent.change(textarea, { target: { value: 'apiVersion: v1' } })
    fireEvent.click(screen.getByRole('button', { name: /validate kubeconfig/i }))
    await waitFor(() => expect(updateData).toHaveBeenCalledWith('clusterInfo', expect.objectContaining({ cluster_name: 'my-cluster' })))
  })

  it('shows error when validateKubeconfig rejects', async () => {
    api.validateKubeconfig.mockRejectedValueOnce(new Error('invalid yaml'))
    render(<DeployTargetStep {...defaultProps} data={{ target: 'kubernetes', kubeconfig: '' }} />)
    fireEvent.click(screen.getByRole('button', { name: /paste/i }))
    const textarea = screen.getByPlaceholderText(/paste your kubeconfig yaml/i)
    fireEvent.change(textarea, { target: { value: 'bad yaml' } })
    fireEvent.click(screen.getByRole('button', { name: /validate kubeconfig/i }))
    await waitFor(() => expect(screen.getByText(/invalid yaml/i)).toBeInTheDocument())
  })
})
