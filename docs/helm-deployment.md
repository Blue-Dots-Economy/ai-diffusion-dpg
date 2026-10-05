# Helm Deployment (local Kubernetes)

How to deploy the AI Diffusion DPG stack with the charts in `automation/helm/` onto a local Kubernetes cluster, for any domain under `dev-kit/configs/`. Verified end to end on Colima's k3s (Kubernetes v1.33.3) with the `blue-dots` domain. Any cluster with a default StorageClass should work the same way. The layout matches what the dev-kit deploy wizard does (`dev-kit/dev_kit/agent/app.py`, `_run_k8s_deploy` and `_dpg_helm_values`), so a manual install and a dev-kit install produce the same releases.

For the Docker Compose setup, see [local-setup.md](local-setup.md).

## Chart layout

```
automation/helm/
├── dpg-common/            library chart: the templates every DPG chart shares
├── dpg-services/          one chart per DPG service
│   ├── trust-layer/  memory-layer/  knowledge-engine/  action-gateway/
│   ├── agent-core/   observability-layer/  dev-kit/
│   └── reach-layer/       parent chart, one sub-chart per channel
│       └── charts/web/  charts/mcp/  charts/voice/  charts/bridge/
└── infra/                 redis, memgraph, otel-collector, jaeger, prometheus, loki, grafana
```

Each service chart has its own `Chart.yaml`, `values.yaml` (every setting for that service) and `templates/`, where each template is a one-line include of a `dpg-common` template. The templates themselves, and every key they read, are documented in `automation/helm/dpg-common/templates/_deployment.tpl`. A fix in `dpg-common` applies to every service.

`charts/dpg-common` inside each chart is a symlink to the library, so no `helm dependency build` step is needed. Helm prints `found symbolic link in path ... Contents of linked file included and used` on every command; that is expected.

Conventions the charts rely on:

- **One namespace for everything** (the dev-kit defaults to `dpg`).
- **Release name = chart directory name.** Services find each other by release name (`memory-layer`, `otel-collector`, ...). The Reach Layer is one release, `reach-layer`; its web channel keeps that name, and the other channels are `reach-layer-mcp`, `reach-layer-voice` and `reach-layer-bridge`.
- **Config is injected at install time.** Each DPG chart takes `--set-file dpgConfig=dev-kit/dpg/<block>.yaml` and `--set-file domainConfig=dev-kit/configs/<domain>/<block>.yaml`. The `reach-layer` parent takes the same two files as `global.dpgConfig` / `global.domainConfig`, which hands them to every channel. Nothing domain-specific is baked into the charts.
- The shared YAML addresses blocks by their docker-compose names (`http://memory_layer:8002`, `http://otelcol:4317`). Kubernetes Service names cannot contain underscores, so the charts rewrite those hostnames when they render the ConfigMaps (`dpg-common.serviceHosts`; override per chart with a `serviceHosts` value).

## 1. Prerequisites

| Tool | Verified version |
|---|---|
| A local Kubernetes cluster with a default StorageClass | Colima 0.8.4 (k3s v1.33.3, 4 CPU / 8 GiB) |
| helm | v3.17 |
| kubectl | v1.35 |

The full stack requests about 4.5 GiB of memory, so give the cluster at least 6 GiB. Images are pulled from GHCR (public, no login), about 7 GB for the DPG images.

### Start a cluster and isolate its kubeconfig

Write the local cluster's credentials to a dedicated kubeconfig file and point `KUBECONFIG` at it for every command below. If your default kubeconfig also holds EKS contexts, a stray `helm install` can then never land on a real cluster.

| Cluster | Start it and write `~/.kube/local-dpg.yaml` |
|---|---|
| Colima (tested) | `colima start --kubernetes` (add `--disk <current size>` if Colima says it cannot shrink the disk), then `colima ssh -- sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/local-dpg.yaml` |
| kind (not tested here) | `kind create cluster --name dpg`, then `kind get kubeconfig --name dpg > ~/.kube/local-dpg.yaml` |
| k3d (not tested here) | `k3d cluster create dpg`, then `k3d kubeconfig get dpg > ~/.kube/local-dpg.yaml` |
| minikube (not tested here) | `KUBECONFIG=~/.kube/local-dpg.yaml minikube start --memory 6g` |

