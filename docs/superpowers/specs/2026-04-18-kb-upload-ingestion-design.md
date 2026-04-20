# KB Document Upload & Ingestion Architecture Design

**Date:** 2026-04-20 (revised)
**Branch:** feat/devkit-file-upload-url-fetch
**Issue:** #130
**Status:** Production design — PoC is complete, this is a production feature.

---

## Goal

Allow operators to upload Knowledge Base (KB) documents into the Knowledge Engine after deployment. Documents are ingested into ChromaDB and immediately available to the agent's RAG pipeline. Supports local file upload and Azure Blob Storage fetch. All service-to-service communication is JWT-authenticated.

---

## Non-Goals

- OpenAPI spec upload to cloud storage (specs are config-time only — used for Action Gateway tool configuration and not needed after deploy)
- Multi-cloud in this iteration — StorageBackend ABC makes AWS S3 / GCP GCS addable later without API changes
- Re-ingestion scheduling / cron (manual trigger only)
- Per-user document scoping (future — tracked as open question below)
- Listing previously ingested files in the UI (future work)
- ChromaDB chunk deletion on file expiry (future — expiry column is stored, cleanup task is not implemented in this iteration)
- RAG retrieval filtering by enabled/disabled flag (future — column is stored, filtering is not implemented in this iteration)

---

## Architecture Overview

```
Dev-Kit (VM)
  ├─ Chat (knowledge phase): ask where docs are (local/cloud) → determine mode → save Azure creds if needed → project.json
  ├─ Deploy wizard: inject Azure creds + callback URL + JWT secrets as K8s secrets
  └─ IngestDocumentsStep (post-deploy, step 8):
       │  JWT (devkit-signed, sub=devkit, user_id=<config>)
       ▼
Reach Layer (VM, public-facing)
  └─ POST /ingest/upload  (streaming proxy)
       │  JWT (reach-signed, internal)
       ▼
Knowledge Engine (K8s, ClusterIP)
  └─ POST /upload → batch enqueue → async queue worker → ingest_single()
       │
       ├─ Mode A: cloud_upload_ingest → write to Azure Blob + ingest from PVC copy
       ├─ Mode B: cloud_fetch_ingest  → fetch from Azure Blob → ingest from PVC copy
       └─ Mode C: local_write_ingest  → write to /data/kb PVC → ingest
            │
            │  JWT (ke-signed, 5 min), per-job callback on completion
            ▼
Dev-Kit (VM)
  └─ POST /api/ingest/callback → update in-memory job map + project.json
       ▲
       │  Frontend polls every <poll_interval_seconds>s
       │  Stops on: ingested | failed | poll timeout (<poll_timeout_minutes>)
Dev-Kit Frontend
  └─ GET /api/ingest/job/{job_id}
```

### Approved Architecture Exception

Reach Layer calling KE for the upload proxy path is an approved exception to the rule *"Only Agent Core initiates calls to other blocks."* This is the second approved exception, alongside Reach Layer → Memory Layer (session restore). Scope is strictly bounded to `POST /ingest/upload` and `GET /ingest/job/{id}` proxy only. All other Reach Layer → KE paths remain prohibited.

---

## Dev-Kit Config File (new)

**Location:** `dev-kit/dev_kit/config/devkit.yaml`

All values are framework-scoped. This file is the single source of truth for dev-kit operational parameters.

```yaml
# dev-kit/dev_kit/config/devkit.yaml

# Identity used for all upload requests until login is implemented.
# When login is added, user_id will come from the authenticated session instead.
user_id: "devkit-operator"

upload:
  # Maximum number of files allowed per batch submission.
  max_files_per_upload: 5
  # Maximum size (MB) for a single file. Files exceeding this are rejected at the frontend.
  max_file_size_mb: 30
  # Supported file extensions for KB document upload.
  supported_extensions:
    - ".pdf"
    - ".txt"
    - ".md"
    - ".csv"
    - ".docx"
    - ".html"

polling:
  # How often the frontend polls for job status (seconds).
  poll_interval_seconds: 5
  # Maximum total polling duration before showing a timeout message (minutes).
  poll_timeout_minutes: 15
```

**Config loading:** `dev-kit/dev_kit/config/loader.py` reads `devkit.yaml` once at startup and exposes a `DevKitConfig` dataclass. Config values are never re-read in request paths.

---

## JWT Authentication Design

### Service Identity Model

Each service pair that communicates shares a distinct HS256 secret. Compromise of one secret does not affect other pairs.

| Caller | Callee | Secret name | Who holds what |
|--------|--------|-------------|----------------|
| Dev-Kit | Reach Layer | `DEVKIT_TO_REACH_SECRET` | Dev-Kit signs, Reach Layer verifies |
| Reach Layer | Knowledge Engine | `REACH_TO_KE_SECRET` | Reach Layer signs, KE verifies |
| Knowledge Engine | Dev-Kit | `KE_TO_DEVKIT_SECRET` | KE signs, Dev-Kit verifies |

### Token Payload (service-to-service)

```python
# Dev-Kit → Reach Layer token
{
    "sub": "devkit",
    "iss": "devkit",
    "user_id": "<devkit.yaml user_id>",   # stored in KE ingestion records table
    "iat": <unix timestamp>,
    "exp": <iat + 86400>,     # 24h lifetime for upload session tokens
}

# Reach Layer → KE token
{
    "sub": "reach_layer",
    "iss": "reach_layer",
    "iat": <unix timestamp>,
    "exp": <iat + 300>,       # 5 min — short-lived for internal forwarding
}

# KE → Dev-Kit callback token
{
    "sub": "knowledge_engine",
    "iss": "knowledge_engine",
    "iat": <unix timestamp>,
    "exp": <iat + 300>,       # 5 min — single callback use
}
```

`user_id` is extracted from the dev-kit JWT payload by KE and written to the ingestion records table. It is not a request body field — this prevents callers from forging arbitrary user IDs.

Reach layer already uses HS256 via PyJWT (`issue_session_token` / `verify_session_token` in `reach_layer/web/src/auth.py`). The same pattern is extended here.

