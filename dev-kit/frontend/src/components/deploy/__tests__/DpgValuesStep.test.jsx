import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'

vi.mock('../../../api', () => ({
  api: {
    getDpgValues: vi.fn().mockResolvedValue([
      { block: 'agent_core', content: 'agent_core_yaml: true' },
      { block: 'knowledge_engine', content: 'ke_yaml: true' },
      { block: 'trust_layer', content: 'trust_yaml: true' },
      { block: 'memory_layer', content: 'memory_yaml: true' },
      { block: 'observability_layer', content: 'obs_yaml: true' },
      { block: 'action_gateway', content: 'ag_yaml: true' },
      { block: 'reach_layer', content: 'reach_yaml: true' },
    ]),
    updateDpgValue: vi.fn().mockResolvedValue({}),
  },
}))

vi.mock('../../../hooks/useYamlEditor', () => ({
  default: () => ({
    startEdit: vi.fn(),
    cancelEdit: vi.fn(),
    getContent: vi.fn().mockReturnValue('edited content'),
    setReadOnly: vi.fn(),
  }),
}))

import DpgValuesStep from '../DpgValuesStep'
import { api } from '../../../api'

describe('DpgValuesStep', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows loading state initially', () => {
    render(<DpgValuesStep slug="test-proj" />)
    expect(screen.getByText(/loading dpg values/i)).toBeInTheDocument()
  })

  it('shows Agent Core tab heading after loading', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => expect(screen.getByText(/agent core.*framework defaults/i)).toBeInTheDocument())
  })

  it('renders tab bar with block names', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => {
      expect(screen.getAllByText(/agent core/i).length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText(/knowledge engine/i).length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText(/reach layer/i).length).toBeGreaterThanOrEqual(1)
    })
  })

  it('shows Edit button per tab', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument())
  })

  it('shows Save and Cancel after clicking Edit', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument()
  })

  it('returns to Edit button after Cancel', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
  })

  it('calls updateDpgValue with correct args on Save', async () => {
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() =>
      expect(api.updateDpgValue).toHaveBeenCalledWith('test-proj', 'agent_core', 'edited content')
    )
  })

  it('shows the validation error banner above the editor when save fails', async () => {
    api.updateDpgValue.mockRejectedValueOnce({
      response: { data: { detail: 'agent.primary_model: not a valid model id' } },
    })
    render(<DpgValuesStep slug="test-proj" />)
    await waitFor(() => screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/save failed/i)
    expect(alert).toHaveTextContent(/not a valid model id/i)
  })
})
