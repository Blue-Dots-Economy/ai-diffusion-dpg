# ai-diffusion-dpg on the shared VM

Docker Compose deployment for the shared EC2 host. Run it from a clone of this
repo on the VM — the images carry the code, and the clone supplies the domain
config, which is mounted straight out of `dev-kit/`.

Kept separate from `automation/docker/*` on purpose: those two files serve local
development, and nothing here changes them. The secrets init container below is
needed by this deployment only.

## What runs

11 services: the 6 DPG blocks, the bridge channel, `redis`, `memgraph`,
`otelcol`, and a one-shot `init_secrets`.

Deliberately absent: the **voice channel and ngrok** — the telephony platform
owns the call path —
`web` / `cli` / `mcp`, `dev-kit`, and the grafana / jaeger / loki / prometheus
UIs.

## Secrets

Two parameters are stored in AWS SSM Parameter Store as `SecureString`:

| Parameter | Variable |
| --- | --- |
| `<SSM_PREFIX>/openai_api_key` | `OPENAI_API_KEY` |
| `<SSM_PREFIX>/blue_dots_api_key` | `BLUE_DOTS_API_KEY` |

`init_secrets` runs first, fetches both using the EC2 instance role, and writes
`/secrets/env` into a bind-mounted host directory under **`/dev/shm`**, which is
tmpfs on Linux — plaintext exists only in memory, never on persistent disk and
never in git. `SECRETS_DIR` overrides the location.

It is deliberately **not** a tmpfs-backed named volume. Those are not shared
between containers: Docker mounts a fresh tmpfs into each one, so a file written
by the init container is simply absent in the next. A host bind mount is what
makes the handover work.

The directory is `0711` and the file `0444`, so the non-root images can read it
while the filename stays undiscoverable by listing. Anything running as root on
the VM can still read it — as it could read the container's environment either
way — so treat the host itself as the trust boundary. `agent_core` and `action_gateway` mount that volume
read-only and are gated on `service_completed_successfully`, so they cannot
start before it lands.

No image change is required. A container cannot inject environment variables
into another container — in Compose or in Kubernetes — so the handoff is
file-based, and this compose file overrides the entrypoint at runtime:

```yaml
entrypoint: ["python", "/opt/dpg/exec_with_secrets.py"]
command: ["python", "main.py"]
```

