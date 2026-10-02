# ai-diffusion-dpg on the shared VM

Docker Compose deployment for the shared EC2 host. Run it from a clone of this
repo on the VM — the images carry the code, and the clone supplies the domain
config, which is mounted straight out of `dev-kit/`.

This compose file is standalone and holds what is specific to the VM: the
secrets init container, loopback-only ports, persistent volumes and shared-host
limits. The observability config (collector, Prometheus and blackbox configs,
Grafana datasources, dashboards and alert rules) is shared with the local
compose files and mounted from `automation/docker/`; its README describes what
each dashboard and alert does.

## What runs

The 6 DPG blocks, the bridge channel, `redis`, `memgraph`, `otelcol`, the
monitoring stack (`jaeger`, `loki`, `prometheus`, `grafana` and the
`blackbox_exporter`, `cadvisor` and `redis_exporter` exporters), and two
one-shot init containers, `init_secrets` and `init_kb_perms`.

Deliberately absent: the **voice channel and ngrok** — the telephony platform
owns the call path — plus `web` / `cli` / `mcp` and `dev-kit`.

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

> **Updating a VM that is already running?** See [DEPLOY.md](DEPLOY.md) — which
> change needs a new image, how to move keys and clusters, verification and
> rollback. This section covers first-time setup only.

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

### Dashboards and alerts

Shared with the local compose files; see `automation/docker/README.md`
(Monitoring) for the dashboards and the 17 alert rules. Here the exporters run
unconditionally, with no profile. VM specifics:

- **Discord webhooks** are secrets. Store them in SSM as
  `<SSM_PREFIX>/discord_webhook_critical`, `…_warning` and `…_info`; on a host
  without SSM, set `DISCORD_WEBHOOK_CRITICAL` / `_WARNING` / `_INFO` in `.env`.
  `init_secrets` writes each to `/secrets/discord_webhook_<severity>`, and
  Grafana's entrypoint loads them into the environment the shared contact
  points read. An unset webhook gets an unroutable placeholder (the
  `init_secrets` log says which), so a missing one degrades to "alerts visible
  in Grafana only" rather than stopping Grafana. To change a webhook, update
  it in SSM or `.env`, then run `docker compose up -d --force-recreate
  init_secrets` and, once that has finished, `docker compose restart grafana`.
  Do it in two steps: Grafana reads the files as it starts, and one combined
  command can start it before `init_secrets` has rewritten them.
- **Services this VM does not run** (web, voice, MCP, dev-kit) show DOWN on
  Service Status and never alert: *Service down* only fires for a service that
  was up in the last 24 hours.
- **`GRAFANA_ROOT_URL`** (default `http://localhost:3001`) is the base of the
  dashboard links in each alert, which open through the SSH tunnel above.
- **Retention:** Prometheus keeps 15 days on a volume; Loki and Grafana state
  are on volumes too. Jaeger keeps traces in memory, so a restart loses them.
  The bridge channel emits no traces, so every trace starts at Agent Core.

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