```bash
chmod 600 ~/.kube/local-dpg.yaml
export KUBECONFIG=~/.kube/local-dpg.yaml
kubectl get nodes                  # the local node(s), Ready
kubectl get storageclass           # one marked (default)
```

`colima start` also switches your Docker CLI context to `colima`; switch back afterwards with `docker context use <previous>`.

## 2. Choose a domain and its keys

```bash
DOMAIN=blue-dots                   # any folder under dev-kit/configs/ that has the <block>.yaml files
ls dev-kit/configs/$DOMAIN/        # one <block>.yaml per DPG block
```

**LLM key.** Agent Core calls the provider named in `dev-kit/configs/$DOMAIN/agent_core.yaml` (`agent.provider`; `blue-dots` uses `openai`). Export the key for that provider. The install passes every key that is set; the charts leave empty ones out of the Secret.

```bash
grep -m1 -E '^\s+provider:' dev-kit/configs/$DOMAIN/agent_core.yaml
export OPENAI_API_KEY=sk-...       # or ANTHROPIC_API_KEY / GOOGLE_API_KEY
```

A dummy value lets everything start; chat turns then end with `error_type: api_error`.

**Channels.** Agent Core only accepts turns on the channels the domain declares under `channels:` in its `agent_core.yaml` (`blue-dots` declares `bridge`; `mcp` comes from the framework defaults). Step 3 enables the matching Reach sub-charts, and step 4 tests through one of them.

```bash
python3 -c "import yaml,sys; print(list((yaml.safe_load(open(sys.argv[1])) or {}).get('channels', {})))" dev-kit/configs/$DOMAIN/agent_core.yaml
```

**Connector secrets.** Action Gateway refuses to start while any `secret_env` declared in the domain's `action_gateway.yaml` is unset. Keep them in the same per-domain file the Docker Compose setup uses (gitignored); dummy values are enough to boot:

```bash
grep -oE '^\s*secret_env: *[A-Z0-9_]+' dev-kit/configs/$DOMAIN/action_gateway.yaml | awk '{print $2}' | sort -u   # what the domain needs
cp dev-kit/configs/$DOMAIN/secrets.env.example dev-kit/configs/$DOMAIN/secrets.env
# edit dev-kit/configs/$DOMAIN/secrets.env: one KEY=value line per secret_env
```

## 3. Install

Run from the repo root. The order follows the dev-kit's `DEPLOY_PHASES`: storage, then observability infra, then trust, memory, intelligence, core and reach. Kubernetes has no `depends_on`. `agent-core` has an init container that waits for Action Gateway's `/health` (`waitFor` in its values), because it loads its tool list from Action Gateway at startup and exits if a tool is missing. Other blocks retry their dependencies on their own.

