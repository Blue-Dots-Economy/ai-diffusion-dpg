import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    getDependencies: vi.fn().mockResolvedValue([
      { name: 'redis', content: 'image: redis:7' },
      { name: 'memgraph', content: 'image: memgraph:latest' },
    ]),
    updateDependency: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../../../hooks/useYamlEditor', () => ({
  default: () => ({
    startEdit: vi.fn(),
    cancelEdit: vi.fn(),
    getContent: vi.fn().mockReturnValue('image: redis:8'),
    setReadOnly: vi.fn(),
  }),
}))

import DependenciesStep from '../DependenciesStep'
import { api } from '../../../api'

const defaultProps = { slug: 'test-proj', data: {}, updateData: vi.fn() }

describe('DependenciesStep', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows loading state initially', () => {
    render(<DependenciesStep {...defaultProps} />)
    expect(screen.getByText(/loading dependencies/i)).toBeInTheDocument()
  })

  it('renders service names as accordion headers after load', async () => {
    render(<DependenciesStep {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText('Redis')).toBeInTheDocument()
      expect(screen.getByText('Memgraph')).toBeInTheDocument()
    })
  })

  it('shows Edit button after expanding an accordion card', async () => {
    render(<DependenciesStep {...defaultProps} />)
    await waitFor(() => screen.getByText('Redis'))
    // Click the accordion header to expand
    fireEvent.click(screen.getByText('Redis').closest('button'))
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
  })

  it('shows Save and Cancel after clicking Edit inside expanded card', async () => {
    render(<DependenciesStep {...defaultProps} />)
    await waitFor(() => screen.getByText('Redis'))
    fireEvent.click(screen.getByText('Redis').closest('button'))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument()
  })

  it('calls updateDependency with correct service name on Save', async () => {
    render(<DependenciesStep {...defaultProps} />)
    await waitFor(() => screen.getByText('Redis'))
    fireEvent.click(screen.getByText('Redis').closest('button'))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(api.updateDependency).toHaveBeenCalledWith('test-proj', 'redis', 'image: redis:8'))
  })
})
