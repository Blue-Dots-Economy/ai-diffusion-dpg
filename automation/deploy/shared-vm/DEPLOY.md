# Updating a shared VM that is already running

First-time setup is in `README.md`. This covers every subsequent deploy, which
is what we actually do, and where the recurring failures live.

```bash
cd /opt/ai-diffusion-dpg
```

**`sudo` on docker, never on git.** Docker needs `sudo` on this VM. Git must not
be sudoed — the clone is owned by the deploying user, and a single
`sudo git pull` leaves root-owned objects that make the next person's pull fail
with a permissions error that looks nothing like its cause.

**This guide is branch-agnostic.** Substitute `<BRANCH>` with whatever you are
deploying — `develop`, `main`, a release branch, a fix branch you want to try on
the VM. The steps do not change. Pick it once and use the same one throughout:

```bash
BRANCH=develop        # or main, or any branch you want on the VM
```

> The clone tracks a moving branch, and a branch can be **deleted** upstream
> while the VM still points at it. If `git pull` complains that the upstream is
> gone, that is what happened — §1 switches you cleanly to another branch.

---

## First: which kind of change is this?

The commonest way a deploy appears to do nothing is getting this wrong.

| changed | needs |
| --- | --- |
| `dev-kit/configs/**`, `dev-kit/dpg/**` | git pull + restart |
| any `*/src/**` Python | **new image** |
| `.env` values | restart the consumer (and `init_secrets` for the keys it manages) |
| `docker-compose.yml` | `up -d` |

> **Config is mounted from the clone, not baked into the image.** A correct
> `DPG_IMAGE_TAG` with a stale checkout runs new code against an old prompt, and
> the symptoms look like a model problem rather than a deploy problem.

### Check before you assume it is config-only

```bash
cd /opt/ai-diffusion-dpg
git fetch origin "$BRANCH"
git log --oneline HEAD..origin/"$BRANCH"                          # what you are missing
git diff --name-only HEAD..origin/"$BRANCH" | grep -E '^[a-z_]+/src/'
```

**Any output from that last command means you need a new image.** Deploying the
config alone will then fail: layer configs are `extra="forbid"`, so a config
declaring a key the old image's schema does not know is a hard startup error,
not a warning. Look for `extra_forbidden` in the logs if a layer will not start.

---

## 1. Point the clone at the branch you are deploying

```bash
cd /opt/ai-diffusion-dpg
git status                                   # expect a clean tree; stash or discard first
git fetch origin "$BRANCH"
git checkout -B "$BRANCH" origin/"$BRANCH"   # works whether or not the local branch exists,
                                             # and recovers from a deleted upstream branch
git log --oneline -1                         # confirm the commit you expect
git branch -vv | head -3                     # confirm what it now tracks
```

`checkout -B` is deliberate: plain `git checkout <branch> && git pull` fails if
the local branch is missing, or if the branch it used to track no longer exists.

---

## 2. Edit `.env`

`.env` is **not** in git — it is per-VM, and it is where everything
environment-specific lives. Always back it up first; rollback is then one copy.

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
cp .env .env.bak-$(date +%F-%H%M)
```

Open it with whichever editor you prefer:

```bash
nano .env          # arrows to move, edit in place
                   #   Ctrl+O then Enter to save, Ctrl+X to quit
                   #   Ctrl+K cuts a line, Ctrl+W searches

vi .env            # i to start typing, Esc to stop
                   #   :wq Enter to save and quit, :q! Enter to quit discarding
                   #   /TOOL_RESULT Enter jumps to the first match
```

### What sits in `.env`

| var | what it is | notes |
| --- | --- | --- |
| `DPG_IMAGE_TAG` | which image runs, e.g. `sha-386f3e7` | **must match the checkout** — see the warning above |
| `DOMAIN` | which `dev-kit/configs/<domain>/` is mounted | `blue-dots` |
| `BLUE_DOTS_ORG_ID` | `x-acting-org-id` sent upstream | environment-specific, not a credential |
| `SIGNALS_BASE_URL` | Signals API | all three Signals URLs must name the **same** cluster |
| `SIGNALS_SEARCH_URL` | job search | |
| `SIGNALS_INSTANCE_URL` | travels **inside** the apply body | the one people forget — see §4 |
| `TOOL_RESULT_KEY_SECRET` | HMAC key that pseudonymises owner ids in the tool-result store | **required** — see below |
| `AWS_REGION`, `SSM_PREFIX` | where `init_secrets` fetches keys from | `OPENAI_API_KEY` / `BLUE_DOTS_API_KEY` come from Parameter Store |
| `LOG_LEVEL` | `INFO` normally, `DEBUG` to diagnose | |
| `HITL_WEBHOOK_URL`, `HITL_WEBHOOK_SECRET` | human-handoff receiver | only used when the domain sets `trust.hitl.queue_backend: webhook`; leave blank otherwise |
| `GF_SECURITY_ADMIN_PASSWORD`, `DISCORD_WEBHOOK_*` | Grafana and alerting | |

#### `TOOL_RESULT_KEY_SECRET` — required, and silent when missing

Generate one **per VM**, once:

```bash
openssl rand -hex 32
```

It is not an auth credential and never leaves the host. It HMACs the session/user
id into the Redis key name, so a caller's phone number never appears as a key.

**Leaving it empty does not merely disable a cache.** Tool results stop surviving
between turns, so a job list read out on one turn is gone by the next, the
caller's pick resolves against nothing, and the call ends with **no profile and
no application** while the bot still says both happened. Do not share one value
across environments, and keep it stable on a given VM.

Then verify what you typed:

```bash
grep -E '^(DPG_IMAGE_TAG|DOMAIN|TOOL_RESULT_KEY_SECRET)=' .env
grep -E '^SIGNALS_(BASE|SEARCH|INSTANCE)_URL=' .env
```

---

## 3. Deploy

### Config-only change

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
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

### Code change — new image

Never build on the VM. CI builds, the VM pulls.

**On your machine**, once the branch has what you want:

```bash
gh workflow run build-images.yaml --ref "$BRANCH" -f ref="$BRANCH" -f service=all
gh run list --workflow build-images.yaml --branch "$BRANCH" --limit 1
```

Use `service=all` unless you are certain only one service changed. Note the
short SHA of the green run — that is your `DPG_IMAGE_TAG`.

**On the VM** — config and image move together:

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
```

