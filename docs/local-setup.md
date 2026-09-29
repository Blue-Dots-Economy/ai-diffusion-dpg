# Local Setup (Docker Compose)

How to run the AI Diffusion DPG stack on one machine with `automation/docker/docker-compose.dev.yml`, the compose file the dev-kit deploy wizard also uses as its template. Everything below was verified on a clean run; places where the README or older docs disagree are called out in [Differences from the README](#differences-from-the-readme).

## 1. Prerequisites

| Tool | Verified version | Notes |
|---|---|---|
| Docker Engine | 29.3 (Docker Desktop, Linux) | Any engine with Compose v2 should work |
| Docker Compose | v5.1.1 | `docker compose` plugin, not the legacy `docker-compose` binary |
| git | 2.43 | |
| curl | any | For the verification steps |

Resources:

- **CPU/GPU:** no GPU needed. All LLM calls go to a hosted provider, and KE embeddings default to `chroma_default` (CPU).
- **Memory:** about 1.2 GB used by the 16 core containers when idle. A Docker Desktop VM with 4 GB is enough.
- **Disk:** about 11 GB of images for the full file (`knowledge-engine` and `reach-layer-voice` are about 2 GB each). Keep 15 GB or more free.
- **Architecture:** GHCR images are `linux/amd64`.

Python, uv and Node are only needed to run tests or build images from source (`docker-compose.yml`), not for this guide.

## 2. Clone

```bash
git clone https://github.com/Blue-Dots-Economy/ai-diffusion-dpg.git
cd ai-diffusion-dpg
git checkout main
```

The GHCR images are public, so no `docker login` is needed. `docker-compose.dev.yml` pulls the tag in `DPG_IMAGE_TAG` (default `sha-646216d`). That tag is a CI build and does not necessarily match the commit you have checked out, so if a service behaves differently from the source, check the tag first.

## 3. Configure

```bash
cp automation/docker/env.example automation/docker/.env
cp dev-kit/configs/kkb/secrets.env.example dev-kit/configs/kkb/secrets.env
mkdir -p knowledge_engine/data
```

- `automation/docker/.env` is gitignored and loaded by Compose automatically. Never commit it. It holds deployment-wide settings: LLM keys, voice credentials, image tag, `DOMAIN`.
- `dev-kit/configs/<DOMAIN>/secrets.env` holds the **domain's connector secrets**: one `KEY=value` per `auth.secret_env` declared in that domain's `action_gateway.yaml`. It is gitignored, and Compose loads it into Action Gateway only (`env_file`, optional). For kkb that is `ONEST_API_KEY`.
- `knowledge_engine/data/` is bind-mounted read-only into Knowledge Engine as `/app/data`. It is gitignored (`**/data`) and does not exist in a fresh clone. Create it yourself, otherwise Docker creates it root-owned.

### Which keys you actually need

| Variable | Needed for | What happens without it |
|---|---|---|
| `OPENAI_API_KEY` | Agent Core LLM calls (kkb sets `agent.provider: openai`) | Stack boots; every non-scripted chat turn returns `error_type: api_error` (OpenAI 401) |
| `ONEST_API_KEY` (in `dev-kit/configs/kkb/secrets.env`) | kkb `onest_market_lookup` connector in Action Gateway | **Action Gateway crash-loops** (`action_gateway.startup_missing_tools`), and Agent Core and every Reach channel never start |
| `VOBIZ_AUTH_ID` (+ `VOBIZ_AUTH_TOKEN`, `VOBIZ_FROM_NUMBER`, `RAYA_API_KEY`, `PUBLIC_URL`) | `reach_layer_voice` | Voice exits at startup: `reach_layer.channels.voice.vobiz.auth_id is required` |
| `NGROK_AUTHTOKEN` | `ngrok` tunnel for voice | ngrok cannot authenticate; skip the service locally |
| `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | Only if the domain's `agent.provider` is `anthropic` / `google` | n/a for kkb |
| Everything else in `env.example` | Optional features (web auth, upload chain, Azure KB, Memgraph auth) | Defaults work locally |

**Dummy values are fine for boot testing.** With `OPENAI_API_KEY=sk-dummy`, `ONEST_API_KEY=dummy` and dummy Vobiz values, every core service goes healthy; only the LLM call itself fails. Put a real key in `OPENAI_API_KEY` to get real answers.

To run a different domain, set `DOMAIN=<folder>` (for example `blue-dots`); each block then loads `dev-kit/configs/<DOMAIN>/<block>.yaml`, and Action Gateway loads `dev-kit/configs/<DOMAIN>/secrets.env`. List every `secret_env` from that domain's `action_gateway.yaml` in its `secrets.env`: `grep secret_env dev-kit/configs/<DOMAIN>/action_gateway.yaml` (blue-dots needs `BLUE_DOTS_API_KEY`, `BLUE_DOTS_ORG_ID`, `BLUE_DOTS_SEARCH_API_KEY`).

## 4. Start

Run from `automation/docker`. Use an explicit project name so volumes are namespaced (`dpg-local_*`) and don't mix with dev-kit deployments (`dpg-<slug>_*`).

```bash
cd automation/docker
export COMPOSE_PROJECT_NAME=dpg-local
COMPOSE="docker compose -f docker-compose.dev.yml"

$COMPOSE pull                     # ~11 GB on the first run

# Core stack: 7 DPG blocks + web + voice channels + dev-kit + infra + observability
CORE="redis memgraph action_gateway knowledge_engine memory_layer trust_layer \
      observability_layer agent_core reach_layer_web reach_layer_voice dev_kit \
      otelcol jaeger loki prometheus grafana"
$COMPOSE up -d --wait $CORE       # ~2.5 min on a cold start
```

- Leave `reach_layer_voice` out of `CORE` if you have no Vobiz values at all, not even dummy ones.
- `reach_layer_mcp` and `reach_layer_bridge` are left out on purpose: both crash at startup in the current images (see [Known issues](#6-common-issues-and-fixes)). Neither publishes a host port.
- `ngrok` needs a real `NGROK_AUTHTOKEN`. Start it separately only when you are testing inbound calls: `$COMPOSE up -d ngrok`.
- A plain `$COMPOSE up -d` starts everything, including the broken services, and stops at the first unhealthy dependency. Use the explicit list.

Startup order is driven by healthchecks: `redis` + `memgraph` → `memory_layer`; `action_gateway` → `agent_core` → Reach channels. `agent_core` has a 120 s `start_period`, so `--wait` takes a couple of minutes even when everything is fine.

## 5. Verify

**Containers.** All 16 should be `Up`, and every service that has a healthcheck should be `(healthy)`:

```bash
$COMPOSE ps
```

**Internal health endpoints.** The block ports are not published to the host, so query them from inside the network:

```bash
for p in agent_core:8000 knowledge_engine:8001 memory_layer:8002 trust_layer:8003 \
         observability_layer:8004 action_gateway:9999; do
  printf '%-26s ' $p
  docker exec agent_core python3 -c "import urllib.request;print(urllib.request.urlopen('http://$p/health',timeout=5).read().decode())"
done
```

Expected: `{"status":"ok"}` for each block. Action Gateway also lists its adapters, for example `"onest_market_lookup":true`.

**Host endpoints:**

| URL | Expect |
|---|---|
| http://localhost:8005/ | 200, KKB web chat UI |
| http://localhost:8005/health | `{"status":"ok"}` |
| http://localhost:8006/health | 200 (voice) |
| http://localhost:8080/ | 200, dev-kit Configuration Agent |
| http://localhost:16686/ | Jaeger UI; the service dropdown lists `agent_core`, `trust_layer`, `memory_layer`, ... after a chat turn |
| http://localhost:3000/ | Grafana (admin / `GF_SECURITY_ADMIN_PASSWORD`, default `admin`) |
| http://localhost:9090/-/ready | Prometheus ready |
| http://localhost:3100/ready | `ready`. Loki sometimes returns 503 `Ingester not ready`; retry after a few seconds |

**Functional check: a chat turn through every block.**

```bash
chat() { curl -s -X POST localhost:8005/chat -H 'content-type: application/json' \
  -d "{\"session_id\":\"smoke-1\",\"user_id\":\"smoke\",\"message\":\"$1\"}"; echo; }

chat "Hello"                                   # consent prompt (scripted, no LLM)
chat "haan"                                    # consent accepted (scripted)
chat "Electrician jobs in Pune, what salary?"  # full pipeline incl. LLM
```

- With a real `OPENAI_API_KEY`, the third call returns a `response_text` and `error_type: null`.
- With a dummy key it returns `error_type: "api_error"`. That still proves every block before the LLM is wired correctly.

Confirm the step trace in Agent Core:

```bash
docker logs agent_core 2>&1 | grep -oE '\[STEP [0-9a-z]+\][^(]{0,60}' | tail -20
```

You should see Memory context bundle (1) → Trust input check (3) → Language Normalisation (4) → NLU (5) → Routing (6) → Prompt Assembly (7) → LLM call (8) → async Audit / Memory write / Observability emit (11b–13). With a real key there are also Trust output check and, for tool turns, Action Gateway / Knowledge Engine calls.

## 6. Common issues and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `memgraph` restarting: `Unexpected positional argument(s): '/usr/lib/memgraph/memgraph'` | The unpinned `memgraph/memgraph` image now uses the binary as its ENTRYPOINT, and compose repeated the binary path in `command` | Fixed in compose: image pinned to `3.13.1`, `command` carries flags only |
| `action_gateway` restarting, logs `adapter_factory_build_error` then `action_gateway.startup_missing_tools`; `agent_core` stuck in `Created` | A connector `secret_env` is unset (kkb: `ONEST_API_KEY`) | Add it to `dev-kit/configs/<DOMAIN>/secrets.env` (a dummy is fine locally), then `$COMPOSE up -d action_gateway` |
| Stray `knowledge-engine/` directory appears at the repo root | Compose bind-mounted `../../knowledge-engine/data` (hyphen) | Fixed in compose: path is now `knowledge_engine/data`. Delete the stray empty directory |
| `reach_layer_voice` restarting: `vobiz.auth_id is required` | Voice requires Vobiz credentials at boot | Set the Vobiz variables (dummy values are OK) or leave voice out of the service list |
| `reach_layer_bridge` restarting: `Config file not found: /app/reach_layer/bridge/config/dpg.yaml` | Code bug: `bridge/main.py` `_dpg_config_path()` ignores `CONFIG_FOLDER` and resolves `config/dpg.yaml` against `WORKDIR /app/reach_layer/bridge` | Open issue, needs a code fix. Leave it out of the service list |
| `reach_layer_mcp` restarting: `ImportError: cannot import name 'RequestContext' from 'mcp.server.lowlevel.server'` | `mcp>=1.0` is unpinned with no lockfile; the image resolved `mcp 2.2.0`. Once that's fixed, MCP also expects `CONFIG_FOLDER/reach_layer.yaml`, but compose mounts `domain.yaml` | Open issue, needs a dependency pin + mount/code fix. Leave it out of the service list |
| `ngrok` restarting | No or invalid `NGROK_AUTHTOKEN` | Only start it with a real token |
| `dependency failed to start: container X is unhealthy` from a plain `up -d` | One of the services above; Compose aborts the whole `up` at the first unhealthy dependency | `$COMPOSE ps -a`, then `docker logs <container>` for the one that is `Restarting` |
| Chat returns `error_type: api_error` | LLM key missing or invalid; `docker logs agent_core` shows `401 Unauthorized ... invalid_api_key` | Set a real `OPENAI_API_KEY` (or the key for your domain's provider) and `$COMPOSE up -d agent_core` |
| Consent prompt shows again after a restart | Redis has no volume, so session state is lost on `down` | Expected locally. Memgraph profiles (`memgraph_data`) do persist |
| Loki `/ready` returns 503 `Ingester not ready` | Ingester warm-up; also seen intermittently long after start | Retry. Logs still arrive: `curl -s localhost:3100/loki/api/v1/labels` |
| Knowledge retrieval returns nothing | `docker-compose.dev.yml` does not ingest on startup, and the kkb source documents (`labour_schemes.pdf` etc.) are not in the repo | Upload documents through the dev-kit, or copy them into `knowledge_engine/data/` and run `docker exec knowledge_engine python -m scripts.ingest --config config/knowledge_engine.yaml` (not verified in this setup) |

## 7. Stop, reset and clean up

```bash
cd automation/docker
export COMPOSE_PROJECT_NAME=dpg-local
COMPOSE="docker compose -f docker-compose.dev.yml"

$COMPOSE stop                  # stop containers, keep them
$COMPOSE down                  # remove containers + network; volumes are kept
$COMPOSE down -v               # ALSO delete dpg-local_* volumes (Chroma, Memgraph, KB uploads, dev-kit keystore)
$COMPOSE down -v --rmi all     # ...and remove the pulled images (~11 GB)
```

Volumes created by this project: `dpg-local_chroma_data`, `dpg-local_memgraph_data`, `dpg-local_kb_data`, `dpg-local_devkit_keystore`. Deleting `chroma_data` forces a re-ingest; deleting `devkit_keystore` means any open dev-kit browser tab must be hard-refreshed.

To rebuild one service after a config change (the domain YAML is bind-mounted, so a restart is enough):

```bash
$COMPOSE up -d --force-recreate agent_core
```

## Differences from the README

These are points where `README.md`, `CLAUDE.md` or `automation/docker/README.md` no longer match what actually runs:

1. **CLI channel.** The docs say `docker compose -f docker-compose.dev.yml run --rm reach_layer` (or `--profile cli run --rm reach_layer`). There is no `reach_layer` service: channels were split into `reach_layer_web/voice/mcp/bridge/cli`, and `reach_layer_cli` is commented out in both compose files. Use the web channel at http://localhost:8005 instead.
2. **API key.** The Quick Start shows `ANTHROPIC_API_KEY` first, but the reference kkb domain uses OpenAI. `ONEST_API_KEY` is also mandatory for kkb and is not mentioned anywhere.
3. **Image source.** `automation/docker/README.md` says pre-built images come from Docker Hub. They come from GHCR (`ghcr.io/blue-dots-economy/ai-diffusion-dpg/*`).
4. **Ingest on startup.** `automation/docker/README.md` says KE ingests on first start. That is only true for `docker-compose.yml` (the local build). `docker-compose.dev.yml` does not ingest on startup.
5. **Resource table.** `automation/docker/README.md` lists 512 MB / 2 GB limits. The compose files actually set 256 MB for every DPG block, including Knowledge Engine.
6. **"All services except reach_layer".** `docker compose up -d` currently starts every service, including voice, ngrok, MCP and bridge. Without the right keys and the fixes above, it fails.
