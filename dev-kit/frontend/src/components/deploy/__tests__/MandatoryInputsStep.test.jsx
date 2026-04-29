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

describe('MandatoryInputsStep — channel credentials', () => {
  it('does not show Web or Voice sections when channel_secrets is absent', () => {
    render(<MandatoryInputsStep {...defaultProps} />)
    expect(screen.queryByText(/web channel/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/voice channel/i)).not.toBeInTheDocument()
  })

  it('does not show channel sections when channel_secrets is empty array', () => {
    const props = {
      ...defaultProps,
      project: { ...defaultProps.project, channel_secrets: [] },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.queryByText(/web channel/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/voice channel/i)).not.toBeInTheDocument()
  })

  it('shows Web Channel section and Google Client ID field when section=web present', () => {
    const props = {
      ...defaultProps,
      project: {
        ...defaultProps.project,
        channel_secrets: [
          {
            env_var: 'GOOGLE_CLIENT_ID',
            label: 'Google Client ID',
            required: true,
            section: 'web',
            secret: false,
            description: 'Google OAuth Client ID.',
          },
        ],
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.getByText(/web channel/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/google client id/i)).toBeInTheDocument()
  })

  it('shows Voice Channel section and all five voice fields when section=voice present', () => {
    const props = {
      ...defaultProps,
      project: {
        ...defaultProps.project,
        channel_secrets: [
          { env_var: 'VOBIZ_AUTH_ID', label: 'Vobiz Auth ID', required: true, section: 'voice', secret: true, description: 'Vobiz Auth ID.' },
          { env_var: 'VOBIZ_AUTH_TOKEN', label: 'Vobiz Auth Token', required: true, section: 'voice', secret: true, description: 'Vobiz Auth Token.' },
          { env_var: 'RAYA_API_KEY', label: 'Raya API Key', required: true, section: 'voice', secret: true, description: 'Raya API Key.' },
          { env_var: 'PUBLIC_URL', label: 'Voice Public URL', required: true, section: 'voice', secret: false, description: 'HTTPS URL.' },
          { env_var: 'VOBIZ_FROM_NUMBER', label: 'Vobiz From Number', required: true, section: 'voice', secret: false, description: 'Caller ID.' },
        ],
      },
    }
    render(<MandatoryInputsStep {...props} />)
    expect(screen.getByText(/voice channel/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/vobiz auth id/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/vobiz auth token/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/raya api key/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/voice public url/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/vobiz from number/i)).toBeInTheDocument()
  })

  it('calls onUpdate with correct channel_secrets shape when plain-text field changes', () => {
    const onUpdate = vi.fn()
    const props = {
      ...defaultProps,
      onUpdate,
      project: {
        ...defaultProps.project,
        channel_secrets: [
          {
            env_var: 'GOOGLE_CLIENT_ID',
            label: 'Google Client ID',
            required: true,
            section: 'web',
            secret: false,
            description: 'Google OAuth Client ID.',
          },
        ],
      },
    }
    render(<MandatoryInputsStep {...props} />)
    fireEvent.change(screen.getByLabelText(/google client id/i), { target: { value: 'my-client-123' } })
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        secrets: expect.objectContaining({
          channel_secrets: expect.objectContaining({ GOOGLE_CLIENT_ID: 'my-client-123' }),
        }),
      }),
    )
  })
})
