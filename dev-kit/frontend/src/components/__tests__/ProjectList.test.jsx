import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import ProjectList from '../ProjectList'

// Mock the api module
vi.mock('../../api', () => ({
  api: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    getEnums: vi.fn(),
  },
}))

import { api } from '../../api'

const sampleProjects = [
  { slug: 'farmer-friendly', name: 'Farmer Friendly', description: 'Crop disease diagnosis', current_phase: 'memory' },
  { slug: 'rural-jobs', name: 'Rural Jobs', description: '', current_phase: null },
]

const sampleEnums = {
  languages: ['english', 'hindi', 'hinglish', 'tamil', 'telugu', 'kannada', 'marathi', 'bengali'],
  providers: ['anthropic', 'openai'],
  anthropic_models: [],
  openai_models: [],
  embedding_providers: [],
  raya_voices: [],
}

describe('ProjectList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listProjects.mockResolvedValue(sampleProjects)
    api.getEnums.mockResolvedValue(sampleEnums)
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
      expect(api.createProject).toHaveBeenCalledWith(
        'New Proj',
        '',
        expect.objectContaining({
          project_name: 'New Proj',
          selected_channels: ['web'],
          default_language: 'english',
          supported_languages: ['english'],
        }),
      )
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
