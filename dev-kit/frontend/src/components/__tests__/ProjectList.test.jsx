import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import ProjectList from '../ProjectList'

// Mock the api module
vi.mock('../../api', () => ({
  api: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    listImportableProjects: vi.fn(),
    importProject: vi.fn(),
  },
}))

vi.mock('../ImportModal', () => ({
  default: vi.fn(() => null),
}))

import { api } from '../../api'
import ImportModal from '../ImportModal'

const sampleProjects = [
  { slug: 'farmer-friendly', name: 'Farmer Friendly', description: 'Crop disease diagnosis', current_phase: 'memory' },
  { slug: 'rural-jobs', name: 'Rural Jobs', description: '', current_phase: null },
]

describe('ProjectList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listProjects.mockResolvedValue(sampleProjects)
  })

  it('renders the hero heading', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    expect(screen.getByText('DPG Configuration Agent')).toBeInTheDocument()
  })

  it('renders existing projects after load', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('Farmer Friendly')).toBeInTheDocument()
      expect(screen.getByText('Rural Jobs')).toBeInTheDocument()
    })
  })

  it('shows "No projects yet" when list is empty', async () => {
    api.listProjects.mockResolvedValue([])
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/No projects yet/)).toBeInTheDocument()
    })
  })

  it('calls onOpen with slug when project card is clicked', async () => {
    const onOpen = vi.fn()
    render(<ProjectList onOpen={onOpen} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))
    fireEvent.click(screen.getByText('Farmer Friendly').closest('div[class*="cursor-pointer"]'))
    expect(onOpen).toHaveBeenCalledWith('farmer-friendly')
  })

  it('shows delete confirmation modal when Delete is clicked', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))

    // Hover to show the Delete button (opacity-0 group-hover:opacity-100)
    // In tests we can just find and click the button directly
    const deleteButtons = screen.getAllByTitle('Delete project')
    fireEvent.click(deleteButtons[0])

    expect(screen.getByText('Delete project?')).toBeInTheDocument()
    expect(screen.getByText(/"Farmer Friendly" will be permanently deleted/)).toBeInTheDocument()
  })

  it('shows warning bullets in delete confirmation', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))

    const deleteButtons = screen.getAllByTitle('Delete project')
    fireEvent.click(deleteButtons[0])

    expect(screen.getByText('All conversation history will be lost')).toBeInTheDocument()
    expect(screen.getByText('All generated YAML configs will be deleted')).toBeInTheDocument()
    expect(screen.getByText('This action cannot be undone')).toBeInTheDocument()
  })

  it('cancels delete and keeps project in list', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))

    const deleteButtons = screen.getAllByTitle('Delete project')
    fireEvent.click(deleteButtons[0])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByText('Delete project?')).toBeNull()
    expect(screen.getByText('Farmer Friendly')).toBeInTheDocument()
    expect(api.deleteProject).not.toHaveBeenCalled()
  })

  it('calls deleteProject and removes project after confirmation', async () => {
    api.deleteProject.mockResolvedValue({})
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))

    const deleteButtons = screen.getAllByTitle('Delete project')
    fireEvent.click(deleteButtons[0])
    fireEvent.click(screen.getByRole('button', { name: 'Delete project' }))

    await waitFor(() => {
      expect(api.deleteProject).toHaveBeenCalledWith('farmer-friendly')
      expect(screen.queryByText('Farmer Friendly')).toBeNull()
    })
  })

  it('creates a new project and calls onOpen', async () => {
    const newProject = { slug: 'new-proj', name: 'New Proj', description: '', current_phase: null }
    api.createProject.mockResolvedValue(newProject)
    const onOpen = vi.fn()
    render(<ProjectList onOpen={onOpen} />)

    fireEvent.change(screen.getByPlaceholderText(/Project name/), { target: { value: 'New Proj' } })
    fireEvent.click(screen.getByRole('button', { name: /Create & Start/ }))

    await waitFor(() => {
      expect(api.createProject).toHaveBeenCalledWith('New Proj', '')
      expect(onOpen).toHaveBeenCalledWith('new-proj')
    })
  })

  it('shows error message when project creation fails', async () => {
    api.createProject.mockRejectedValue(new Error('Name already taken'))
    render(<ProjectList onOpen={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/Project name/), { target: { value: 'Duplicate' } })
    fireEvent.click(screen.getByRole('button', { name: /Create & Start/ }))

    await waitFor(() => {
      expect(screen.getByText('Name already taken')).toBeInTheDocument()
    })
  })

  it('disables submit button when project name is empty', () => {
    render(<ProjectList onOpen={vi.fn()} />)
    const submitBtn = screen.getByRole('button', { name: /Create & Start/ })
    expect(submitBtn).toBeDisabled()
  })
})

describe('ProjectList — Import button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listProjects.mockResolvedValue(sampleProjects)
    ImportModal.mockImplementation(({ onClose }) => (
      <div data-testid="import-modal">
        <button onClick={onClose}>Close</button>
      </div>
    ))
  })

  it('renders the Import existing button', () => {
    render(<ProjectList onOpen={vi.fn()} />)
    expect(screen.getByRole('button', { name: /import existing/i })).toBeInTheDocument()
  })

  it('opens ImportModal when Import existing is clicked', () => {
    render(<ProjectList onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /import existing/i }))
    expect(screen.getByTestId('import-modal')).toBeInTheDocument()
  })

  it('closes ImportModal when onClose is called', () => {
    render(<ProjectList onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /import existing/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('import-modal')).toBeNull()
  })

  it('adds imported project to list and opens it via onImport callback', async () => {
    const importedProject = {
      slug: 'kkb', name: 'Kkb', imported: true,
      current_phase: 'review', phases_completed: [],
    }
    ImportModal.mockImplementation(({ onImport }) => (
      <div>
        <button onClick={() => onImport(importedProject)}>Trigger import</button>
      </div>
    ))
    const onOpen = vi.fn()
    render(<ProjectList onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: /import existing/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Trigger import' }))
    await waitFor(() => {
      expect(onOpen).toHaveBeenCalledWith('kkb')
      expect(screen.getByText('Kkb')).toBeInTheDocument()
    })
  })

  it('shows imported badge on imported projects in the list', async () => {
    const importedProject = {
      slug: 'kkb', name: 'Kkb', description: '', imported: true,
      current_phase: 'review', phases_completed: [],
    }
    api.listProjects.mockResolvedValue([...sampleProjects, importedProject])
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('Kkb')).toBeInTheDocument()
      expect(screen.getByText('Imported')).toBeInTheDocument()
    })
  })
})
