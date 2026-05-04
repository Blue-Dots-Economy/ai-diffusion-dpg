import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import IngestDocumentsStep from '../IngestDocumentsStep'

// Mock the api module
vi.mock('../../../api', () => ({
  api: {
    getDevKitConfig: vi.fn().mockResolvedValue({
      upload: { max_files_per_upload: 5, max_file_size_mb: 30, supported_extensions: ['.pdf', '.txt'] },
      polling: { poll_interval_seconds: 5, poll_timeout_minutes: 15 },
    }),
    getProjectDocTypes: vi.fn().mockResolvedValue({ doc_types: ['general'], default_doc_type: 'general' }),
    getIngestJobs: vi.fn().mockResolvedValue([]),
    getDeployStatus: vi.fn().mockResolvedValue({ overall: 'idle', services: [] }),
    submitIngestBatch: vi.fn(),
    getJobStatus: vi.fn(),
  },
}))

const defaultProps = {
  slug: 'test-project',
  project: { azure_storage: null },
  onNext: vi.fn(),
  onBack: vi.fn(),
}

describe('IngestDocumentsStep', () => {
  it('renders Add File button after config loads', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => expect(screen.getByText(/\+ Add File/i)).toBeInTheDocument())
  })

  it('adds a row when Add File is clicked', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    expect(screen.getByRole('combobox')).toBeInTheDocument()
  })

  it('removes a row when × is clicked', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const removeBtn = screen.getByTitle(/remove/i)
    fireEvent.click(removeBtn)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('disables Add File when max_files_per_upload reached', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    for (let i = 0; i < 5; i++) {
      fireEvent.click(screen.getByText(/\+ Add File/i))
    }
    expect(screen.getByText(/\+ Add File/i).closest('button')).toBeDisabled()
  })

  it('does not show Azure modes when project has no azure_storage', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const modeSelect = screen.getByRole('combobox')
    const options = Array.from(modeSelect.options).map(o => o.value)
    expect(options).not.toContain('cloud_fetch_ingest')
    expect(options).not.toContain('cloud_upload_ingest')
  })

  it('shows Azure modes when project has azure_storage', async () => {
    const props = {
      ...defaultProps,
      project: { azure_storage: { needed: true, account_name: 'a', account_key: 'k', container_name: 'c' } },
    }
    render(<IngestDocumentsStep {...props} />)
    await waitFor(() => screen.getByText(/\+ Add File/i))
    fireEvent.click(screen.getByText(/\+ Add File/i))
    const allSelects = screen.getAllByRole('combobox')
    const allOptions = allSelects.flatMap(s => Array.from(s.options).map(o => o.value))
    expect(allOptions).toContain('cloud_fetch_ingest')
  })

  it('shows skip button', async () => {
    render(<IngestDocumentsStep {...defaultProps} />)
    await waitFor(() => screen.getByText(/skip/i))
    expect(screen.getByText(/skip/i)).toBeInTheDocument()
  })

  describe('has_knowledge_base prop', () => {
    it('renders alternate UI when has_knowledge_base is false', async () => {
      const props = { ...defaultProps, project: { ...defaultProps.project, has_knowledge_base: false } }
      render(<IngestDocumentsStep {...props} />)
      expect(screen.getByText(/No documents need to be ingested/i)).toBeInTheDocument()
    })

    it('does not render Add File button when has_knowledge_base is false', async () => {
      const props = { ...defaultProps, project: { ...defaultProps.project, has_knowledge_base: false } }
      render(<IngestDocumentsStep {...props} />)
      expect(screen.queryByText(/\+ Add File/i)).not.toBeInTheDocument()
    })

    it('renders normal ingest form when has_knowledge_base is true', async () => {
      const props = { ...defaultProps, project: { ...defaultProps.project, has_knowledge_base: true } }
      render(<IngestDocumentsStep {...props} />)
      await waitFor(() => expect(screen.getByText(/\+ Add File/i)).toBeInTheDocument())
    })

    it('renders normal ingest form when has_knowledge_base is absent (safe default)', async () => {
      render(<IngestDocumentsStep {...defaultProps} />)
      await waitFor(() => expect(screen.getByText(/\+ Add File/i)).toBeInTheDocument())
    })
  })
})
