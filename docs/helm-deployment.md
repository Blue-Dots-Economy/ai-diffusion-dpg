# Helm Deployment (local Kubernetes)

How to deploy the AI Diffusion DPG stack with the charts in `automation/helm/` onto a local Kubernetes cluster. This was verified end to end on Colima's k3s (Kubernetes v1.33.3). The layout matches what the dev-kit deploy wizard does (`dev-kit/dev_kit/agent/app.py`, `_run_k8s_deploy`), so a manual install and a dev-kit install produce the same releases.

For the Docker Compose setup, see [local-setup.md](local-setup.md).

## Chart layout

15 independent charts, no umbrella chart and no dependencies between them:

| Group | Charts |
|---|---|
| `automation/helm/infra/` | `redis`, `memgraph`, `otel-collector`, `jaeger`, `prometheus`, `loki`, `grafana` |
| `automation/helm/dpg/` | `trust-layer`, `memory-layer`, `knowledge-engine`, `action-gateway`, `agent-core`, `reach-layer`, `observability-layer`, `dev-kit` |

Conventions the charts rely on:

- **One namespace for everything** (the dev-kit defaults to `dpg`).
- **Release name = chart directory name.** Services find each other by release name (`memory-layer`, `otel-collector`, ...).
- **Config is injected at install time.** Each DPG chart takes `--set-file dpgConfig=dev-kit/dpg/<block>.yaml` and `--set-file domainConfig=dev-kit/configs/<domain>/<block>.yaml`. Nothing domain-specific is baked into the charts.
- The shared YAML addresses blocks by their docker-compose names (`http://memory_layer:8002`, `http://otelcol:4317`). Kubernetes Service names cannot contain underscores, so each DPG chart rewrites those hostnames using `serviceHosts` in its `values.yaml` when it renders the ConfigMaps.

## 1. Prerequisites

| Tool | Verified version |
|---|---|
| Colima (k3s) | 0.8.4, k3s v1.33.3, 4 CPU / 8 GiB |
| helm | v3.17 |
| kubectl | v1.35 |

Any local cluster with a default StorageClass works (kind, k3d, minikube). Colima's k3s ships `local-path`.

The full stack requests about 4.5 GiB of memory. Images are pulled from GHCR (public, no login), about 7 GB for the DPG images.

### Start the cluster and isolate the kubeconfig

```bash
colima start --kubernetes          # add --disk <current size> if colima complains it cannot shrink the disk
colima ssh -- sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/colima-k3s.yaml
chmod 600 ~/.kube/colima-k3s.yaml
export KUBECONFIG=~/.kube/colima-k3s.yaml
kubectl get nodes                  # should show the single "colima" node, Ready
```

Using a dedicated `KUBECONFIG` file is deliberate. If your default kubeconfig also has EKS contexts, a stray `helm install` can never land on a real cluster. `colima start` also switches your Docker CLI context to `colima`; switch back afterwards with `docker context use <previous>`.

## 2. Install

Run from the repo root. The order follows the dev-kit's `DEPLOY_PHASES`: storage, then observability infra, then trust, memory, intelligence, core and reach. Kubernetes has no `depends_on`. `agent-core` has an init container that waits for Action Gateway's `/health` (`waitFor` in its values), because it loads its tool list from Action Gateway at startup and exits if a tool is missing. Other blocks retry their dependencies on their own.

```bash
export KUBECONFIG=~/.kube/colima-k3s.yaml
NS=dpg
DOMAIN=kkb
OPENAI_API_KEY=sk-...        # a dummy value lets everything start; chat turns then return api_error
ONEST_API_KEY=...            # kkb onest_market_lookup connector; a dummy is fine locally

infra() { helm upgrade --install "$1" "automation/helm/infra/$1" -n $NS --create-namespace; }
dpg() {
  local chart=$1 block=${1//-/_}; shift
  helm upgrade --install "$chart" "automation/helm/dpg/$chart" -n $NS --create-namespace \
    --set-file dpgConfig=dev-kit/dpg/$block.yaml \
    --set-file domainConfig=dev-kit/configs/$DOMAIN/$block.yaml "$@"
}

for c in redis memgraph otel-collector jaeger prometheus loki grafana; do infra $c; done
dpg trust-layer
dpg memory-layer
dpg knowledge-engine
dpg action-gateway --set extraSecrets.ONEST_API_KEY=$ONEST_API_KEY
dpg agent-core     --set openaiApiKey=$OPENAI_API_KEY
dpg reach-layer
dpg observability-layer

# Optional: the Configuration Agent UI (needs at least one LLM key)
helm upgrade --install dev-kit automation/helm/dpg/dev-kit -n $NS --set openaiApiKey=$OPENAI_API_KEY

kubectl -n $NS wait --for=condition=Ready pod --all --timeout=420s
```

