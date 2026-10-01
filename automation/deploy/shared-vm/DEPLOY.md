# Deploying a change to the shared VM

For a VM that is **already running**. First-time setup is in `README.md`.

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
```

**`sudo` on docker, never on git.** Docker commands need `sudo` on this VM.
Git commands must NOT be sudoed — the clone is owned by the deploying user, and
a single `sudo git pull` leaves root-owned objects behind that make the next
person's pull fail with a permissions error that looks nothing like its cause.

---

## First: which kind of change is it?

The commonest way a deploy appears to do nothing is getting this wrong.

| changed | needs |
| --- | --- |
| `dev-kit/configs/**`, `dev-kit/dpg/**` | git pull + restart |
| any `*/src/**` Python | **new image** |
| `.env` values | re-run `init_secrets` + restart |
| `docker-compose.yml` | `up -d` |

> **Config is mounted from the clone, not baked into the image.** A correct
> `DPG_IMAGE_TAG` with a stale checkout runs new code against an old prompt,
> and the symptoms look like a model problem rather than a deploy problem.

### Check before you assume it is config-only

```bash
cd /opt/ai-diffusion-dpg
git fetch origin deploy/voicera-vm
git log --oneline HEAD..origin/deploy/voicera-vm          # what you are missing
git diff --name-only HEAD..origin/deploy/voicera-vm | grep -E '^[a-z_]+/src/'
```

**Any output from that last command means you need a new image.** Deploying the
config alone will then fail: layer configs are `extra="forbid"`, so a config
declaring a key the old image's schema does not know is a hard startup error,
not a warning. Look for `extra_forbidden` in the logs if a layer will not come
up.

---

## A. Config-only change

```bash
cd /opt/ai-diffusion-dpg
git checkout deploy/voicera-vm && git pull      # NO sudo
git log --oneline -1                            # confirm the commit you expect

cd automation/deploy/shared-vm
sudo docker compose up -d --force-recreate agent_core action_gateway
sudo docker compose ps
```

Restart the layer that owns the file you changed:

| file | restart |
| --- | --- |
| `agent_core.yaml` | `agent_core` |
| `action_gateway.yaml` | `action_gateway` **and** `agent_core` — it caches tool schemas at startup |
| `trust_layer.yaml` | `trust_layer` |
| `memory_layer.yaml` | `memory_layer` |
| `reach_layer.yaml` | `reach_layer_bridge` |

---

## B. Code change — new image

Never build on the VM. CI builds, the VM pulls.

**On your machine**, once the branch is merged:

```bash
gh workflow run build-images.yaml --ref deploy/voicera-vm \
  -f ref=deploy/voicera-vm -f service=all
gh run list --workflow build-images.yaml --branch deploy/voicera-vm --limit 1
```

Use `service=all` unless you are certain only one service changed. Note the
short SHA of the green run.

**On the VM** — config and image move together, in one step:

```bash
cd /opt/ai-diffusion-dpg
git checkout deploy/voicera-vm && git pull      # NO sudo

cd automation/deploy/shared-vm
cp .env .env.bak-$(date +%F)
sed -i "s/^DPG_IMAGE_TAG=.*/DPG_IMAGE_TAG=sha-<short>/" .env
grep DPG_IMAGE_TAG .env

sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
```

---

## C. Changing keys, or the Signals cluster

Secrets reach the containers through `init_secrets`, which writes
`/secrets/env` **once at startup**. Restarting the layers alone keeps the old
values — this has been mistaken for an upstream outage.

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
cp .env .env.bak-$(date +%F)                    # always; rollback is one copy
nano .env
```

| var | meaning |
| --- | --- |
| `OPENAI_API_KEY` | LLM key |
| `BLUE_DOTS_API_KEY` | Signals key. `init_secrets` derives `BLUE_DOTS_SEARCH_API_KEY` from it; do not set that separately unless the two genuinely differ |
| `BLUE_DOTS_ORG_ID` | `x-acting-org-id`. Not a credential, but environment-specific |
| `DPG_IMAGE_TAG` | which image runs |
| `DOMAIN` | which `dev-kit/configs/<domain>/` is mounted |

