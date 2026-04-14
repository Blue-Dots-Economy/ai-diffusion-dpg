# Kubernetes Deployment Guide

Deploy all 7 DPG services on any Kubernetes cluster.

---

## Prerequisites

- `kubectl` configured and pointing at your cluster (`kubectl cluster-info`)
- `helm` v3+ installed
- Docker Hub images available (`sanketikahub/dpg-*:latest`)
- Anthropic API key exported: `export ANTHROPIC_API_KEY=sk-ant-...`

---

## 1. Deploy all services

Run from `automation/helm/`:

```bash
# Memory Layer
helm install memory-layer ./dpg/memory-layer -n memory-layer --create-namespace

# Trust Layer
helm install trust-layer ./dpg/trust-layer -n trust-layer --create-namespace

# Observability Layer
helm install observability-layer ./dpg/observability-layer -n observability-layer --create-namespace

# Action Gateway
helm install action-gateway ./dpg/action-gateway -n action-gateway --create-namespace

# Knowledge Engine (runs ingest init container on every deploy — may take 2-3 min)
helm install knowledge-engine ./dpg/knowledge-engine -n knowledge-engine --create-namespace

# Agent Core (requires API key — never stored in files)
helm install agent-core ./dpg/agent-core -n agent-core --create-namespace \
  --set anthropicApiKey=$ANTHROPIC_API_KEY

# Reach Layer
helm install reach-layer ./dpg/reach-layer -n reach-layer --create-namespace
```

---

## 2. Verify all pods are running

```bash
kubectl get pods -A
```

All pods should show `Running`. Knowledge Engine may take a few minutes — the `ingest` init container downloads the embedding model and builds the ChromaDB index on first deploy.

To watch Knowledge Engine:
```bash
kubectl get pods -n knowledge-engine -w
```

To check init container logs:
```bash
kubectl logs -n knowledge-engine -l app=knowledge-engine -c ingest
```

---

## 3. Access the Reach Layer CLI

Exec into the Reach Layer pod:

```bash
# Get the pod name
kubectl get pods -n reach-layer

# Exec into the pod
kubectl exec -it -n reach-layer <pod-name> -- /bin/sh
```

Once inside, start the CLI:
```bash
python -m reach_layer.cli
```

Or send a turn directly via curl from inside the pod:
```bash
curl -s -X POST http://localhost:8005/turn \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-1", "user_input": "kaam chahiye"}'
```

---

## 4. Upgrading a service

```bash
# Any service (example: trust-layer)
helm upgrade trust-layer ./dpg/trust-layer -n trust-layer

# Agent Core — always re-pass the API key
helm upgrade agent-core ./dpg/agent-core -n agent-core \
  --set anthropicApiKey=$ANTHROPIC_API_KEY
```

---

## 5. Teardown

```bash
helm uninstall knowledge-engine -n knowledge-engine
helm uninstall agent-core       -n agent-core
helm uninstall reach-layer      -n reach-layer
helm uninstall action-gateway   -n action-gateway
helm uninstall trust-layer      -n trust-layer
helm uninstall observability-layer   -n observability-layer
helm uninstall memory-layer     -n memory-layer
```