The images are Docker Hardened Images with no shell, so the file cannot be
`source`-d. `bin/exec_with_secrets.py`, mounted read-only and run by the
image's own Python, reads it, exports the variables and `exec`s the image's
command, so the service stays PID 1 and gets signals directly (#413). Nothing
is baked in, nothing is rebuilt, and no other deployment is affected. The one
cost is that the startup command now appears here as well as in the image's
`CMD`, so keep the two in step if the image's command ever changes.

Longer term the services should read secrets from files natively, which would
remove this override altogether; see #413.

Two notes:

- `BLUE_DOTS_SEARCH_API_KEY` is a **distinct variable name** read by the search
  connector, but currently carries the **same value** as `BLUE_DOTS_API_KEY`, so
  only two parameters are stored. It must still be set — Action Gateway calls
  `sys.exit(1)` when a connector credential is unset *or empty*.
- `BLUE_DOTS_ORG_ID` is an organisation UUID, not a credential. Plain `.env`.

### Prerequisite: IMDSv2 hop limit

Reaching instance metadata from inside a container needs the instance's
`http-put-response-hop-limit` set to **2**. The AWS default of 1 blocks
containers, and the failure looks like an IAM problem when it is not.

```bash
aws ec2 modify-instance-metadata-options \
    --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required
```

The instance role needs `ssm:GetParameter` on the parameter paths and
`kms:Decrypt` on the key — the latter must be granted explicitly if it is a
customer-managed key rather than `alias/aws/ssm`.

## Deploy

```bash
git clone https://github.com/Blue-Dots-Economy/ai-diffusion-dpg.git
cd ai-diffusion-dpg/automation/deploy/shared-vm

cp env.example .env     # AWS_REGION, SSM_PREFIX, BLUE_DOTS_ORG_ID, DPG_IMAGE_TAG
chmod 600 .env

docker compose up -d
docker compose ps
```

`.env` is the only file you create, and it is git-ignored.

Config is mounted from the clone: `dev-kit/dpg/*.yaml` for framework defaults
and `dev-kit/configs/${DOMAIN:-blue-dots}/*.yaml` for the domain. Set `DOMAIN`
in `.env` to deploy a different one.

The clone is there for config, not code — **keep it on a release tag or a known
commit**, not a moving branch, or a stray `git pull` silently changes what the
running containers are configured with. `DPG_IMAGE_TAG` and the checkout are two
separate versions; keep them deliberately in step.

### Verify

```bash
docker compose logs init_secrets     # prints lengths only, never values
curl -s localhost:8008/health        # {"status":"ok"}
```

A full turn through the endpoint the telephony platform calls:

```bash
curl -N localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o","stream":true,
       "metadata":{"caller_phone":"919900112233"},
       "messages":[{"role":"user","content":"hello"}]}'
```

## Reaching the bridge

`8008` is published on **127.0.0.1 only**, never `0.0.0.0`: the bridge has **no
authentication**, and that decision is conditional on it not being externally
routable. Do not change this without an auth story.

Two supported options:

- **Shared network (preferred).** The telephony platform's compose joins `dpg_net` as an
  external network and uses `http://reach_layer_bridge:8008`. Delete the
  `ports:` block from `reach_layer_bridge` when doing this.
- **Loopback.** The telephony platform reaches `127.0.0.1:8008` on the host, via host
  networking or `extra_hosts: ["host.docker.internal:host-gateway"]`.

## Observability

`otelcol` receives traces, metrics and logs from every DPG service and forwards
them to Jaeger, Prometheus and Loki; Grafana reads all three.

Nothing here is authenticated beyond Grafana's admin password, so all of it is
bound to `127.0.0.1`. Reach it over an SSH tunnel:

```bash
ssh -L 3001:127.0.0.1:3001 \
    -L 16686:127.0.0.1:16686 \
    -L 9090:127.0.0.1:9090 <user>@<vm>
```

| UI | URL through the tunnel | What it shows |
| --- | --- | --- |
| Grafana | <http://localhost:3001> | everything; `admin` / `$GF_SECURITY_ADMIN_PASSWORD` (default `admin`) |
| Jaeger | <http://localhost:16686> | per-turn traces, span by span |
| Prometheus | <http://localhost:9090> | raw metric series |

Grafana is on **3001**, not the usual 3000 — the telephony platform's frontend
uses 3000 on this host.

Loki is deliberately not published: Grafana queries it inside `dpg_net` and
nothing needs it from the host.

Set a real Grafana password in `.env` before deploying:

```
GF_SECURITY_ADMIN_PASSWORD=<something other than admin>
```

### Checking it works

```bash
# Prometheus is scraping the collector
curl -s 'http://127.0.0.1:9090/api/v1/targets?state=active' | grep -o '"health":"[a-z]*"'

# Jaeger has traces from the DPG services
curl -s http://127.0.0.1:16686/api/services
```

Expect `"health":"up"` and a service list including `agent_core`,
`action_gateway`, `trust_layer`, `memory_layer` and `knowledge_engine`.

Metrics and traces appear within a few seconds of the first turn. Loki's
`/ready` returns 503 for the first minute or so after start while the ingester
ring settles — that is normal and does not mean logs are being dropped; query
`/loki/api/v1/labels` through Grafana instead.

### Dashboards

Provisioned from files into Grafana's **DPG** folder, read-only in the UI.
Grafana opens on **Service Status**; every dashboard has a *DPG dashboards*
menu (top right) that switches between them and keeps the time range.

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

They are fed by three exporters in this compose file: `blackbox_exporter`
(health probes for every service), `cadvisor` (containers) and
`redis_exporter`. Prometheus uses this directory's `prometheus/prometheus.yml`,
not the one under `automation/docker/`, so local development is unaffected.

On Traces, the service map is a header link to Jaeger's own UI
(`localhost:16686/dependencies`, so it also works through the SSH tunnel
above): Grafana 10.3's Jaeger data source has no dependency-graph query. The
bridge channel emits no traces, so every trace starts at Agent Core.

Memgraph has no dashboard: its Prometheus metrics endpoint is an Enterprise
feature. Its up/down status is on Service Status (TCP probe on 7687).

LLM provider failures, such as an invalid API key, do not appear in the HTTP
metrics — Agent Core still answers the bridge with a 200 and an empty reply.
They show up as ERROR logs: the *Error logs* tile on Service Traffic.

The community dashboards are committed pre-adapted. To bump one or add
another, edit the pinned list in `grafana/import_community_dashboards.py` and
run it (Python 3.10+, standard library only); Grafana picks the files up
within 10 s. The script records every adaptation it makes and why.

## Pinning by digest

GHCR tags are mutable. For a reproducible deployment, resolve the digest once
and pin it:

```bash
docker buildx imagetools inspect \
  ghcr.io/blue-dots-economy/ai-diffusion-dpg/agent-core:sha-646216d
```

then replace the tag with `@sha256:<digest>` in `docker-compose.yml`.

## Notes

- All images are `linux/amd64`, published public — no `docker login` needed.
- The only published ports are the bridge, the web UI and the three
  observability UIs, and every one of them is bound to `127.0.0.1`. The three
  exporters publish nothing; Prometheus reaches them inside `dpg_net`.
- `cadvisor` runs privileged with read-only mounts of `/`, `/sys` and
  `/var/lib/docker` — its documented requirement for reading every
  container's cgroup.
- Logs are capped at 10 MB × 3 per container so a shared box cannot fill up.
