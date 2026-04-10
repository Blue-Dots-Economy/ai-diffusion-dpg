# Deploy Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add end-to-end deployment capability to the dev-kit UI — a 7-step wizard that reviews DPG values, configures dependencies, selects resource tiers, collects secrets, chooses a deploy target (Docker/K8s), previews rendered templates, and deploys all 14 services with live status tracking.

**Architecture:** New `deployer/` backend module with Helm and Docker Compose drivers. 7 existing DPG Helm charts refactored to config-agnostic (domain/dpg YAML injected via `--set-file`). 7 new infra Helm charts created. Frontend adds a `deploy` view with 8 new React components in `components/deploy/`, built on shared components extracted from existing code.

**Tech Stack:** FastAPI (Python), React 18, Tailwind CSS, CodeMirror 6, Helm 3 CLI, Docker Compose CLI, Vite.

**Spec:** `docs/superpowers/specs/2026-04-08-deploy-wizard-design.md`

---

## File Map

### Group A: Shared Frontend Extraction

| File | Action | Responsibility |
|---|---|---|
| `dev-kit/frontend/src/constants.js` | **Create** | BLOCKS, BLOCK_LABELS, BLOCK_DESC, STATUS_PILL, STATUS_COLORS, STATUS_DOT |
| `dev-kit/frontend/src/components/shared/StatusBadge.jsx` | **Create** | Reusable status pill component |
| `dev-kit/frontend/src/components/shared/TabBar.jsx` | **Create** | Reusable tabbed navigation with status dots |
| `dev-kit/frontend/src/components/shared/Modal.jsx` | **Create** | Base modal wrapper (overlay + card + close) |
| `dev-kit/frontend/src/components/shared/StatusBanner.jsx` | **Create** | Colored banner (success/warning/info/error) |
| `dev-kit/frontend/src/hooks/useYamlEditor.js` | **Create** | CodeMirror YAML setup, edit/save/cancel, theme |
| `dev-kit/frontend/src/components/Dashboard.jsx` | Modify | Import from constants.js and shared components |
| `dev-kit/frontend/src/components/YamlPanel.jsx` | Modify | Import from constants.js, shared, and hook |
| `dev-kit/frontend/src/components/DiffModal.jsx` | Modify | Import from constants.js and shared components |
| `dev-kit/frontend/src/components/ConfigEditor.jsx` | Modify | Import from constants.js, shared, and hook |

### Group B: Helm Chart Restructuring

| File | Action | Responsibility |
|---|---|---|
| `automation/helm/dpg/agent-core/` | **Move + Modify** | Config-agnostic: dpgConfig/domainConfig as string passthrough |
| `automation/helm/dpg/knowledge-engine/` | **Move + Modify** | Same pattern + KE-specific init container + PVC |
| `automation/helm/dpg/memory-layer/` | **Move + Modify** | Same pattern, Memgraph templates removed |
| `automation/helm/dpg/trust-layer/` | **Move + Modify** | Config-agnostic |
| `automation/helm/dpg/action-gateway/` | **Move + Modify** | Config-agnostic |
| `automation/helm/dpg/reach-layer/` | **Move + Modify** | Config-agnostic |
| `automation/helm/dpg/observability-layer/` | **Move + Modify** | Config-agnostic |
| `automation/helm/infra/redis/` | **Create** | Redis chart |
| `automation/helm/infra/memgraph/` | **Create** | Memgraph chart (extracted from memory-layer) |
| `automation/helm/infra/otel-collector/` | **Create** | OTel Collector chart |
| `automation/helm/infra/jaeger/` | **Create** | Jaeger chart |
| `automation/helm/infra/prometheus/` | **Create** | Prometheus chart |
| `automation/helm/infra/loki/` | **Create** | Loki chart |
| `automation/helm/infra/grafana/` | **Create** | Grafana chart |

### Group C: Backend Deployer Module

| File | Action | Responsibility |
|---|---|---|
| `dev-kit/dev_kit/agent/deployer/__init__.py` | **Create** | Package init |
| `dev-kit/dev_kit/agent/deployer/presets.py` | **Create** | Resource preset definitions + application |
| `dev-kit/dev_kit/agent/deployer/dependencies.py` | **Create** | Infra service default configs |
| `dev-kit/dev_kit/agent/deployer/kubeconfig.py` | **Create** | Kubeconfig validation + cluster info |
| `dev-kit/dev_kit/agent/deployer/helm.py` | **Create** | Helm template/install/status |
| `dev-kit/dev_kit/agent/deployer/compose.py` | **Create** | Docker Compose generation/up/status |
| `dev-kit/dev_kit/agent/app.py` | Modify | Add 10 deploy endpoints |
| `dev-kit/tests/test_deployer_presets.py` | **Create** | Preset tests |
| `dev-kit/tests/test_deployer_dependencies.py` | **Create** | Dependency config tests |
| `dev-kit/tests/test_deployer_kubeconfig.py` | **Create** | Kubeconfig validation tests |
| `dev-kit/tests/test_deployer_helm.py` | **Create** | Helm command generation tests |
| `dev-kit/tests/test_deployer_compose.py` | **Create** | Compose generation tests |
| `dev-kit/tests/test_app_deploy_routes.py` | **Create** | Deploy endpoint tests |

### Group D: Frontend Deploy Wizard

| File | Action | Responsibility |
|---|---|---|
| `dev-kit/frontend/src/components/deploy/DeployWizard.jsx` | **Create** | Wizard container: step state, nav, data accumulation |
| `dev-kit/frontend/src/components/deploy/StepIndicator.jsx` | **Create** | Top progress bar with step dots |
| `dev-kit/frontend/src/components/deploy/DpgValuesStep.jsx` | **Create** | Step 1: Tabbed YAML editors for DPG defaults |
| `dev-kit/frontend/src/components/deploy/DependenciesStep.jsx` | **Create** | Step 2: Collapsible infra service cards |
| `dev-kit/frontend/src/components/deploy/ResourcePresetStep.jsx` | **Create** | Step 3: Low/Medium/High cards + summary table |
| `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx` | **Create** | Step 4: API key + optional secrets form |
| `dev-kit/frontend/src/components/deploy/DeployTargetStep.jsx` | **Create** | Step 5: Docker/K8s + kubeconfig upload/paste |
| `dev-kit/frontend/src/components/deploy/PreviewStep.jsx` | **Create** | Step 6: Read-only helm template / compose preview |
| `dev-kit/frontend/src/components/deploy/DeployStatusStep.jsx` | **Create** | Step 7: Live status board with polling |
| `dev-kit/frontend/src/App.jsx` | Modify | Add deploy view + openDeploy nav function |
| `dev-kit/frontend/src/components/Dashboard.jsx` | Modify | Add Deploy button to HealthBanner |
| `dev-kit/frontend/src/api.js` | Modify | Add 10 deploy API methods |

---

## Group A: Shared Frontend Extraction

### Task 1: Create constants.js

**Files:**
- Create: `dev-kit/frontend/src/constants.js`

- [ ] **Step 1: Create the constants file**

```javascript
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
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
  running: 'bg-green-900 text-green-300 border-green-700',
  failed: 'bg-red-900 text-red-300 border-red-700',
  starting: 'bg-blue-900 text-blue-300 border-blue-700',
  queued: 'bg-gray-800 text-gray-400 border-gray-700',
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
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/constants.js
git commit -m "refactor: extract shared constants (BLOCKS, STATUS_PILL, BLOCK_LABELS)"
```

---

### Task 2: Create shared StatusBadge component

**Files:**
- Create: `dev-kit/frontend/src/components/shared/StatusBadge.jsx`

- [ ] **Step 1: Create StatusBadge**

```jsx
// dev-kit/frontend/src/components/shared/StatusBadge.jsx
import React from 'react'
import { STATUS_PILL } from '../../constants'

export default function StatusBadge({ status }) {
  const cls = STATUS_PILL[status] || STATUS_PILL.pending
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full border shrink-0 ${cls}`}>
      {status}
    </span>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/components/shared/StatusBadge.jsx
git commit -m "refactor: add shared StatusBadge component"
```

---

### Task 3: Create shared TabBar component

**Files:**
- Create: `dev-kit/frontend/src/components/shared/TabBar.jsx`

- [ ] **Step 1: Create TabBar**

```jsx
// dev-kit/frontend/src/components/shared/TabBar.jsx
import React from 'react'
import { STATUS_DOT } from '../../constants'

/**
 * Reusable tabbed navigation with optional status dots.
 *
 * Props:
 *   tabs: [{ key, label, status?, indicator? }]
 *   activeKey: string
 *   onSelect: (key) => void
 */