Then rewrite the secrets file and restart its consumers:

```bash
sudo docker compose up -d --force-recreate init_secrets
sudo docker compose logs --tail=5 init_secrets
#   init_secrets: wrote /secrets/env from env (openai=NN signals=NN)
#   "from env" = read from .env.  Lengths only — it never prints values.

sudo docker compose up -d --force-recreate action_gateway agent_core
```

### Switching Signals cluster

URLs live in config, credentials in `.env`, and **both must move together**.
Pointing at one cluster with another's keys gives 401/403, not a clear error.

```bash
grep -c 'signals.bluedotseconomy.org' \
  /opt/ai-diffusion-dpg/dev-kit/configs/blue-dots/action_gateway.yaml
#   expect 5
```

| | URL | org id |
| --- | --- | --- |
| UAT | `https://signals.bluedotseconomy.org` | `org_cdce36e6-6a38-4c2c-b264-c581dc8c62e2` |
| test cluster | `https://dev-signals.serveirc.com` | *(its own)* |

That count of **5** is the check worth doing. Four are `base_url`s; the fifth is
`apply_job`'s static `instance_url`, which travels inside the apply body. If
only four moved, applications reach the new cluster declaring the old one's
instance URL, and nothing fails loudly.

---

## Verifying any deploy

```bash
sudo docker compose ps                  # every DPG layer healthy
curl -s localhost:8008/health           # {"status":"ok"}
sudo docker compose logs --tail=40 agent_core action_gateway \
  | grep -iE 'error|traceback|validation|extra_forbidden'
```

Then one real turn — the only check that proves the whole chain, including
upstream auth:

```bash
curl -sN localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model":"blue-dots","stream":true,
    "messages":[{"role":"user","content":"नमस्ते"}],
    "metadata":{"caller_phone":"919700000201","call_id":"smoke-1"}}'
```

A reply means config loaded, the LLM key works, and `fetch_profile` reached
Signals and authenticated.

Use a phone number nobody else is testing with — every turn writes real
profiles upstream.

If it fails, the cause is usually right here:

```bash
sudo docker compose logs --tail=60 action_gateway | grep -E '40[0-9]|50[0-9]'
#   401 / 403 -> wrong key or org id for this cluster
#   404       -> base_url or path wrong
```

---

## Rolling back

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
cp .env.bak-<date> .env                                      # keys + image tag

cd /opt/ai-diffusion-dpg
git checkout <previous-commit>                               # config, NO sudo

cd automation/deploy/shared-vm
sudo docker compose pull
sudo docker compose up -d --force-recreate
```

Roll config and image back **together**, for the same reason they deploy
together.

---

## Things that have actually gone wrong here

- **Layers restarted but not `init_secrets`** after a key change, so the old key
  stayed live and it looked like an upstream outage.
- **`git pull` failed on ownership** after someone else deployed. Check
  `ls -ld /opt/ai-diffusion-dpg` and confirm with `git log --oneline -1` rather
  than assuming the pull worked. Prevented by never sudoing git.
- **Compose output suppressed** with `>/dev/null`, so a failed
  `--force-recreate` went unnoticed and the old container kept serving.
- **Image updated, config not** — new code read an old prompt, and nothing in
  the logs explained the odd behaviour.
- **Config updated, image not** — the layer did not start at all.
  `extra_forbidden` in `docker compose logs agent_core` is the signature.
- **The clone tracks a moving branch** (`deploy/voicera-vm`), so a `git pull`
  can bring more than you intended. Always read
  `git log --oneline HEAD..origin/deploy/voicera-vm` before pulling, and check
  whether anything under `*/src/` changed.
