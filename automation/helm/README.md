# Helm Charts

Kubernetes charts for the DPG stack. Full guide, including verification, Reach channels, upgrade/uninstall behaviour, key values and the differences from docker-compose: [docs/helm-deployment.md](../../docs/helm-deployment.md).

```
dpg-common/     library chart: templates shared by every DPG chart
dpg-services/   trust-layer  memory-layer  knowledge-engine  action-gateway
                agent-core  observability-layer  dev-kit
                reach-layer (parent) -> charts/web  charts/mcp  charts/voice  charts/bridge
infra/          redis  memgraph  otel-collector  jaeger  prometheus  loki  grafana
```

## Conventions

- All releases go in **one namespace** (the dev-kit defaults to `dpg`), and each **release name is the chart directory name**. Services reach each other by release name (`memory-layer`, `otel-collector`, ...). The Reach web channel keeps the name `reach-layer`; other channels are `reach-layer-<channel>`.
- Each service chart's templates are one-line includes of `dpg-common` templates; all settings live in the chart's `values.yaml`. The supported keys are documented in `dpg-common/templates/_deployment.tpl`.
- `charts/dpg-common` in each chart is a symlink to the library, so no `helm dependency build` step is needed (Helm prints a "found symbolic link" notice; that is expected).
- DPG charts receive their config at install time: `--set-file dpgConfig=dev-kit/dpg/<block>.yaml` and `--set-file domainConfig=dev-kit/configs/<domain>/<block>.yaml`; the `reach-layer` parent takes them as `global.dpgConfig` / `global.domainConfig`. Compose hostnames inside that YAML (`memory_layer`, `otelcol`) are rewritten to release names when the ConfigMaps render.
- Images: `ghcr.io/blue-dots-economy/ai-diffusion-dpg/<service>`, default tag `sha-28f0517` (main at #385). Earlier images reject the current dev-kit configs at startup.
- Secrets are passed with `--set` at install time and never committed: `agent-core` `openaiApiKey` / `anthropicApiKey` / `googleApiKey`, `action-gateway` `extraSecrets.<ENV_VAR>` for each connector `secret_env`, `dev-kit` at least one LLM key, `reach-layer` `voice.*` when voice is enabled.

The dev-kit deploy wizard (`dev-kit/dev_kit/agent/app.py`, `_run_k8s_deploy` / `_dpg_helm_values`) follows these same conventions.

## Quick install

From the repo root, against a local cluster:

```bash
NS=dpg DOMAIN=blue-dots
infra() { helm upgrade --install "$1" "automation/helm/infra/$1" -n $NS --create-namespace; }
dpg() {
  local chart=$1 block=${1//-/_}; shift
  helm upgrade --install "$chart" "automation/helm/dpg-services/$chart" -n $NS --create-namespace \
    --set-file dpgConfig=dev-kit/dpg/$block.yaml \
    --set-file domainConfig=dev-kit/configs/$DOMAIN/$block.yaml "$@"
}

for c in redis memgraph otel-collector jaeger prometheus loki grafana; do infra $c; done
dpg trust-layer
dpg memory-layer
dpg knowledge-engine
dpg action-gateway --set-string extraSecrets.<KEY>=...     # one per connector secret_env
dpg agent-core     --set-string openaiApiKey=$OPENAI_API_KEY
dpg observability-layer
kubectl -n $NS create secret generic reach-layer-auth --from-literal=session-secret=$(openssl rand -hex 32)
helm upgrade --install reach-layer automation/helm/dpg-services/reach-layer -n $NS \
  --set-file global.dpgConfig=dev-kit/dpg/reach_layer.yaml \
  --set-file global.domainConfig=dev-kit/configs/$DOMAIN/reach_layer.yaml \
  --set web.auth.enabled=true --set-string web.auth.googleClientId=<client-id> \
  --set web.auth.sessionSecretName=reach-layer-auth \
  --set bridge.enabled=true                                # channels the domain declares

kubectl -n $NS wait --for=condition=Ready pod --all --timeout=420s
kubectl -n $NS port-forward svc/reach-layer 8005:8005     # web UI at http://localhost:8005 (Google sign-in)
```

## Teardown

```bash
for r in $(helm list -n dpg --short); do helm uninstall $r -n dpg --wait; done
```

`helm uninstall` deletes the Memgraph and Knowledge Engine PVCs and the data in them.

## Not covered by these charts

ngrok and Ingress (the voice channel needs a public HTTPS endpoint in production). See the differences table in [docs/helm-deployment.md](../../docs/helm-deployment.md#differences-from-docker-compose).
