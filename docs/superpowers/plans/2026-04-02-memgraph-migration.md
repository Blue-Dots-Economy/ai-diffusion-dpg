# Memgraph Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Neo4j server with Memgraph in the Memory Layer while keeping all Cypher queries and the `neo4j` Python driver (Apache 2.0, Bolt-compatible) unchanged.

**Architecture:** The `neo4j` Python driver connects to Memgraph via the Bolt protocol without code changes. Only the server image, config key names, env var names, and Python file/class names change. All Cypher queries are identical.

**Tech Stack:** Python 3.11, `neo4j>=5.0` driver (Bolt), Memgraph (`memgraph/memgraph-platform`), Redis, FastAPI, Docker Compose, Helm.

---

## File Map

| Action | File |
|---|---|
| Rename + update | `memory_layer/src/neo4j_user_store.py` → `graph_user_store.py` |
| Rename + update | `memory_layer/src/neo4j_journey_store.py` → `graph_journey_store.py` |
| Rename + update | `memory_layer/src/neo4j_context_store.py` → `graph_context_store.py` |
| Modify | `memory_layer/src/memory_layer.py` |
| Modify | `memory_layer/pyproject.toml` |
| Modify | `dev-kit/dpg/memory_layer.yaml` |
| Modify | `automation/docker/docker-compose.dev.yml` |
| Modify | `automation/helm/memory-layer/values.yaml` |
| Modify | `automation/helm/memory-layer/templates/deployment.yaml` |
| Create | `automation/helm/memory-layer/templates/memgraph-deployment.yaml` |
| Create | `automation/helm/memory-layer/templates/memgraph-service.yaml` |
| Create | `automation/helm/memory-layer/templates/memgraph-pvc.yaml` |
| Rename + update | `memory_layer/tests/test_neo4j_stores.py` → `test_graph_stores.py` |
| Modify | `ARCHITECTURE.md` |
| Modify | `memory_layer/MemoryLayer_Design_v2.md` |

---

## Task 1: Rename and update graph store files

**Files:**
- Create: `memory_layer/src/graph_user_store.py`
- Create: `memory_layer/src/graph_journey_store.py`
- Create: `memory_layer/src/graph_context_store.py`
- Delete (after): `memory_layer/src/neo4j_user_store.py`, `neo4j_journey_store.py`, `neo4j_context_store.py`

- [ ] **Step 1: Copy `neo4j_user_store.py` to `graph_user_store.py`**

```bash
cp memory_layer/src/neo4j_user_store.py memory_layer/src/graph_user_store.py
```

- [ ] **Step 2: Rename class and log strings in `graph_user_store.py`**

Replace the module docstring first line:
```
# memory_layer/src/graph_user_store.py
```

Replace class name:
```python
# Old
class Neo4jUserStore:

# New
class GraphUserStore:
```

Replace all log operation strings (use find-and-replace):
- `"neo4j_user_store.init"` → `"graph_user_store.init"`
- `"neo4j_user_store.user_exists"` → `"graph_user_store.user_exists"`
- `"neo4j_user_store.create_user_graph"` → `"graph_user_store.create_user_graph"`
- `"neo4j_user_store.get_profile"` → `"graph_user_store.get_profile"`
- `"neo4j_user_store.upsert_profile_field"` → `"graph_user_store.upsert_profile_field"`
- `"neo4j_user_store.delete_user"` → `"graph_user_store.delete_user"`

Also rename the error log keys (same pattern, e.g. `"neo4j_user_store.user_exists_error"` → `"graph_user_store.user_exists_error"`).

The `from neo4j import GraphDatabase, Driver` import line stays unchanged — the `neo4j` package is still the driver.

- [ ] **Step 3: Copy `neo4j_journey_store.py` to `graph_journey_store.py`**

```bash
cp memory_layer/src/neo4j_journey_store.py memory_layer/src/graph_journey_store.py
```

- [ ] **Step 4: Rename class and log strings in `graph_journey_store.py`**