### Secret Generation and Injection

Secrets are **auto-generated** at deploy time — not manually entered. Dev-kit generates three random 32-byte hex strings using `secrets.token_hex(32)` on first visit to MandatoryInputsStep and stores them in deploy state. They are injected into Helm values and become K8s secrets:

- `DEVKIT_TO_REACH_SECRET` → K8s secret in Reach Layer + env var in Dev-Kit
- `REACH_TO_KE_SECRET` → K8s secret in KE + env var in Reach Layer
- `KE_TO_DEVKIT_SECRET` → K8s secret in Dev-Kit + env var in KE

### Token Validation

Every service validates the incoming `Authorization: Bearer <token>` header via `verify_service_token(token, secret)` — a new function following the same shape as `verify_session_token`. Returns the decoded payload. Rejects expired, malformed, or wrong-issuer tokens with HTTP 401.

---

## 1. Knowledge Phase — Chat Changes

**What the agent collects (updated — no file list):**

The agent asks one question only: whether the operator has Azure Blob Storage. The per-file upload mode (local, fetch from Azure, or upload to Azure) is chosen per file at IngestDocumentsStep — not locked in during chat. This supports mixed batches (e.g. one file already in Azure, another on local disk) without any chat-phase changes.

```
Agent: "Do you have Azure Blob Storage for your KB documents?
        - If yes: I need your account name, account key, and container name.
        - If no: no setup needed — you will upload local files after deployment."

  ┌─ "Yes, I have Azure" ─────────────────────────────────────────────────────┐
  │  → Agent calls set_azure_storage({account_name, account_key,              │
  │    container_name})                                                        │
  │  → At IngestDocumentsStep, per file the operator chooses:                 │
  │      • "Fetch from Azure" (cloud_fetch_ingest) — file already in Azure    │
  │      • "Upload local + push to Azure" (cloud_upload_ingest) — local file  │
  │      • "Upload local only" (local_write_ingest) — local, no Azure needed  │
  └───────────────────────────────────────────────────────────────────────────┘

  ┌─ "No cloud storage" ──────────────────────────────────────────────────────┐
  │  → No tool call needed                                                     │
  │  → At IngestDocumentsStep, only "Upload local only" (local_write_ingest)  │
  │    is available (Azure option is hidden if no creds configured)            │
  └───────────────────────────────────────────────────────────────────────────┘
```

**Mixed batch example** — supported in a single "Upload & Ingest" submission:
- File A (`rural_jobs_handbook.pdf`): already in Azure → `cloud_fetch_ingest`
- File B (`terms.txt`): on local disk → `local_write_ingest`

Both are submitted in one batch request. KE processes each file with its own mode independently.

The `set_azure_storage` tool is only called if the operator has Azure. If not, the knowledge phase ends with no tool call.

**New agent tool** — `set_azure_storage`:

```json
{
  "name": "set_azure_storage",
  "description": "Save Azure Blob Storage credentials for KB document ingestion. Call only if the operator confirms they have Azure storage.",
  "input_schema": {
    "type": "object",
    "properties": {
      "account_name":   { "type": "string" },
      "account_key":    { "type": "string" },
      "container_name": { "type": "string" }
    },
    "required": ["account_name", "account_key", "container_name"]
  }
}
```

**Stored in `project.json` (azure_storage only — no document list):**

```json
{
  "slug": "rural-jobs-assistant",
  "azure_storage": {
    "account_name": "mystorageacct",
    "account_key": "BASE64KEY==",
    "container_name": "dpg-kb-docs"
  }
}
```

If the operator has no Azure storage, `azure_storage` is absent from `project.json`. Document filenames and details are **not** collected in the chat phase — operators add and upload files directly in IngestDocumentsStep after deployment.

> **Security note:** `project.json` is a local operator file equivalent to `.env`. Never commit the `configs/` directory to source control. A `.gitignore` entry must be added for `dev-kit/configs/*/project.json`. Encryption of `azure_storage.account_key` at rest is an open question — see §Open Questions.

---

## 2. Deploy Wizard — MandatoryInputsStep Changes

**New fields added:**

**Azure Blob Storage (conditional — only shown if `azure_storage` is present in `project.json`):**
- Azure Account Name (pre-filled from project.json, editable)
- Azure Account Key (pre-filled, masked)
- Azure Container Name (pre-filled, editable)

These become the `{{ .Release.Name }}-azure-creds` K8s secret in Helm (optional, only created if values provided).

**Dev-Kit Callback URL (always shown):**
- Label: "Dev-Kit Callback URL"
- Placeholder: `https://devkit.your-vm.example.com`
- Description: "The URL of this Dev-Kit instance, reachable from inside the Kubernetes cluster. The Knowledge Engine uses this to notify when ingestion completes."
- Stored in deploy state → becomes `KE_DEVKIT_CALLBACK_URL` env var in KE.

**KE Internal Service URL:**
- Label: "KE Internal Service URL"
- Placeholder: `http://knowledge-engine.dpg.svc.cluster.local:8001`
- Description: "Internal Kubernetes service URL for KE. Used by Reach Layer to proxy upload requests."
- Becomes `KE_INTERNAL_URL` env var in Reach Layer.

**JWT secrets** are auto-generated silently — dev-kit creates them on first visit to this step and stores in deploy state. Not shown to the operator.

---

## 3. Deploy Wizard — Step 8: Ingest Documents (new, post-deploy)

**Trigger:** After step 7 (DeployStatusStep) reports all services healthy.

**New component:** `IngestDocumentsStep.jsx`

**UI behaviour:**

- Operator starts with an empty file list.
- Clicking **"+ Add File"** appends a new row. Each row has:
  - Mode selector: `Local file` | `Upload local + push to Azure` | `Fetch from Azure`
    - Azure modes are only shown if `azure_storage` is present in `project.json` (i.e. operator configured Azure in chat phase)
    - If no Azure is configured, only `Local file` is available
  - If `Local file` or `Upload local + push to Azure`: file picker (filtered to `supported_extensions` from devkit.yaml)
  - If `Fetch from Azure`: text input for cloud_path (e.g. `docs/handbook.pdf`)
  - Filename display (auto-filled from selected file or derived from cloud_path)
  - Remove (×) button
