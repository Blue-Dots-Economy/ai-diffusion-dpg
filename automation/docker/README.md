# Running DPG Services with Docker Compose

## Prerequisites
- Docker Desktop installed and running
- `OPENAI_API_KEY` (the Blue Dots configuration uses OpenAI for the agent and the embeddings)
- To **build** the DPG images locally (`--build`, or no pre-built image available): be logged in to Docker. Building pulls the `dhi.io` base images through your Docker login, so log in to Docker Hub first. Every Dockerfile except `knowledge_engine/` is based on Docker Hardened Images (dhi.io), and dhi.io refuses anonymous pulls. Pulling the pre-built images from GHCR does not need it.
- The DHI runtime images have **no shell** (knowledge_engine, on `python:3.14-slim`, is the exception) — `docker compose exec <service> sh` does not work. Use exec form with the venv's python instead, e.g. `docker compose exec agent_core python -c "..."`.

---

## Quick Start

```bash
# 1. Set your API key
export OPENAI_API_KEY=sk-...

# 2. Build the images from this checkout and start all services
cd automation/docker
export GIT_SHA=<release tag>   # e.g. 202610-s1-rc1; use `local` until releases are tagged
docker compose -f docker-compose.yml build
DPG_IMAGE_TAG=$GIT_SHA docker compose -f docker-compose.dev.yml up -d

# 3. Watch Knowledge Engine finish ingest (first run only — takes ~3-4 min)
docker compose -f docker-compose.dev.yml logs -f knowledge_engine

# 4. Once all containers are healthy, check them
docker compose -f docker-compose.dev.yml ps
```

The **bridge** (`reach_layer_bridge`, port 8008) is the default and the only
Reach Layer channel started out of the box. It is a generic OpenAI
chat-completions-compatible endpoint (`POST /v1/chat/completions`), so any
system that speaks that API can connect to the agent, for example your own
voice pipeline. [VoicERA](https://github.com/COSS-India/VoicEra), an external
DPG voice service, is one example.

The other channels are optional and sit behind compose profiles, so a plain
`up` or `build` skips them:

| Channel | Service | Profile |
|---|---|---|
| Web chat, for local/dev testing only | `reach_layer_web` (8005) | `web` |
| Voice (telephony), with the optional `ngrok` tunnel | `reach_layer_voice` (8006) | `voice` |
| MCP server | `reach_layer_mcp` (8007) | `mcp` |

The CLI is not a compose service; you build and run it on its own. Each
optional channel also needs a channel block in the use case's
`agent_core.yaml` and `reach_layer.yaml`. Web chat runs without login
(`auth.enabled: false`); Google sign-in is optional and needs `enabled: true`
plus `GOOGLE_CLIENT_ID` and `REACH_SESSION_SECRET`. The exact edits for each
channel are in the
[optional channels guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-channels/).
For example, to add web chat once its blocks are in place:

```bash
docker compose -f docker-compose.dev.yml --profile web up -d reach_layer_web
```

A `DOMAIN` exported in your shell overrides the one in `.env`, so unset it if
you want the `.env` value.

---

## Two Compose Files

| File | Use when |
|---|---|
| `docker-compose.dev.yml` | Running with pre-built images from GHCR (`ghcr.io/blue-dots-economy/ai-diffusion-dpg/<block>:<tag>`; pick the tag with `DPG_IMAGE_TAG`) |
| `docker-compose.yml` | Local development — builds images from source |

### Local build workflow
```bash
docker compose -f docker-compose.yml build   # build the images from source
docker compose -f docker-compose.yml up -d    # start all services
```

The dev compose file has no `build:` sections, so build with
`docker-compose.yml`. It tags builds `${GIT_SHA:-latest}`; to make the dev file
use them, set `DPG_IMAGE_TAG` to the `GIT_SHA` you built with.

The Blue Dots configuration in this checkout sets its Signals URLs as
`${SIGNALS_*:-…}` placeholders, which only an Action Gateway image built from
this commit or later expands. So `DPG_IMAGE_TAG` must name a release tag that
includes this change, or a local build of it. Older tags, including the default `sha-646216d` and
`latest`, would call the placeholder text as the URL. Build from source, as the
quick start does, until a release tag is published.

---

## Running against a local Signals

To point the stack at a Signals instance on your machine instead of the hosted
one, layer `local-signals.override.yml` on top of the dev file:

```bash
docker compose -f docker-compose.dev.yml -f local-signals.override.yml up -d
```

The override moves dev-kit to 8081 and Loki to 3101 (Signals uses 8080 and
3100), points `KE_DEVKIT_CALLBACK_URL` at 8081, lets Action Gateway reach the
host through `host.docker.internal`, and publishes the bridge on
`127.0.0.1:8008` (loopback only), so you can call it with `curl` or any
OpenAI-compatible client on the same machine. The full walkthrough is in the
[local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/).

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
#    dev-kit/configs/blue-dots/knowledge_engine.yaml → knowledge.blocks.static_knowledge_base.sources

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
Discord routing are provisioned from files under `automation/docker/grafana/provisioning/`,
read-only in the UI: the dashboards are in `automation/docker/grafana/provisioning/dashboards/` next to
the provider config, alerting in `automation/docker/grafana/provisioning/alerting/`. Every dashboard has
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
scrapes them with `automation/docker/prometheus/prometheus.yml`, which keeps only this stack's
containers.

**Alerts.** 17 rules in `automation/docker/grafana/provisioning/alerting/`, routed by their
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
`automation/docker/grafana/import_community_dashboards.py` and run it (Python 3.10+, standard
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

Limits set in `docker-compose.dev.yml` (`deploy.resources.limits`):

| Service | RAM | CPU |
|---|---|---|
| action_gateway | 256 MB | 0.5 |
| agent_core | 256 MB | 0.5 |
| dev_kit | 256 MB | 0.5 |
| knowledge_engine | 256 MB | 0.5 |
| memory_layer | 256 MB | 0.25 |
| observability_layer | 256 MB | 0.1 |
| trust_layer | 256 MB | 0.25 |
| reach_layer_web | 256 MB | 0.25 |
| reach_layer_voice | 256 MB | 0.5 |
| reach_layer_mcp | 256 MB | 0.25 |
| reach_layer_bridge | 256 MB | 0.25 |

The monitoring and support containers (Grafana, Loki, Prometheus, the
collector, Jaeger, Memgraph, Redis and the exporters) have their own limits in
the same file. `docker-compose.yml` gives dev_kit 512 MB and 0.1 CPU.

> If Knowledge Engine runs out of memory during ingest, raise its limit in the
> compose file, or switch to `embedding_provider: openai` in
> `dev-kit/dpg/knowledge_engine.yaml` (needs `OPENAI_API_KEY`).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `agent_core` stuck in "Created" | Dependencies not healthy yet | Wait — it starts automatically once `action_gateway` is healthy |
| `knowledge_engine` unhealthy after 3 min | OOM during ingest | Increase Docker Desktop memory limit or switch to OpenAI embeddings |
| A Reach Layer channel can't connect | `agent_core` not healthy yet | Run `docker compose ps` and wait for agent_core to show healthy |