Replace class name:
```python
# Old
class Neo4jJourneyStore:

# New
class GraphJourneyStore:
```

Replace all log operation strings:
- `"neo4j_journey_store.*"` → `"graph_journey_store.*"` (all occurrences)

- [ ] **Step 5: Copy `neo4j_context_store.py` to `graph_context_store.py`**

```bash
cp memory_layer/src/neo4j_context_store.py memory_layer/src/graph_context_store.py
```

- [ ] **Step 6: Rename class and log strings in `graph_context_store.py`**

Replace class name:
```python
# Old
class Neo4jContextStore:

# New
class GraphContextStore:
```

Replace all log operation strings:
- `"neo4j_context_store.*"` → `"graph_context_store.*"` (all occurrences)

- [ ] **Step 7: Delete the old neo4j_* files**

```bash
rm memory_layer/src/neo4j_user_store.py
rm memory_layer/src/neo4j_journey_store.py
rm memory_layer/src/neo4j_context_store.py
```

- [ ] **Step 8: Verify no neo4j_* references remain in src/**

```bash
grep -r "neo4j_user_store\|neo4j_journey_store\|neo4j_context_store\|Neo4jUserStore\|Neo4jJourneyStore\|Neo4jContextStore" memory_layer/src/
```

Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add memory_layer/src/
git commit -m "refactor: rename neo4j_*_store files and classes to graph_*_store (GH-20)"
```

---

## Task 2: Update `memory_layer.py` imports and driver init

**Files:**
- Modify: `memory_layer/src/memory_layer.py`

- [ ] **Step 1: Update imports**

In `memory_layer/src/memory_layer.py`, replace:

```python
from neo4j import GraphDatabase

from session_store import RedisSessionStore
from neo4j_user_store import Neo4jUserStore
from neo4j_journey_store import Neo4jJourneyStore
from neo4j_context_store import Neo4jContextStore
```

With:

```python
from neo4j import GraphDatabase

from session_store import RedisSessionStore
from graph_user_store import GraphUserStore
from graph_journey_store import GraphJourneyStore
from graph_context_store import GraphContextStore
```

- [ ] **Step 2: Update driver init block**

Replace the Neo4j init block (lines ~104–117):

```python
        # Initialise Neo4j driver + stores
        neo4j_cfg = config.get("neo4j", {})
        neo4j_uri = neo4j_cfg.get("uri", "bolt://localhost:7687")
        neo4j_user = neo4j_cfg.get("user", "neo4j")
        neo4j_password = neo4j_cfg.get("password", "neo4j")
        neo4j_timeout = neo4j_cfg.get("connection_timeout_s", 5)

        self._neo4j_driver = GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
            connection_timeout=neo4j_timeout,
        )
        self._user_store = Neo4jUserStore(self._neo4j_driver, self._declared_fields)
        self._journey_store = Neo4jJourneyStore(self._neo4j_driver, journey_children)
        self._context_store = Neo4jContextStore(self._neo4j_driver)
```

With:

```python
        # Initialise Memgraph driver + stores (neo4j driver connects via Bolt — Apache 2.0)
        memgraph_cfg = config.get("memgraph", {})
        memgraph_uri = memgraph_cfg.get("uri", "bolt://localhost:7687")
        memgraph_user = memgraph_cfg.get("user", "memgraph")
        memgraph_password = memgraph_cfg.get("password", "")
        memgraph_timeout = memgraph_cfg.get("connection_timeout_s", 5)

        self._graph_driver = GraphDatabase.driver(
            memgraph_uri,
            auth=(memgraph_user, memgraph_password),
            connection_timeout=memgraph_timeout,
        )
        self._user_store = GraphUserStore(self._graph_driver, self._declared_fields)
        self._journey_store = GraphJourneyStore(self._graph_driver, journey_children)
        self._context_store = GraphContextStore(self._graph_driver)
```

- [ ] **Step 3: Verify no neo4j_ or Neo4j references remain in memory_layer.py**

```bash
grep -n "neo4j_\|Neo4j" memory_layer/src/memory_layer.py
```

Expected: no output (the `from neo4j import GraphDatabase` import is fine — that's the package name, not a store reference).

- [ ] **Step 4: Commit**

```bash
git add memory_layer/src/memory_layer.py
git commit -m "refactor: update memory_layer.py to use graph_* stores and memgraph config (GH-20)"
```

---

## Task 3: Update Python dependencies and DPG config

**Files:**
- Modify: `memory_layer/pyproject.toml`
- Modify: `dev-kit/dpg/memory_layer.yaml`

- [ ] **Step 1: Update `pyproject.toml` comment**

In `memory_layer/pyproject.toml`, replace:

```toml
    "neo4j>=5.0",        # persistent graph store (User, Journey, Context nodes)
```

With:

```toml
    "neo4j>=5.0",        # Apache 2.0 — connects to Memgraph via Bolt; Neo4j server not used
```

- [ ] **Step 2: Update `dev-kit/dpg/memory_layer.yaml`**

Replace the entire `neo4j:` block:

```yaml
neo4j:
  uri: "bolt://neo4j:7687"
  user: "neo4j"
  password: null               
  connection_timeout_s: 5
```

With:

```yaml
memgraph:
  uri: "bolt://memgraph:7687"
  user: "memgraph"
  password: null
  connection_timeout_s: 5
```

- [ ] **Step 3: Commit**

```bash
git add memory_layer/pyproject.toml dev-kit/dpg/memory_layer.yaml
git commit -m "refactor: update pyproject.toml and memory_layer.yaml for Memgraph (GH-20)"
```

---

## Task 4: Update tests

**Files:**
- Create: `memory_layer/tests/test_graph_stores.py`
- Delete: `memory_layer/tests/test_neo4j_stores.py`

- [ ] **Step 1: Copy test file**

```bash
cp memory_layer/tests/test_neo4j_stores.py memory_layer/tests/test_graph_stores.py
```

- [ ] **Step 2: Update imports and class/test names in `test_graph_stores.py`**

Replace imports:

```python
# Old
from src.neo4j_user_store import Neo4jUserStore
from src.neo4j_context_store import Neo4jContextStore
from src.neo4j_journey_store import Neo4jJourneyStore

# New
from src.graph_user_store import GraphUserStore
from src.graph_context_store import GraphContextStore
from src.graph_journey_store import GraphJourneyStore
```

Replace all class names throughout the file:
- `Neo4jUserStore` → `GraphUserStore`
- `Neo4jContextStore` → `GraphContextStore`
- `Neo4jJourneyStore` → `GraphJourneyStore`

Replace all test class names:
- `TestNeo4jUserStoreInit` → `TestGraphUserStoreInit`
- `TestNeo4jUserStoreUserExists` → `TestGraphUserStoreUserExists`
- `TestNeo4jUserStoreCreateUserGraph` → `TestGraphUserStoreCreateUserGraph`
- `TestNeo4jUserStoreGetProfile` → `TestGraphUserStoreGetProfile`
- `TestNeo4jUserStoreUpsertProfileField` → `TestGraphUserStoreUpsertProfileField`
- `TestNeo4jUserStoreDeleteUser` → `TestGraphUserStoreDeleteUser`
- `TestNeo4jContextStoreInit` → `TestGraphContextStoreInit`
- `TestNeo4jContextStoreCreateSignal` → `TestGraphContextStoreCreateSignal`
- `TestNeo4jContextStoreGetSignals` → `TestGraphContextStoreGetSignals`
- `TestNeo4jJourneyStoreInit` → `TestGraphJourneyStoreInit`
- `TestNeo4jJourneyStoreCreateJourney` → `TestGraphJourneyStoreCreateJourney`
- `TestNeo4jJourneyStoreCloseJourney` → `TestGraphJourneyStoreCloseJourney`
- `TestNeo4jJourneyStoreGetLastJourneySummary` → `TestGraphJourneyStoreGetLastJourneySummary`
- `TestNeo4jJourneyStoreCreateJourneyChild` → `TestGraphJourneyStoreCreateJourneyChild`
- `TestNeo4jJourneyStoreMergeSessionFields` → `TestGraphJourneyStoreMergeSessionFields`

Also update the module docstring first line:
```python
# Old
"""
tests/test_neo4j_stores.py

Unit tests for Neo4jUserStore, Neo4jContextStore, and Neo4jJourneyStore.

# New
"""
tests/test_graph_stores.py

Unit tests for GraphUserStore, GraphContextStore, and GraphJourneyStore.
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd memory_layer && uv run pytest tests/test_graph_stores.py -v
```

Expected: all tests pass (same logic, only names changed).

- [ ] **Step 4: Delete old test file**

```bash
rm memory_layer/tests/test_neo4j_stores.py
```

- [ ] **Step 5: Run full test suite with coverage**

```bash
cd memory_layer && uv run pytest --cov=src --cov-report=term-missing
```

Expected: coverage ≥70%, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add memory_layer/tests/
git commit -m "test: rename test_neo4j_stores to test_graph_stores, update all class references (GH-20)"
```

---

## Task 5: Update Docker Compose

**Files:**
- Modify: `automation/docker/docker-compose.dev.yml`

- [ ] **Step 1: Replace the `neo4j` service block**

In `automation/docker/docker-compose.dev.yml`, replace the entire `neo4j:` service block (lines 55–75):

```yaml
  # ---------------------------------------------------------------------------
  # Neo4j — persistent user profile store for Memory Layer
  # ---------------------------------------------------------------------------
  neo4j:
    image: neo4j:5
    container_name: neo4j
    environment:
      - NEO4J_AUTH=neo4j/dpg_password          # override at runtime for production
    volumes:
      - neo4j_data:/data
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.2'
          memory: 512M
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: unless-stopped
```

With:

```yaml
  # ---------------------------------------------------------------------------
  # Memgraph — persistent user profile store for Memory Layer (open-source, Bolt-compatible)
  # ---------------------------------------------------------------------------
  memgraph:
    image: memgraph/memgraph-platform
    container_name: memgraph
    command: [
      "/usr/lib/memgraph/memgraph",
      "--storage-wal-enabled=true",
      "--storage-snapshot-interval-sec=300"
    ]
    volumes:
      - memgraph_data:/var/lib/memgraph
    networks:
      - dpg_net
    deploy:
      resources:
        limits:
          cpus: '0.2'
          memory: 512M
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7444 || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: unless-stopped
```

- [ ] **Step 2: Update `memory_layer` service env vars and depends_on**

Replace env vars in the `memory_layer` service:

```yaml
# Old
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=dpg_password            # must match NEO4J_AUTH above

# New
      - MEMGRAPH_URI=bolt://memgraph:7687
      - MEMGRAPH_USER=memgraph
      - MEMGRAPH_PASSWORD=
```

Replace `depends_on` in the `memory_layer` service:

```yaml
# Old
      neo4j:
        condition: service_healthy

# New
      memgraph:
        condition: service_healthy
```

- [ ] **Step 3: Update volumes block at the bottom of the file**

Replace:

```yaml
  neo4j_data:                              # Neo4j graph database, persisted across container restarts
```

With:

```yaml
  memgraph_data:                           # Memgraph graph database, persisted across container restarts
```

- [ ] **Step 4: Verify no neo4j references remain**

```bash
grep -n "neo4j\|NEO4J" automation/docker/docker-compose.dev.yml
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add automation/docker/docker-compose.dev.yml
git commit -m "refactor: replace Neo4j with Memgraph in docker-compose.dev.yml (GH-20)"
```

---

## Task 6: Update Helm chart

**Files:**
- Modify: `automation/helm/memory-layer/values.yaml`
- Modify: `automation/helm/memory-layer/templates/deployment.yaml`
- Create: `automation/helm/memory-layer/templates/memgraph-deployment.yaml`
- Create: `automation/helm/memory-layer/templates/memgraph-service.yaml`
- Create: `automation/helm/memory-layer/templates/memgraph-pvc.yaml`

- [ ] **Step 1: Update `values.yaml`**

Replace the `neo4j:` block:

```yaml
# Old
# Neo4j connection — persistent user profile store.
# Override password at install time: --set neo4j.password=<secret>
neo4j:
  uri: bolt://neo4j:7687
  user: neo4j
  password: dpg_password
```

With:

```yaml
# Memgraph connection — persistent user profile store (open-source, Bolt-compatible).
# Override password at install time: --set memgraph.password=<secret>
memgraph:
  uri: bolt://memgraph:7687
  user: memgraph
  password: ""
  storageClass: ""          # leave empty to use cluster default
  storageSize: 1Gi
  image: memgraph/memgraph-platform
  tag: latest
```

- [ ] **Step 2: Update `templates/deployment.yaml` env vars**

Replace:

```yaml
            - name: NEO4J_URI
              value: {{ .Values.neo4j.uri | quote }}
            - name: NEO4J_USER
              value: {{ .Values.neo4j.user | quote }}
            - name: NEO4J_PASSWORD
              value: {{ .Values.neo4j.password | quote }}
```

With:

```yaml
            - name: MEMGRAPH_URI
              value: {{ .Values.memgraph.uri | quote }}
            - name: MEMGRAPH_USER
              value: {{ .Values.memgraph.user | quote }}
            - name: MEMGRAPH_PASSWORD
              value: {{ .Values.memgraph.password | quote }}
```

- [ ] **Step 3: Create `templates/memgraph-deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-memgraph
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}-memgraph
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ .Release.Name }}-memgraph
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}-memgraph
    spec:
      containers:
        - name: memgraph
          image: "{{ .Values.memgraph.image }}:{{ .Values.memgraph.tag }}"
          imagePullPolicy: IfNotPresent
          args:
            - "--storage-wal-enabled=true"
            - "--storage-snapshot-interval-sec=300"
          ports:
            - containerPort: 7687   # Bolt
            - containerPort: 7444   # HTTP status
          volumeMounts:
            - name: memgraph-data
              mountPath: /var/lib/memgraph
          resources:
            requests:
              cpu: 100m
              memory: 512Mi
            limits:
              cpu: 500m
              memory: 1Gi
          readinessProbe:
            httpGet:
              path: /
              port: 7444
            initialDelaySeconds: 15
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 7444
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: memgraph-data
          persistentVolumeClaim:
            claimName: {{ .Release.Name }}-memgraph-data
```

- [ ] **Step 4: Create `templates/memgraph-service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: memgraph
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}-memgraph
spec:
  type: ClusterIP
  selector:
    app: {{ .Release.Name }}-memgraph
  ports:
    - name: bolt
      port: 7687
      targetPort: 7687
    - name: http
      port: 7444
      targetPort: 7444
```

- [ ] **Step 5: Create `templates/memgraph-pvc.yaml`**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .Release.Name }}-memgraph-data
  namespace: {{ .Release.Namespace }}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {{ .Values.memgraph.storageClass | default "" }}
  resources:
    requests:
      storage: {{ .Values.memgraph.storageSize }}
```

- [ ] **Step 6: Verify no neo4j references remain in the memory-layer chart**

```bash
grep -rn "neo4j\|NEO4J" automation/helm/memory-layer/
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add automation/helm/memory-layer/
git commit -m "refactor: replace Neo4j with Memgraph in memory-layer Helm chart (GH-20)"
```

---

## Task 7: Update documentation

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `memory_layer/MemoryLayer_Design_v2.md`

- [ ] **Step 1: Update `ARCHITECTURE.md`**

Make the following replacements (use find-and-replace):

| Find | Replace |
|---|---|
| `Redis + Neo4j (not in-process dict)` | `Redis + Memgraph (not in-process dict)` |
| `Redis (session/profile store, RedisJSON) + Neo4j` | `Redis (session/profile store, RedisJSON) + Memgraph` |
| `Neo4j context graph:` | `Memgraph context graph:` |
| `memory_layer/src/neo4j_user_store.py`, `neo4j_journey_store.py`, `neo4j_context_store.py` | `memory_layer/src/graph_user_store.py`, `graph_journey_store.py`, `graph_context_store.py` |
| `Redis (session/profile) + Neo4j (context graph)` | `Redis (session/profile) + Memgraph (context graph)` |
| `Neo4j typed attribute graph` | `Memgraph typed attribute graph` |
| All remaining `Neo4j` → `Memgraph` and `neo4j` → `memgraph` (lower-case) in the Memory Layer sections |  |

Key lines to update (line numbers from grep output):
- Line 56: section heading
- Line 60: current implementation description
- Line 62: context graph description
- Line 170: table row
- Line 178: edge types paragraph
- Line 183: file list
- Line 442: implementation status table
- Line 463: context graph row
- Lines 494–518: new/returning user sequence diagrams

Also update the `backend:` field in the config section (line ~403):

```yaml
# Old
    backend: neo4j

# New
    backend: memgraph
```

And update all sequence diagram lines that say `neo4j.find_user`, `neo4j.create_user_graph`, `neo4j.create_journey`:

```
# Old
3. Memory Layer: neo4j.find_user(user_id) → None  (new user)
4. Memory Layer: neo4j.create_user_graph(user_id)
5. Memory Layer: neo4j.create_journey(user_id, journey_id)

# New
3. Memory Layer: memgraph.find_user(user_id) → None  (new user)
4. Memory Layer: memgraph.create_user_graph(user_id)
5. Memory Layer: memgraph.create_journey(user_id, journey_id)
```

Also update all Redis/Neo4j hot-path references that say "Neo4j is keyed by user_id":

```
# Old
Redis is keyed by `session_id`. Neo4j is keyed by `user_id`.

# New
Redis is keyed by `session_id`. Memgraph is keyed by `user_id`.
```

- [ ] **Step 2: Update `memory_layer/MemoryLayer_Design_v2.md`**

Replace all occurrences of `Neo4j` with `Memgraph` and `neo4j` with `memgraph` in this file. The section heading `## 3. Neo4j Graph Model` should become `## 3. Memgraph Graph Model`.

- [ ] **Step 3: Verify no stale Neo4j server references remain**

```bash
grep -n "Neo4j\|neo4j" ARCHITECTURE.md memory_layer/MemoryLayer_Design_v2.md
```

Review output — the only acceptable remaining occurrences are references to the `neo4j` Python driver package (Apache 2.0) or the `neo4j` import in source file headers. All references to the Neo4j _server_ should now say Memgraph.

- [ ] **Step 4: Commit**

```bash
git add ARCHITECTURE.md memory_layer/MemoryLayer_Design_v2.md
git commit -m "docs: update ARCHITECTURE.md and MemoryLayer_Design_v2.md for Memgraph migration (GH-20)"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full memory_layer test suite**

```bash
cd memory_layer && uv run pytest --cov=src --cov-report=term-missing -v
```

Expected: all tests pass, coverage ≥70%.

- [ ] **Step 2: Verify no stale neo4j store references anywhere in the repo**

```bash
grep -rn "neo4j_user_store\|neo4j_journey_store\|neo4j_context_store\|Neo4jUserStore\|Neo4jJourneyStore\|Neo4jContextStore" .
```

Expected: no output.

- [ ] **Step 3: Verify env var consistency across Docker and Helm**

```bash
grep -n "NEO4J\|neo4j" automation/docker/docker-compose.dev.yml automation/helm/memory-layer/values.yaml automation/helm/memory-layer/templates/deployment.yaml
```

Expected: no output.

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "refactor: complete Neo4j → Memgraph migration in Memory Layer (GH-20)"
```
