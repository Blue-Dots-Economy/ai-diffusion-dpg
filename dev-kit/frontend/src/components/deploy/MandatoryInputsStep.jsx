import React from 'react'

/**
 * Uncontrolled password input that never reflects the secret value in the DOM.
 * When a value is already stored in parent state, shows a masked indicator
 * instead of pre-populating the field — preventing the value from appearing
 * in the browser DevTools Elements panel.
 */
function SecretInput({ existingValue, onUpdate, placeholder, id, className }) {
  const [isEditing, setIsEditing] = React.useState(!existingValue)

  const baseClass = className || 'w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors'

  if (!isEditing && existingValue) {
    return (
      <div className="flex items-center justify-between w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2">
        <span className="text-gray-300 text-sm tracking-widest select-none">••••••••</span>
        <button
          type="button"
          onClick={() => { setIsEditing(true); onUpdate('') }}
          className="text-xs text-blue-400 hover:text-blue-300 ml-2 shrink-0"
        >
          Change
        </button>
      </div>
    )
  }

  return (
    <input
      id={id}
      type="password"
      autoComplete="new-password"
      placeholder={placeholder}
      onChange={e => onUpdate(e.target.value)}
      className={baseClass}
    />
  )
}

function PhoneNumberInput({ value, onChange }) {
  const digits = (value || '').replace(/\D/g, '')
  // Initialise: assume first 2 chars are country code if value already stored
  const [cc, setCc] = React.useState(() => digits.length >= 2 ? digits.slice(0, 2) : '')
  const [local, setLocal] = React.useState(() => digits.length >= 2 ? digits.slice(2) : digits)

  function handleCc(raw) {
    const cleaned = raw.replace(/\D/g, '').slice(0, 4)
    setCc(cleaned)
    onChange(cleaned + local)
  }

  function handleLocal(raw) {
    const cleaned = raw.replace(/\D/g, '')
    setLocal(cleaned)
    onChange(cc + cleaned)
  }

  const inputClass = 'bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors'

  return (
    <div className="flex gap-2">
      <div className="flex items-center">
        <span className="text-gray-500 text-sm mr-1">+</span>
        <input
          type="text"
          value={cc}
          onChange={e => handleCc(e.target.value)}
          placeholder="91"
          maxLength={4}
          className={`${inputClass} w-16`}
        />
      </div>
      <input
        type="text"
        value={local}
        onChange={e => handleLocal(e.target.value)}
        placeholder="9240024444"
        className={`${inputClass} flex-1`}
      />
    </div>
  )
}