On a cold cluster, image pulls dominate the time taken. With images already cached, all 15 pods were Ready in under a minute.

## 3. Verify

```bash
kubectl -n dpg get pods          # 15 pods, all 1/1 Running, 0 restarts

# Block health endpoints, from inside the cluster
for p in agent-core:8000 knowledge-engine:8001 memory-layer:8002 trust-layer:8003 \
         observability-layer:8004 action-gateway:9999 reach-layer:8005; do
  printf '%-26s ' $p
  kubectl -n dpg exec deploy/agent-core -- python3 -c \
    "import urllib.request;print(urllib.request.urlopen('http://$p/health',timeout=5).read().decode())"
done
```

**Functional check.** This is the same chat turn used for compose:

```bash
kubectl -n dpg port-forward svc/reach-layer 8005:8005 &

chat() { curl -s -X POST localhost:8005/chat -H 'content-type: application/json' \
  -d "{\"session_id\":\"smoke-1\",\"user_id\":\"smoke\",\"message\":\"$1\"}"; echo; }
chat "Hello"                          # consent prompt (scripted)
chat "haan"                           # consent accepted (scripted)
chat "Electrician jobs in Pune?"      # full pipeline incl. LLM

kubectl -n dpg logs deploy/agent-core | grep -oE '\[STEP [0-9a-z]+\][^(]{0,60}' | tail -20
```

- The step trace should show Memory (`http://memory-layer:8002`), Trust, NLU, routing, prompt assembly and the LLM call, followed by async audit, memory write and observability emit.
- With a dummy OpenAI key the third turn returns `error_type: api_error` (OpenAI 401). With a real key it returns an answer.
- The web UI is at http://localhost:8005 while the port-forward is running. Colima does not expose NodePorts on the host, so use port-forwards for Grafana (`svc/grafana 3000`), Jaeger (`svc/jaeger 16686`) and the dev-kit (`svc/dev-kit 8080`).

**Telemetry.** Jaeger's `/api/services` should list `agent_core`, `trust_layer`, `memory_layer`, `knowledge_engine`, `action_gateway` and `reach_layer.web` after a turn. Prometheus should show the target `otel-collector:8889` as `up`.

## 4. Upgrade

```bash
# Re-run the same install commands; helm upgrade --install is idempotent.
dpg agent-core --set openaiApiKey=$OPENAI_API_KEY   # always re-pass secrets (or use --reuse-values)
```

- **Config changes roll pods automatically.** Every chart that renders a ConfigMap carries a `checksum/<configmap>` pod annotation, so editing `dev-kit/configs/<domain>/*.yaml` and upgrading restarts the affected block. The services only read config at startup.
- **`memgraph` and `knowledge-engine` use `strategy: Recreate`.** Both are single-writer stores on ReadWriteOnce PVCs; a rolling update would start a second pod against the same volume, and memgraph exits when you do that.

## 5. Uninstall and cleanup

```bash
for r in $(helm list -n dpg --short); do helm uninstall $r -n dpg --wait; done
kubectl get pvc,pv -n dpg          # expect nothing left
kubectl delete ns dpg              # optional
colima stop                        # or: colima stop && colima start (without --kubernetes) to drop k3s
```

**Warning:** `helm uninstall` **deletes the PVCs** (`memgraph-pvc`, `knowledge-engine-chroma-data`, `knowledge-engine-kb-data`) because the charts template them directly. With `local-path` (reclaim policy `Delete`) the data is gone. This differs from compose, where `down` keeps named volumes.

## Key values

