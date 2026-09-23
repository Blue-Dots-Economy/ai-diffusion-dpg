# ai-diffusion-dpg on the VoicEra VM

Self-contained Docker Compose deployment. The VM needs **this directory only** —
no git clone, no repo tree. The images carry the code.

Kept separate from `automation/docker/*` on purpose: those two files serve local
development, and nothing here changes them.

## What runs

11 services: the 6 DPG blocks, the bridge channel, `redis`, `memgraph`,
`otelcol`, and a one-shot `init_secrets`.

Deliberately absent: the **voice channel and ngrok** (VoicEra owns telephony),
`web` / `cli` / `mcp`, `dev-kit`, and the grafana / jaeger / loki / prometheus
UIs.

## Secrets

Two parameters are stored in AWS SSM Parameter Store as `SecureString`:

| Parameter | Variable |
| --- | --- |
| `<SSM_PREFIX>/openai_api_key` | `OPENAI_API_KEY` |
| `<SSM_PREFIX>/blue_dots_api_key` | `BLUE_DOTS_API_KEY` |

`init_secrets` runs first, fetches both using the EC2 instance role, and writes
`/secrets/env` to a **tmpfs** volume — plaintext exists only in memory, never on
disk and never in git. `agent_core` and `action_gateway` mount that volume
read-only and are gated on `service_completed_successfully`, so they cannot
start before it lands.

No image change is required. A container cannot inject environment variables
into another container — in Compose or in Kubernetes — so the handoff is
file-based, and this compose file overrides the entrypoint at runtime:

```yaml
entrypoint: ["/bin/sh", "-c"]
command: ['. /secrets/env && exec uv run python main.py']
```

That sources the file and then execs the image's own command. Nothing is baked
in, nothing is rebuilt, and no other deployment is affected. The one cost is
that the startup command now appears here as well as in the image's `CMD`, so
keep the two in step if the image's command ever changes.

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
./sync-config.sh blue-dots     # from a checkout: fills ./config and ./otelcol
cp env.example .env           # set AWS_REGION, SSM_PREFIX, BLUE_DOTS_ORG_ID
chmod 600 .env
# ship this directory to the VM, then:
docker compose up -d
docker compose ps
```

`config/`, `otelcol/` and `.env` are git-ignored — they are produced per
deployment, not committed.

### Verify

```bash
docker compose logs init_secrets     # prints lengths only, never values
curl -s localhost:8008/health        # {"status":"ok"}
```

A full turn through the endpoint VoicEra calls:

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

- **Shared network (preferred).** VoicEra's compose joins `dpg_net` as an
  external network and uses `http://reach_layer_bridge:8008`. Delete the
  `ports:` block from `reach_layer_bridge` when doing this.
- **Loopback.** VoicEra reaches `127.0.0.1:8008` on the host, via host
  networking or `extra_hosts: ["host.docker.internal:host-gateway"]`.

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
- No service except the bridge publishes a port, and that one is loopback-only.
- Logs are capped at 10 MB × 3 per container so a shared box cannot fill up.