export default function MandatoryInputsStep({ data, updateData, onUpdate, project, onNext, onBack }) {
  const secrets = data.secrets || {}

  function update(field, value) {
    const updated = { ...secrets, [field]: value }
    if (onUpdate) {
      onUpdate({ secrets: updated })
    } else if (updateData) {
      updateData('secrets', updated)
    }
  }

  function updateChannelSecret(envVar, value) {
    const updated = {
      ...secrets,
      channel_secrets: { ...(secrets.channel_secrets || {}), [envVar]: value },
    }
    if (onUpdate) {
      onUpdate({ secrets: updated })
    } else if (updateData) {
      updateData('secrets', updated)
    }
  }

  function updateToolSecret(envVar, value) {
    const updated = {
      ...secrets,
      tool_secrets: { ...(secrets.tool_secrets || {}), [envVar]: value },
    }
    if (onUpdate) {
      onUpdate({ secrets: updated })
    } else if (updateData) {
      updateData('secrets', updated)
    }
  }

  const requiredSecrets = project?.required_secrets || []
  const azureNeeded = project?.azure_storage?.needed === true
  const channelSecrets = project?.channel_secrets || []
  const webSecrets = channelSecrets.filter(d => d.section === 'web')
  const voiceSecrets = channelSecrets.filter(d => d.section === 'voice')

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deployment Inputs</h2>
      <p className="text-sm text-gray-400 mb-6">
        Provide required secrets and optional configuration. All values are
        encrypted in your browser before being sent.
      </p>

      {/* Anthropic API Key — always required */}
      <div className="mb-8">
        <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
          Required
        </h3>
        <div className="border border-gray-700 rounded-xl p-4 bg-gray-900">
          <label className="block text-xs text-gray-300 mb-1">
            Anthropic API Key <span className="text-red-400">*</span>
          </label>
          <SecretInput
            existingValue={secrets.anthropic_api_key}
            onUpdate={v => update('anthropic_api_key', v)}
            placeholder="sk-ant-..."
          />
          <p className="text-xs text-gray-500 mt-1">Used by Agent Core for LLM calls.</p>
        </div>
      </div>

      {/* Tool API keys — one field per REST tool that has auth configured */}
      {requiredSecrets.length > 0 && (
        <div className="mb-8">
          <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
            Tool API Keys
          </h3>
          <div className="border border-gray-700 rounded-xl p-4 bg-gray-900 flex flex-col gap-4">
            {requiredSecrets.map(({ env_var, tool_id, description }) => (
              <div key={env_var}>
                <label className="block text-xs text-gray-300 mb-1">
                  {env_var} <span className="text-red-400">*</span>
                </label>
                <SecretInput
                  existingValue={(secrets.tool_secrets || {})[env_var]}
                  onUpdate={v => updateToolSecret(env_var, v)}
                  placeholder={`API key for ${tool_id}`}
                />
                {description && (
                  <p className="text-xs text-gray-500 mt-1">Used by tool: {description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Web Channel credentials — shown only when web channel is selected */}
      {webSecrets.length > 0 && (
        <div className="mb-8">
          <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
            Web Channel
          </h3>
          <div className="border border-gray-700 rounded-xl p-4 bg-gray-900 flex flex-col gap-4">
            {webSecrets.map(({ env_var, label, required, description }) => (
              <div key={env_var}>
                <label htmlFor={`channel_${env_var}`} className="block text-xs text-gray-300 mb-1">
                  {label} {required && <span className="text-red-400">*</span>}
                </label>
                <input
                  id={`channel_${env_var}`}
                  type="text"
                  value={(secrets.channel_secrets || {})[env_var] || ''}
                  onChange={e => updateChannelSecret(env_var, e.target.value)}
                  className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
                {description && <p className="text-xs text-gray-500 mt-1">{description}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Voice Channel credentials — shown only when voice channel is selected */}
      {voiceSecrets.length > 0 && (
        <div className="mb-8">
          <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-purple-400" />
            Voice Channel
          </h3>
          <div className="border border-gray-700 rounded-xl p-4 bg-gray-900 flex flex-col gap-4">
            {voiceSecrets.map(({ env_var, label, required, secret, description }) => (
              <div key={env_var}>
                <label htmlFor={`channel_${env_var}`} className="block text-xs text-gray-300 mb-1">
                  {label} {required && <span className="text-red-400">*</span>}
                </label>
                {env_var === 'VOBIZ_FROM_NUMBER' ? (
                  <PhoneNumberInput
                    value={(secrets.channel_secrets || {})[env_var] || ''}
                    onChange={v => updateChannelSecret(env_var, v)}
                  />
                ) : secret ? (
                  <SecretInput
                    id={`channel_${env_var}`}
                    existingValue={(secrets.channel_secrets || {})[env_var]}
                    onUpdate={v => updateChannelSecret(env_var, v)}
                    placeholder={label}
                  />
                ) : (
                  <input
                    id={`channel_${env_var}`}
                    type="text"
                    value={(secrets.channel_secrets || {})[env_var] || ''}
                    onChange={e => updateChannelSecret(env_var, e.target.value)}
                    className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
                  />
                )}
                {description && <p className="text-xs text-gray-500 mt-1">{description}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Optional infra secrets */}
      <div className="mb-6">
        <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-gray-500" />
          Optional
        </h3>
        <div className="border border-gray-700 rounded-xl p-4 bg-gray-900 flex flex-col gap-4">
          <div>
            <label className="block text-xs text-gray-300 mb-1">Namespace Prefix</label>
            <input
              type="text"
              value={secrets.namespace_prefix || 'dpg'}
              onChange={e => update('namespace_prefix', e.target.value)}
              placeholder="dpg"
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
            <p className="text-xs text-gray-500 mt-1">Prefix for Kubernetes namespaces (default: dpg).</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-gray-300 mb-1">Redis Password</label>
              <SecretInput
                existingValue={secrets.redis_password}
                onUpdate={v => update('redis_password', v)}
                placeholder="Leave empty for no auth"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-300 mb-1">Memgraph Password</label>
              <SecretInput
                existingValue={secrets.memgraph_password}
                onUpdate={v => update('memgraph_password', v)}
                placeholder="Leave empty for no auth"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-300 mb-1">Grafana Admin Password</label>
            <SecretInput
              existingValue={secrets.grafana_admin_password}
              onUpdate={v => update('grafana_admin_password', v)}
              placeholder="admin"
            />
          </div>
        </div>
      </div>

      {/* Dev-Kit Callback URL + KE Internal URL */}
      <div className="mb-4">
        <div className="border border-gray-700 rounded-xl p-4 bg-gray-900 flex flex-col gap-4">
          <div>
            <label htmlFor="devkit_callback_url" className="block text-xs text-gray-300 mb-1">Dev-Kit Callback URL</label>
            <input
              id="devkit_callback_url"
              type="url"
              placeholder="https://devkit.your-vm.example.com"
              value={secrets.devkit_callback_url || ''}
              onChange={e => update('devkit_callback_url', e.target.value)}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
            <p className="text-xs text-gray-500 mt-1">
              URL of this Dev-Kit, reachable from inside the cluster.
            </p>
          </div>
          <div>
            <label htmlFor="ke_internal_url" className="block text-xs text-gray-300 mb-1">KE Internal Service URL</label>
            <input
              id="ke_internal_url"
              type="url"
              placeholder="http://knowledge-engine.dpg.svc.cluster.local:8001"
              value={secrets.ke_internal_url || ''}
              onChange={e => update('ke_internal_url', e.target.value)}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
            <p className="text-xs text-gray-500 mt-1">
              Internal Kubernetes service URL for KE.
            </p>
          </div>
        </div>
      </div>

      {/* Azure Blob Storage — shown only if domain declared Azure in the knowledge phase */}
      {azureNeeded && (
        <div className="mb-4">
          <fieldset className="border border-gray-700 rounded-xl p-4 bg-gray-900">
            <legend className="text-sm font-medium text-gray-300 px-1">Azure Blob Storage</legend>
            <div className="flex flex-col gap-4 mt-2">
              <div>
                <label htmlFor="azure_storage_account" className="block text-xs text-gray-300 mb-1">Azure Account Name</label>
                <input
                  id="azure_storage_account"
                  type="text"
                  value={secrets.azure_storage_account || ''}
                  onChange={e => update('azure_storage_account', e.target.value)}
                  className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
              </div>
              <div>
                <label htmlFor="azure_storage_key" className="block text-xs text-gray-300 mb-1">Azure Account Key</label>
                <SecretInput
                  id="azure_storage_key"
                  existingValue={secrets.azure_storage_key}
                  onUpdate={v => update('azure_storage_key', v)}
                  placeholder="Paste your Azure storage account key"
                />
              </div>
              <div>
                <label htmlFor="azure_container_name" className="block text-xs text-gray-300 mb-1">Azure Container Name</label>
                <input
                  id="azure_container_name"
                  type="text"
                  value={secrets.azure_container_name || ''}
                  onChange={e => update('azure_container_name', e.target.value)}
                  placeholder="e.g. kb-documents"
                  className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
              </div>
            </div>
          </fieldset>
        </div>
      )}

      <p className="text-xs text-gray-500 italic">
        Fields marked <span className="text-red-400">*</span> are required.
        All secret values are encrypted in your browser before transmission.
      </p>
    </div>
  )
}
