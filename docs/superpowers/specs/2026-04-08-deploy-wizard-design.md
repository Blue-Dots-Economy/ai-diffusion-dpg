# Deploy Wizard — Design Spec

**Date:** 2026-04-08
**Branch:** devkit-ui-enhancements
**Goal:** Add end-to-end deployment capability to the dev-kit UI. After all 7 DPG configs are complete, users walk through a wizard to review framework values, configure dependency services, select resource tiers, provide secrets, choose a deploy target (Docker Compose or Kubernetes), preview rendered templates, and deploy all 14 services with live status tracking.

---

## 1. Scope

### In Scope
- 7-step deployment wizard in the React SPA
- Helm chart refactoring: make 7 existing DPG charts config-agnostic
- 7 new Helm charts for infrastructure dependencies (Redis, Memgraph, OTel Collector, Jaeger, Prometheus, Loki, Grafana)
- Backend deployer module (`dev-kit/dev_kit/agent/deployer/`)
- Docker Compose dynamic generation
- Kubernetes deployment via Helm with kubeconfig
- Resource presets (low/medium/high) for 7 DPG layers
- Live deployment status board with health polling
- Helm template preview (read-only)

### Out of Scope
- ASR/TTS pipeline, model training, infrastructure provisioning beyond Helm/Compose
- Custom Helm chart authoring by users
- Multi-tenancy, rollback/versioning UI
- CI/CD pipeline integration
- Ingress/load balancer configuration

---

## 2. Services Deployed (14 Total)

### 7 DPG Layers
| Service | Image | Default Port |
|---------|-------|-------------|
| Agent Core | sanketikahub/dpg-agent-core:0.1.0 | 8000 |
| Knowledge Engine | sanketikahub/dpg-knowledge-engine:0.1.0 | 8001 |
| Memory Layer | sanketikahub/dpg-memory-layer:0.1.0 | 8002 |
| Trust Layer | sanketikahub/dpg-trust-layer:0.1.0 | 8003 |
| Observability Layer | sanketikahub/dpg-observability-layer:0.1.0 | 8004 |
| Action Gateway | sanketikahub/dpg-action-gateway:0.1.0 | 9999 |
| Reach Layer | sanketikahub/dpg-reach-layer:0.1.0 | 8005 |

### 7 Infrastructure Dependencies
| Service | Image | Default Port | Needed By |
|---------|-------|-------------|-----------|
| Redis | redis:7-alpine | 6379 | Memory Layer |
| Memgraph | memgraph/memgraph:latest | 7687, 7444 | Memory Layer |
| OTel Collector | otel/opentelemetry-collector-contrib:0.96.0 | 4317, 4318, 8889 | Observability Layer |
| Jaeger | jaegertracing/all-in-one:1.55 | 16686, 14250 | OTel Collector |
| Prometheus | prom/prometheus:v2.50.1 | 9090 | OTel Collector |
| Loki | grafana/loki:2.9.4 | 3100 | OTel Collector |
| Grafana | grafana/grafana:10.3.3 | 3000 | UI only |

---

## 3. Wizard Flow (7 Steps)

### Entry Point
Dashboard's green "All configs complete — ready to deploy" banner shows a **Deploy** button. Clicking navigates to the `deploy` view.

### Step 1: DPG Framework Values
- Tabbed view — one tab per DPG layer
- CodeMirror YAML editor per tab (same pattern as YamlPanel)
- Edit/save per tab with validation
- Content: framework defaults from `dev-kit/dpg/*.yaml` (ports, timeouts, retry configs)
- Note: "These are framework defaults. Domain configs were built in the Configuration Agent."

### Step 2: Dependency Services
- Two-column layout: "Data Services" (Redis, Memgraph) | "Observability Stack" (OTel Collector, Jaeger, Prometheus, Loki, Grafana)
- Each service is a collapsible card showing image, port, resources
- Expanding reveals CodeMirror editor with the service's values
- Defaults pre-filled; user edits only if needed

