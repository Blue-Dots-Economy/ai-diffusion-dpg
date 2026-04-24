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
  createProject: (name, description) => request('POST', '/projects', { name, description }),
  getProject: (slug) => request('GET', `/projects/${slug}`),
  deleteProject: (slug) => request('DELETE', `/projects/${slug}`),

  // Chat
  chat: (slug, message) => request('POST', `/projects/${slug}/chat`, { message }),
  getHistory: (slug) => request('GET', `/projects/${slug}/history`),

  // Checkpoints
  getCheckpoints: (slug) => request('GET', `/projects/${slug}/checkpoints`),
  restoreCheckpoint: (slug, phase) => request('POST', `/projects/${slug}/checkpoints/${phase}/restore`),
  getCheckpointPreview: (slug, phase) => request('GET', `/projects/${slug}/checkpoints/${phase}/preview`),

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
  getDeployPreview: (slug, options) => request('POST', `/projects/${slug}/deploy/preview`, options),
  executeDeploy: (slug, options) => request('POST', `/projects/${slug}/deploy/execute`, options),
  getDeployStatus: (slug) => request('GET', `/projects/${slug}/deploy/status`),

  // Ingest endpoints
  getDevKitConfig: () =>
    request('GET', '/devkit-config'),

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
}
