import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import ProjectList from '../ProjectList'

vi.mock('../../api', () => ({
  api: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    listImportableProjects: vi.fn(),
    importProject: vi.fn(),
  },
}))

import { api } from '../../api'

const sampleProjects = [
  { slug: 'farmer-friendly', name: 'Farmer Friendly', description: 'Crop disease diagnosis', current_phase: 'memory' },
  { slug: 'rural-jobs', name: 'Rural Jobs', description: '', current_phase: null },
]

const sampleImportable = [
  { slug: 'kkb', detected_blocks: ['agent_core', 'knowledge_engine'], validation_errors: {} },
  { slug: 'hospital', detected_blocks: ['agent_core'], validation_errors: { agent_core: ['missing field'] } },
]

describe('ProjectList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listProjects.mockResolvedValue(sampleProjects)
    api.listImportableProjects.mockResolvedValue([])
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

  it('shows "No projects yet" when list is empty and no importable folders', async () => {
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
    expect(screen.getByRole('button', { name: /Create & Start/ })).toBeDisabled()
  })

  it('shows imported badge on imported projects', async () => {
    api.listProjects.mockResolvedValue([
      ...sampleProjects,
      { slug: 'kkb', name: 'Kkb', description: '', imported: true, current_phase: 'review', phases_completed: [] },
    ])
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('Kkb')).toBeInTheDocument()
      expect(screen.getByText('Imported')).toBeInTheDocument()
    })
  })
})

describe('ProjectList — Import existing section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listProjects.mockResolvedValue(sampleProjects)
    api.listImportableProjects.mockResolvedValue(sampleImportable)
  })

  it('renders Import Existing Config Folder section heading', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('Import Existing Config Folder')).toBeInTheDocument()
    })
  })

  it('lists importable folders with slug and block count', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('kkb')).toBeInTheDocument()
      expect(screen.getByText('hospital')).toBeInTheDocument()
      expect(screen.getByText(/2 blocks/i)).toBeInTheDocument()
      expect(screen.getByText(/1 block/i)).toBeInTheDocument()
    })
  })

  it('shows validation warning badge for folder with errors', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/Validation issues/i)).toBeInTheDocument()
    })
  })

  it('shows Import button for each importable folder', async () => {
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => {
      const importBtns = screen.getAllByRole('button', { name: /import →/i })
      expect(importBtns).toHaveLength(2)
    })
  })

  it('calls importProject and onOpen when Import is clicked', async () => {
    const imported = { slug: 'kkb', name: 'Kkb', imported: true, current_phase: 'review', phases_completed: [] }
    api.importProject.mockResolvedValue(imported)
    const onOpen = vi.fn()
    render(<ProjectList onOpen={onOpen} />)
    await waitFor(() => screen.getByText('kkb'))
    fireEvent.click(screen.getAllByRole('button', { name: /import →/i })[0])
    await waitFor(() => {
      expect(api.importProject).toHaveBeenCalledWith('kkb')
      expect(onOpen).toHaveBeenCalledWith('kkb')
    })
  })

  it('removes folder from importable list after successful import', async () => {
    api.importProject.mockResolvedValue({
      slug: 'kkb', name: 'Kkb', imported: true, current_phase: 'review', phases_completed: [],
    })
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('kkb'))
    fireEvent.click(screen.getAllByRole('button', { name: /import →/i })[0])
    await waitFor(() => {
      expect(screen.queryByText('kkb')).toBeNull()
    })
  })

  it('shows error when import fails', async () => {
    api.importProject.mockRejectedValue(new Error('Already managed'))
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('kkb'))
    fireEvent.click(screen.getAllByRole('button', { name: /import →/i })[0])
    await waitFor(() => {
      expect(screen.getByText('Already managed')).toBeInTheDocument()
    })
  })

  it('hides import section when no importable folders exist', async () => {
    api.listImportableProjects.mockResolvedValue([])
    render(<ProjectList onOpen={vi.fn()} />)
    await waitFor(() => screen.getByText('Farmer Friendly'))
    expect(screen.queryByText('Import Existing Config Folder')).toBeNull()
  })
})
