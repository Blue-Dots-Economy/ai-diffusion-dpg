import React from 'react'

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
          <input
            type="password"
            value={secrets.anthropic_api_key || ''}
            onChange={e => update('anthropic_api_key', e.target.value)}
            placeholder="sk-ant-..."
            className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
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
                <input
                  type="password"
                  value={(secrets.tool_secrets || {})[env_var] || ''}
                  onChange={e => updateToolSecret(env_var, e.target.value)}
                  placeholder={`API key for ${tool_id}`}
                  className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
                />
                {description && (
                  <p className="text-xs text-gray-500 mt-1">Used by tool: {description}</p>
                )}
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
              <input
                type="password"
                value={secrets.redis_password || ''}
                onChange={e => update('redis_password', e.target.value)}
                placeholder="Leave empty for no auth"
                className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-300 mb-1">Memgraph Password</label>
              <input
                type="password"
                value={secrets.memgraph_password || ''}
                onChange={e => update('memgraph_password', e.target.value)}
                placeholder="Leave empty for no auth"
                className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-300 mb-1">Grafana Admin Password</label>
            <input
              type="password"
              value={secrets.grafana_admin_password || 'admin'}
              onChange={e => update('grafana_admin_password', e.target.value)}
              placeholder="admin"
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
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
                <input
                  id="azure_storage_key"
                  type="password"
                  placeholder="Paste your Azure storage account key"
                  value={secrets.azure_storage_key || ''}
                  onChange={e => update('azure_storage_key', e.target.value)}
                  className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
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
