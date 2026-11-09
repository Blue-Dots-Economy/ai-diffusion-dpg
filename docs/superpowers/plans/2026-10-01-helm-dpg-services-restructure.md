# Plan: restructure DPG Helm charts into `dpg-services/` (one chart per service)

## Context

`automation/helm/dpg/` has 8 copy-pasted charts (identical ConfigMaps, helpers, services; deployments differing by 0–44 lines), which caused drift bugs fixed in Phase 2. A generic single-chart prototype (`dpg-service` + per-block values files) was tried and rejected in favour of **one Helm chart per service under `automation/helm/dpg-services/`**, with the **Reach Layer as a parent chart holding one sub-chart per channel: web, mcp, voice, bridge**. Shared template code lives in a **library chart** so each service chart is small and fixes land once.

Decisions: all 4 Reach channels as sub-charts · library chart for shared code · reset `refactor/helm-generic-dpg-service-chart` to the pushed `feature/local-setup-and-helm-validation` tip (drop the prototype commit) · infra charts stay in `automation/helm/infra/`.

## Target layout

```
automation/helm/
├── dpg-common/                      # library chart (type: library), named templates only
│   └── templates/_{config,deployment,service,secret,pvc,helpers}.tpl
├── dpg-services/
│   ├── trust-layer/  memory-layer/  knowledge-engine/  action-gateway/
│   ├── agent-core/   observability-layer/  dev-kit/
│   │   ├── Chart.yaml               # dependency: dpg-common
│   │   ├── charts/dpg-common -> ../../../dpg-common   (relative symlink)
│   │   ├── values.yaml              # this service's settings + defaults
│   │   └── templates/deployment.yaml, service.yaml, configmap.yaml, [secret.yaml, pvc.yaml]
│   │                                # each a one-line {{ include "dpg-common.<kind>" . }}
│   └── reach-layer/                 # parent chart, release "reach-layer"
│       ├── Chart.yaml               # deps: dpg-common + web, mcp, voice, bridge (condition: <ch>.enabled)
│       ├── values.yaml              # web.enabled: true; mcp/voice/bridge.enabled: false
│       └── charts/{web,mcp,voice,bridge}/  (Chart.yaml, values.yaml, templates/)
└── infra/                           # unchanged
```

## Key design points

- **Names and selectors stay identical** for every existing release (`<release>`, `app: <release>`, `<release>-dpg-config`, `<release>-domain-config`, `<release>-secrets`, PVC names), so existing installs upgrade **in place without pod churn**.
- **Reach web keeps the name `reach-layer`** (Deployment, Service, selector): preserves DNS, dev-kit NodePort 30805 (avoids a port clash during upgrade) and the dev-kit's pod-status prefix match (`app.py:3552`). Other channels: `reach-layer-mcp`, `reach-layer-voice`, `reach-layer-bridge`.
- **Library dependency without a build step:** each chart's `charts/dpg-common` is a relative symlink (Helm follows symlinks; Docker `COPY automation/` keeps it inside the dev-kit image). Fallback if this misbehaves: `file://` dependency + committed `helm dependency build` output.
- **Library templates are driven by per-chart values**, reusing the prototype's verified logic: `dpg.renderConfig` (hostname rewrite), dotted value lookup, ordered `env` list (literal / values lookup / `secretKeyRef`, with `ifSet`, `when`, `required`), `secretEnvFromValues` + `extraSecrets`, `persistence` map → PVCs, `strategy`, `podSecurityContext`, `waitFor` init container, probes, checksum annotations, and **configurable config mount paths** (needed for bridge/voice).
- **Dev-kit value keys keep working** per chart: `openaiApiKey`/`anthropicApiKey`, `extraSecrets.*`, `uploadAuth.*`, `azure.*`, `redis.url`, `memgraph.password`, `resources.*`.
- **Reach config reaches all sub-charts via globals:** `--set-file global.dpgConfig=… global.domainConfig=…` on the parent release (sub-charts cannot see parent top-level values). The library reads `.Values.dpgConfig | default .Values.global.dpgConfig`, so service charts keep top-level `dpgConfig/domainConfig`.
- **Known gap to close in step 5: k8s pod-status reporting is not channel-aware.** `dev-kit/dev_kit/agent/app.py:3547-3563` matches pods to `state.services` entries by name-prefix, and `DEPLOY_PHASES` (`dev-kit/dev_kit/agent/deployer/helm.py:40`) carries a single `"reach_layer"` entry for k8s deploys. Once `voice.enabled`/`mcp.enabled` are wired from `selected_channels`, one `reach-layer` release can produce up to 4 pods (`reach-layer-*`, `reach-layer-mcp-*`, `reach-layer-voice-*`, `reach-layer-bridge-*`) that all match that single entry — each poll overwrites it with whichever pod was processed last, so a crash-looping channel can be masked by a healthy one and `state.overall` becomes nondeterministic. The compose path already avoids this by giving each channel its own service key (`reach_layer_web/voice/mcp/cli`, `app.py:3057-3060`); the k8s path needs the same per-channel breakdown in `state.services` before multi-channel k8s deploys can be trusted.

