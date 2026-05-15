const BASE = '/api'

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = err.detail
    throw new Error(typeof detail === 'string' ? detail : (detail?.error || res.statusText))
  }
  return res.json()
}

export const api = {
  // Projects
  listProjects: () => request('GET', '/projects'),
  createProject: (name, description, intakeFields = {}) =>
    request('POST', '/projects', { name, description, ...intakeFields }),
  getProject: (slug) => request('GET', `/projects/${slug}`),
  deleteProject: (slug) => request('DELETE', `/projects/${slug}`),

  // Chat
  chat: (slug, message) => request('POST', `/projects/${slug}/chat`, { message }),
  getHistory: (slug) => request('GET', `/projects/${slug}/history`),

  // Configs
  getConfigs: (slug) => request('GET', `/projects/${slug}/configs`),
  getConfig: (slug, block) => request('GET', `/projects/${slug}/configs/${block}`),
  updateConfig: (slug, block, content) => request('PUT', `/projects/${slug}/configs/${block}`, { content }),
  validateConfigs: (slug) => request('POST', `/projects/${slug}/configs/validate`),
  exportConfigs: (slug) => `${BASE}/projects/${slug}/configs/export`, // returns URL string for native browser download

  // Workflow graph
  getGraph: (slug) => request('GET', `/projects/${slug}/workflow/graph`),

  // Schema
  getSchemaDescriptions: (block) => request('GET', `/schemas/${block}`),

  // Deploy
  getDpgValues: (slug) => request('GET', `/projects/${slug}/deploy/dpg-values`),
  updateDpgValue: (slug, block, content) => request('PUT', `/projects/${slug}/deploy/dpg-values/${block}`, { content }),
  getDependencies: (slug) => request('GET', `/projects/${slug}/deploy/dependencies`),
  updateDependency: (slug, service, content) => request('PUT', `/projects/${slug}/deploy/dependencies/${service}`, { content }),
  getResourcePresets: (slug) => request('GET', `/projects/${slug}/deploy/resource-presets`),
  applyResourcePreset: (slug, tier) => request('POST', `/projects/${slug}/deploy/resource-presets/${tier}`),
  validateKubeconfig: (slug, content) => request('POST', `/projects/${slug}/deploy/validate-kubeconfig`, { content }),
  validateDeployConfig: (slug) => request('POST', `/projects/${slug}/deploy/validate`),
  getDeployPreview: (slug, options) => request('POST', `/projects/${slug}/deploy/preview`, options),
  executeDeploy: (slug, options) => request('POST', `/projects/${slug}/deploy/execute`, options),
  getDeployStatus: (slug) => request('GET', `/projects/${slug}/deploy/status`),
  reloadConfigs: (slug) => request('POST', `/projects/${slug}/configs/reload`),

  // Deterministic-wizard endpoints (Phase 11)
  getDeployFields: (slug) => request('GET', `/projects/${slug}/deploy-fields`),
  saveDeploySettings: (slug, overrides) => request('POST', `/projects/${slug}/deploy-settings`, { overrides }),
  getFieldStatus: (slug) => request('GET', `/projects/${slug}/field-status`),

  // Ingest endpoints
  getDevKitConfig: () =>
    request('GET', '/devkit-config'),

  // Open enum values from dev_kit/schemas/enums_config.yaml. Used by the
  // project creation form (language pickers) and downstream forms that
  // present provider / model / voice choices.
  getEnums: () =>
    request('GET', '/enums'),

  getProjectDocTypes: (slug) =>
    request('GET', `/projects/${slug}/ingest/doc-types`),

  submitIngestBatch: (slug, formData) =>
    // formData is a FormData object containing metadata + file parts
    // Must use raw fetch (not request()) so the browser sets the multipart boundary
    fetch(`/api/ingest/submit`, {
      method: 'POST',
      body: formData,
      // Do NOT set Content-Type — browser sets it with correct boundary for multipart
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      return res.json()
    }),

  getJobStatus: (jobId) =>
    request('GET', `/ingest/job/${jobId}`),

  getIngestJobs: (limit = 100) =>
    request('GET', `/ingest/jobs?limit=${limit}`),

  restartService: (slug, service) =>
    request('POST', `/projects/${slug}/deploy/services/${service}/restart`),

  destroyProject: (slug, removeVolumes = false) =>
    request('POST', `/projects/${slug}/destroy`, { remove_volumes: removeVolumes }),
}