- Each file in the batch can have a different mode — mixed batches are fully supported
- Maximum rows = `max_files_per_upload` from devkit.yaml. "+ Add File" is disabled once reached.
- Clicking **"Upload & Ingest"** submits all rows as a single batch request.
- Per-file size validated client-side before submission (`max_file_size_mb` from devkit.yaml).
- Duplicate filenames rejected before submission with an inline error.

**UI layout:**

```
Ingest Knowledge Documents
──────────────────────────────────────────────────────────
Upload your documents into the Knowledge Engine.
They will be ingested into the vector store immediately.

⚠ Max 5 files · Max 30 MB per file

  ┌────────────────────────────────┬────────────┬──────┐
  │ rural_jobs_handbook.pdf        │ Local file │  [×] │
  ├────────────────────────────────┼────────────┼──────┤
  │ docs/fasal_bima_guide.pdf      │ Azure Blob │  [×] │
  ├────────────────────────────────┼────────────┼──────┤
  │ ✓ terms_and_conditions.txt     │ Local file │  [×] │
  │   Ingested — 41 chunks         │            │      │
  └────────────────────────────────┴────────────┴──────┘

  [+ Add File]                [Upload & Ingest]

                                    [Skip]  [Done →]
```

**Per-file status states:** `pending` → `queued (position N)` → `ingesting…` → `✓ ingested (N chunks)` | `✗ failed: <reason>`

**Polling lifecycle:**
- Frontend starts polling `GET /api/ingest/job/{job_id}` every `poll_interval_seconds` after submission.
- Polling stops for a file when its status is `ingested` or `failed`.
- If all files in the batch reach a terminal state, polling stops completely.
- If total elapsed time exceeds `poll_timeout_minutes`:
  - If the poll API itself returns an error → KE is likely down → show: "Knowledge Engine may be unavailable. Re-select your files and try again."
  - If the poll API returns `ingesting` → KE is still processing → continue polling for another `poll_timeout_minutes` period.
- "Upload & Ingest" button is re-enabled for a new batch at any time (does not block on in-progress jobs).

---

## 4. Dev-Kit Backend — New Endpoints

**`POST /api/ingest/submit`**

Accepts a multipart batch from the browser, issues a service JWT, and forwards to Reach Layer.

**Request format:** `multipart/form-data`

```
--boundary
Content-Disposition: form-data; name="metadata"
Content-Type: application/json

[
  {"filename": "rural_jobs_handbook.pdf", "mode": "local_write_ingest"},
  {"filename": "fasal_bima_guide.pdf",    "mode": "cloud_fetch_ingest", "cloud_path": "docs/fasal_bima_guide.pdf"}
]
--boundary
Content-Disposition: form-data; name="files"; filename="rural_jobs_handbook.pdf"
Content-Type: application/octet-stream

<binary>
--boundary--
```

- `cloud_fetch_ingest` entries have no corresponding file part (no binary content needed).
- `local_write_ingest` and `cloud_upload_ingest` entries must have a matching file part (matched by `filename`, not by index).
- File parts without a matching metadata entry are rejected with HTTP 422.

**Dev-kit backend steps:**
1. Parse `metadata` JSON, validate each entry (extension, size ≤ `max_file_size_mb`, no path separators, no duplicates).
2. Issue a service JWT: `sub=devkit`, `user_id=<devkit.yaml user_id>`, signed with `DEVKIT_TO_REACH_SECRET`, 24h TTL.
3. Stream the full multipart body to `POST <REACH_LAYER_URL>/ingest/upload` with JWT in `Authorization: Bearer`.
4. Return batch response to frontend.

**Response:**
```json
{
  "batch_id": "a1b2c3d4-...",
  "jobs": [
    {"filename": "rural_jobs_handbook.pdf", "job_id": "550e8400-..."},
    {"filename": "fasal_bima_guide.pdf",    "job_id": "661f9511-..."}
  ]
}
```

---

**`GET /api/ingest/job/{job_id}`**

Returns current status from the in-memory job map. Called by the frontend poller.

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "rural_jobs_handbook.pdf",
  "status": "ingesting",
  "queue_position": null,
  "chunks_added": null,
  "error": null,
  "queued_at": "2026-04-20T10:22:00Z"
}
```

Status values: `queued` | `ingesting` | `ingested` | `failed`

---

**`POST /api/ingest/callback`**

Called by KE when a job completes. Protected by `KE_TO_DEVKIT_SECRET` JWT validation.

Request body:
```json
{
  "job_id": "550e8400-...",
  "status": "ingested",
  "chunks_added": 47,
  "error": null
}
```

Dev-kit steps:
1. Validate JWT (`Authorization: Bearer <ke-signed-token>`).
2. Update in-memory job map entry.
3. If status is `ingested`, persist to `project.json` (optional audit trail — `ingest_log` array).

---

## 5. Reach Layer — Upload Proxy (new)

**New endpoints on Reach Layer:**

**`POST /ingest/upload`**

Validates the dev-kit JWT, then **streams** the full multipart body to KE without buffering.

```python
@router.post("/ingest/upload")
async def ingest_upload(
    request: Request,
    authorization: str = Header(...),
):
    # 1. Validate dev-kit JWT
    verify_service_token(authorization.removeprefix("Bearer "), DEVKIT_TO_REACH_SECRET)

    # 2. Issue reach layer JWT for KE (5 min)
    ke_token = issue_service_token("reach_layer", REACH_TO_KE_SECRET, ttl=300)

    # 3. Stream (not buffer) multipart body to KE
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KE_INTERNAL_URL}/upload",
            content=request.stream(),      # streaming passthrough — no full-body buffer
            headers={
                "Content-Type": request.headers["Content-Type"],
                "Authorization": f"Bearer {ke_token}",
            },
            timeout=60.0,
        )
    return Response(content=response.content, status_code=response.status_code,
                    media_type=response.headers.get("content-type"))