```bash
export KUBECONFIG=~/.kube/local-dpg.yaml
NS=dpg
DOMAIN=blue-dots                   # as chosen in step 2

infra() { helm upgrade --install "$1" "automation/helm/infra/$1" -n $NS --create-namespace; }
dpg() {
  local chart=$1 block=${1//-/_}; shift
  helm upgrade --install "$chart" "automation/helm/dpg-services/$chart" -n $NS --create-namespace \
    --set-file dpgConfig=dev-kit/dpg/$block.yaml \
    --set-file domainConfig=dev-kit/configs/$DOMAIN/$block.yaml "$@"
}

# Every LLM key that is exported; empty ones are dropped by the charts.
LLM_KEYS=(--set-string "openaiApiKey=${OPENAI_API_KEY:-}"
          --set-string "anthropicApiKey=${ANTHROPIC_API_KEY:-}"
          --set-string "googleApiKey=${GOOGLE_API_KEY:-}")

# One extraSecrets.<KEY> per line of the domain's secrets.env.
CONNECTOR_SECRETS=()
while IFS='=' read -r key value; do
  CONNECTOR_SECRETS+=(--set-string "extraSecrets.$key=$value")
done < <(grep -E '^[A-Z0-9_]+=' dev-kit/configs/$DOMAIN/secrets.env)

for c in redis memgraph otel-collector jaeger prometheus loki grafana; do infra $c; done
dpg trust-layer
dpg memory-layer
dpg knowledge-engine
dpg action-gateway "${CONNECTOR_SECRETS[@]}"
dpg agent-core     "${LLM_KEYS[@]}"
dpg observability-layer

# Reach Layer: one release for all channels; config goes in through global.*.
# The framework defaults turn on Google sign-in for the web channel, so it needs
# a session-signing Secret and a Google OAuth client ID (a placeholder is enough
# to start it; signing in needs a real one). Only web runs by default; add
# --set <channel>.enabled=true for the channels the domain declares (step 2).
kubectl -n $NS create secret generic reach-layer-auth \
  --from-literal=session-secret=$(openssl rand -hex 32) --dry-run=client -o yaml | kubectl apply -f -
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-placeholder.apps.googleusercontent.com}
REACH_CHANNELS=(--set bridge.enabled=true)   # blue-dots declares the bridge channel

helm upgrade --install reach-layer automation/helm/dpg-services/reach-layer -n $NS --create-namespace \
  --set-file global.dpgConfig=dev-kit/dpg/reach_layer.yaml \
  --set-file global.domainConfig=dev-kit/configs/$DOMAIN/reach_layer.yaml \
  --set web.auth.enabled=true --set-string web.auth.googleClientId=$GOOGLE_CLIENT_ID \
  --set web.auth.sessionSecretName=reach-layer-auth \
  "${REACH_CHANNELS[@]}"

# Optional: the Configuration Agent UI (needs at least one LLM key)
helm upgrade --install dev-kit automation/helm/dpg-services/dev-kit -n $NS "${LLM_KEYS[@]}"

kubectl -n $NS wait --for=condition=Ready pod --all --timeout=420s
```

`--set-string` treats commas and backslashes in a value as syntax; escape them (`\,`) if a key contains one.

On a cold cluster, image pulls dominate the time taken. With images already cached, all 16 pods (15 plus the bridge channel) were Ready in about a minute.

## 4. Verify

```bash
kubectl -n dpg get pods          # 16 pods with the bridge channel, all 1/1 Running, 0 restarts

# Block health endpoints, from inside the cluster
for p in agent-core:8000 knowledge-engine:8001 memory-layer:8002 trust-layer:8003 \
         observability-layer:8004 action-gateway:9999 reach-layer:8005; do
  printf '%-26s ' $p
  kubectl -n dpg exec deploy/agent-core -c agent-core -- python3 -c \
    "import urllib.request;print(urllib.request.urlopen('http://$p/health',timeout=5).read().decode())"
done
```

Action Gateway's `/health` lists the domain's tools as adapters, each `true`.

**Functional check.** Send turns through a channel the domain declares (step 2). For `blue-dots` that is the bridge, an OpenAI chat-completions endpoint that is only reachable inside the cluster:

```bash
bridge() { kubectl -n dpg exec deploy/agent-core -c agent-core -- python3 -c "
import json, sys, urllib.request
body = {'model': 'agent-core', 'stream': False, 'messages': [{'role': 'user', 'content': sys.argv[1]}],
        'metadata': {'caller_phone': '910000000001', 'call_id': 'smoke-1'}}
req = urllib.request.Request('http://reach-layer-bridge:8008/v1/chat/completions', json.dumps(body).encode(),
                             {'content-type': 'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=60))['choices'][0]['message']['content'])" "$1"; }
bridge "Hello"                        # the domain's greeting (scripted, no LLM)
```

To exercise the LLM path, send turns straight to Agent Core with the domain's channel name; this is what every Reach channel calls:

```bash
turn() { kubectl -n dpg exec deploy/agent-core -c agent-core -- python3 -c "
import json, sys, urllib.request
req = urllib.request.Request('http://agent-core:8000/process_turn',
    json.dumps({'session_id': 'smoke-2', 'user_id': 'smoke', 'channel': 'bridge', 'user_message': sys.argv[1]}).encode(),
    {'content-type': 'application/json'})
d = json.load(urllib.request.urlopen(req, timeout=60)); print(d['error_type'], d['response_text'][:80])" "$1"; }
turn "Hello"
turn "What jobs are available?"       # full pipeline incl. LLM

kubectl -n dpg logs deploy/agent-core -c agent-core | grep -E 'HTTP Request: POST' | tail -8
```