### Step 3: Resource Preset
- Three cards: Low / Medium / High
- Selecting one applies resources to **7 DPG layers only** (not infra services)
- Infrastructure services retain their own defaults
- Summary table below shows all 14 services:
  - 7 DPG layers: values from selected preset
  - 7 infra services: defaults (grayed out, with note "edit in Step 2")

#### Resource Presets

**DPG Layers:**

| Preset | Standard Layers (5) | Agent Core | Knowledge Engine |
|--------|---------------------|------------|-----------------|
| Low | 50m/250m CPU, 256Mi/512Mi | 100m/500m, 512Mi/1Gi | 250m/500m, 512Mi/1Gi |
| Medium | 100m/500m, 512Mi/1Gi | 250m/1000m, 1Gi/2Gi | 500m/1000m, 1Gi/2Gi |
| High | 250m/1000m, 1Gi/2Gi | 500m/2000m, 2Gi/4Gi | 1000m/2000m, 2Gi/4Gi |

**Infrastructure Defaults (fixed, editable in Step 2):**

| Service | CPU req/limit | Memory req/limit |
|---------|--------------|-----------------|
| Redis | 50m/100m | 64Mi/128Mi |
| Memgraph | 100m/500m | 256Mi/1Gi |
| OTel Collector | 50m/200m | 64Mi/256Mi |
| Jaeger | 50m/200m | 128Mi/512Mi |
| Prometheus | 50m/200m | 128Mi/512Mi |
| Loki | 50m/200m | 128Mi/512Mi |
| Grafana | 50m/200m | 128Mi/256Mi |

### Step 4: Mandatory Inputs
- Grouped form: Required section + Optional section
- Required: Anthropic API Key (masked, show/hide toggle, validated non-empty + `sk-ant-` prefix)
- Optional (with defaults pre-filled): Namespace Prefix (`dpg`), Memgraph Password (empty), Redis Password (empty), Grafana Admin Password (`admin`)
- Note: "Fields marked * are required. All others have sensible defaults and can be left unchanged."

### Step 5: Deploy Target
- Two cards: Docker Compose (local dev) | Kubernetes (production)
- If K8s selected, kubeconfig input appears with two options:
  - **Upload file** — drag & drop zone
  - **Paste kubeconfig** — CodeMirror editor (toggled via buttons)
- Backend validates kubeconfig on submit, returns cluster info (name, version, node count)
- "Next" blocked until validation passes

### Step 6: Review & Preview
- Read-only rendered view
- For K8s: tabbed view, one tab per service showing `helm template` output
- For Docker: single tab showing generated `docker-compose.yml`
- CodeMirror in read-only mode with YAML highlighting
- Summary banner: "14 services will be deployed to {target}"
- **Deploy** button (replaces Next) with confirmation modal

### Step 7: Deployment Status (Live)
- Services grouped by deployment phase
- Polls `/deploy/status` every 3 seconds
- Status: Queued → Starting → Running / Failed
- Health: Pending → Healthy / Unhealthy
- On all healthy: green banner with access URLs (Reach Layer UI, Grafana, Jaeger)
- On failure: red status with error message, retry button per service

---

## 4. Deployment Order (Dependency-Aware, 5 Phases)

```
Phase 1 (Data):          Redis, Memgraph
Phase 2 (Observability): Jaeger, Prometheus, Loki, Grafana  (exporters first)
Phase 3 (Telemetry):     OTel Collector  (connects to Phase 2 exporters)
Phase 4 (DPG Backend):   Memory Layer, Trust Layer, Action Gateway, Knowledge Engine
Phase 5 (DPG Core):      Observability Layer, Agent Core
Phase 6 (DPG Frontend):  Reach Layer
```

Each phase waits for the previous phase's services to be healthy before starting. Services within the same phase deploy in parallel.

---

## 5. Helm Chart Structure

### Directory Layout

```
automation/helm/
├── dpg/                        # 7 DPG layer charts
│   ├── agent-core/
│   ├── knowledge-engine/
│   ├── memory-layer/
│   ├── trust-layer/
│   ├── action-gateway/
│   ├── reach-layer/
│   └── observability-layer/
│
└── infra/                      # 7 infrastructure charts
    ├── redis/
    ├── memgraph/
    ├── otel-collector/
    ├── jaeger/
    ├── prometheus/
    ├── loki/
    └── grafana/
```

