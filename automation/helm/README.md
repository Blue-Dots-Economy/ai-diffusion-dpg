# Helm Charts

Kubernetes charts for the DPG stack. Full guide, including verification, upgrade/uninstall behaviour, key values and the differences from docker-compose: [docs/helm-deployment.md](../../docs/helm-deployment.md).

```
infra/  redis  memgraph  otel-collector  jaeger  prometheus  loki  grafana
dpg/    trust-layer  memory-layer  knowledge-engine  action-gateway
        agent-core  reach-layer  observability-layer  dev-kit
```

## Conventions

- All releases go in **one namespace** (the dev-kit defaults to `dpg`), and each **release name is the chart directory name**. Services reach each other by release name (`memory-layer`, `otel-collector`, ...).
- DPG charts receive their config at install time: `--set-file dpgConfig=dev-kit/dpg/<block>.yaml` and `--set-file domainConfig=dev-kit/configs/<domain>/<block>.yaml`. Compose hostnames inside that YAML (`memory_layer`, `otelcol`) are rewritten to release names via `serviceHosts` in each chart's `values.yaml`.
- Images: `ghcr.io/blue-dots-economy/ai-diffusion-dpg/<service>`, default tag `sha-646216d` (same as `automation/docker/docker-compose.dev.yml`).
- Secrets are passed with `--set` at install time and never committed: `agent-core` `openaiApiKey` / `anthropicApiKey` / `googleApiKey`, `action-gateway` `extraSecrets.<ENV_VAR>` for each connector `secret_env` (kkb: `ONEST_API_KEY`), `dev-kit` at least one LLM key.

The dev-kit deploy wizard (`dev-kit/dev_kit/agent/app.py`, `_run_k8s_deploy`) follows these same conventions.

## Quick install

From the repo root, against a local cluster:

```bash
NS=dpg DOMAIN=kkb
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

kubectl -n $NS wait --for=condition=Ready pod --all --timeout=420s
kubectl -n $NS port-forward svc/reach-layer 8005:8005     # web chat at http://localhost:8005
```

## Teardown

```bash
for r in $(helm list -n dpg --short); do helm uninstall $r -n dpg --wait; done
```

`helm uninstall` deletes the Memgraph and Knowledge Engine PVCs and the data in them.

## Not covered by these charts

The voice, MCP and bridge Reach channels, ngrok, and Ingress. See the differences table in [docs/helm-deployment.md](../../docs/helm-deployment.md#differences-from-docker-compose).