```

> **Streaming is required.** `await request.body()` would buffer the entire batch in memory. With max 5 files × 30 MB, that is up to 150 MB. The proxy must use `request.stream()` to pass bytes through without accumulation.

**`GET /ingest/job/{job_id}`**

Polling fallback — proxies to `GET <KE_INTERNAL_URL>/upload/job/{job_id}` with a reach-signed JWT. Used when the dev-kit wants to confirm job status independently of callbacks.

**CORS:** Reach layer `cors.allowed_origins` must include the dev-kit VM URL. Configurable in YAML — not hardcoded:

```yaml
# reach_layer/web/config/reach_layer.yaml
cors:
  allowed_origins:
    - "https://devkit.your-vm.example.com"
    - "http://localhost:5173"   # dev mode only
ke_internal_url: "http://knowledge-engine.dpg.svc.cluster.local:8001"
```

---

## 6. Knowledge Engine — Upload API

**New endpoint:** `POST /upload`

Accepts a multipart batch, validates each file, inserts all rows into the SQLite ingestion records table atomically, then enqueues all jobs.

```python
@router.post("/upload")
async def upload_batch(
    request: Request,
    authorization: str = Header(...),
):
    # 1. Validate reach layer JWT; extract user_id from dev-kit token chain
    payload = verify_service_token(authorization.removeprefix("Bearer "), REACH_TO_KE_SECRET)
    user_id = payload.get("user_id", "unknown")

    # 2. Parse multipart: metadata JSON + file parts
    form = await request.form()
    metadata_entries = json.loads(form["metadata"])
    file_parts = {f.filename: f for f in form.getlist("files")}

    # 3. Validate each entry
    validated = []
    for entry in metadata_entries:
        filename = entry["filename"]
        mode = entry["mode"]
        safe_name = Path(filename).name
        if safe_name != filename or "/" in filename or "\\" in filename:
            raise HTTPException(422, f"Invalid filename: {filename}")
        ext = Path(safe_name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(422, f"Unsupported extension: {ext}")
        if mode in ("cloud_upload_ingest", "cloud_fetch_ingest") and not AZURE_CONFIGURED:
            raise HTTPException(400, "Azure storage not configured on this deployment")
        if mode in ("cloud_upload_ingest", "local_write_ingest") and safe_name not in file_parts:
            raise HTTPException(422, f"No file part found for: {filename}")
        if mode == "cloud_fetch_ingest" and not entry.get("cloud_path"):
            raise HTTPException(422, f"cloud_path required for: {filename}")
        validated.append((safe_name, mode, entry.get("cloud_path"), file_parts.get(safe_name)))

    # 4. Check queue capacity
    if ingest_queue.qsize() + len(validated) > MAX_QUEUE_SIZE:
        raise HTTPException(429, "Queue full — try again later")

    # 5. Atomic: insert all DB rows first, then enqueue all jobs
    batch_id = str(uuid.uuid4())
    jobs = []
    db_rows = []
    for safe_name, mode, cloud_path, file_obj in validated:
        job_id = str(uuid.uuid4())
        file_bytes = await file_obj.read() if file_obj else None
        db_rows.append(IngestionRecord(
            job_id=job_id, batch_id=batch_id, filename=safe_name, mode=mode,
            cloud_path=cloud_path, status="queued", user_id=user_id,
            file_size_bytes=len(file_bytes) if file_bytes else None,
        ))
        jobs.append(IngestJob(job_id, batch_id, safe_name, mode, cloud_path, file_bytes))

    try:
        db.insert_batch(db_rows)          # single SQLite transaction
    except Exception as e:
        raise HTTPException(500, f"DB insert failed: {e}")

    # Enqueue after successful DB insert. If enqueue fails, rollback DB rows.
    try:
        for job in jobs:
            await ingest_queue.put(job)
            job_store[job.job_id] = JobStatus(job.job_id, job.filename, "queued",
                                               queue_position=ingest_queue.qsize())
    except Exception as e:
        db.rollback_batch(batch_id)
        raise HTTPException(500, f"Queue enqueue failed: {e}")

    return {
        "batch_id": batch_id,
        "jobs": [{"filename": j.filename, "job_id": j.job_id} for j in jobs],
    }
```

**`GET /upload/job/{job_id}`**

Returns job status from `job_store`. Reached via reach layer proxy when dev-kit polls for fallback status.

```json
{
  "job_id": "550e8400-...",
  "status": "queued",
  "queue_position": 2,
  "chunks_added": null,
  "error": null
}
```

**Async Queue Worker** (started once at KE startup):

```python
async def _queue_worker():
    """Process upload jobs sequentially. One job at a time to protect ChromaDB writes."""
    while True:
        job = await ingest_queue.get()
        job_store[job.job_id].status = "ingesting"
        db.update_status(job.job_id, "ingesting")
        try:
            storage = get_storage_backend()
            file_path = await _stage_file(storage, job)   # write to PVC or fetch from Azure
            chunks = block.ingest_single(config, file_path)
            job_store[job.job_id] = JobStatus(job.job_id, job.filename, "ingested", chunks_added=chunks)
            db.update_status(job.job_id, "ingested", chunks_added=chunks, ingested_at=utcnow())
            await _send_callback(job.job_id, "ingested", chunks_added=chunks)
        except Exception as e:
            logger.error("ke.ingest_failed", extra={"job_id": job.job_id, "error": str(e), "status": "failure"})
            job_store[job.job_id] = JobStatus(job.job_id, job.filename, "failed", error=str(e))
            db.update_status(job.job_id, "failed", error=str(e))
            await _send_callback(job.job_id, "failed", error=str(e))
        finally:
            _cleanup_temp_file(job)   # always remove temp staging files
            ingest_queue.task_done()
```

**Queue constraints:**
- `MAX_QUEUE_SIZE` = 20 (accommodates multiple concurrent operator sessions).
- Job IDs: `uuid.uuid4()` — non-enumerable.
- `job_store` (in-memory): lost on pod restart. Jobs in flight at restart must be re-submitted by the operator. This is documented operational behaviour.

**KE Callback to Dev-Kit:**

```python
async def _send_callback(job_id: str, status: str, **kwargs):
    """Notify dev-kit of job completion. Retries 3× with exponential backoff."""
    if not DEVKIT_CALLBACK_URL:
        return
    token = issue_service_token("knowledge_engine", KE_TO_DEVKIT_SECRET, ttl=300)
    payload = {"job_id": job_id, "status": status, **kwargs}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{DEVKIT_CALLBACK_URL}/api/ingest/callback",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0,
                )
                if r.status_code < 500:
                    return   # 2xx or 4xx — do not retry
        except Exception:
            pass
        await asyncio.sleep(2 ** attempt)   # 1s, 2s, 4s
    logger.warning("ke.callback_failed", extra={"job_id": job_id, "status": "failure", "operation": "send_callback"})
    # Fallback: dev-kit polling via reach layer will eventually detect completion.