### DPG Charts — Config-Agnostic Pattern

All 7 DPG charts follow the same pattern. `values.yaml` contains only infrastructure values:

```yaml
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

# Injected at deploy time via --set-file
dpgConfig: ""
domainConfig: ""
```

ConfigMap templates are passthroughs:

```yaml
# templates/configmap-dpg.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-dpg-config
data:
  dpg.yaml: |
{{ .Values.dpgConfig | indent 4 }}
```

```yaml
# templates/configmap-domain.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-domain-config
data:
  domain.yaml: |
{{ .Values.domainConfig | indent 4 }}
```

Agent Core additionally has `secret.yaml` for the Anthropic API key.

Memory Layer chart — Memgraph templates removed (extracted to `infra/memgraph/`). References Memgraph/Redis via environment variables pointing to standalone services.

### Infrastructure Charts — Minimal Pattern

Each infra chart has: `Chart.yaml`, `values.yaml`, `templates/` (deployment, service, optionally configmap/pvc).

Example — Redis:
```yaml
# values.yaml
image:
  repository: redis
  tag: "7-alpine"
service:
  port: 6379
resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits: { cpu: 100m, memory: 128Mi }
password: ""
```

Example — Memgraph:
```yaml
image:
  repository: memgraph/memgraph
  tag: "latest"
service:
  boltPort: 7687
  httpPort: 7444
resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits: { cpu: 500m, memory: 1Gi }
persistence:
  size: 1Gi
  storageClass: ""
password: ""
flags:
  walEnabled: true
  snapshotIntervalSec: 300
```

OTel Collector receives its full config via `--set-file config=...` from `automation/docker/otelcol/otelcol-config.yaml` (reused).

---

## 6. Backend Architecture

### New Endpoints (in `app.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/projects/{slug}/deploy/dpg-values` | GET | Return all 7 DPG framework YAML files |
| `/api/projects/{slug}/deploy/dpg-values/{block}` | PUT | Update a DPG framework YAML |
| `/api/projects/{slug}/deploy/dependencies` | GET | Return dependency service configs |
| `/api/projects/{slug}/deploy/dependencies/{service}` | PUT | Update a dependency service config |
| `/api/projects/{slug}/deploy/resource-presets` | GET | Return 3 preset definitions |
| `/api/projects/{slug}/deploy/resource-presets/{tier}` | POST | Apply preset to 7 DPG layers, return updated values |
| `/api/projects/{slug}/deploy/validate-kubeconfig` | POST | Upload + validate kubeconfig, return cluster info |
| `/api/projects/{slug}/deploy/preview` | POST | Run `helm template` or generate docker-compose, return rendered output |
| `/api/projects/{slug}/deploy/execute` | POST | Trigger deployment |
| `/api/projects/{slug}/deploy/status` | GET | Poll deployment status of all 14 services |

### New Backend Module

```
dev-kit/dev_kit/agent/deployer/
├── __init__.py
├── helm.py          # Helm template rendering, helm install, status polling
├── compose.py       # Docker Compose generation, docker compose up, status polling
├── kubeconfig.py    # Kubeconfig parsing, validation, cluster info extraction
├── presets.py       # Resource preset definitions and application logic
└── dependencies.py  # Dependency service config defaults and management
```

### Deployment State Persistence

Added to `project.json`:
```json
{
  "deployment": {
    "target": "kubernetes",
    "status": "in_progress",
    "resource_preset": "medium",
    "services": {
      "redis": { "status": "running", "namespace": "dpg-redis" },
      "agent_core": { "status": "pending", "namespace": "dpg-agent-core" }
    },
    "started_at": "2026-04-08T10:30:00Z",
    "completed_at": null
  }
}
```

---

## 7. Config Injection — Data Flow

### How configs reach containers