### Key changes managed by `init_secrets`

`OPENAI_API_KEY` and `BLUE_DOTS_API_KEY` reach the containers through
`init_secrets`, which writes `/secrets/env` **once at startup**. Restarting the
layers alone keeps the old values — this has been mistaken for an upstream
outage.

```bash
sudo docker compose up -d --force-recreate init_secrets
sudo docker compose logs --tail=5 init_secrets
#   init_secrets: wrote /secrets/env from env (openai=NN signals=NN)
#   lengths only — it never prints values

sudo docker compose up -d --force-recreate action_gateway agent_core
```

`TOOL_RESULT_KEY_SECRET` is **not** one of these: compose passes it straight to
`memory_layer`, so recreating that container is enough.

---

## 4. Switching Signals cluster

URLs and credentials must move together. Pointing at one cluster with another's
keys gives 401/403, not a clear error.

Since the URLs became env-driven, the config holds only variable references —
so the check is on `.env`, not the YAML:

```bash
grep -E '^SIGNALS_(BASE|SEARCH|INSTANCE)_URL=' .env     # all three, same cluster
grep -E '^BLUE_DOTS_ORG_ID=' .env                       # the matching org id
```

To confirm the config still binds all five places to those variables:

```bash
grep -vE '^\s*#' /opt/ai-diffusion-dpg/dev-kit/configs/"$DOMAIN"/action_gateway.yaml \
  | grep -cE '(base_url|value):.*\$\{SIGNALS_(BASE|SEARCH|INSTANCE)_URL'
#   expect 5
```

Four are `base_url`s; the fifth is `apply_job`'s `instance_url`, which travels
inside the apply body. **If only the base URLs move, applications reach the new
cluster declaring the old one's instance URL, and nothing fails loudly.**

> Do not grep for the hostname itself — the file documents the defaults in
> comments, so a hostname count reads higher than the number of real settings.

---

## 5. Verify

```bash
sudo docker compose ps                  # every DPG layer healthy
curl -s localhost:8008/health           # {"status":"ok"}

sudo docker compose logs --tail=40 agent_core action_gateway \
  | grep -iE 'error|traceback|validation|extra_forbidden'

sudo docker compose logs memory_layer | grep tool_result_store
#   SILENCE is success.
#   "tool_result_store.disabled" => TOOL_RESULT_KEY_SECRET did not reach the
#   container. Fix .env and recreate memory_layer before going further.
```

Then one real turn — the only check that proves the whole chain, including
upstream auth:

```bash
curl -sN localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model":"blue-dots","stream":true,
    "messages":[{"role":"user","content":"नमस्ते"}],
    "metadata":{"caller_phone":"919900099001","call_id":"smoke-1"}}'
```

And for anything touching the conversation, a **full** call on one phone and
call_id — consent, age, trade, city, pick a job, apply — then confirm the
profile and the application exist upstream. A healthy container proves the
process started, not that a caller can be served.

The per-turn diagnostics are on the turn banner once calls are flowing:

```bash
sudo docker compose logs --tail=200 agent_core \
  | grep -oE 'acts=[^ ]+ +relation=[^ ]+ +pending=[^ ]+ +resolved=[A-Za-z]+'
```

---

## 6. Rollback

```bash
cd /opt/ai-diffusion-dpg/automation/deploy/shared-vm
cp .env.bak-<the one you made> .env
sudo docker compose up -d
```

To go back to a previous image, set the old `DPG_IMAGE_TAG` and
`sudo docker compose up -d`. If the config also moved, check the old branch or
commit out in the clone as well — they must match.

---

## What has actually gone wrong here

- **A config pull without a matching image.** Layer configs are `extra="forbid"`;
  a new key against an old image is a hard startup failure, not a warning.
- **A new image with a stale checkout.** New code, old prompt. Looks like a model
  problem.
- **`sudo git pull`.** Root-owned objects; the next person's pull fails with an
  unrelated-looking error.
- **Changing a key and restarting only the layers.** `init_secrets` writes
  `/secrets/env` at startup; without recreating it the old value stays.
- **Moving the Signals base URLs but not `instance_url`.** Applications go to the
  new cluster declaring the old one. Nothing errors.
- **Leaving `TOOL_RESULT_KEY_SECRET` empty.** One startup line in `memory_layer`
  is the only warning; every call then silently writes nothing.
- **Tracking a branch that was deleted upstream.** `git pull` fails in a way that
  reads like a network problem. Use `git checkout -B` as in §1.