### Per-channel wiring (from code, fixes the compose mount bugs without app changes)

| Channel | Port | dpg.yaml mounted at | Domain file mounted as | Notes |
|---|---|---|---|---|
| web | 8005 | `/app/config/dpg.yaml` | `/app/config/reach_layer.yaml` + `CONFIG_FOLDER` | as today; `REACH_LAYER_WEB_MODE`, auth, upload-chain env |
| mcp | 8007 | `/app/config/dpg.yaml` | `/app/config/reach_layer.yaml` + `CONFIG_FOLDER` | `sha-646216d` image has the mcp 2.x ImportError; test with a post-#405 tag |
| voice | 8006 | `DPG_CONFIG_PATH=/app/config/dpg.yaml` | `DOMAIN_CONFIG_PATH=/app/config/domain.yaml` | Vobiz/Raya keys as Secret; `PUBLIC_URL`; `NLTK_DATA=/tmp/nltk_data`; optional `/var/recordings` volume |
| bridge | 8008 | `/app/reach_layer/config/dpg.yaml` (where `main.py` looks) | `/app/config/domain.yaml` + `CONFIG_FOLDER` | no auth: ClusterIP only, never exposed |

## Steps (scoped commits on `refactor/helm-generic-dpg-service-chart`)

1. **Reset branch** to `origin/feature/local-setup-and-helm-validation` (`git reset --hard`, local-only commit dropped). The branch currently also carries **uncommitted** changes under `automation/helm/dpg-service/templates/` (plus an untracked `pvc.yaml`) on top of that commit — confirm with the user whether these are disposable prototype edits or need to be stashed/committed elsewhere before the reset, since `git reset --hard` silently discards uncommitted work along with the commit. Capture baseline renders of all 8 old charts with dev-kit-style inputs (keys, uploadAuth, NodePort, resources, waitFor off).
2. **`dpg-common` library chart**: port the prototype helpers/templates into named `dpg-common.*` templates; verify the symlink dependency renders with plain `helm template` (no build step). Commit.
3. **Service charts** (trust-layer, observability-layer, memory-layer, action-gateway, agent-core, knowledge-engine, dev-kit) under `dpg-services/`, each with its own values.yaml. Prove each renders **semantically identical** to its old chart across the baseline cases. Commit.
4. **Reach parent + 4 channel sub-charts**; web identical to the old reach-layer chart; mcp/voice/bridge off by default. Commit.
5. **Dev-kit switch** (`dev-kit/dev_kit/agent/app.py`): DPG chart base `HELM_BASE/"dpg"` → `HELM_BASE/"dpg-services"` in `_run_k8s_deploy` (~3219-3225) and the helm preview (~2754-2785); reach release gets `web.`-prefixed values (`web.service.type/nodePort`, `web.uploadAuth.*`, `web.resources.*`) and `global.dpgConfig/domainConfig`; pass `selected_channels` into `_run_k8s_deploy` (caller ~3030) and set `voice.enabled`/`mcp.enabled` from it (web always on, matching compose; bridge stays off since compose has no bridge channel mapping). Also give the k8s pod-status poller per-channel visibility into `reach_layer` (see the known gap above) — e.g. split the `"reach_layer"` entry in `state.services` into per-enabled-channel keys the way `_run_docker_deploy` already does (`app.py:3057-3060`), so the prefix-match loop at `app.py:3547-3563` can't conflate a failing channel's pod with a healthy one. Update/add tests in `dev-kit/tests/` (`test_app_deploy_routes.py`, `test_deployer_helm.py`). Commit.
6. **Remove `automation/helm/dpg/`** and update `docs/helm-deployment.md` (layout, paths, reach `global.*` flags, enabling channels, per-channel requirements) and `automation/helm/README.md`. Commit.

## Verification

- `helm lint` every chart (reach via its parent); semantic render diff old vs new for all 8 services (script from the prototype run: parse YAML, compare objects by kind/name) — only intended differences allowed, listed in the commit.
- **In-place upgrade on Colima** (current `dpg` namespace): `helm upgrade` each release onto its new chart → **same pod names** for all 8 (no restarts), then the doc's health + chat + telemetry checks.
- Enable channels on Colima: `voice.enabled=true` with dummy Vobiz values → Ready; `bridge.enabled=true` → Ready (validates the dpg.yaml mount fix); `mcp.enabled=true` with a post-#405 image tag → report result.
- Enable **more than one channel at once** (e.g. `voice.enabled=true` + `bridge.enabled=true`) and confirm the dev-kit UI reports per-channel status correctly rather than one channel's pod status masking another's — exercises the per-channel status-poller fix from step 5, which single-channel-at-a-time testing above would not catch.
- Fresh end-to-end run of the updated `docs/helm-deployment.md` (uninstall all, install from scratch, all checks).
- Dev-kit tests: `cd dev-kit && uv run pytest` (full suite), with focus on the deploy-route and helm tests.
- Nothing pushed unless asked.