- The first turn is the domain's scripted greeting. Later turns call the LLM: with a dummy key they return `api_error` (401 from the provider); with a real key, an answer.
- Agent Core's log should show calls to `http://trust-layer:8003`, `http://memory-layer:8002` (context bundle, audit, write) and `http://observability-layer:8004`, all by their Kubernetes Service names.
- An undeclared channel fails the turn with `Unsupported channel: <name>` in Agent Core's log.
- With sign-in enabled, the web channel's `/chat` returns `401` until a browser session signs in through Google; `/health` still answers. Use port-forwards to reach the web UI (`svc/reach-layer 8005`), Grafana (`svc/grafana 3000`), Jaeger (`svc/jaeger 16686`) and the dev-kit (`svc/dev-kit 8080`); NodePorts are not reachable from the host on every local cluster (Colima, for one).

**Telemetry.** Jaeger's `/api/services` should list `agent_core`, `trust_layer`, `memory_layer`, `knowledge_engine`, `action_gateway`, `observability_layer` and `reach_layer.web` after a turn. Prometheus should show the target `otel-collector:8889` as `up`.

## 5. Reach Layer channels

The `reach-layer` release runs one Deployment per enabled channel. Each channel also needs `channels.<channel>.enabled: true` in the domain's `reach_layer.yaml`, otherwise the service refuses to start.