export default function TabBar({ tabs, activeKey, onSelect }) {
  return (
    <div className="flex overflow-x-auto border-b border-gray-800 bg-gray-900 shrink-0">
      {tabs.map(tab => {
        const isActive = tab.key === activeKey
        return (
          <button
            key={tab.key}
            onClick={() => onSelect(tab.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors shrink-0 ${
              isActive
                ? 'border-blue-500 text-white bg-gray-800'
                : 'border-transparent text-gray-400 hover:text-gray-200'
            }`}
          >
            {tab.status && (
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[tab.status] || 'bg-gray-600'}`} />
            )}
            {tab.label}
            {tab.indicator !== undefined && (
              <span className="ml-1 text-[10px] opacity-70">{tab.indicator}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/components/shared/TabBar.jsx
git commit -m "refactor: add shared TabBar component"
```

---

### Task 4: Create shared Modal wrapper

**Files:**
- Create: `dev-kit/frontend/src/components/shared/Modal.jsx`

- [ ] **Step 1: Create Modal**

```jsx
// dev-kit/frontend/src/components/shared/Modal.jsx
import React from 'react'

/**
 * Base modal overlay wrapper.
 *
 * Props:
 *   onClose: () => void
 *   size: 'sm' | 'md' | 'lg' | 'xl' (default 'md')
 *   children: content
 */
const SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export default function Modal({ onClose, size = 'md', children }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full mx-4 ${SIZES[size]}`}
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/components/shared/Modal.jsx
git commit -m "refactor: add shared Modal wrapper component"
```

---

### Task 5: Create shared StatusBanner component

**Files:**
- Create: `dev-kit/frontend/src/components/shared/StatusBanner.jsx`

- [ ] **Step 1: Create StatusBanner**

```jsx
// dev-kit/frontend/src/components/shared/StatusBanner.jsx
import React from 'react'

const VARIANTS = {
  success: 'border-green-700 bg-green-950/40',
  warning: 'border-yellow-700 bg-yellow-950/30',
  error: 'border-red-700 bg-red-950/30',
  info: 'border-gray-700 bg-gray-900',
}

const ICONS = {
  success: '✅',
  warning: '⚠️',
  error: '❌',
  info: '🔧',
}

/**
 * Colored info banner for status display.
 *
 * Props:
 *   variant: 'success' | 'warning' | 'error' | 'info'
 *   title: string
 *   subtitle: string (optional)
 *   action: ReactNode (optional, right side)
 */
export default function StatusBanner({ variant = 'info', title, subtitle, action }) {
  return (
    <div className={`rounded-xl border px-4 py-3 mb-6 flex items-center justify-between ${VARIANTS[variant]}`}>
      <div className="flex items-center gap-3">
        <span className="text-xl">{ICONS[variant]}</span>
        <div>
          <p className="text-sm font-medium">{title}</p>
          {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/components/shared/StatusBanner.jsx
git commit -m "refactor: add shared StatusBanner component"
```

---

### Task 6: Create useYamlEditor hook

**Files:**
- Create: `dev-kit/frontend/src/hooks/useYamlEditor.js`

- [ ] **Step 1: Create the hook**

```javascript
// dev-kit/frontend/src/hooks/useYamlEditor.js
import { useRef, useEffect, useCallback } from 'react'
import { EditorView, keymap } from '@codemirror/view'
import { EditorState, Compartment } from '@codemirror/state'
import { basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { defaultKeymap } from '@codemirror/commands'

/**
 * Custom hook for CodeMirror YAML editor with edit/save/cancel.
 *
 * Args:
 *   containerRef: React ref for the DOM container
 *   content: string — current YAML content
 *   options: { readOnly, dark }
 *
 * Returns:
 *   { editorView, editing, startEdit, cancelEdit, getContent }
 */
export default function useYamlEditor(containerRef, content, options = {}) {
  const { readOnly = true, dark = true } = options
  const viewRef = useRef(null)
  const editableComp = useRef(new Compartment())
  const originalRef = useRef('')

  useEffect(() => {
    if (!containerRef.current) return
    // Destroy previous editor if present
    if (viewRef.current) {
      viewRef.current.destroy()
      viewRef.current = null
    }

    const extensions = [
      basicSetup,
      yaml(),
      keymap.of(defaultKeymap),
      editableComp.current.of(EditorView.editable.of(!readOnly)),
    ]
    if (dark) extensions.push(oneDark)

    const state = EditorState.create({
      doc: content || '',
      extensions,
    })

    viewRef.current = new EditorView({
      state,
      parent: containerRef.current,
    })

    return () => {
      if (viewRef.current) {
        viewRef.current.destroy()
        viewRef.current = null
      }
    }
  }, [content, dark]) // eslint-disable-line react-hooks/exhaustive-deps

  const startEdit = useCallback(() => {
    if (!viewRef.current) return
    originalRef.current = viewRef.current.state.doc.toString()
    viewRef.current.dispatch({
      effects: editableComp.current.reconfigure(EditorView.editable.of(true)),
    })
  }, [])

  const cancelEdit = useCallback(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: {
        from: 0,
        to: viewRef.current.state.doc.length,
        insert: originalRef.current,
      },
      effects: editableComp.current.reconfigure(EditorView.editable.of(false)),
    })
  }, [])

  const getContent = useCallback(() => {
    if (!viewRef.current) return ''
    return viewRef.current.state.doc.toString()
  }, [])

  const setReadOnly = useCallback((ro) => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: editableComp.current.reconfigure(EditorView.editable.of(!ro)),
    })
  }, [])

  return { viewRef, startEdit, cancelEdit, getContent, setReadOnly }
}
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/hooks/useYamlEditor.js
git commit -m "refactor: add useYamlEditor hook for shared CodeMirror setup"
```

---

### Task 7: Refactor existing components to use shared code

**Files:**
- Modify: `dev-kit/frontend/src/components/Dashboard.jsx`
- Modify: `dev-kit/frontend/src/components/YamlPanel.jsx`
- Modify: `dev-kit/frontend/src/components/DiffModal.jsx`
- Modify: `dev-kit/frontend/src/components/ConfigEditor.jsx`

- [ ] **Step 1: Refactor Dashboard.jsx**

Remove local `BLOCKS`, `BLOCK_LABELS`, `BLOCK_DESC`, `STATUS_COLORS`, `STATUS_PILL` constants. Add imports:

```javascript
import { BLOCKS, BLOCK_LABELS, BLOCK_DESC, STATUS_COLORS, STATUS_PILL } from '../constants'
import StatusBadge from './shared/StatusBadge'
import StatusBanner from './shared/StatusBanner'
```

Replace the inline `<span>` status pill (line 144) with `<StatusBadge status={status} />`.

Replace `HealthBanner` with `StatusBanner`:
```jsx
<StatusBanner
  variant={allComplete ? 'success' : hasStale ? 'error' : 'info'}
  title={allComplete ? 'All configs complete — ready to deploy' :
         hasStale ? 'Some configs have validation errors' :
         'Configuration in progress'}
  subtitle={`${counts.complete}/${total} complete${counts.draft > 0 ? ` · ${counts.draft} draft` : ''}${counts.stale > 0 ? ` · ${counts.stale} stale` : ''}${counts.pending > 0 ? ` · ${counts.pending} pending` : ''}`}
  action={hasStale ? (
    <span className="text-xs text-red-400 bg-red-950 border border-red-800 px-2 py-1 rounded-lg">
      Fix stale configs
    </span>
  ) : null}
/>
```

- [ ] **Step 2: Refactor YamlPanel.jsx**

Remove local `BLOCK_LABELS`, `STATUS_PILL`, `STATUS_DOT` constants. Add:

```javascript
import { BLOCKS, BLOCK_LABELS, STATUS_PILL, STATUS_DOT } from '../constants'
import StatusBadge from './shared/StatusBadge'
import TabBar from './shared/TabBar'
```

Replace the block tab rendering (lines 218-242) with `<TabBar>` component. Replace inline status pills with `<StatusBadge>`.

- [ ] **Step 3: Refactor DiffModal.jsx**

Remove local `BLOCK_LABELS`, `STATUS_PILL`. Add:

```javascript
import { BLOCK_LABELS, STATUS_PILL } from '../constants'
import StatusBadge from './shared/StatusBadge'
import Modal from './shared/Modal'
```

Wrap content in `<Modal size="xl">` instead of the inline overlay div. Replace status pills with `<StatusBadge>`.

- [ ] **Step 4: Refactor ConfigEditor.jsx**

Remove local `STATUS_PILL`. Add:

```javascript
import { STATUS_PILL } from '../constants'
import StatusBadge from './shared/StatusBadge'
```

Replace inline status pill with `<StatusBadge>`.

- [ ] **Step 5: Verify the frontend builds and existing functionality works**

Run:
```bash
cd dev-kit/frontend && npm run build
```
Expected: Build succeeds with no errors.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/frontend/src/components/Dashboard.jsx dev-kit/frontend/src/components/YamlPanel.jsx dev-kit/frontend/src/components/DiffModal.jsx dev-kit/frontend/src/components/ConfigEditor.jsx
git commit -m "refactor: migrate existing components to shared StatusBadge, TabBar, Modal, constants"
```

---

## Group B: Helm Chart Restructuring

### Task 8: Move existing DPG charts to dpg/ subdirectory

**Files:**
- Move: `automation/helm/{agent-core,knowledge-engine,memory-layer,trust-layer,action-gateway,reach-layer,observability-layer}` → `automation/helm/dpg/`

- [ ] **Step 1: Create directory and move charts**

```bash
mkdir -p automation/helm/dpg
git mv automation/helm/agent-core automation/helm/dpg/
git mv automation/helm/knowledge-engine automation/helm/dpg/
git mv automation/helm/memory-layer automation/helm/dpg/
git mv automation/helm/trust-layer automation/helm/dpg/
git mv automation/helm/action-gateway automation/helm/dpg/
git mv automation/helm/reach-layer automation/helm/dpg/
git mv automation/helm/observability-layer automation/helm/dpg/
```

- [ ] **Step 2: Update README.md references if any**

Check `automation/helm/README.md` and update chart paths from `./agent-core` to `./dpg/agent-core` etc.

- [ ] **Step 3: Commit**

```bash
git add automation/helm/
git commit -m "chore: move DPG helm charts to automation/helm/dpg/"
```

---

### Task 9: Refactor DPG charts to config-agnostic pattern

**Files:**
- Modify: All 7 `automation/helm/dpg/*/values.yaml`
- Modify: All `automation/helm/dpg/*/templates/configmap-dpg.yaml`
- Modify: All `automation/helm/dpg/*/templates/configmap-domain.yaml`

- [ ] **Step 1: Refactor agent-core values.yaml**

Replace the full file with config-agnostic version:

```yaml
# automation/helm/dpg/agent-core/values.yaml
# Config-agnostic: dpgConfig and domainConfig injected at deploy time via --set-file

anthropicApiKey: ""
configFolder: /app/config
replicaCount: 1

image:
  repository: sanketikahub/dpg-agent-core
  tag: "0.1.0"
  pullPolicy: IfNotPresent

service:
  port: 8000

resources:
  requests:
    cpu: 100m
    memory: 512Mi
  limits:
    cpu: 500m
    memory: 1Gi

healthCheck:
  readiness:
    initialDelaySeconds: 15
    periodSeconds: 10
  liveness:
    initialDelaySeconds: 30
    periodSeconds: 20

# Injected at deploy time — do not set here
dpgConfig: ""
domainConfig: ""
```

- [ ] **Step 2: Refactor agent-core configmap-dpg.yaml template**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-dpg-config
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
data:
  dpg.yaml: |
{{ .Values.dpgConfig | indent 4 }}
```

- [ ] **Step 3: Refactor agent-core configmap-domain.yaml template**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-domain-config
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
data:
  domain.yaml: |
{{ .Values.domainConfig | indent 4 }}
```

- [ ] **Step 4: Update agent-core deployment.yaml to use service.port**

Replace hardcoded `8000` in containerPort, readinessProbe, livenessProbe with `{{ .Values.service.port }}`. Use health check values from `{{ .Values.healthCheck }}`.

- [ ] **Step 5: Repeat for remaining 6 DPG charts**

Apply the same pattern to knowledge-engine, memory-layer, trust-layer, action-gateway, reach-layer, observability-layer. Each gets:
- Stripped `values.yaml` with `dpgConfig: ""` and `domainConfig: ""`
- Image: `sanketikahub/dpg-{layer}:0.1.0`
- Passthrough configmap templates

**Per-chart specifics:**
- **knowledge-engine:** Keep `storage.chromadb.size`, `storage.chromadb.storageClass`, init container, PVC template. Port: 8001.
- **memory-layer:** Remove memgraph-deployment.yaml, memgraph-service.yaml, memgraph-pvc.yaml. Add env vars for `REDIS_URL`, `MEMGRAPH_URI`, `MEMGRAPH_USER`, `MEMGRAPH_PASSWORD` from values. Port: 8002.
- **trust-layer:** Port: 8003.
- **action-gateway:** Port: 9999.
- **reach-layer:** Port: 8005.
- **observability-layer:** Port: 8004.

- [ ] **Step 6: Verify all charts are valid**

```bash
for chart in automation/helm/dpg/*/; do
  echo "=== $(basename $chart) ==="
  helm template test "$chart" --set dpgConfig="test: true" --set domainConfig="test: true" 2>&1 | head -5
done
```

Expected: Each chart renders without template errors (agent-core will warn about anthropicApiKey — that's expected).

- [ ] **Step 7: Commit**

```bash
git add automation/helm/dpg/
git commit -m "refactor: make all 7 DPG helm charts config-agnostic (--set-file injection)"
```

---

### Task 10: Create infrastructure Helm charts

**Files:**
- Create: `automation/helm/infra/redis/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/memgraph/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/otel-collector/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/jaeger/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/prometheus/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/loki/` (Chart.yaml, values.yaml, templates/)
- Create: `automation/helm/infra/grafana/` (Chart.yaml, values.yaml, templates/)

- [ ] **Step 1: Create Redis chart**

`automation/helm/infra/redis/Chart.yaml`:
```yaml
apiVersion: v2
name: redis
description: Redis — session/turn state cache for Memory Layer
type: application
version: 0.1.0
appVersion: "7"
```

`automation/helm/infra/redis/values.yaml`:
```yaml
image:
  repository: redis
  tag: "7-alpine"
  pullPolicy: IfNotPresent

service:
  port: 6379

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 100m
    memory: 128Mi

password: ""
```

`automation/helm/infra/redis/templates/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: redis
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          {{- if .Values.password }}
          command: ["redis-server", "--requirepass", {{ .Values.password | quote }}]
          {{- end }}
          ports:
            - containerPort: {{ .Values.service.port }}
          resources:
            requests:
              cpu: {{ .Values.resources.requests.cpu }}
              memory: {{ .Values.resources.requests.memory }}
            limits:
              cpu: {{ .Values.resources.limits.cpu }}
              memory: {{ .Values.resources.limits.memory }}
          readinessProbe:
            tcpSocket:
              port: {{ .Values.service.port }}
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: {{ .Values.service.port }}
            initialDelaySeconds: 10
            periodSeconds: 20
```

`automation/helm/infra/redis/templates/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
spec:
  type: ClusterIP
  selector:
    app: {{ .Release.Name }}
  ports:
    - name: redis
      port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
```

- [ ] **Step 2: Create Memgraph chart**

`automation/helm/infra/memgraph/Chart.yaml`:
```yaml
apiVersion: v2
name: memgraph
description: Memgraph — graph database for user profiles and context
type: application
version: 0.1.0
appVersion: "latest"
```

`automation/helm/infra/memgraph/values.yaml`:
```yaml
image:
  repository: memgraph/memgraph
  tag: "latest"
  pullPolicy: IfNotPresent

service:
  boltPort: 7687
  httpPort: 7444

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 1Gi

persistence:
  size: 1Gi
  storageClass: ""

password: ""

flags:
  walEnabled: true
  snapshotIntervalSec: 300
```

`automation/helm/infra/memgraph/templates/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: memgraph
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args:
            - "--storage-wal-enabled={{ .Values.flags.walEnabled }}"
            - "--storage-snapshot-interval-sec={{ .Values.flags.snapshotIntervalSec }}"
          ports:
            - containerPort: {{ .Values.service.boltPort }}
            - containerPort: {{ .Values.service.httpPort }}
          volumeMounts:
            - name: memgraph-data
              mountPath: /var/lib/memgraph
          resources:
            requests:
              cpu: {{ .Values.resources.requests.cpu }}
              memory: {{ .Values.resources.requests.memory }}
            limits:
              cpu: {{ .Values.resources.limits.cpu }}
              memory: {{ .Values.resources.limits.memory }}
          readinessProbe:
            httpGet:
              path: /
              port: {{ .Values.service.httpPort }}
            initialDelaySeconds: 15
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: {{ .Values.service.httpPort }}
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: memgraph-data
          persistentVolumeClaim:
            claimName: {{ .Release.Name }}-data
```

`automation/helm/infra/memgraph/templates/pvc.yaml`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .Release.Name }}-data
  namespace: {{ .Release.Namespace }}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: {{ .Values.persistence.size }}
  {{- if .Values.persistence.storageClass }}
  storageClassName: {{ .Values.persistence.storageClass }}
  {{- end }}
```

`automation/helm/infra/memgraph/templates/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}
spec:
  type: ClusterIP
  selector:
    app: {{ .Release.Name }}
  ports:
    - name: bolt
      port: {{ .Values.service.boltPort }}
      targetPort: {{ .Values.service.boltPort }}
    - name: http
      port: {{ .Values.service.httpPort }}
      targetPort: {{ .Values.service.httpPort }}
```

- [ ] **Step 3: Create OTel Collector chart**

`automation/helm/infra/otel-collector/Chart.yaml`:
```yaml
apiVersion: v2
name: otel-collector
description: OpenTelemetry Collector — receives traces/metrics/logs, exports to backends
type: application
version: 0.1.0
appVersion: "0.96.0"
```

`automation/helm/infra/otel-collector/values.yaml`:
```yaml
image:
  repository: otel/opentelemetry-collector-contrib
  tag: "0.96.0"
  pullPolicy: IfNotPresent

service:
  grpcPort: 4317
  httpPort: 4318
  metricsPort: 8889

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi

# Full otelcol config YAML — injected at deploy time via --set-file
config: ""
```

Templates: deployment (mounts config as ConfigMap at `/etc/otelcol/config.yaml`, command `["--config=/etc/otelcol/config.yaml"]`), service (3 ports), configmap.

- [ ] **Step 4: Create Jaeger chart**

`automation/helm/infra/jaeger/values.yaml`:
```yaml
image:
  repository: jaegertracing/all-in-one
  tag: "1.55"
  pullPolicy: IfNotPresent

service:
  uiPort: 16686
  grpcPort: 14250

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 512Mi
```

Templates: deployment, service (2 ports).

- [ ] **Step 5: Create Prometheus chart**

`automation/helm/infra/prometheus/values.yaml`:
```yaml
image:
  repository: prom/prometheus
  tag: "v2.50.1"
  pullPolicy: IfNotPresent

service:
  port: 9090

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 512Mi

# Prometheus config YAML — injected at deploy time via --set-file
config: ""
```

Templates: deployment (mounts config as ConfigMap at `/etc/prometheus/prometheus.yml`), service, configmap.

- [ ] **Step 6: Create Loki chart**

`automation/helm/infra/loki/values.yaml`:
```yaml
image:
  repository: grafana/loki
  tag: "2.9.4"
  pullPolicy: IfNotPresent

service:
  port: 3100

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 512Mi
```

Templates: deployment, service.

- [ ] **Step 7: Create Grafana chart**

`automation/helm/infra/grafana/values.yaml`:
```yaml
image:
  repository: grafana/grafana
  tag: "10.3.3"
  pullPolicy: IfNotPresent

service:
  port: 3000

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 256Mi

adminPassword: "admin"
```

Templates: deployment (sets `GF_SECURITY_ADMIN_PASSWORD` env var), service.

- [ ] **Step 8: Validate all infra charts**

```bash
for chart in automation/helm/infra/*/; do
  echo "=== $(basename $chart) ==="
  helm template test "$chart" 2>&1 | head -5
done
```

Expected: All charts render without errors.

- [ ] **Step 9: Commit**

```bash
git add automation/helm/infra/
git commit -m "feat: add 7 infrastructure Helm charts (redis, memgraph, otel, jaeger, prometheus, loki, grafana)"
```

---

## Group C: Backend Deployer Module

### Task 11: Create presets.py

**Files:**
- Create: `dev-kit/dev_kit/agent/deployer/__init__.py`
- Create: `dev-kit/dev_kit/agent/deployer/presets.py`
- Test: `dev-kit/tests/test_deployer_presets.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/tests/test_deployer_presets.py
import pytest
from dev_kit.agent.deployer.presets import PRESETS, apply_preset


def test_presets_have_three_tiers():
    assert set(PRESETS.keys()) == {"low", "medium", "high"}


def test_each_preset_has_seven_dpg_blocks():
    for tier, blocks in PRESETS.items():
        assert len(blocks) == 7, f"{tier} should have 7 blocks"
        assert "agent_core" in blocks
        assert "knowledge_engine" in blocks


def test_agent_core_gets_more_resources_than_standard():
    for tier in PRESETS:
        ac = PRESETS[tier]["agent_core"]
        tl = PRESETS[tier]["trust_layer"]
        assert int(ac["limits"]["cpu"].rstrip("m")) >= int(tl["limits"]["cpu"].rstrip("m"))


def test_apply_preset_returns_resources_per_block():
    result = apply_preset("medium")
    assert "agent_core" in result
    assert "requests" in result["agent_core"]
    assert "limits" in result["agent_core"]


def test_apply_preset_invalid_tier():
    with pytest.raises(ValueError, match="Unknown preset"):
        apply_preset("ultra")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_deployer_presets.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement presets.py**

```python
# dev-kit/dev_kit/agent/deployer/__init__.py
"""Deployer module — Helm and Docker Compose deployment drivers."""
```

```python
# dev-kit/dev_kit/agent/deployer/presets.py
"""Resource preset definitions for DPG layer deployments."""

PRESETS = {
    "low": {
        "agent_core":          {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "knowledge_engine":    {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "memory_layer":        {"requests": {"cpu": "50m",  "memory": "256Mi"}, "limits": {"cpu": "250m",  "memory": "512Mi"}},
        "trust_layer":         {"requests": {"cpu": "50m",  "memory": "256Mi"}, "limits": {"cpu": "250m",  "memory": "512Mi"}},
        "action_gateway":      {"requests": {"cpu": "50m",  "memory": "256Mi"}, "limits": {"cpu": "250m",  "memory": "512Mi"}},
        "reach_layer":         {"requests": {"cpu": "50m",  "memory": "256Mi"}, "limits": {"cpu": "250m",  "memory": "512Mi"}},
        "observability_layer": {"requests": {"cpu": "50m",  "memory": "256Mi"}, "limits": {"cpu": "250m",  "memory": "512Mi"}},
    },
    "medium": {
        "agent_core":          {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "knowledge_engine":    {"requests": {"cpu": "500m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "memory_layer":        {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "trust_layer":         {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "action_gateway":      {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "reach_layer":         {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
        "observability_layer": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m",  "memory": "1Gi"}},
    },
    "high": {
        "agent_core":          {"requests": {"cpu": "500m", "memory": "2Gi"},   "limits": {"cpu": "2000m", "memory": "4Gi"}},
        "knowledge_engine":    {"requests": {"cpu": "1000m","memory": "2Gi"},   "limits": {"cpu": "2000m", "memory": "4Gi"}},
        "memory_layer":        {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "trust_layer":         {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "action_gateway":      {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "reach_layer":         {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
        "observability_layer": {"requests": {"cpu": "250m", "memory": "1Gi"},   "limits": {"cpu": "1000m", "memory": "2Gi"}},
    },
}


def apply_preset(tier: str) -> dict:
    """Apply a resource preset tier to all 7 DPG blocks.

    Args:
        tier: One of 'low', 'medium', 'high'.

    Returns:
        Dict mapping block name to {requests, limits}.

    Raises:
        ValueError: If tier is not recognized.
    """
    if tier not in PRESETS:
        raise ValueError(f"Unknown preset '{tier}'. Must be one of: {list(PRESETS.keys())}")
    return {block: dict(res) for block, res in PRESETS[tier].items()}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dev-kit && uv run pytest tests/test_deployer_presets.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/deployer/ dev-kit/tests/test_deployer_presets.py
git commit -m "feat: add deployer presets module with low/medium/high resource tiers"
```

---

### Task 12: Create dependencies.py

**Files:**
- Create: `dev-kit/dev_kit/agent/deployer/dependencies.py`
- Test: `dev-kit/tests/test_deployer_dependencies.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/tests/test_deployer_dependencies.py
import pytest
import yaml
from dev_kit.agent.deployer.dependencies import (
    INFRA_SERVICES, get_defaults, get_service_config, update_service_config,
)


def test_infra_services_has_seven_entries():
    assert len(INFRA_SERVICES) == 7


def test_get_defaults_returns_all_services():
    defaults = get_defaults()
    assert "redis" in defaults
    assert "memgraph" in defaults
    assert "otel_collector" in defaults
    assert "jaeger" in defaults
    assert "prometheus" in defaults
    assert "loki" in defaults
    assert "grafana" in defaults


def test_each_default_has_image_and_resources():
    for name, cfg in get_defaults().items():
        assert "image" in cfg, f"{name} missing image"
        assert "resources" in cfg, f"{name} missing resources"


def test_get_service_config_returns_yaml_string():
    result = get_service_config("redis")
    parsed = yaml.safe_load(result)
    assert parsed["image"]["repository"] == "redis"


def test_get_service_config_unknown():
    with pytest.raises(ValueError, match="Unknown"):
        get_service_config("unknown_service")


def test_update_service_config():
    new_yaml = yaml.dump({"image": {"repository": "redis", "tag": "6-alpine"}, "resources": {}})
    update_service_config("redis", new_yaml)
    result = yaml.safe_load(get_service_config("redis"))
    assert result["image"]["tag"] == "6-alpine"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dev-kit && uv run pytest tests/test_deployer_dependencies.py -v
```

- [ ] **Step 3: Implement dependencies.py**

```python
# dev-kit/dev_kit/agent/deployer/dependencies.py
"""Infrastructure service default configurations for deployment."""

import copy
import yaml

INFRA_SERVICES = {
    "redis": {
        "image": {"repository": "redis", "tag": "7-alpine"},
        "service": {"port": 6379},
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}},
        "password": "",
    },
    "memgraph": {
        "image": {"repository": "memgraph/memgraph", "tag": "latest"},
        "service": {"boltPort": 7687, "httpPort": 7444},
        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "500m", "memory": "1Gi"}},
        "persistence": {"size": "1Gi", "storageClass": ""},
        "password": "",
        "flags": {"walEnabled": True, "snapshotIntervalSec": 300},
    },
    "otel_collector": {
        "image": {"repository": "otel/opentelemetry-collector-contrib", "tag": "0.96.0"},
        "service": {"grpcPort": 4317, "httpPort": 4318, "metricsPort": 8889},
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}},
    },
    "jaeger": {
        "image": {"repository": "jaegertracing/all-in-one", "tag": "1.55"},
        "service": {"uiPort": 16686, "grpcPort": 14250},
        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "512Mi"}},
    },
    "prometheus": {
        "image": {"repository": "prom/prometheus", "tag": "v2.50.1"},
        "service": {"port": 9090},
        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "512Mi"}},
    },
    "loki": {
        "image": {"repository": "grafana/loki", "tag": "2.9.4"},
        "service": {"port": 3100},
        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "512Mi"}},
    },
    "grafana": {
        "image": {"repository": "grafana/grafana", "tag": "10.3.3"},
        "service": {"port": 3000},
        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}},
        "adminPassword": "admin",
    },
}

_overrides: dict = {}


def get_defaults() -> dict:
    """Return deep copy of all infrastructure service defaults."""
    return copy.deepcopy(INFRA_SERVICES)


def get_service_config(name: str) -> str:
    """Return YAML string for a single infra service config.

    Args:
        name: Service key (e.g. 'redis', 'memgraph').

    Returns:
        YAML string of the service config.

    Raises:
        ValueError: If name is not a known infrastructure service.
    """
    if name not in INFRA_SERVICES:
        raise ValueError(f"Unknown infrastructure service '{name}'. Known: {list(INFRA_SERVICES.keys())}")
    data = _overrides.get(name, INFRA_SERVICES[name])
    return yaml.dump(data, default_flow_style=False)


def update_service_config(name: str, yaml_content: str) -> None:
    """Update the runtime config for an infrastructure service.

    Args:
        name: Service key.
        yaml_content: New YAML content string.

    Raises:
        ValueError: If name is unknown or YAML is invalid.
    """
    if name not in INFRA_SERVICES:
        raise ValueError(f"Unknown infrastructure service '{name}'.")
    parsed = yaml.safe_load(yaml_content)
    if not isinstance(parsed, dict):
        raise ValueError("Invalid YAML: expected a mapping.")
    _overrides[name] = parsed
```

- [ ] **Step 4: Run tests**

```bash
cd dev-kit && uv run pytest tests/test_deployer_dependencies.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/deployer/dependencies.py dev-kit/tests/test_deployer_dependencies.py
git commit -m "feat: add deployer dependencies module with infra service defaults"
```

---

### Task 13: Create kubeconfig.py

**Files:**
- Create: `dev-kit/dev_kit/agent/deployer/kubeconfig.py`
- Test: `dev-kit/tests/test_deployer_kubeconfig.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/tests/test_deployer_kubeconfig.py
import pytest
import yaml
from dev_kit.agent.deployer.kubeconfig import parse_kubeconfig, validate_kubeconfig


VALID_KUBECONFIG = yaml.dump({
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [{"name": "test-cluster", "cluster": {"server": "https://127.0.0.1:6443"}}],
    "contexts": [{"name": "test-ctx", "context": {"cluster": "test-cluster", "user": "test-user"}}],
    "current-context": "test-ctx",
    "users": [{"name": "test-user", "user": {"token": "fake-token"}}],
})


def test_parse_valid_kubeconfig():
    result = parse_kubeconfig(VALID_KUBECONFIG)
    assert result["cluster_name"] == "test-cluster"
    assert result["server"] == "https://127.0.0.1:6443"
    assert result["current_context"] == "test-ctx"


def test_parse_invalid_yaml():
    with pytest.raises(ValueError, match="Invalid"):
        parse_kubeconfig("not: valid: yaml: {{")


def test_parse_missing_clusters():
    bad = yaml.dump({"apiVersion": "v1", "kind": "Config"})
    with pytest.raises(ValueError, match="clusters"):
        parse_kubeconfig(bad)


def test_parse_wrong_kind():
    bad = yaml.dump({"apiVersion": "v1", "kind": "Secret"})
    with pytest.raises(ValueError, match="kind"):
        parse_kubeconfig(bad)
```

- [ ] **Step 2: Implement kubeconfig.py**

```python
# dev-kit/dev_kit/agent/deployer/kubeconfig.py
"""Kubeconfig parsing and validation for Kubernetes deployments."""

import logging
import asyncio
import tempfile
import os
import yaml

logger = logging.getLogger(__name__)


def parse_kubeconfig(content: str) -> dict:
    """Parse a kubeconfig YAML string and extract cluster info.

    Args:
        content: Raw kubeconfig YAML string.

    Returns:
        Dict with cluster_name, server, current_context, user.

    Raises:
        ValueError: If content is not valid kubeconfig.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in kubeconfig: {e}")

    if not isinstance(data, dict):
        raise ValueError("Invalid kubeconfig: expected a YAML mapping.")
    if data.get("kind") != "Config":
        raise ValueError(f"Invalid kubeconfig: kind must be 'Config', got '{data.get('kind')}'.")
    if not data.get("clusters"):
        raise ValueError("Invalid kubeconfig: no clusters defined.")
    if not data.get("contexts"):
        raise ValueError("Invalid kubeconfig: no contexts defined.")

    current_ctx_name = data.get("current-context", "")
    cluster_info = data["clusters"][0].get("cluster", {})
    cluster_name = data["clusters"][0].get("name", "unknown")

    return {
        "cluster_name": cluster_name,
        "server": cluster_info.get("server", ""),
        "current_context": current_ctx_name,
    }


async def validate_kubeconfig(content: str) -> dict:
    """Validate kubeconfig by parsing and optionally running kubectl cluster-info.

    Args:
        content: Raw kubeconfig YAML string.

    Returns:
        Dict with cluster_name, server, current_context, valid, version, node_count.

    Raises:
        ValueError: If content is not valid kubeconfig.
    """
    info = parse_kubeconfig(content)

    # Write to temp file for kubectl
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    try:
        tmp.write(content)
        tmp.close()

        # Try kubectl cluster-info
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "version", "--short", "--kubeconfig", tmp.name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        if proc.returncode == 0:
            info["valid"] = True
            info["version"] = stdout.decode().strip().split("\n")[0] if stdout else ""
        else:
            info["valid"] = False
            info["error"] = stderr.decode().strip() if stderr else "kubectl failed"

        # Try getting node count
        proc2 = await asyncio.create_subprocess_exec(
            "kubectl", "get", "nodes", "--no-headers", "--kubeconfig", tmp.name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
        if proc2.returncode == 0 and stdout2:
            info["node_count"] = len([l for l in stdout2.decode().strip().split("\n") if l.strip()])
        else:
            info["node_count"] = 0

    except asyncio.TimeoutError:
        info["valid"] = False
        info["error"] = "kubectl timed out after 10s"
    finally:
        os.unlink(tmp.name)

    return info
```

- [ ] **Step 3: Run tests**

```bash
cd dev-kit && uv run pytest tests/test_deployer_kubeconfig.py -v
```
Expected: All 4 tests PASS (parsing tests don't need kubectl).

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/deployer/kubeconfig.py dev-kit/tests/test_deployer_kubeconfig.py
git commit -m "feat: add deployer kubeconfig parser and validator"
```

---

### Task 14: Create helm.py

**Files:**
- Create: `dev-kit/dev_kit/agent/deployer/helm.py`
- Test: `dev-kit/tests/test_deployer_helm.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/tests/test_deployer_helm.py
import pytest
from dev_kit.agent.deployer.helm import build_helm_command, build_template_command, DEPLOY_PHASES


def test_deploy_phases_has_six_phases():
    assert len(DEPLOY_PHASES) == 6


def test_build_helm_command_dpg_block():
    cmd = build_helm_command(
        chart_path="/charts/dpg/agent-core",
        release_name="agent-core",
        namespace="dpg-agent-core",
        kubeconfig_path="/tmp/kc",
        set_values={"anthropicApiKey": "sk-ant-test"},
        set_files={"dpgConfig": "/tmp/dpg.yaml", "domainConfig": "/tmp/domain.yaml"},
    )
    assert "helm" in cmd[0]
    assert "--namespace" in cmd
    assert "dpg-agent-core" in cmd
    assert "--set-file" in cmd
    assert "--kubeconfig" in cmd


def test_build_helm_command_infra():
    cmd = build_helm_command(
        chart_path="/charts/infra/redis",
        release_name="redis",
        namespace="dpg-redis",
        kubeconfig_path="/tmp/kc",
    )
    assert "redis" in cmd
    assert "--create-namespace" in cmd


def test_build_template_command():
    cmd = build_template_command(
        chart_path="/charts/dpg/agent-core",
        release_name="agent-core",
        set_values={"anthropicApiKey": "test"},
        set_files={"dpgConfig": "/tmp/dpg.yaml"},
    )
    assert cmd[1] == "template"
    assert "--set-file" in cmd
```

- [ ] **Step 2: Implement helm.py**

```python
# dev-kit/dev_kit/agent/deployer/helm.py
"""Helm deployment driver — builds commands, runs installs, polls status."""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEPLOY_PHASES = [
    {"name": "Data", "services": ["redis", "memgraph"]},
    {"name": "Observability", "services": ["jaeger", "prometheus", "loki", "grafana"]},
    {"name": "Telemetry", "services": ["otel_collector"]},
    {"name": "DPG Backend", "services": ["memory_layer", "trust_layer", "action_gateway", "knowledge_engine"]},
    {"name": "DPG Core", "services": ["observability_layer", "agent_core"]},
    {"name": "DPG Frontend", "services": ["reach_layer"]},
]


def build_helm_command(
    chart_path: str,
    release_name: str,
    namespace: str,
    kubeconfig_path: str,
    set_values: Optional[dict] = None,
    set_files: Optional[dict] = None,
    upgrade: bool = False,
) -> list[str]:
    """Build a helm install/upgrade command list.

    Args:
        chart_path: Path to the Helm chart directory.
        release_name: Helm release name.
        namespace: Kubernetes namespace.
        kubeconfig_path: Path to kubeconfig file.
        set_values: Dict of --set key=value pairs.
        set_files: Dict of --set-file key=path pairs.
        upgrade: If True, use 'upgrade --install' instead of 'install'.

    Returns:
        List of command arguments.
    """
    if upgrade:
        cmd = ["helm", "upgrade", "--install", release_name, chart_path]
    else:
        cmd = ["helm", "install", release_name, chart_path]

    cmd.extend(["--namespace", namespace, "--create-namespace"])
    cmd.extend(["--kubeconfig", kubeconfig_path])

    for key, val in (set_values or {}).items():
        cmd.extend(["--set", f"{key}={val}"])

    for key, path in (set_files or {}).items():
        cmd.extend(["--set-file", f"{key}={path}"])

    return cmd


def build_template_command(
    chart_path: str,
    release_name: str,
    set_values: Optional[dict] = None,
    set_files: Optional[dict] = None,
) -> list[str]:
    """Build a helm template command for preview rendering.

    Args:
        chart_path: Path to the Helm chart directory.
        release_name: Helm release name.
        set_values: Dict of --set key=value pairs.
        set_files: Dict of --set-file key=path pairs.

    Returns:
        List of command arguments.
    """
    cmd = ["helm", "template", release_name, chart_path]

    for key, val in (set_values or {}).items():
        cmd.extend(["--set", f"{key}={val}"])

    for key, path in (set_files or {}).items():
        cmd.extend(["--set-file", f"{key}={path}"])

    return cmd


async def run_helm_command(cmd: list[str], timeout: int = 120) -> dict:
    """Execute a helm command and return result.

    Args:
        cmd: Command argument list.
        timeout: Timeout in seconds.

    Returns:
        Dict with success, stdout, stderr.
    """
    logger.info("helm_command", extra={"operation": "helm.run", "command": " ".join(cmd[:5])})
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode() if stdout else "",
            "stderr": stderr.decode() if stderr else "",
        }
    except asyncio.TimeoutError:
        return {"success": False, "stdout": "", "stderr": f"Helm command timed out after {timeout}s"}


async def get_pod_status(namespace: str, kubeconfig_path: str) -> list[dict]:
    """Get pod status for a namespace via kubectl.

    Args:
        namespace: Kubernetes namespace.
        kubeconfig_path: Path to kubeconfig file.

    Returns:
        List of dicts with name, status, ready.
    """
    proc = await asyncio.create_subprocess_exec(
        "kubectl", "get", "pods", "-n", namespace, "-o", "json",
        "--kubeconfig", kubeconfig_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        return []

    import json
    data = json.loads(stdout.decode())
    pods = []
    for item in data.get("items", []):
        phase = item.get("status", {}).get("phase", "Unknown")
        conditions = item.get("status", {}).get("conditions", [])
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
        pods.append({
            "name": item.get("metadata", {}).get("name", ""),
            "status": phase.lower(),
            "ready": ready,
        })
    return pods
```

- [ ] **Step 3: Run tests**

```bash
cd dev-kit && uv run pytest tests/test_deployer_helm.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/deployer/helm.py dev-kit/tests/test_deployer_helm.py
git commit -m "feat: add deployer helm driver with command builders and phase ordering"
```

---

### Task 15: Create compose.py

**Files:**
- Create: `dev-kit/dev_kit/agent/deployer/compose.py`
- Test: `dev-kit/tests/test_deployer_compose.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/tests/test_deployer_compose.py
import pytest
import yaml
from dev_kit.agent.deployer.compose import generate_compose


def test_generate_compose_has_all_services():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={"agent_core": {"limits": {"cpu": "1.0", "memory": "2G"}}},
        secrets={"anthropic_api_key": "sk-test"},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    assert "services" in parsed
    # 7 DPG + 7 infra = 14
    assert len(parsed["services"]) == 14


def test_generate_compose_agent_core_has_api_key():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={"anthropic_api_key": "sk-test"},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    agent_env = parsed["services"]["agent_core"].get("environment", [])
    assert any("ANTHROPIC_API_KEY" in str(e) for e in agent_env)


def test_generate_compose_has_network():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    assert "dpg_net" in parsed.get("networks", {})


def test_generate_compose_volume_mounts():
    result = generate_compose(
        project_slug="test",
        dpg_dir="/tmp/dpg",
        domain_dir="/tmp/domain",
        resources={},
        secrets={},
        infra_configs={},
    )
    parsed = yaml.safe_load(result)
    ac_volumes = parsed["services"]["agent_core"].get("volumes", [])
    assert any("dpg.yaml" in str(v) for v in ac_volumes)
    assert any("domain.yaml" in str(v) for v in ac_volumes)
```

- [ ] **Step 2: Implement compose.py**

```python
# dev-kit/dev_kit/agent/deployer/compose.py
"""Docker Compose deployment driver — generates compose files and manages containers."""

import asyncio
import logging
import yaml

logger = logging.getLogger(__name__)

DPG_SERVICES = {
    "agent_core":          {"image": "sanketikahub/dpg-agent-core:0.1.0",          "port": 8000},
    "knowledge_engine":    {"image": "sanketikahub/dpg-knowledge-engine:0.1.0",    "port": 8001},
    "memory_layer":        {"image": "sanketikahub/dpg-memory-layer:0.1.0",        "port": 8002},
    "trust_layer":         {"image": "sanketikahub/dpg-trust-layer:0.1.0",         "port": 8003},
    "observability_layer": {"image": "sanketikahub/dpg-observability-layer:0.1.0", "port": 8004},
    "action_gateway":      {"image": "sanketikahub/dpg-action-gateway:0.1.0",      "port": 9999},
    "reach_layer":         {"image": "sanketikahub/dpg-reach-layer:0.1.0",         "port": 8005},
}

INFRA_IMAGES = {
    "redis":          "redis:7-alpine",
    "memgraph":       "memgraph/memgraph:latest",
    "otel_collector": "otel/opentelemetry-collector-contrib:0.96.0",
    "jaeger":         "jaegertracing/all-in-one:1.55",
    "prometheus":     "prom/prometheus:v2.50.1",
    "loki":           "grafana/loki:2.9.4",
    "grafana":        "grafana/grafana:10.3.3",
}

DEPENDS_ON = {
    "memory_layer": ["redis", "memgraph"],
    "agent_core": ["knowledge_engine", "memory_layer", "trust_layer", "observability_layer", "action_gateway"],
    "reach_layer": ["agent_core"],
    "observability_layer": ["otel_collector"],
    "otel_collector": ["jaeger", "prometheus", "loki"],
}


def generate_compose(
    project_slug: str,
    dpg_dir: str,
    domain_dir: str,
    resources: dict,
    secrets: dict,
    infra_configs: dict,
) -> str:
    """Generate a docker-compose.yml string for deploying all 14 services.

    Args:
        project_slug: Project identifier.
        dpg_dir: Path to DPG framework YAML directory.
        domain_dir: Path to domain config YAML directory.
        resources: Per-block resource overrides {block: {limits: {cpu, memory}}}.
        secrets: Deployment secrets {anthropic_api_key, redis_password, memgraph_password, grafana_admin_password}.
        infra_configs: Infra service config overrides.

    Returns:
        YAML string of the generated docker-compose file.
    """
    services = {}

    # Infrastructure services
    services["redis"] = {
        "image": INFRA_IMAGES["redis"],
        "networks": ["dpg_net"],
        "healthcheck": {
            "test": ["CMD", "redis-cli", "ping"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
        },
    }
    if secrets.get("redis_password"):
        services["redis"]["command"] = ["redis-server", "--requirepass", secrets["redis_password"]]

    services["memgraph"] = {
        "image": INFRA_IMAGES["memgraph"],
        "command": ["--storage-wal-enabled=true", "--storage-snapshot-interval-sec=300"],
        "volumes": ["memgraph_data:/var/lib/memgraph"],
        "networks": ["dpg_net"],
        "healthcheck": {
            "test": ["CMD-SHELL", "curl -f http://localhost:7444 || exit 1"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "15s",
        },
    }

    services["otel_collector"] = {
        "image": INFRA_IMAGES["otel_collector"],
        "command": ["--config=/etc/otelcol/config.yaml"],
        "volumes": ["./otelcol/otelcol-config.yaml:/etc/otelcol/config.yaml:ro"],
        "networks": ["dpg_net"],
        "depends_on": {n: {"condition": "service_started"} for n in DEPENDS_ON.get("otel_collector", [])},
    }

    for svc in ["jaeger", "prometheus", "loki", "grafana"]:
        services[svc] = {
            "image": INFRA_IMAGES[svc],
            "networks": ["dpg_net"],
        }

    if secrets.get("grafana_admin_password"):
        services["grafana"]["environment"] = [f"GF_SECURITY_ADMIN_PASSWORD={secrets['grafana_admin_password']}"]

    # DPG services
    for block, info in DPG_SERVICES.items():
        block_file = block + ".yaml"
        svc = {
            "image": info["image"],
            "volumes": [
                f"{dpg_dir}/{block_file}:/app/config/dpg.yaml:ro",
                f"{domain_dir}/{block_file}:/app/config/domain.yaml:ro",
            ],
            "environment": ["CONFIG_FOLDER=/app/config"],
            "networks": ["dpg_net"],
            "healthcheck": {
                "test": ["CMD-SHELL", f"python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:{info['port']}/health', timeout=3)\""],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
                "start_period": "30s",
            },
        }

        if block == "agent_core":
            svc["environment"].append(f"ANTHROPIC_API_KEY={secrets.get('anthropic_api_key', '')}")

        if block == "memory_layer":
            redis_pw = secrets.get("redis_password", "")
            redis_url = f"redis://:{redis_pw}@redis:6379/0" if redis_pw else "redis://redis:6379/0"
            svc["environment"].extend([
                f"REDIS_URL={redis_url}",
                "MEMGRAPH_URI=bolt://memgraph:7687",
                "MEMGRAPH_USER=memgraph",
                f"MEMGRAPH_PASSWORD={secrets.get('memgraph_password', '')}",
            ])

        if block == "reach_layer":
            svc["ports"] = [f"{info['port']}:{info['port']}"]

        if block in resources:
            res = resources[block]
            svc["deploy"] = {"resources": {"limits": {
                "cpus": str(res.get("limits", {}).get("cpu", "0.5")).rstrip("m"),
                "memory": res.get("limits", {}).get("memory", "512M"),
            }}}

        if block in DEPENDS_ON:
            svc["depends_on"] = {dep: {"condition": "service_healthy"} for dep in DEPENDS_ON[block]}

        services[block] = svc

    compose = {
        "services": services,
        "networks": {"dpg_net": {"driver": "bridge"}},
        "volumes": {
            "memgraph_data": {},
            "chroma_data": {},
        },
    }

    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


async def run_compose_up(compose_path: str, timeout: int = 300) -> dict:
    """Run docker compose up -d.

    Args:
        compose_path: Path to docker-compose.yml.
        timeout: Timeout in seconds.

    Returns:
        Dict with success, stdout, stderr.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose_path, "up", "-d",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return {
        "success": proc.returncode == 0,
        "stdout": stdout.decode() if stdout else "",
        "stderr": stderr.decode() if stderr else "",
    }


async def get_compose_status(compose_path: str) -> list[dict]:
    """Get container status via docker compose ps.

    Args:
        compose_path: Path to docker-compose.yml.

    Returns:
        List of dicts with name, status, health.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose_path, "ps", "--format", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        return []

    import json
    containers = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            containers.append({
                "name": data.get("Service", data.get("Name", "")),
                "status": data.get("State", "unknown").lower(),
                "health": data.get("Health", "").lower(),
            })
        except json.JSONDecodeError:
            continue
    return containers
```

- [ ] **Step 3: Run tests**

```bash
cd dev-kit && uv run pytest tests/test_deployer_compose.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/deployer/compose.py dev-kit/tests/test_deployer_compose.py
git commit -m "feat: add deployer compose driver with dynamic compose file generation"
```

---

### Task 16: Add deploy endpoints to app.py

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`
- Test: `dev-kit/tests/test_app_deploy_routes.py`

- [ ] **Step 1: Write failing tests for deploy routes**

```python
# dev-kit/tests/test_app_deploy_routes.py
import pytest
from httpx import AsyncClient, ASGITransport
from dev_kit.agent.app import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_get_dpg_values(client):
    # Create a project first
    await client.post("/api/projects", json={"name": "Deploy Test", "description": "test"})
    resp = await client.get("/api/projects/deploy-test/deploy/dpg-values")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 7


@pytest.mark.asyncio
async def test_get_dependencies(client):
    resp = await client.get("/api/projects/deploy-test/deploy/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert "redis" in data


@pytest.mark.asyncio
async def test_get_resource_presets(client):
    resp = await client.get("/api/projects/deploy-test/deploy/resource-presets")
    assert resp.status_code == 200
    data = resp.json()
    assert "low" in data
    assert "medium" in data
    assert "high" in data


@pytest.mark.asyncio
async def test_apply_resource_preset(client):
    resp = await client.post("/api/projects/deploy-test/deploy/resource-presets/medium")
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_core" in data


@pytest.mark.asyncio
async def test_apply_invalid_preset(client):
    resp = await client.post("/api/projects/deploy-test/deploy/resource-presets/ultra")
    assert resp.status_code == 400
```

- [ ] **Step 2: Add deploy endpoints to app.py**

Add these endpoints after the existing config/workflow endpoints:

```python
# --- Deploy endpoints ---

from dev_kit.agent.deployer.presets import PRESETS, apply_preset
from dev_kit.agent.deployer.dependencies import get_defaults as get_infra_defaults, get_service_config, update_service_config
from dev_kit.agent.deployer.kubeconfig import parse_kubeconfig, validate_kubeconfig as _validate_kc


@app.get("/api/projects/{slug}/deploy/dpg-values")
async def get_dpg_values(slug: str):
    """Return all 7 DPG framework YAML files."""
    dpg_dir = DPG_DIR  # dev-kit/dpg/
    results = []
    for block in BLOCKS:
        path = dpg_dir / f"{block}.yaml"
        content = path.read_text() if path.exists() else ""
        results.append({"block": block, "content": content})
    return results


@app.put("/api/projects/{slug}/deploy/dpg-values/{block}")
async def update_dpg_value(slug: str, block: str, body: dict):
    """Update a DPG framework YAML file."""
    if block not in BLOCKS:
        raise HTTPException(400, f"Unknown block: {block}")
    path = DPG_DIR / f"{block}.yaml"
    path.write_text(body["content"])
    return {"status": "ok"}


@app.get("/api/projects/{slug}/deploy/dependencies")
async def get_dependencies(slug: str):
    """Return all infrastructure service configs."""
    defaults = get_infra_defaults()
    result = {}
    for name, cfg in defaults.items():
        result[name] = {"config": get_service_config(name), "defaults": cfg}
    return result


@app.put("/api/projects/{slug}/deploy/dependencies/{service}")
async def update_dependency(slug: str, service: str, body: dict):
    """Update an infrastructure service config."""
    try:
        update_service_config(service, body["content"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok"}


@app.get("/api/projects/{slug}/deploy/resource-presets")
async def get_resource_presets(slug: str):
    """Return the 3 resource preset definitions."""
    return PRESETS


@app.post("/api/projects/{slug}/deploy/resource-presets/{tier}")
async def apply_resource_preset_endpoint(slug: str, tier: str):
    """Apply a resource preset to 7 DPG layers."""
    try:
        return apply_preset(tier)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{slug}/deploy/validate-kubeconfig")
async def validate_kubeconfig_endpoint(slug: str, body: dict):
    """Validate a kubeconfig and return cluster info."""
    try:
        return await _validate_kc(body["content"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{slug}/deploy/preview")
async def get_deploy_preview(slug: str, body: dict):
    """Run helm template or generate docker-compose preview."""
    # Implementation depends on target — returns rendered YAML per service
    target = body.get("target", "docker")
    # Placeholder — full implementation in compose.py/helm.py integration
    return {"target": target, "preview": ""}


@app.post("/api/projects/{slug}/deploy/execute")
async def execute_deploy(slug: str, body: dict):
    """Trigger deployment of all 14 services."""
    # Full implementation runs helm install or docker compose up
    return {"status": "started"}


@app.get("/api/projects/{slug}/deploy/status")
async def get_deploy_status(slug: str):
    """Poll deployment status of all services."""
    # Full implementation reads from project.json deployment state
    return {"services": {}, "status": "idle"}
```

- [ ] **Step 3: Run tests**

```bash
cd dev-kit && uv run pytest tests/test_app_deploy_routes.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py dev-kit/tests/test_app_deploy_routes.py
git commit -m "feat: add 10 deploy REST endpoints to dev-kit backend"
```

---

## Group D: Frontend Deploy Wizard

### Task 17: Add deploy API methods to api.js

**Files:**
- Modify: `dev-kit/frontend/src/api.js`

- [ ] **Step 1: Add deploy methods**

Add after the existing `getSchemaDescriptions` method:

```javascript
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
```

- [ ] **Step 2: Commit**

```bash
git add dev-kit/frontend/src/api.js
git commit -m "feat: add 10 deploy API methods to frontend client"
```

---

### Task 18: Create StepIndicator and DeployWizard container

**Files:**
- Create: `dev-kit/frontend/src/components/deploy/StepIndicator.jsx`
- Create: `dev-kit/frontend/src/components/deploy/DeployWizard.jsx`

- [ ] **Step 1: Create StepIndicator**

```jsx
// dev-kit/frontend/src/components/deploy/StepIndicator.jsx
import React from 'react'

const STEPS = [
  { key: 1, label: 'DPG Values' },
  { key: 2, label: 'Dependencies' },
  { key: 3, label: 'Resources' },
  { key: 4, label: 'Inputs' },
  { key: 5, label: 'Target' },
  { key: 6, label: 'Preview' },
  { key: 7, label: 'Status' },
]

export default function StepIndicator({ currentStep, completedSteps }) {
  return (
    <div className="flex items-center gap-1 px-4 py-3 bg-gray-900 border-b border-gray-800 overflow-x-auto">
      {STEPS.map((step, i) => {
        const isActive = step.key === currentStep
        const isDone = completedSteps.includes(step.key)
        return (
          <React.Fragment key={step.key}>
            {i > 0 && <div className={`h-px flex-1 min-w-[12px] ${isDone ? 'bg-green-700' : 'bg-gray-700'}`} />}
            <div className="flex items-center gap-1.5 shrink-0">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium border ${
                isActive ? 'border-blue-500 bg-blue-600 text-white' :
                isDone ? 'border-green-700 bg-green-900 text-green-300' :
                'border-gray-700 bg-gray-800 text-gray-500'
              }`}>
                {isDone ? '✓' : step.key}
              </div>
              <span className={`text-xs whitespace-nowrap ${
                isActive ? 'text-white font-medium' : 'text-gray-500'
              }`}>
                {step.label}
              </span>
            </div>
          </React.Fragment>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Create DeployWizard**

```jsx
// dev-kit/frontend/src/components/deploy/DeployWizard.jsx
import React, { useState, useCallback } from 'react'
import StepIndicator from './StepIndicator'
import DpgValuesStep from './DpgValuesStep'
import DependenciesStep from './DependenciesStep'
import ResourcePresetStep from './ResourcePresetStep'
import MandatoryInputsStep from './MandatoryInputsStep'
import DeployTargetStep from './DeployTargetStep'
import PreviewStep from './PreviewStep'
import DeployStatusStep from './DeployStatusStep'

export default function DeployWizard({ slug, onBack }) {
  const [step, setStep] = useState(1)
  const [completed, setCompleted] = useState([])
  const [data, setData] = useState({
    dpgValues: {},
    dependencies: {},
    preset: null,
    resources: {},
    secrets: { anthropic_api_key: '', namespace_prefix: 'dpg', memgraph_password: '', redis_password: '', grafana_admin_password: 'admin' },
    target: null, // 'docker' | 'kubernetes'
    kubeconfig: '',
    clusterInfo: null,
  })

  const updateData = useCallback((key, value) => {
    setData(prev => ({ ...prev, [key]: value }))
  }, [])

  function handleNext() {
    if (!completed.includes(step)) {
      setCompleted(prev => [...prev, step])
    }
    setStep(prev => Math.min(prev + 1, 7))
  }

  function handleBack() {
    setStep(prev => Math.max(prev - 1, 1))
  }

  const stepProps = { slug, data, updateData }
  const steps = {
    1: <DpgValuesStep {...stepProps} />,
    2: <DependenciesStep {...stepProps} />,
    3: <ResourcePresetStep {...stepProps} />,
    4: <MandatoryInputsStep {...stepProps} />,
    5: <DeployTargetStep {...stepProps} />,
    6: <PreviewStep {...stepProps} />,
    7: <DeployStatusStep {...stepProps} />,
  }

  const isDeployStep = step === 7

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
            ← Dashboard
          </button>
          <h1 className="text-lg font-semibold">Deploy Configuration</h1>
        </div>
      </div>

      <StepIndicator currentStep={step} completedSteps={completed} />

      {/* Step content */}
      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-5xl mx-auto w-full">
        {steps[step]}
      </div>

      {/* Footer nav */}
      {!isDeployStep && (
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-800">
          <button
            onClick={step === 1 ? onBack : handleBack}
            className="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-xl transition-colors"
          >
            ← {step === 1 ? 'Dashboard' : 'Back'}
          </button>
          {step === 6 ? (
            <button
              onClick={handleNext}
              className="text-sm bg-green-700 hover:bg-green-600 text-white px-5 py-2 rounded-xl font-medium transition-colors"
            >
              Deploy
            </button>
          ) : (
            <button
              onClick={handleNext}
              className="text-sm bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-xl transition-colors"
            >
              Next →
            </button>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/StepIndicator.jsx dev-kit/frontend/src/components/deploy/DeployWizard.jsx
git commit -m "feat: add DeployWizard container and StepIndicator components"
```

---

### Task 19: Create wizard step components (Steps 1-5)

**Files:**
- Create: `dev-kit/frontend/src/components/deploy/DpgValuesStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/DependenciesStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/ResourcePresetStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/DeployTargetStep.jsx`

Each step component receives `{ slug, data, updateData }` props.

- [ ] **Step 1: Create DpgValuesStep (Step 1)**

Tabbed CodeMirror editors for 7 `dev-kit/dpg/*.yaml` files. Uses `TabBar` from shared, `useYamlEditor` hook. Loads values via `api.getDpgValues(slug)` on mount. Edit/save per tab via `api.updateDpgValue()`.

- [ ] **Step 2: Create DependenciesStep (Step 2)**

Two-column layout: "Data Services" (Redis, Memgraph) and "Observability Stack" (5 services). Each service is a collapsible card. Collapsed: image, port, resource summary. Expanded: CodeMirror editor for YAML values. Loads via `api.getDependencies(slug)`. Save via `api.updateDependency()`.

- [ ] **Step 3: Create ResourcePresetStep (Step 3)**

Three cards (Low/Medium/High) with cluster resource estimates. Loads presets via `api.getResourcePresets(slug)`. On select, calls `api.applyResourcePreset(slug, tier)`. Below: summary table showing all 14 services — 7 DPG with preset values, 7 infra with defaults (grayed out).

- [ ] **Step 4: Create MandatoryInputsStep (Step 4)**

Form with Required section (API key) and Optional section (namespace prefix, passwords). Updates `data.secrets` via `updateData`. Note at bottom: "Fields marked * are required. All others have sensible defaults and can be left unchanged."

- [ ] **Step 5: Create DeployTargetStep (Step 5)**

Two selection cards: Docker Compose / Kubernetes. If K8s selected, shows two toggle buttons: "Upload File" (drag-and-drop zone) and "Paste" (CodeMirror editor). On submit, calls `api.validateKubeconfig(slug, content)`. Shows cluster info (name, version, nodes) on success.

- [ ] **Step 6: Verify build**

```bash
cd dev-kit/frontend && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/
git commit -m "feat: add deploy wizard steps 1-5 (DPG values, dependencies, resources, inputs, target)"
```

---

### Task 20: Create PreviewStep and DeployStatusStep (Steps 6-7)

**Files:**
- Create: `dev-kit/frontend/src/components/deploy/PreviewStep.jsx`
- Create: `dev-kit/frontend/src/components/deploy/DeployStatusStep.jsx`

- [ ] **Step 1: Create PreviewStep (Step 6)**

Tabbed read-only CodeMirror view. Calls `api.getDeployPreview(slug, { target, secrets, preset, ... })` on mount. For K8s: one tab per service showing `helm template` output. For Docker: single tab showing generated `docker-compose.yml`. Summary banner at top showing service count and target.

- [ ] **Step 2: Create DeployStatusStep (Step 7)**

Calls `api.executeDeploy(slug, ...)` on mount. Polls `api.getDeployStatus(slug)` every 3 seconds. Shows services grouped by deployment phase (6 phases). Each service row: name, status icon (queued/starting/running/failed), health. On all healthy: green banner with access URLs. On failure: red status with error message and retry button.

- [ ] **Step 3: Verify build**

```bash
cd dev-kit/frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/components/deploy/PreviewStep.jsx dev-kit/frontend/src/components/deploy/DeployStatusStep.jsx
git commit -m "feat: add deploy wizard steps 6-7 (preview and live status board)"
```

---

### Task 21: Wire deploy view into App.jsx and Dashboard

**Files:**
- Modify: `dev-kit/frontend/src/App.jsx`
- Modify: `dev-kit/frontend/src/components/Dashboard.jsx`

- [ ] **Step 1: Add deploy view to App.jsx**

Add import and view handler:

```javascript
import DeployWizard from './components/deploy/DeployWizard'

// Add after openConfig function:
function openDeploy(slug) {
  setActiveSlug(slug)
  setView('deploy')
}

// Add after the config view conditional:
if (view === 'deploy') {
  return (
    <DeployWizard
      slug={activeSlug}
      onBack={() => openDashboard(activeSlug)}
    />
  )
}
```

- [ ] **Step 2: Add Deploy button to Dashboard HealthBanner**

In Dashboard.jsx, update the HealthBanner to accept an `onDeploy` prop and show a Deploy button when all configs are complete:

```jsx
// In Dashboard component, pass onDeploy to HealthBanner:
<HealthBanner configs={configs} onDeploy={() => onDeploy(slug)} />

// In HealthBanner, add Deploy button when allComplete:
{allComplete && (
  <button
    onClick={onDeploy}
    className="text-sm bg-green-700 hover:bg-green-600 text-white px-4 py-2 rounded-xl font-medium transition-colors"
  >
    Deploy →
  </button>
)}
```

Update Dashboard props to include `onDeploy`:
```jsx
export default function Dashboard({ slug, onChat, onEditConfig, onBack, onDeploy }) {
```

Update App.jsx Dashboard usage:
```jsx
<Dashboard
  slug={activeSlug}
  onChat={() => setView('chat')}
  onEditConfig={(block) => openConfig(activeSlug, block)}
  onBack={() => setView('projects')}
  onDeploy={() => openDeploy(activeSlug)}
/>
```

- [ ] **Step 3: Build and verify**

```bash
cd dev-kit/frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/App.jsx dev-kit/frontend/src/components/Dashboard.jsx
git commit -m "feat: wire deploy wizard into App routing and Dashboard deploy button"
```

---

### Task 22: Run all tests and final verification

- [ ] **Step 1: Run backend tests**

```bash
cd dev-kit && uv run pytest tests/ -v --tb=short
```
Expected: All tests pass.

- [ ] **Step 2: Run frontend build**

```bash
cd dev-kit/frontend && npm run build
```
Expected: Build succeeds.

- [ ] **Step 3: Run frontend tests if they exist**

```bash
cd dev-kit/frontend && npm test -- --run
```

- [ ] **Step 4: Verify helm charts**

```bash
for chart in automation/helm/dpg/*/; do helm template test "$chart" --set dpgConfig="test: true" --set domainConfig="test: true" 2>&1 | head -3; done
for chart in automation/helm/infra/*/; do helm template test "$chart" 2>&1 | head -3; done
```

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test and build issues from deploy wizard implementation"
```