```

**AZURE_CONFIGURED check:** At startup, KE checks for `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, `AZURE_CONTAINER_NAME`. Sets `AZURE_CONFIGURED = True` if all are present. Cloud mode requests fail fast with HTTP 400 if not configured.

---

## 7. KE Ingestion Records — SQLite

**Purpose:** Persistent audit log of all uploaded and ingested files. Enables future features: expiry-based cleanup, per-file enable/disable, per-user scoping.

**SQLite file location:** `/data/kb/ke_metadata.db` — stored on the same PVC as KB documents. Survives pod restarts. Lost only if the PVC is deleted.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS ingestion_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL UNIQUE,       -- UUID4 per file
    batch_id        TEXT NOT NULL,              -- UUID4 per batch submission
    filename        TEXT NOT NULL,              -- sanitized basename
    file_size_bytes INTEGER,                    -- null for cloud_fetch_ingest
    source_type     TEXT NOT NULL,              -- local | cloud
    cloud_path      TEXT,                       -- null if local
    mode            TEXT NOT NULL,              -- cloud_upload_ingest | cloud_fetch_ingest | local_write_ingest
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued | ingesting | ingested | failed | expired
    chunks_added    INTEGER,                    -- null until ingested
    error           TEXT,                       -- null unless failed
    user_id         TEXT NOT NULL,              -- from JWT sub; devkit.yaml value until login is implemented
    uploaded_at     TEXT NOT NULL,              -- ISO 8601 UTC
    ingested_at     TEXT,                       -- null until ingested
    expires_at      TEXT,                       -- null if no expiry; ISO 8601 UTC
    enabled         INTEGER NOT NULL DEFAULT 1  -- 1=enabled, 0=disabled (future RAG filter)
);

CREATE INDEX IF NOT EXISTS idx_batch_id ON ingestion_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_filename  ON ingestion_records(filename);
CREATE INDEX IF NOT EXISTS idx_user_id   ON ingestion_records(user_id);
```

**What is implemented now vs future:**

| Column | Implemented now | Future work |
|--------|----------------|-------------|
| `status` | Written on every state change | — |
| `chunks_added` | Written on `ingested` | — |
| `expires_at` | Stored (null if not set) | Background cleanup task to delete expired chunks from ChromaDB |
| `enabled` | Stored (default 1) | `StaticKnowledgeBaseBlock.retrieve()` filters by `enabled=1` |

**`db` module location:** `knowledge_engine/src/db/ingestion_db.py`

```python
class IngestionDB:
    """SQLite-backed store for ingestion records.

    Future: if Redis is needed for speed, implement a parallel class following
    the same method signatures. No ABC is used now — YAGNI — but the interface
    is narrow enough to replace without affecting callers.
    """

    def __init__(self, db_path: Path): ...
    def insert_batch(self, records: list[IngestionRecord]) -> None: ...   # single transaction
    def rollback_batch(self, batch_id: str) -> None: ...
    def update_status(self, job_id: str, status: str, **kwargs) -> None: ...
    def get_record(self, job_id: str) -> IngestionRecord | None: ...
```

---

## 8. Storage Abstraction

**Location:** `knowledge_engine/src/storage/`

```
knowledge_engine/src/storage/
  __init__.py       ← get_storage_backend() factory
  base.py           ← StorageBackend ABC
  azure_blob.py     ← AzureBlobStorageBackend
  local_pvc.py      ← LocalPVCStorageBackend
```

**`base.py`:**

```python
from abc import ABC, abstractmethod

class StorageBackend(ABC):
    """Abstract base for KB document storage backends.

    Concrete implementations: AzureBlobStorageBackend, LocalPVCStorageBackend.
    Add AWS S3 / GCP GCS by subclassing — no changes to callers required.
    """

    @abstractmethod
    def upload(self, content: bytes, filename: str) -> str:
        """Upload content and return the storage path (blob name or absolute local path).

        Args:
            content: Raw file bytes.
            filename: Basename only — no path separators.

        Raises:
            StorageError: On upload failure after retries.
        """

    @abstractmethod
    def download(self, path: str) -> bytes:
        """Download content from the given path.

        Args:
            path: Blob name or absolute local path.

        Raises:
            StorageError: If path does not exist or download fails.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the backend is reachable and writable."""
```

**`azure_blob.py`** — uses `azure-storage-blob>=12.0`. Reads credentials from env vars at construction time (not per-request). Timeout + retry on all SDK calls.

**`local_pvc.py`** — writes to `/data/kb/`. PVC is mounted at this path via Helm chart. For `cloud_upload_ingest` mode: file is written to `/data/kb/` *and* uploaded to Azure Blob (both).

**Factory:**

```python
def get_storage_backend() -> StorageBackend:
    """Return Azure backend if all Azure env vars are set, else local PVC."""
    acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key  = os.environ.get("AZURE_STORAGE_KEY")
    cont = os.environ.get("AZURE_CONTAINER_NAME")
    if acct and key and cont:
        return AzureBlobStorageBackend(acct, key, cont)
    return LocalPVCStorageBackend()
