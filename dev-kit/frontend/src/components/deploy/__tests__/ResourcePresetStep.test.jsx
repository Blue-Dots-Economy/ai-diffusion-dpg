import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    getResourcePresets: vi.fn().mockResolvedValue({
      low: { agent_core: { requests: { cpu: '250m', memory: '256Mi' } } },
      medium: { agent_core: { requests: { cpu: '500m', memory: '512Mi' } } },
      high: { agent_core: { requests: { cpu: '1', memory: '1Gi' } } },
    }),
    getDependencies: vi.fn().mockResolvedValue({}),
    applyResourcePreset: vi.fn().mockResolvedValue({ agent_core: { requests: { cpu: '250m', memory: '256Mi' } } }),
  },
}))

import ResourcePresetStep from '../ResourcePresetStep'
import { api } from '../../../api'

const defaultProps = {
  slug: 'test-proj',
  data: { preset: null, resources: {} },
  updateData: vi.fn(),
}

describe('ResourcePresetStep', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows loading state initially', () => {
    render(<ResourcePresetStep {...defaultProps} />)
    expect(screen.getByText(/loading presets/i)).toBeInTheDocument()
  })

  it('renders all tier options after load', async () => {
    render(<ResourcePresetStep {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Low')).toBeInTheDocument()
      expect(screen.getByText('Medium')).toBeInTheDocument()
      expect(screen.getByText('High')).toBeInTheDocument()
    })
  })

  it('calls applyResourcePreset with tier when a preset is clicked', async () => {
    render(<ResourcePresetStep {...defaultProps} />)
    await waitFor(() => screen.getByText('Low'))
    fireEvent.click(screen.getByText('Low'))
    await waitFor(() => expect(api.applyResourcePreset).toHaveBeenCalledWith('test-proj', 'low'))
  })

  it('calls updateData with preset tier and resources after selection', async () => {
    const updateData = vi.fn()
    render(<ResourcePresetStep {...defaultProps} updateData={updateData} />)
    await waitFor(() => screen.getByText('Low'))
    fireEvent.click(screen.getByText('Low'))
    await waitFor(() => {
      expect(updateData).toHaveBeenCalledWith('preset', 'low')
      expect(updateData).toHaveBeenCalledWith('resources', expect.any(Object))
    })
  })

  it('shows description text for each tier', async () => {
    render(<ResourcePresetStep {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText(/minimal resources/i)).toBeInTheDocument()
      expect(screen.getByText(/balanced resources/i)).toBeInTheDocument()
      expect(screen.getByText(/production-grade/i)).toBeInTheDocument()
    })
  })
})
