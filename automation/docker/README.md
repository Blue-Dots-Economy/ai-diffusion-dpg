# Running DPG Services with Docker Compose

## Prerequisites
- Docker Desktop installed and running
- `ANTHROPIC_API_KEY` (get one from [console.anthropic.com](https://console.anthropic.com))

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