```

**`_stage_file` helper** (inside queue worker):

| Mode | Operator scenario | Action |
|------|-------------------|--------|
| `local_write_ingest` | File on local machine, no Azure | Write bytes to `/data/kb/<filename>` (PVC). Ingest from PVC. |
| `cloud_upload_ingest` | File on local machine, operator has Azure | Write bytes to `/data/kb/<filename>` (PVC) AND upload to `AzureBlobStorageBackend`. Ingest from PVC. |
| `cloud_fetch_ingest` | File already in Azure Blob Storage | Download from Azure via `AzureBlobStorageBackend.download(cloud_path)` → write to `/data/kb/<filename>` (PVC). Ingest from PVC. |

> **Why "ingest from PVC" in all modes?** ChromaDB runs inside the KE container and reads documents from the local filesystem only — it cannot read directly from Azure. All three modes therefore produce a local copy of the file on the PVC (`/data/kb/<filename>`) before `ingest_single()` chunks and embeds it. For `cloud_upload_ingest`, the PVC copy is the original bytes the operator uploaded. For `cloud_fetch_ingest`, it is the file downloaded from Azure. The PVC copy persists permanently (it is the source of truth for re-ingestion). For `cloud_fetch_ingest`, the downloaded file is the "staging copy" that is kept on the PVC after ingestion — it is not cleaned up, because it is the only local copy.

**Temp file cleanup:** Only truly temporary files (intermediate buffers, partial downloads) are cleaned up in `finally`. The staged `/data/kb/<filename>` is kept permanently on the PVC.

---

## 9. StaticKnowledgeBaseBlock — `ingest_single`

```python
def ingest_single(self, config: dict, file_path: Path) -> int:
    """Ingest a single document into the existing ChromaDB collection.

    Deletes all existing chunks for this filename before re-ingesting,
    ensuring no duplicate chunks on re-upload of the same file.
    Appends to the collection — does not affect other documents.

    Serialization is guaranteed by the queue worker (one job at a time).
    No asyncio.Lock is needed here.

    Args:
        config: Full KE YAML config dict.
        file_path: Absolute path to the document on /data/kb PVC (must exist).

    Returns:
        Number of chunks added.

    Raises:
        ValueError: If file format is not supported.
        KnowledgeEngineError: If ChromaDB write fails.
    """
```

**Deduplication:** Before adding chunks, query ChromaDB for all entries with `metadata.source == file_path.name` and delete them. Handles re-upload cleanly.

---

## 10. K8s Helm Chart Changes

**`knowledge-engine/templates/deployment.yaml`:**
- **Remove** `initContainers` block entirely (replaced by upload API)
- Add `/data/kb` PVC volume mount
- Add Azure + JWT + callback env vars from K8s secrets

```yaml
containers:
  - name: knowledge-engine
    env:
      - name: AZURE_STORAGE_ACCOUNT
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-azure-creds
            key: account_name
            optional: true
      - name: AZURE_STORAGE_KEY
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-azure-creds
            key: account_key
            optional: true
      - name: AZURE_CONTAINER_NAME
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-azure-creds
            key: container_name
            optional: true
      - name: KE_DEVKIT_CALLBACK_URL
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-ingest-config
            key: devkit_callback_url
      - name: KE_TO_DEVKIT_SECRET
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-jwt-secrets
            key: ke_to_devkit_secret
      - name: REACH_TO_KE_SECRET
        valueFrom:
          secretKeyRef:
            name: {{ .Release.Name }}-jwt-secrets
            key: reach_to_ke_secret
    volumeMounts:
      - name: kb-data
        mountPath: /data/kb
volumes:
  - name: kb-data
    persistentVolumeClaim:
      claimName: {{ .Release.Name }}-kb-data
```

**New K8s secrets:**
- `{{ .Release.Name }}-azure-creds` — optional; created only if `azureStorage.enabled = true` in values
- `{{ .Release.Name }}-ingest-config` — `devkit_callback_url`
- `{{ .Release.Name }}-jwt-secrets` — all three JWT shared secrets (auto-generated by dev-kit)

**New PVC (`pvc-kb.yaml`):**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .Release.Name }}-kb-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: {{ .Values.kbStorage.size | default "5Gi" }}
```

The SQLite metadata DB (`ke_metadata.db`) lives on this same PVC alongside the KB document files.

**NetworkPolicy (optional, `networkpolicy.yaml`):**

```yaml
{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ .Release.Name }}-ke-egress
spec:
  podSelector:
    matchLabels:
      app: {{ .Release.Name }}
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock:
            cidr: {{ .Values.networkPolicy.devkitCIDR }}
      ports:
        - port: 443
        - port: 80
{{- end }}
```

Supports both same-VM (loopback CIDR) and cross-VM deployments. When `networkPolicy.enabled = false`, default cluster egress rules apply.

---

## 11. Affected Files Summary

