import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock the api module
vi.mock('../../api', () => ({
  api: {
    getHistory: vi.fn().mockResolvedValue([]),
    getCheckpoints: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getConfigs: vi.fn().mockResolvedValue([]),
    chat: vi.fn().mockResolvedValue({ reply: 'ok', phase: 'tools', graph: null, checkpoint_created: null }),
  },
}))

// ThemeContext mock
vi.mock('../../ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggle: vi.fn() }),
}))

// Sub-component mocks
vi.mock('../PhaseBar', () => ({ default: () => null }))
vi.mock('../FlowGraph', () => ({ default: () => null }))
vi.mock('../YamlPanel', () => ({ default: () => null }))
vi.mock('../DiffModal', () => ({ default: () => null }))

import Chat from '../Chat'
import { api } from '../../api'

describe('Chat — file attachment', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders a file attachment button', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByTitle(/attach spec file/i)).toBeInTheDocument()
    })
  })

  it('sends a chat message containing file content when a file is attached', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    expect(fileInput).toBeTruthy()

    const specContent = 'openapi: "3.0.0"\npaths:\n  /test:\n    get:\n      summary: Test'
    const file = new File([specContent], 'api.yaml', { type: 'text/yaml' })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    await waitFor(() => {
      expect(api.chat).toHaveBeenCalledWith(
        'test-project',
        expect.stringContaining('[Attached: api.yaml]')
      )
      expect(api.chat).toHaveBeenCalledWith(
        'test-project',
        expect.stringContaining(specContent)
      )
    })
  })

  it('resets the file input value after selection so the same file can be re-selected', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    const specContent = 'openapi: "3.0.0"\npaths: {}'
    const file = new File([specContent], 'api.yaml', { type: 'text/yaml' })

    // Simulate the browser setting a value before the change event
    Object.defineProperty(fileInput, 'value', { value: 'C:\\fakepath\\api.yaml', writable: true, configurable: true })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    // attachFile should have reset value to '' immediately
    expect(fileInput.value).toBe('')

    // Wait for the async api.chat call triggered by FileReader to complete
    await waitFor(() => expect(api.chat).toHaveBeenCalled())
  })

  it('shows an error message when api.chat fails during file attachment', async () => {
    api.chat.mockRejectedValueOnce(new Error('Network error'))

    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    const specContent = 'openapi: "3.0.0"\npaths: {}'
    const file = new File([specContent], 'api.yaml', { type: 'text/yaml' })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    await waitFor(() => {
      expect(screen.getByText(/Error: Network error/i)).toBeInTheDocument()
    })
  })

  it('does not send a message if file exceeds 500 KB', async () => {
    render(<Chat slug="test-project" onDashboard={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => screen.getByTitle(/attach spec file/i))

    const fileInput = document.querySelector('input[type="file"]')
    const bigContent = 'x'.repeat(600 * 1024)  // 600 KB
    const file = new File([bigContent], 'big.yaml', { type: 'text/yaml' })

    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file], configurable: true })
      fireEvent.change(fileInput)
    })

    // No message should be sent
    expect(api.chat).not.toHaveBeenCalled()
  })
})
