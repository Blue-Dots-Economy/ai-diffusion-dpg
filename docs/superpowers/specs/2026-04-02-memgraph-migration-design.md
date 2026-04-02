# Design: Replace Neo4j with Memgraph in Memory Layer

**Date:** 2026-04-02
**Issue:** sanketika-labs/ai-diffusion-dpg#20
**Status:** Approved

---

## Context

The framework is a Digital Public Good (DPG) — all components must be open-source. Neo4j Community Edition carries GPL-3 and proprietary Enterprise licensing that restricts production use. Memgraph is fully open-source (BSL → Apache 2.0), wire-compatible with Neo4j via Bolt protocol and Cypher, and designed for low-latency in-memory graph workloads — a better fit for per-turn context graph reads.

---

## Approach

Option A — minimal rename + driver swap. No Cypher query changes. The `neo4j` Python driver (Apache 2.0) connects to Memgraph via Bolt protocol unchanged. The Neo4j server image is replaced; the client library is retained with a comment documenting the compliance rationale.

---

## Changes

### 1. Python source (`memory_layer/src/`)

**File and class renames:**

| Old file | New file | Old class | New class |
|---|---|---|---|
| `neo4j_user_store.py` | `graph_user_store.py` | `Neo4jUserStore` | `GraphUserStore` |
| `neo4j_journey_store.py` | `graph_journey_store.py` | `Neo4jJourneyStore` | `GraphJourneyStore` |
| `neo4j_context_store.py` | `graph_context_store.py` | `Neo4jContextStore` | `GraphContextStore` |

**`memory_layer.py`:**
- Imports updated from `neo4j_*` to `graph_*`
- Driver variable renamed: `_neo4j_driver` → `_graph_driver`
- Config block read from `memgraph:` instead of `neo4j:`
- All log operation strings updated: `neo4j_*` → `graph_*`

No Cypher query changes — all queries use standard parameterised Cypher and work unchanged against Memgraph via Bolt.

### 2. Config (`dev-kit/dpg/memory_layer.yaml`)

`neo4j:` block renamed to `memgraph:` with updated defaults:

```yaml
memgraph:
  uri: "bolt://memgraph:7687"
  user: "memgraph"
  password: null
  connection_timeout_s: 5
```

### 3. Python dependencies (`memory_layer/pyproject.toml`)

`neo4j>=5.0` dependency retained. Inline comment added:

```
"neo4j>=5.0",  # Apache 2.0 — connects to Memgraph via Bolt; Neo4j server not used
```

### 4. Docker Compose (`automation/docker/docker-compose.dev.yml`)

- Replace `neo4j` service with `memgraph` service:
  - Image: `memgraph/memgraph-platform`
  - Persistence flags: `--storage-wal-enabled=true`, `--storage-snapshot-interval-sec=300`
  - Volume: `memgraph_data:/var/lib/memgraph`
  - Healthcheck: `wget -qO- http://localhost:7444 || exit 1`
- Update `memory_layer` service:
  - Env vars: `NEO4J_URI/USER/PASSWORD` → `MEMGRAPH_URI/USER/PASSWORD`
  - `depends_on`: `neo4j` → `memgraph`
- Replace `neo4j_data` volume with `memgraph_data`

### 5. Helm (`automation/helm/memory-layer/`)

**`values.yaml`:**
- Rename `neo4j:` block to `memgraph:`, URI default updated to `bolt://memgraph:7687`

**`templates/deployment.yaml`:**
- Env vars renamed: `NEO4J_URI/USER/PASSWORD` → `MEMGRAPH_URI/USER/PASSWORD`

**New templates:**
- `templates/memgraph-deployment.yaml` — deploys Memgraph container with WAL/snapshot persistence flags
- `templates/memgraph-service.yaml` — ClusterIP service exposing Bolt port `7687` and HTTP port `7444`
- `templates/memgraph-pvc.yaml` — PersistentVolumeClaim for `memgraph_data`

### 6. Tests (`memory_layer/tests/`)

- Rename `test_neo4j_stores.py` → `test_graph_stores.py`
- Update imports and mock patch paths: `neo4j_user_store.Neo4jUserStore` → `graph_user_store.GraphUserStore`, etc.
- No new test logic — behaviour is unchanged, only names change
- Coverage target ≥70% must still pass

### 7. Documentation

- `ARCHITECTURE.md`: update Memory Layer section — replace Neo4j references with Memgraph
- `memory_layer/MemoryLayer_Design_v2.md`: update all Neo4j references to Memgraph

---

## Compatibility note

Memgraph is Bolt-protocol and Cypher compatible with Neo4j. The `neo4j` Python driver (Apache 2.0) connects to Memgraph without modification. All current Cypher queries in the three store files work without change — verified by the issue author.

## Persistence

Memgraph durability is configured via:
- `--storage-wal-enabled=true` — write-ahead log for crash recovery
- `--storage-snapshot-interval-sec=300` — periodic snapshots every 5 minutes

This satisfies the persistent store requirement while retaining in-memory read performance (<1ms per query vs ~5–20ms for Neo4j).

## Out of scope

- A standalone Helm chart for Memgraph — it is scoped as a backing store within the memory-layer chart
- Cypher query changes — no modifications needed
- Data migration from an existing Neo4j instance — PoC scope, no production data to migrate
