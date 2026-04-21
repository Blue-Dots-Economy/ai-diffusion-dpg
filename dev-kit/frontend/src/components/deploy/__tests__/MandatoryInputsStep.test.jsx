import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import MandatoryInputsStep from '../MandatoryInputsStep'

const defaultProps = {
  data: {
    secrets: {
      anthropic_api_key: '',
      memgraph_password: '',
      redis_password: '',
      grafana_admin_password: 'admin',
      devkit_callback_url: '',
      ke_internal_url: '',
    },
  },
  project: { slug: 'test', azure_storage: null },
  onUpdate: vi.fn(),
  onNext: vi.fn(),
  onBack: vi.fn(),
}

describe('MandatoryInputsStep — new fields', () => {
  it('renders Dev-Kit Callback URL field', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.getByLabelText(/dev-kit callback url/i)).toBeInTheDocument()
  })

  it('renders KE Internal Service URL field', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.getByLabelText(/ke internal service url/i)).toBeInTheDocument()
  })

  it('does NOT show Azure fields when azure_storage is null', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.queryByLabelText(/azure account name/i)).not.toBeInTheDocument()
  })

  it('shows Azure fields when azure_storage.needed is true', () => {
    const props = {
      ...defaultProps,
      project: {
        slug: 'test',
        azure_storage: {
          needed: true,
          account_name: 'myaccount',
          account_key: '***KEY',
          container_name: 'kb-docs',
        },
      },
      data: {
        secrets: {
          ...defaultProps.data.secrets,
          azure_account_name: '',
          azure_account_key: '',
          azure_container_name: '',
        },
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.getByLabelText(/azure account name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/azure account key/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/azure container name/i)).toBeInTheDocument()
  })

  it('does NOT show Azure fields when azure_storage.needed is not true', () => {
    const props = {
      ...defaultProps,
      project: {
        slug: 'test',
        azure_storage: { needed: false, account_name: 'myaccount', container_name: 'kb-docs' },
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.queryByLabelText(/azure account name/i)).not.toBeInTheDocument()
  })

  it('does not pre-fill Azure account name from project.azure_storage (user fills it)', () => {
    const props = {
      ...defaultProps,
      project: {
        slug: 'test',
        azure_storage: { needed: true, account_name: 'prefilledacct', account_key: '***XYZ', container_name: 'docs' },
      },
      data: {
        secrets: { ...defaultProps.data.secrets, azure_account_name: '' },
      },
    }
    render(<MandatoryInputsStep {...props} />)
    // Azure section is shown but account name field is empty (no pre-fill from project)
    expect(screen.getByLabelText(/azure account name/i)).toHaveValue('')
  })
})