| Channel | Enable with | Resources | Needs |
|---|---|---|---|
| web | on by default (`web.enabled`) | `reach-layer` :8005 | `web.auth.*` while the config enables Google sign-in (the framework default); `web.webMode=routing_only` for voice-only deployments |
| mcp | `--set mcp.enabled=true` | `reach-layer-mcp` :8007 | nothing extra (images from before #405 fail with an `mcp` ImportError) |
| voice | `--set voice.enabled=true` | `reach-layer-voice` :8006 | `voice.vobiz.authId`, `voice.vobiz.authToken`, `voice.vobiz.fromNumber`, `voice.rayaApiKey`, `voice.publicUrl`; Vobiz must reach `publicUrl`, so production needs an Ingress (not part of these charts) |
| bridge | `--set bridge.enabled=true` | `reach-layer-bridge` :8008 | no authentication by design: keep it ClusterIP, never expose it |

Channels are switched on through `REACH_CHANNELS` in step 3. Every `helm upgrade` keeps only the values passed on that run, so list every channel you want each time and keep the `web.auth.*` flags: leaving them out turns sign-in off (the web channel then exits) and leaving out a channel removes it. For example, to add voice to the `blue-dots` setup from step 3:

```bash
REACH_CHANNELS=(--set bridge.enabled=true
                --set voice.enabled=true
                --set-string voice.vobiz.authId=... --set-string voice.vobiz.authToken=...
                --set-string voice.vobiz.fromNumber=... --set-string voice.rayaApiKey=...
                --set-string voice.publicUrl=https://...)

# The reach-layer command from step 3, unchanged:
helm upgrade --install reach-layer automation/helm/dpg-services/reach-layer -n $NS --create-namespace \
  --set-file global.dpgConfig=dev-kit/dpg/reach_layer.yaml \
  --set-file global.domainConfig=dev-kit/configs/$DOMAIN/reach_layer.yaml \
  --set web.auth.enabled=true --set-string web.auth.googleClientId=$GOOGLE_CLIENT_ID \
  --set web.auth.sessionSecretName=reach-layer-auth \
  "${REACH_CHANNELS[@]}"
kubectl -n $NS rollout status deploy/reach-layer-voice
```

`vobiz.fromNumber` takes digits only. Voice starts with placeholder values; placing calls needs real Vobiz credentials and a `publicUrl` Vobiz can reach.

The dev-kit enables `voice` and `mcp` from the channels selected in the wizard (web always runs; without web selected it runs in `routing_only` mode), as the compose deploy does. `bridge` is not a wizard channel; the dev-kit enables it when the domain's `agent_core.yaml` declares a `bridge` channel.

All four channels were verified Ready on Colima with `sha-28f0517`, and a chat completion through the bridge returned the domain's greeting. Voice was started with placeholder Vobiz values; no call was placed.

## 6. Upgrade

```bash
# Re-run the same install commands; helm upgrade --install is idempotent.
dpg agent-core "${LLM_KEYS[@]}"   # always re-pass secrets (or use --reuse-values)
```

- **`kubectl wait --all` on a re-run** can print `Error from server (NotFound)` for a pod that is being replaced (for example a channel you just switched off); run it again once the rollout settles.
- **Config changes roll pods automatically.** Every chart that renders a ConfigMap carries a `checksum/<configmap>` pod annotation, so editing `dev-kit/configs/<domain>/*.yaml` and upgrading restarts the affected block. The services only read config at startup.
- **`memgraph` and `knowledge-engine` use `strategy: Recreate`.** Both are single-writer stores on ReadWriteOnce PVCs; a rolling update would start a second pod against the same volume, and memgraph exits when you do that.
- **Switching domain** in place: change `DOMAIN` and the keys, then re-run step 3. Every DPG chart's config changes, so every block restarts.
- **Moving from the old `automation/helm/dpg/` charts:** upgrade each release onto its `dpg-services/` chart with the same release name. Verified on Colima: apart from config content, the rendered objects are the same, so a block's pods restart only if its config files changed. For `reach-layer`, switch the config flags to `global.dpgConfig` / `global.domainConfig` and prefix web settings with `web.`.

## 7. Uninstall and cleanup

```bash
for r in $(helm list -n dpg --short); do helm uninstall $r -n dpg --wait; done
kubectl get pvc,pv -n dpg          # PVCs gone; Released PVs disappear within a few seconds
kubectl delete ns dpg              # optional
```

`kubectl delete ns dpg` also removes the `reach-layer-auth` Secret from step 3, which is not part of any release.

Then stop or delete the cluster: `colima stop`, `kind delete cluster --name dpg`, `k3d cluster delete dpg`, or `minikube stop`.

**Warning:** `helm uninstall` **deletes the PVCs** (`memgraph-pvc`, `knowledge-engine-chroma-data`, `knowledge-engine-kb-data`) because the charts template them directly. With a `Delete` reclaim policy (the default for `local-path` and most local provisioners) the data is gone. This differs from compose, where `down` keeps named volumes.

## Key values

Every DPG chart (and each Reach channel sub-chart) supports the keys documented in `dpg-common/templates/_deployment.tpl`: `image.*`, `service.*`, `resources`, `healthCheck`, `probes.path`, `env`, `secretEnvFromValues`, `extraSecrets`, `persistence`, `strategy`, `podSecurityContext`, `waitFor`, `configFile`, `config.*` and `serviceHosts`. Service-specific values:

| Chart | Value | Default | Notes |
|---|---|---|---|
| all DPG | `image.repository` / `image.tag` | `ghcr.io/blue-dots-economy/ai-diffusion-dpg/<svc>` / `sha-28f0517` | Images must be built from code that understands the dev-kit configs: images before `sha-28f0517` (#385) reject the current configs at startup. `docker-compose.dev.yml` still defaults to the older `sha-646216d` |
| all DPG | `resources.*`, `healthCheck.*` | per chart | The dev-kit overrides resources from its presets |
| agent-core | `openaiApiKey`, `anthropicApiKey`, `googleApiKey`, `geminiApiKey`, `ollamaApiKey`, `ollamaEndpoint` | empty | Set the key(s) for the domain's provider(s); unset keys are omitted from the Secret |
| agent-core | `logLevel` | `INFO` | |
| agent-core | `waitFor` / `waitImage` | `[http://action-gateway:9999/health]` / `busybox:1.36` | Health URLs the init container waits on; `[]` disables it |
| action-gateway | `extraSecrets.<ENV_VAR>` | `{}` | One entry per connector `secret_env` in the domain's `action_gateway.yaml`. Action Gateway will not start if one is missing |
| memory-layer | `redis.url`, `memgraph.uri` / `user` / `password` | in-cluster `redis` / `memgraph`, no auth | |
| knowledge-engine | `podSecurityContext.fsGroup` | `101` | GID of the non-root user in the current image; makes PVCs writable on EBS |
| knowledge-engine | `persistence.chroma-data.*`, `persistence.kb-data.*` | 5Gi / 10Gi, default StorageClass | `size`, `storageClass`, `mountPath` per volume |
| knowledge-engine | `uploadAuth.*`, `azure.*` | empty | Upload chain keys and Azure Blob ingestion |
| reach-layer | `global.dpgConfig` / `global.domainConfig` | empty | Config for every channel |
| reach-layer | `web.enabled` / `mcp.enabled` / `voice.enabled` / `bridge.enabled` | `true` / `false` / `false` / `false` | See "Reach Layer channels" |
| reach-layer | `web.webMode` | `full` | `routing_only` for voice-only deployments |
| reach-layer | `web.service.type` / `web.service.nodePort` | `ClusterIP` | The dev-kit sets `NodePort` / `30805` |
| reach-layer | `web.uploadAuth.keInternalUrl` | `http://knowledge-engine:8001` | |
| reach-layer | `web.auth.enabled` / `googleClientId` / `sessionSecretName` / `sessionSecretKey` | disabled / empty / empty / `session-secret` | Must be set while the Reach config enables sign-in (the framework default); the web channel exits at startup without them |
| reach-layer | `voice.vobiz.*`, `voice.rayaApiKey`, `voice.publicUrl` | empty | Required when voice is enabled |
| dev-kit | `openaiApiKey` / `anthropicApiKey` / `googleApiKey` | empty | At least one is required; the chart refuses to render without one |
| memgraph (infra) | `image.tag` | `3.13.1` | Pinned; matches compose |

## Differences from docker-compose

| Area | docker-compose (`docker-compose.dev.yml`) | Helm charts |
|---|---|---|
| Reach channels | `reach_layer_web/voice/mcp/bridge` services | One `reach-layer` release with a sub-chart per channel; only web on by default |
| Voice exposure (+ `ngrok`) | Service on :8006, ngrok tunnel | ClusterIP only; production needs an Ingress for Vobiz webhooks |
| mcp / bridge config | compose mounts the domain file as `domain.yaml` (mcp expects `reach_layer.yaml`) and `dpg.yaml` where bridge does not look, so both fail at startup in compose | Each mounted where the channel looks: mcp gets `reach_layer.yaml`, bridge gets `dpg.yaml` in `/app/reach_layer/config/` |
| `reach_layer_cli` | Commented out | No chart |
| otel collector name | `otelcol` | `otel-collector` (handled by the hostname rewrite, Prometheus and Grafana config) |
| Host exposure | 8005, 8006, 8080, 3000, 9090, 3100, 16686, 4317/4318/8889 published | All `ClusterIP` (reach-layer web `NodePort` only via dev-kit). No Ingress |
| Startup ordering | `depends_on: service_healthy` | `agent-core` init container waits for `action-gateway` `/health` (`waitFor`); other blocks retry. The dev-kit installs in phases |
| Healthchecks | Python `urllib` `/health`; `mgconsole` for memgraph; `redis-cli ping` | HTTP `/health` probes; TCP probes for memgraph and redis; none for the otel collector |
| Resources | Limits 256M for every DPG block, 0.1 to 0.5 CPU | Requests 512Mi / limits 1Gi (KE 1Gi / 2Gi), 500m to 1000m CPU |
| KE volumes | `chroma_data`, `kb_data` named volumes, plus a read-only bind of `knowledge_engine/data` | PVCs for Chroma and KB uploads; no source-docs mount (upload through the dev-kit instead) |
| KE file permissions | `user: root` | `fsGroup: 101` |
| dev-kit | docker.sock + `dev-kit/configs` bind + keystore volume | Runs the UI only: no docker socket, no persisted configs or keystore, so it cannot deploy from inside the cluster |
| Data on teardown | `down` keeps volumes | `helm uninstall` deletes PVCs |
| Connector secrets | `dev-kit/configs/<domain>/secrets.env` via `env_file` | The same file, passed as `extraSecrets.<KEY>` (step 3) |
| Secrets | Env vars from the shell / `.env` | LLM, connector and Vobiz/Raya keys in Secrets. Memgraph password, Azure key and upload-chain keys are **still plain env values** in the pod spec |