| File | Change |
|------|--------|
| `dev-kit/dev_kit/config/devkit.yaml` | **New** — framework config: user_id, max_files, max_size, polling params |
| `dev-kit/dev_kit/config/loader.py` | **New** — `DevKitConfig` dataclass + YAML loader |
| `dev-kit/dev_kit/agent/tools.py` | Add `set_azure_storage` tool + handler (replaces old `set_knowledge_documents`) |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Update knowledge phase prompt — ask about cloud storage only, no file list |
| `dev-kit/dev_kit/agent/app.py` | Add `POST /api/ingest/submit`, `GET /api/ingest/job/{id}`, `POST /api/ingest/callback` |
| `dev-kit/dev_kit/agent/auth.py` | **New** — `issue_service_token`, `verify_service_token` (HS256) |
| `dev-kit/frontend/src/api.js` | Add `submitIngestBatch(slug, formData)`, `getJobStatus(jobId)` |
| `dev-kit/frontend/src/components/deploy/DeployWizard.jsx` | Add step 8, bump max step to 8 |
| `dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx` | **New** — add-file UI, batch submission, per-file polling with timeout |
| `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx` | Add Azure fields (conditional), callback URL field, KE internal URL field |
| `dev-kit/configs/.gitignore` | Add `*/project.json` |
| `reach_layer/web/server.py` | Add `POST /ingest/upload` (streaming proxy), `GET /ingest/job/{id}` proxy |
| `reach_layer/web/src/auth.py` | Add `issue_service_token`, `verify_service_token` |
| `reach_layer/web/config/reach_layer.yaml` | Add `cors.allowed_origins`, `ke_internal_url` |
| `reach_layer/web/templates/deployment.yaml` (Helm) | Add JWT env vars (`DEVKIT_TO_REACH_SECRET`, `REACH_TO_KE_SECRET`), `KE_INTERNAL_URL` |
| `knowledge_engine/src/storage/base.py` | **New** — `StorageBackend` ABC |
| `knowledge_engine/src/storage/azure_blob.py` | **New** — `AzureBlobStorageBackend` |
| `knowledge_engine/src/storage/local_pvc.py` | **New** — `LocalPVCStorageBackend` |
| `knowledge_engine/src/storage/__init__.py` | **New** — `get_storage_backend()` factory |
| `knowledge_engine/src/db/ingestion_db.py` | **New** — `IngestionDB` (SQLite, `/data/kb/ke_metadata.db`) |
| `knowledge_engine/src/blocks/static_knowledge_base.py` | Add `ingest_single()` method |
| `knowledge_engine/src/upload_router.py` | **New** — `POST /upload`, `GET /upload/job/{id}`, queue worker, callback sender |
| `knowledge_engine/src/auth.py` | **New** — `issue_service_token`, `verify_service_token` |
| `knowledge_engine/src/app.py` | Register `upload_router`, start queue worker on startup, init `IngestionDB` |
| `knowledge_engine/pyproject.toml` | Add `azure-storage-blob>=12.0` |
| `automation/helm/dpg/knowledge-engine/templates/deployment.yaml` | Remove `initContainers`, add env vars, add `/data/kb` volume mount |
| `automation/helm/dpg/knowledge-engine/templates/secret-azure.yaml` | **New** — optional Azure K8s secret |
| `automation/helm/dpg/knowledge-engine/templates/secret-ingest.yaml` | **New** — callback URL + JWT secrets |
| `automation/helm/dpg/knowledge-engine/templates/pvc-kb.yaml` | **New** — kb-data PVC (holds both KB docs and SQLite DB) |
| `automation/helm/dpg/knowledge-engine/templates/networkpolicy.yaml` | **New** — optional KE egress to dev-kit |
| `automation/helm/dpg/knowledge-engine/values.yaml` | Add `azureStorage.*`, `kbStorage.size`, `networkPolicy.*` |

---

## 12. Data Flow: End-to-End

```
CHAT PHASE (Knowledge Phase)
  Agent: "Do you have Azure Blob Storage or will you upload files locally?"
  User: "Azure — mystorageacct / BASE64KEY== / dpg-kb-docs"
  Agent → set_azure_storage({...}) → saved to project.json

DEPLOY WIZARD (MandatoryInputsStep)
  Dev-kit reads project.json → pre-fills Azure fields + callback URL
  Operator verifies/edits → confirms
  Dev-kit silently auto-generates 3 JWT secrets → stored in deploy state
  Deploy executes → K8s secrets created → KE starts without init-container

POST-DEPLOY (IngestDocumentsStep — step 8)
  Operator clicks "+ Add File" → selects rural_jobs_handbook.pdf (local)
  Operator clicks "+ Add File" → enters "docs/fasal_bima_guide.pdf" (Azure fetch)
  Operator clicks "Upload & Ingest"

  1. Frontend validates all entries (extension ✓, size ✓, no duplicates ✓)

  2. Browser builds multipart body:
       metadata = [
         {"filename": "rural_jobs_handbook.pdf",  "mode": "local_write_ingest"},
         {"filename": "fasal_bima_guide.pdf",      "mode": "cloud_fetch_ingest",
          "cloud_path": "docs/fasal_bima_guide.pdf"}
       ]
       files = [rural_jobs_handbook.pdf binary]

  3. Browser → POST /api/ingest/submit (devkit backend)

  4. Dev-kit backend:
       - Validates entries ✓
       - Issues JWT: sub=devkit, user_id=devkit-operator (24h)
       - Streams multipart to POST https://reach.vm.example.com/ingest/upload

  5. Reach Layer:
       - Validates dev-kit JWT ✓
       - Issues reach JWT (5 min)
       - Streams multipart to POST http://ke.dpg.svc.cluster.local:8001/upload

  6. KE:
       - Validates reach JWT ✓, extracts user_id=devkit-operator
       - Sanitizes filenames ✓
       - Validates extensions ✓
       - batch_id = UUID4
       - job_1 = UUID4 for rural_jobs_handbook.pdf
       - job_2 = UUID4 for fasal_bima_guide.pdf
       - SQLite: INSERT both rows (single transaction) ✓
       - Queue: enqueue job_1, job_2 ✓
       - Returns: {batch_id, jobs: [{filename, job_id}, ...]}

  7. Response flows back → frontend shows both files as "Queued (position 1/2)"

  Frontend polls GET /api/ingest/job/<job_1> and GET /api/ingest/job/<job_2> every 5s

  KE QUEUE WORKER processes job_1:
    - LocalPVCStorageBackend.upload(bytes, "rural_jobs_handbook.pdf")
      → writes /data/kb/rural_jobs_handbook.pdf
    - ingest_single(config, Path("/data/kb/rural_jobs_handbook.pdf"))
      → deletes old chunks for this filename (if any)
      → chunks + embeds → 47 chunks added to ChromaDB
    - SQLite: UPDATE status=ingested, chunks_added=47, ingested_at=now()
    - POST https://devkit.vm.example.com/api/ingest/callback
        Authorization: Bearer <ke-signed JWT, 5min>
        {"job_id": "<job_1>", "status": "ingested", "chunks_added": 47}

  Dev-kit callback endpoint:
    - Validates KE JWT ✓
    - Updates job_store[job_1].status = "ingested"

  Next frontend poll for job_1 → {status: "ingested", chunks_added: 47}
  UI: row 1 shows ✓ Ingested (47 chunks). Polling for job_1 stops.

  KE processes job_2 (cloud_fetch_ingest) similarly:
    - AzureBlobStorageBackend.download("docs/fasal_bima_guide.pdf") → bytes
    - Writes to /data/kb/fasal_bima_guide.pdf (temp local copy)
    - ingest_single → 33 chunks
    - Cleanup temp file
    - Callback → dev-kit → UI: row 2 ✓ Ingested (33 chunks)

  All files ingested. Polling stops completely.
  Operator clicks "Done →" → deploy flow complete.
```

