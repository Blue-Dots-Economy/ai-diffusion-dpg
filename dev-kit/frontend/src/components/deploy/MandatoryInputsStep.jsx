import React from 'react'

export default function MandatoryInputsStep({ data, updateData }) {
  const secrets = data.secrets || {}

  function update(field, value) {
    updateData('secrets', { ...secrets, [field]: value })
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-1">Deployment Inputs</h2>
      <p className="text-sm text-gray-400 mb-6">Provide required secrets and optional configuration for the deployment.</p>

      {/* Required */}
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

      {/* Optional */}
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

      <p className="text-xs text-gray-500 italic">
        Fields marked <span className="text-red-400">*</span> are required. All others have sensible defaults and can be left unchanged.
      </p>
    </div>
  )
}