```
dev-kit/configs/{project}/          dev-kit/dpg/
  ├── agent_core.yaml                 ├── agent_core.yaml
  ├── knowledge_engine.yaml           ├── knowledge_engine.yaml
  └── ...                             └── ...
         │  domain config                    │  framework defaults
         ▼                                   ▼
┌─────────────────────────────────────────────────────┐
│              Deploy Backend (deployer/)              │
│  Reads both YAML sets                                │
│  Applies user edits from wizard steps 1 & 2          │
│  Applies resource preset from step 3                 │
│  Injects secrets from step 4                         │
│  Rewrites service discovery URLs for target          │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
       Kubernetes                Docker Compose
               │                      │
  helm install \                Generates docker-compose.yml
    --set-file dpgConfig=...    with volume mounts:
    --set-file domainConfig=...   dpg.yaml → /app/config/dpg.yaml
    --set resources...            domain.yaml → /app/config/domain.yaml
    --set anthropicApiKey=...   + env vars for secrets
               │                      │
               ▼                      ▼
  ConfigMap + Secret            Bind mounts + env
  mounted in pods               in containers
               │                      │
               ▼                      ▼
  /app/config/dpg.yaml         /app/config/dpg.yaml
  /app/config/domain.yaml      /app/config/domain.yaml
```

### Service Discovery URL Rewriting

**Kubernetes:** DPG configs have client endpoint URLs using K8s DNS. Namespace prefix from Step 4 is applied:
```yaml
# agent_core dpg.yaml — rewritten at deploy time
clients:
  knowledge_engine:
    url: http://knowledge-engine.dpg-knowledge-engine.svc.cluster.local:8001
```

**Docker Compose:** URLs rewritten to Docker service names on shared network:
```yaml
clients:
  knowledge_engine:
    url: http://knowledge_engine:8001
```

### Temp Deployment Directory

```
/tmp/dpg-deploy-{project-slug}/
├── docker-compose.yml          # Generated (Docker target)
├── dpg/                        # Copied from dev-kit/dpg/ with user edits
│   └── *.yaml
├── domain/                     # Copied from dev-kit/configs/{project}/
│   └── *.yaml
├── otelcol/
│   └── otelcol-config.yaml
├── prometheus/
│   └── prometheus.yml
└── kubeconfig                  # K8s target only, deleted after deploy
```

---

## 8. Frontend Components

### New View
`deploy` added to App.jsx alongside existing `projects | chat | dashboard | config`.

### Component Tree

```
dev-kit/frontend/src/components/deploy/
├── DeployWizard.jsx        # Wizard container: step state, navigation, back/next
├── StepIndicator.jsx       # Top bar: steps 1-7 with progress dots
├── DpgValuesStep.jsx       # Step 1: Tabbed CodeMirror for 7 dpg/*.yaml
├── DependenciesStep.jsx    # Step 2: Expandable cards for 7 infra services
├── ResourcePresetStep.jsx  # Step 3: Three preset cards + summary table
├── MandatoryInputsStep.jsx # Step 4: Form (API key, passwords, namespace prefix)
├── DeployTargetStep.jsx    # Step 5: Docker/K8s cards + kubeconfig upload/paste
├── PreviewStep.jsx         # Step 6: Read-only rendered helm template / compose
└── DeployStatusStep.jsx    # Step 7: Live status board with health polling
```

### API Client Additions (`api.js`)

```javascript
// Deploy endpoints
getDpgValues(slug)
updateDpgValue(slug, block, yaml)
getDependencies(slug)
updateDependency(slug, service, yaml)
getResourcePresets(slug)
applyResourcePreset(slug, tier)
validateKubeconfig(slug, kubeconfig)
getDeployPreview(slug, options)
executeDeploy(slug, options)
getDeployStatus(slug)
```

### Shared Components (extract before building wizard)

Before building the wizard, extract duplicated patterns into reusable components:

```
dev-kit/frontend/src/
├── components/
│   ├── shared/
│   │   ├── StatusBadge.jsx      # Status pill (complete/draft/pending/stale/running/failed)
│   │   ├── TabBar.jsx           # Tabbed navigation with status dots
│   │   ├── Modal.jsx            # Base modal wrapper (overlay + card)
│   │   └── StatusBanner.jsx     # Colored info/warning/success banners
│   ├── hooks/
│   │   └── useYamlEditor.js     # CodeMirror YAML setup, edit/cancel/save, theme
│   └── constants.js             # BLOCK_LABELS, STATUS_PILL, STATUS_COLORS, STATUS_DOT
```

Existing components (ConfigEditor, YamlPanel, DiffModal, Dashboard) refactored to use these shared pieces. Deploy wizard components consume them directly.

### UI Patterns
- Same Tailwind dark theme as existing config agent UI
- CodeMirror with oneDark theme for all YAML editors
- Collapsible cards for dependency services
- Status pills matching existing convention (green/yellow/red)
- Step indicator similar to PhaseBar pattern

### Reach Layer Docker Note
The Reach Layer Dockerfile does NOT include a Node/React build step. React must be pre-built (`npm run build` in `web-src/`) before Docker image build. Pre-built Docker Hub images (`sanketikahub/dpg-reach-layer:0.1.0`) already include the React bundle. No changes needed for deployment — the wizard deploys pre-built images.

---

## 9. Error Handling

### Deployment Failures
| Scenario | Handling |
|----------|----------|
| Helm install fails for one service | Mark Failed with error. Continue deploying independent services. Skip dependents. Retry button per service. |
| Docker compose partial failure | Per-container status from `docker compose ps`. Failed containers show log snippet. Retry available. |
| Kubeconfig invalid | Reject at Step 5 with specific error (unreachable, auth expired, insufficient RBAC). Block Next. |
| Kubeconfig lacks permissions | Dry-run test (`helm install --dry-run`) during validation. Report missing permissions. |
| API key invalid format | Client-side: non-empty + `sk-ant-` prefix. Actual validity tested when Agent Core starts. |
| Cluster out of resources | Pods Pending with event message. User goes back, selects lower preset, redeploys. |
| KE ingest timeout | 180s startup period. Status shows "Ingesting". Warning if exceeds 5 minutes. |
| Service crashes after healthy | Polling continues. Status updates in real-time. |

### Wizard State Protection
- **Browser refresh during wizard:** State saved to `project.json` on each step. Resumes from last completed step.
- **Refresh during deployment:** Status page resumes polling. Deployment is server-side.
- **Navigate away mid-deploy:** Confirmation modal. Deployment continues server-side.
- **Re-deploy over existing:** Detect via `helm list` / `docker compose ps`. Warning shown. Uses `helm upgrade --install`.

### Validation Gates

| Step | Gate |
|------|------|
| 1. DPG Values | All 7 YAMLs parse as valid YAML |
| 2. Dependencies | All service configs parse as valid YAML |
| 3. Resource Preset | One preset selected |
| 4. Mandatory Inputs | API key non-empty + format valid |
| 5. Deploy Target | Target selected. K8s: kubeconfig validated. |
| 6. Preview | Helm template / compose generation succeeds |

---

## 10. Testing Strategy

### Backend Tests
- `test_deployer_helm.py` — helm command generation, template rendering, status parsing
- `test_deployer_compose.py` — compose file generation, status parsing
- `test_deployer_kubeconfig.py` — kubeconfig parsing, validation, cluster info extraction
- `test_deployer_presets.py` — preset application to DPG layers only, not infra
- `test_deployer_dependencies.py` — default configs, user edits, serialization
- `test_app_deploy_routes.py` — all 10 deploy endpoints

### Frontend Tests
- `DeployWizard.test.jsx` — step navigation, validation gates, state persistence
- `DeployStatusStep.test.jsx` — polling, status transitions, completion detection
- `ResourcePresetStep.test.jsx` — preset selection applies to DPG only
- `DeployTargetStep.test.jsx` — Docker/K8s toggle, kubeconfig upload + paste

### Integration
- Mock `helm` and `docker compose` CLI calls in tests — no real deployments in CI
- Validate generated Helm commands and compose files against expected output