---

## 13. Error Handling

| Failure | HTTP | Behaviour |
|---------|------|-----------|
| Invalid or expired JWT | 401 | Rejected at validation layer; not forwarded |
| Filename with path separators | 422 | Rejected at KE immediately |
| Unsupported file extension | 422 | Rejected at dev-kit backend and KE |
| File > `max_file_size_mb` | 413 | Rejected at dev-kit backend (from devkit.yaml limit) |
| More files than `max_files_per_upload` | 422 | Rejected at dev-kit backend |
| Duplicate filename in batch | — | Rejected at frontend before submission |
| Queue full (20 jobs) | 429 | KE returns 429; frontend shows "Queue full — try again" |
| Azure not configured for cloud mode | 400 | KE returns 400; shown in UI per file |
| Azure auth failure | 401/403 | Azure SDK error; propagated as 502 from KE |
| Azure blob not found | 404 | Azure SDK error; propagated as 404 from KE |
| SQLite insert fails | 500 | No jobs enqueued; operator retries the batch |
| Queue enqueue fails after DB insert | 500 | DB rows rolled back; operator retries |
| ChromaDB write fails | 500 | Job marked `failed` in DB; retry by re-uploading file |
| KE callback fails (all retries) | — | Logged; dev-kit polling via reach layer detects completion |
| KE pod restart mid-ingestion | — | `job_store` lost; poll API returns 404 → UI shows "KE unavailable, re-select files" |
| Poll timeout (configured max) | — | If poll 404/error → "KE may be unavailable, re-select files"; if still `ingesting` → extend polling another full period |
| Temp file leak | — | `try/finally` in queue worker guarantees cleanup |
| KE unreachable from reach layer | 503 | Reach layer returns 503; dev-kit shows "KE unreachable" |

---

## 14. Security Controls Summary

| Control | Where | Mechanism |
|---------|-------|-----------|
| Dev-kit → reach layer auth | Reach layer | Verify dev-kit JWT (HS256, DEVKIT_TO_REACH_SECRET) |
| Reach layer → KE auth | KE | Verify reach JWT (HS256, REACH_TO_KE_SECRET) |
| KE → dev-kit callback auth | Dev-kit | Verify KE JWT (HS256, KE_TO_DEVKIT_SECRET) |
| user_id source of truth | KE | Extracted from JWT payload — not from request body |
| Path traversal prevention | KE | `Path(filename).name`; reject if `"/" in filename` |
| File extension whitelist | Dev-kit backend + KE | From `devkit.yaml supported_extensions`; double-enforced |
| File size limit | Dev-kit frontend + backend | From `devkit.yaml max_file_size_mb` |
| Batch size limit | Dev-kit backend | From `devkit.yaml max_files_per_upload` |
| Duplicate file prevention | Dev-kit frontend | Client-side check before submission |
| Queue size limit | KE | HTTP 429 if `queue.qsize() + new_jobs > MAX_QUEUE_SIZE` |
| UUID job IDs | KE | `uuid.uuid4()` — non-enumerable |
| Azure creds in K8s | KE | Stored as K8s Opaque secret; mounted via `secretKeyRef` |
| CORS for dev-kit origin | Reach layer | Configurable `cors.allowed_origins` in reach_layer.yaml |
| KE egress to dev-kit | Helm NetworkPolicy | Optional; CIDR-scoped; supports same-VM and cross-VM |
| No bash/curl direct access | KE `/upload` | Requires valid reach layer JWT — unauthenticated requests rejected with 401 |

---

## 15. Open Questions

1. **Azure account_key encryption in project.json** — Stored unencrypted in the operator's local `project.json`. Should it be encrypted (OS keychain, project-level key)?.

2. **JWT secret custodianship** — Shared secrets are auto-generated by dev-kit at deploy time. Should they be externally managed (HashiCorp Vault, Azure Key Vault)?.

3. **JWT secret rotation** — No rotation mechanism. Compromised secret requires full redeploy to replace K8s secrets. Rotation procedure needed?.

4. **JWT secret stability across redeployments** — JWT secrets are currently auto-generated fresh on each deploy wizard run. The devkit→reach JWT has a 24h lifetime. If the operator deploys at 9am (secrets generated + pushed to K8s), then uploads documents at 3pm, then triggers another deploy at 5pm for any reason (config change, Helm update), new secrets are generated and the K8s secrets are overwritten. The reach layer and KE now hold new secrets. The 24h JWT issued at 9am is immediately invalid — all in-flight or pending uploads fail with 401. **Recommendation:** generate the 3 JWT secrets once on first deploy and store them in `project.json`. On subsequent deploys, read from `project.json` and reuse them — only regenerate if the operator explicitly requests a secret rotation. Flagged for lead review.

5. **Per-user document scoping** — Future: each end-user uploads personal documents via Reach Layer. Requires per-user ChromaDB sub-collections or `user_id` metadata filtering at retrieval time. `ingest_single` should accept an optional `scope` parameter to allow this without API redesign. No decision made — tracked as future work.

6. **Job status durability** — `job_store` is in-memory, lost on pod restart. For production, persist to SQLite (the table already exists)..
