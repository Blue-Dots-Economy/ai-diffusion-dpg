# Running DPG Services with Docker Compose

## Prerequisites
- Docker Desktop installed and running
- `ANTHROPIC_API_KEY` (get one from [console.anthropic.com](https://console.anthropic.com))
- To **build** the DPG images locally (`--build`, or no pre-built image available): `docker login dhi.io` with a Docker Hub account. Every Dockerfile except `knowledge_engine/` is based on [Docker Hardened Images](https://hub.docker.com/hardened-images/catalog), and dhi.io refuses anonymous pulls. Pulling the pre-built images from GHCR does not need it.
- The DHI runtime images have **no shell** (knowledge_engine, on `python:3.14-slim`, is the exception) — `docker compose exec <service> sh` does not work. Use exec form with the venv's python instead, e.g. `docker compose exec agent_core python -c "..."`.

---

## Quick Start

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Start all services
cd automation/docker
docker compose -f docker-compose.dev.yml up -d

# 3. Watch Knowledge Engine finish ingest (first run only — takes ~3-4 min)
docker compose -f docker-compose.dev.yml logs -f knowledge_engine

# 4. Once all containers are healthy, start the CLI
docker compose -f docker-compose.dev.yml --profile cli run --rm reach_layer
```

---

## Two Compose Files

| File | Use when |
|---|---|
| `docker-compose.dev.yml` | Running with pre-built images from Docker Hub |
| `docker-compose.yml` | Local development — builds images from source |

### Local build workflow
```bash
docker compose build          # build all 7 images from source
docker compose up -d          # start all services
docker compose --profile cli run --rm reach_layer
```

---

## Common Commands

```bash
# Check service health
docker compose -f docker-compose.dev.yml ps

# View logs for a specific service
docker compose -f docker-compose.dev.yml logs -f agent_core

# Stop all services (keeps ChromaDB data)
docker compose -f docker-compose.dev.yml down

# Stop and delete all data (forces re-ingest on next start)
docker compose -f docker-compose.dev.yml down -v

# Restart a single service with a new image
docker compose -f docker-compose.dev.yml pull agent_core
docker compose -f docker-compose.dev.yml up -d --no-deps agent_core
```

---

## Adding New Documents / Force Re-ingest

Knowledge Engine skips ingest if ChromaDB already has data.
To add a new document and re-ingest:

```bash
# 1. Copy the new file into the data folder
cp my_new_doc.pdf ../../knowledge_engine/data/

# 2. Register it in the domain config (add a new entry under sources)
#    dev-kit/configs/kkb/knowledge_engine.yaml → knowledge.blocks.static_knowledge_base.sources

# 3. Delete the chroma volume to force re-ingest (mandatory)
docker volume rm docker_chroma_data

# 4. Start services — ingest runs automatically with the new file included
docker compose -f docker-compose.dev.yml up -d
```

> Step 3 is mandatory. Without deleting the volume, ChromaDB already exists and
> the new file is silently skipped — ingest will not run.

---

## Monitoring: Grafana dashboards and alerts

Grafana (<http://localhost:3000>, `admin` / `$GF_SECURITY_ADMIN_PASSWORD`,
default `admin`) opens on **Service Status**. The dashboards, alert rules and
Discord routing are provisioned from files, read-only in the UI: dashboards in
`grafana/dashboards/`, the rest in `grafana/provisioning/`. Every dashboard has
a *DPG dashboards* menu (top right) that switches between them and keeps the
time range.

Prometheus, Grafana, Loki, Jaeger and the collector always run. The exporters
behind the container, Redis and service-status dashboards are opt-in, because
cAdvisor runs privileged with read-only mounts of `/`, `/sys` and
`/var/lib/docker`:

```bash
docker compose -f docker-compose.dev.yml --profile monitoring up -d
# or: COMPOSE_PROFILES=monitoring docker compose -f docker-compose.dev.yml up -d
```

Without the profile, Service Traffic, Traces, Logs and OTel Collector still
work; Service Status, Containers, Redis and Health Probes have no data, and
nothing alerts for what was never started.

| Dashboard | Answers | Source |
| --- | --- | --- |
| DPG - Service Status | Is every service up right now, and when was it not? | built here |
| DPG - Service Traffic | Request rate, 5xx rate, p95 latency per block; error logs | built here |
| DPG - Traces | Recent turns, failed and slow traces; click a trace ID for its waterfall and its logs side by side | built here |
| DPG - Logs | Every log line for one service, searchable | grafana.com 13639 |
| DPG - Containers | CPU, memory, network, disk per container | grafana.com 15798 |
| DPG - Redis | Memory, clients, commands, keys | grafana.com 763 |
| DPG - OTel Collector | Is telemetry flowing, or being refused or dropped? | grafana.com 15983 |
| DPG - Health Probes (detail) | One probe's status, latency and HTTP phases | grafana.com 14928 |

Three exporters (profile `monitoring`) feed them: `blackbox_exporter` (a health
probe per service), `cadvisor` (containers) and `redis_exporter`. Prometheus
scrapes them with `prometheus/prometheus.yml`, which keeps only this stack's
containers.

**Alerts.** 17 rules in `grafana/provisioning/alerting/`, routed by their
`severity` label to a Discord channel each; a resolved message follows when an
alert clears.

| Severity | Alerts | Re-sent while firing |
| --- | --- | --- |
| critical | Service down · Crash-looping (3+ restarts in 10 min) · Out of memory · LLM calls failing | hourly |
| warning | High memory · Host memory high · CPU overload · Latency overload · 5xx rate · Slow health checks · Error log spike · Telemetry dropping · Redis memory high · Monitoring target down | every 4 h |
| info | Container restarted · Heartbeat (daily) | daily |

*Service down* and *Monitoring target down* only consider services that were up
in the last 24 hours, so a service a deployment never started (the dev-kit drops
unused ones) does not alert; a service that crashes on start is caught by
*Crash-looping*. The **Heartbeat** always fires, once a day: if it stops
arriving, Grafana or the host is down and no other alert can be sent either.

**Discord webhooks are secrets.** Set `DISCORD_WEBHOOK_CRITICAL`,
`DISCORD_WEBHOOK_WARNING` and `DISCORD_WEBHOOK_INFO` in your shell or in a
`.env` file next to the compose file; never commit them. An unset one falls
back to an unroutable placeholder (Grafana will not start on an empty Discord
URL): alerts then show in Grafana only, and delivery fails visibly as
`Notify for alerts failed` in `docker compose logs grafana`. To check a
channel, send a test from **Alerting → Contact points**. `GRAFANA_ROOT_URL`
(default `http://localhost:3000`) is the base of the dashboard links in each
message.

**Changing dashboards.** The community ones are committed pre-adapted. To bump
one or add another, edit the pinned list in
`grafana/import_community_dashboards.py` and run it (Python 3.10+, standard
library only); Grafana picks the files up within 10 s. The script records every
adaptation it makes and why.

Not covered: Memgraph has no dashboard (its metrics endpoint is an Enterprise
feature; its up/down status is on Service Status), LLM provider failures such
as an invalid API key do not appear in the HTTP metrics (Agent Core still
answers with a 200, so they show up as the *LLM calls failing* alert and as
ERROR logs), and nothing measures host disk space. On Traces, the service map is
a header link to Jaeger's own UI, because Grafana 10.3's Jaeger data source has
no dependency-graph query.

---

## Resource Requirements

| Service | RAM | CPU |
|---|---|---|
| memory_layer | 512 MB | 0.1 |
| trust_layer | 512 MB | 0.1 |
| observability_layer | 512 MB | 0.1 |
| action_gateway | 512 MB | 0.1 |
| knowledge_engine | **2 GB** | 0.5 |
| agent_core | 512 MB | 0.1 |
| **Total** | **~4.5 GB** | ~1.0 |

> Knowledge Engine needs 2 GB minimum. On low-memory machines, switch to
> `embedding_provider: openai` in `dev-kit/dpg/knowledge_engine.yaml` and
> set `OPENAI_API_KEY` — this reduces KE RAM to ~256 MB.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `agent_core` stuck in "Created" | Dependencies not healthy yet | Wait — it starts automatically once all 5 deps are healthy |
| `knowledge_engine` unhealthy after 3 min | OOM during ingest | Increase Docker Desktop memory limit or switch to OpenAI embeddings |
| `reach_layer` can't connect | `agent_core` not healthy yet | Run `docker compose ps` and wait for agent_core to show healthy |
| Network still in use on `down` | A `reach_layer run` container is still alive | `docker ps -a \| grep reach_layer` then `docker rm -f <id>` |
