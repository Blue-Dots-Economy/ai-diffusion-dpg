// dev-kit/frontend/src/constants.js

export const BLOCKS = [
  'agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer',
  'action_gateway', 'reach_layer', 'observability_layer',
]

export const BLOCK_LABELS = {
  agent_core: 'Agent Core',
  knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer',
  trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway',
  reach_layer: 'Reach Layer',
  observability_layer: 'Observability Layer',
}

export const BLOCK_DESC = {
  agent_core: 'Orchestrator & LLM caller',
  knowledge_engine: 'RAG & prompt assembly',
  memory_layer: 'Session & user state',
  trust_layer: 'Safety & content gate',
  action_gateway: 'External API connector',
  reach_layer: 'Channel UI & delivery',
  observability_layer: 'Telemetry & logging',
}

export const STATUS_PILL = {
  complete:  'bg-green-900 text-green-300 border-green-700',
  healthy:   'bg-green-900 text-green-300 border-green-700',
  draft:     'bg-yellow-900 text-yellow-300 border-yellow-700',
  running:   'bg-yellow-900 text-yellow-300 border-yellow-700',
  starting:  'bg-blue-900 text-blue-300 border-blue-700',
  pending:   'bg-gray-800 text-gray-400 border-gray-700',
  queued:    'bg-gray-800 text-gray-400 border-gray-700',
  failed:    'bg-red-900 text-red-300 border-red-700',
  stale:     'bg-red-900 text-red-300 border-red-700',
}

export const STATUS_COLORS = {
  complete: 'border-green-700 bg-green-950/40',
  draft: 'border-yellow-700 bg-yellow-950/30',
  pending: 'border-gray-700 bg-gray-900',
  stale: 'border-red-700 bg-red-950/30',
}

export const STATUS_DOT = {
  complete: 'bg-green-400',
  draft: 'bg-yellow-400',
  pending: 'bg-gray-600',
  stale: 'bg-red-400',
}
