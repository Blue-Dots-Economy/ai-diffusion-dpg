import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import ImportModal from '../ImportModal'

vi.mock('../../api', () => ({
  api: {
    listImportableProjects: vi.fn(),
    importProject: vi.fn(),
  },
}))

import { api } from '../../api'

const sampleImportable = [
  {
    slug: 'kkb',
    detected_blocks: ['agent_core', 'knowledge_engine', 'trust_layer'],
    validation_errors: {},
  },
  {
    slug: 'farmer-friendly',
    detected_blocks: ['agent_core'],
    validation_errors: { agent_core: ['Missing required field: agent.primary_model'] },
  },
]

describe('ImportModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listImportableProjects.mockResolvedValue(sampleImportable)
  })

  it('renders the modal heading', async () => {
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText(/Import Existing Config Folder/i)).toBeInTheDocument()
  })

  it('lists importable folders after load', async () => {
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('kkb')).toBeInTheDocument()
      expect(screen.getByText('farmer-friendly')).toBeInTheDocument()
    })
  })

  it('shows detected block count for each folder', async () => {
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/3 blocks/i)).toBeInTheDocument()
      expect(screen.getByText(/1 block/i)).toBeInTheDocument()
    })
  })

  it('shows validation warning badge for folder with errors', async () => {
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/Validation issues/i)).toBeInTheDocument()
    })
  })

  it('calls importProject and onImport on folder click', async () => {
    const imported = { slug: 'kkb', name: 'Kkb', imported: true, current_phase: 'review', phases_completed: [] }
    api.importProject.mockResolvedValue(imported)
    const onImport = vi.fn()
    render(<ImportModal onImport={onImport} onClose={vi.fn()} />)
    await waitFor(() => screen.getByText('kkb'))
    fireEvent.click(screen.getByText('kkb').closest('[data-testid="importable-row"]'))
    await waitFor(() => {
      expect(api.importProject).toHaveBeenCalledWith('kkb')
      expect(onImport).toHaveBeenCalledWith(imported)
    })
  })

  it('shows error message when import fails', async () => {
    api.importProject.mockRejectedValue(new Error('Already managed'))
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => screen.getByText('kkb'))
    fireEvent.click(screen.getByText('kkb').closest('[data-testid="importable-row"]'))
    await waitFor(() => {
      expect(screen.getByText('Already managed')).toBeInTheDocument()
    })
  })

  it('shows empty state when no importable folders exist', async () => {
    api.listImportableProjects.mockResolvedValue([])
    render(<ImportModal onImport={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/No importable folders found/i)).toBeInTheDocument()
    })
  })

  it('calls onClose when Cancel is clicked', async () => {
    const onClose = vi.fn()
    render(<ImportModal onImport={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