| Chart | Value | Default | Notes |
|---|---|---|---|
| all DPG | `image.repository` / `image.tag` | `ghcr.io/blue-dots-economy/ai-diffusion-dpg/<svc>` / `sha-646216d` | Same default tag as `docker-compose.dev.yml` |
| all DPG | `serviceHosts` | compose name → release name | Use FQDNs (`memory-layer.<ns>.svc.cluster.local`) if releases live in different namespaces |
| all DPG | `resources.*`, `healthCheck.*` | per chart | The dev-kit overrides resources from its presets |
| agent-core | `openaiApiKey`, `anthropicApiKey`, `googleApiKey`, `geminiApiKey`, `ollamaApiKey`, `ollamaEndpoint` | empty | Set the key(s) for the domain's provider(s); unset keys are omitted from the Secret |
| agent-core | `logLevel` | `INFO` | |
| agent-core | `waitFor` / `waitImage` | `[http://action-gateway:9999/health]` / `busybox:1.36` | Health URLs the init container waits on; `[]` disables it |
| action-gateway | `extraSecrets.<ENV_VAR>` | `{}` | One entry per connector `secret_env` (kkb: `ONEST_API_KEY`). Action Gateway will not start if one is missing |
| knowledge-engine | `podSecurityContext.fsGroup` | `101` | GID of the non-root user in the current image; makes PVCs writable on EBS |
| knowledge-engine | `storage.chromadb.*`, `storage.kbData.*` | 5Gi / 10Gi, default StorageClass | |
| reach-layer | `webMode` | `full` | `routing_only` for voice-only deployments |
| reach-layer | `service.type` / `service.nodePort` | `ClusterIP` | The dev-kit sets `NodePort` / `30805` |
| reach-layer | `uploadAuth.keInternalUrl` | `http://knowledge-engine:8001` | |
| reach-layer | `auth.*` | disabled | Google sign-in; needs a Secret holding `session-secret` |
| dev-kit | `openaiApiKey` / `anthropicApiKey` / `googleApiKey` | empty | At least one is required; the chart refuses to render without one |
| memgraph | `image.tag` | `3.13.1` | Pinned; matches compose |

## Differences from docker-compose

| Area | docker-compose (`docker-compose.dev.yml`) | Helm charts |
|---|---|---|
| Voice channel `reach_layer_voice` (+ `ngrok`) | Service on :8006, ngrok tunnel | **No chart.** Needs a public HTTPS endpoint (Ingress) and Vobiz credentials |
| `reach_layer_mcp`, `reach_layer_bridge` | Services (internal only) | **No chart.** Both images currently crash at startup anyway (see local-setup.md) |
| `reach_layer_cli` | Commented out | No chart |
| otel collector name | `otelcol` | `otel-collector` (handled by `serviceHosts`, Prometheus and Grafana config) |
| Host exposure | 8005, 8006, 8080, 3000, 9090, 3100, 16686, 4317/4318/8889 published | All `ClusterIP` (reach-layer `NodePort` only via dev-kit). No Ingress |
| Startup ordering | `depends_on: service_healthy` | `agent-core` init container waits for `action-gateway` `/health` (`waitFor`); other blocks retry. The dev-kit installs in phases |
| Healthchecks | Python `urllib` `/health`; `mgconsole` for memgraph; `redis-cli ping` | HTTP `/health` probes; TCP probes for memgraph and redis; none for the otel collector |
| Resources | Limits 256M for every DPG block, 0.1 to 0.5 CPU | Requests 512Mi / limits 1Gi (KE 1Gi / 2Gi), 500m to 1000m CPU |
| KE volumes | `chroma_data`, `kb_data` named volumes, plus a read-only bind of `knowledge_engine/data` | PVCs for Chroma and KB uploads; no source-docs mount (upload through the dev-kit instead) |
| KE file permissions | `user: root` | `fsGroup: 101` |
| dev-kit | docker.sock + `dev-kit/configs` bind + keystore volume | Runs the UI only: no docker socket, no persisted configs or keystore, so it cannot deploy from inside the cluster |
| Data on teardown | `down` keeps volumes | `helm uninstall` deletes PVCs |
| Secrets | Env vars from the shell / `.env` | LLM keys and connector keys in Secrets. Memgraph password, Azure key and upload-chain keys are **still plain env values** in the pod spec |
