# KB Document Upload & Ingestion Architecture Design

**Date:** 2026-04-18
**Branch:** feat/devkit-file-upload-url-fetch
**Issue:** #130
**Status:** Production design — PoC is complete, this is a production feature.

---

## Goal

Allow operators to upload Knowledge Base (KB) documents into the Knowledge Engine after deployment. Documents are ingested into ChromaDB and immediately available to the agent's RAG pipeline. Supports local file upload and Azure Blob Storage fetch. All service-to-service communication is JWT-authenticated.

---

## Non-Goals

- OpenAPI spec upload to cloud storage (specs are config-time only)
- Multi-cloud in this iteration — ABC makes AWS S3 / GCP GCS addable later
- Re-ingestion scheduling / cron (manual trigger only)
- Per-user document scoping (future — tracked as open question below)

---

## Architecture Overview

```
Dev-Kit (VM)
  ├─ Chat phase: collect doc list + Azure creds → project.json
  ├─ Deploy wizard: inject Azure creds + callback URL + JWT secrets as K8s secrets
  └─ IngestDocumentsStep (post-deploy):
       │  JWT (devkit-signed)
       ▼
Reach Layer (VM, public)
  └─ /ingest/upload proxy
       │  JWT (reach-signed, internal)
       ▼
Knowledge Engine (K8s, ClusterIP)
  └─ POST /upload → async queue → ingest_single()
       │
       ├─ Mode A: cloud_upload_ingest → Azure Blob + ChromaDB
       ├─ Mode B: cloud_fetch_ingest  → Azure Blob fetch + ChromaDB
       └─ Mode C: local_write_ingest  → /data/kb PVC + ChromaDB
            │
            │  JWT (ke-signed), callback on completion
            ▼
Dev-Kit (VM)
  └─ POST /api/ingest/callback → update job map
       ▲
       │  Frontend polls every 5s
Dev-Kit Frontend
  └─ GET /api/ingest/job/{id}/status
```

### Approved Architecture Exception

Reach Layer calling KE for the upload proxy path is an approved exception to the rule *"Only Agent Core initiates calls to other blocks."* This is the second approved exception after Reach Layer → Memory Layer (session restore). Scope is strictly bounded to `POST /ingest/upload` proxy. All other reach layer → KE paths remain prohibited.

---

## JWT Authentication Design

### Service Identity Model

Each service pair that communicates shares a distinct HS256 secret. This keeps token validation isolated — compromise of one secret does not affect other pairs.

| Caller | Callee | Secret name | Who holds what |
|--------|--------|-------------|----------------|
| Dev-Kit | Reach Layer | `DEVKIT_TO_REACH_SECRET` | Dev-Kit signs, Reach Layer verifies |
| Reach Layer | Knowledge Engine | `REACH_TO_KE_SECRET` | Reach Layer signs, KE verifies |
| Knowledge Engine | Dev-Kit | `KE_TO_DEVKIT_SECRET` | KE signs, Dev-Kit verifies |

### Token Payload (service-to-service)

```python
{
    "sub": "devkit",          # or "reach_layer" / "knowledge_engine"
    "iss": "devkit",          # same as sub
    "iat": <unix timestamp>,
    "exp": <iat + 86400>,     # 24h lifetime for service tokens
}
```

Reach layer already uses HS256 via PyJWT (`issue_session_token` / `verify_session_token` in `reach_layer/web/src/auth.py`). The same pattern is extended to the three service-token pairs above using the same algorithm and validation logic.

### Secret Generation and Injection

Secrets are **auto-generated** by the dev-kit at deploy time — not manually entered by the operator. Dev-kit generates three random 32-byte hex strings using `secrets.token_hex(32)` when the deploy wizard begins, stores them in deploy state, and injects them into the appropriate Helm values:

- `DEVKIT_TO_REACH_SECRET` → K8s secret in Reach Layer + env var in Dev-Kit
- `REACH_TO_KE_SECRET` → K8s secret in KE + env var in Reach Layer
- `KE_TO_DEVKIT_SECRET` → K8s secret in Dev-Kit + env var in KE

### Token Validation Flow

Every service validates the incoming `Authorization: Bearer <token>` header using `verify_service_token(token, secret)` — a new function following the same shape as `verify_session_token`. Returns the `sub` claim. Rejects expired, malformed, or wrong-issuer tokens with HTTP 401.

---

## 1. Knowledge Phase — Chat Changes

**What the agent collects:**

```
After configuring the knowledge base config sections, ask:

1. "What documents will the Knowledge Engine use?
   For each, tell me: filename, a brief description, and whether
   the file is stored locally or already in Azure Blob Storage."

   Collect per document:
     - filename      (e.g. rural_jobs_handbook.pdf)
     - description   (optional)
     - source_type   (local | cloud)
     - cloud_path    (blob path if source_type=cloud, e.g. docs/handbook.pdf)

2. "Do you have Azure Blob Storage for cloud documents or backup?
   If yes, I need: account name, account key, and container name."
```

**New agent tool** — `set_knowledge_documents`:

```json
{
  "name": "set_knowledge_documents",
  "description": "Save the KB document list and optional Azure credentials collected in the knowledge phase.",
  "input_schema": {
    "type": "object",
    "properties": {
      "documents": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "filename":    { "type": "string" },
            "description": { "type": "string" },
            "source_type": { "type": "string", "enum": ["local", "cloud"] },
            "cloud_path":  { "type": "string" }
          },
          "required": ["filename", "source_type"]
        }
      },
      "azure_storage": {
        "type": "object",
        "properties": {
          "account_name":   { "type": "string" },
          "account_key":    { "type": "string" },
          "container_name": { "type": "string" }
        },
        "required": ["account_name", "account_key", "container_name"]
      }
    },
    "required": ["documents"]
  }
}
```

**Stored in `project.json`:**

```json
{
  "slug": "rural-jobs-assistant",
  "knowledge_documents": [
    {
      "filename": "rural_jobs_handbook.pdf",
      "description": "Govt rural jobs scheme handbook",
      "source_type": "local",
      "ingest_status": "pending"
    },
    {
      "filename": "fasal_bima_guide.pdf",
      "description": "Crop insurance eligibility guide",
      "source_type": "cloud",
      "cloud_path": "docs/fasal_bima_guide.pdf",
      "ingest_status": "pending"
    }
  ],
  "azure_storage": {
    "account_name": "mystorageacct",
    "account_key": "BASE64KEY==",
    "container_name": "dpg-kb-docs"
  }
}
```

> **Security note:** `project.json` is a local operator file equivalent to `.env`. Never commit the `configs/` directory to source control. A `.gitignore` entry must be added for `dev-kit/configs/*/project.json`. Encryption of `azure_storage` at rest is an open question — see §Open Questions.

---

## 2. Deploy Wizard — MandatoryInputsStep Changes

**New fields added to the Deployment Inputs step:**

**Azure Blob Storage (conditional — only shown if `azure_storage` is present in `project.json`):**
- Azure Account Name (pre-filled, editable)
- Azure Account Key (pre-filled, masked)
- Azure Container Name (pre-filled, editable)

These become the `knowledge-engine-azure-creds` K8s secret in Helm.

**Dev-Kit Callback URL (always shown when `knowledge_documents` is non-empty):**
- Label: "Dev-Kit Callback URL"
- Placeholder: `https://devkit.your-vm.example.com`
- Description: "The URL of this Dev-Kit instance, reachable from inside the Kubernetes cluster. Used by the Knowledge Engine to notify when ingestion completes."
- Stored in deploy state → becomes `KE_DEVKIT_CALLBACK_URL` env var in KE Helm chart.

**KE internal URL (for Reach Layer → KE proxy):**
- Label: "KE Internal Service URL"
- Placeholder: `http://knowledge-engine.dpg.svc.cluster.local:8001`
- Description: "Internal Kubernetes service URL for KE. Used by Reach Layer to proxy upload requests."
- Becomes `KE_INTERNAL_URL` env var in Reach Layer Helm chart.

**JWT secrets** are auto-generated silently (not shown to user) — dev-kit creates them on first visit to this step and stores in deploy state.

---

## 3. Deploy Wizard — Step 8: Ingest Documents (new)

**Trigger:** After step 7 (DeployStatusStep) succeeds AND `knowledge_documents` is non-empty.

**New component:** `IngestDocumentsStep.jsx`

**Constraints enforced at the frontend:**
- Maximum 20 files per bulk upload
- Maximum 50 MB per file (enforced before submission, not just at API layer)
- Duplicate filenames rejected before submission — if user selects two files with the same name, show an inline error: "Duplicate filename: handbook.pdf — remove one before uploading."
- Accepted file types only shown in file picker: `.pdf,.txt,.md,.csv,.docx,.html`

**UI layout:**

```
Ingest Knowledge Documents
─────────────────────────────────────────────────────────────
Upload your knowledge documents to the Knowledge Engine.
Documents are ingested into the vector store immediately.

⚠ Max 20 files per batch. Max 50 MB per file.

Documents (3):
  ┌──────────────────────────────────┬────────┬────────────────────┐
  │ rural_jobs_handbook.pdf          │ local  │ [Select & Ingest]  │
  │ Govt rural jobs scheme handbook  │        │                    │
  ├──────────────────────────────────┼────────┼────────────────────┤
  │ fasal_bima_guide.pdf             │ cloud  │ [Fetch & Ingest]   │
  │ Crop insurance eligibility guide │ docs/… │                    │
  ├──────────────────────────────────┼────────┼────────────────────┤
  │ terms_and_conditions.txt         │ local  │ ✓ Ingested (41 chunks) │
  └──────────────────────────────────┴────────┴────────────────────┘

  [Select all local files]  [Ingest selected]

                                         [Skip]  [Done →]
```

**Per-file states:** `pending` → `queued` (job_id shown) → `ingesting` → `ingested (N chunks)` | `failed: <reason>`

---

## 4. Dev-Kit Backend — New Endpoints

**`PATCH /api/projects/{slug}/knowledge-documents/{filename}`**

Updates `ingest_status` for one document in `project.json`. Called internally after the job status changes.

**`POST /api/ingest/submit`**

Accepts the file upload from the browser, issues a service JWT, and forwards to Reach Layer.

Request: `multipart/form-data`
- `slug` — project slug
- `filename` — sanitized filename
- `mode` — `cloud_upload_ingest` | `cloud_fetch_ingest` | `local_write_ingest`
- `cloud_path` (optional) — required for `cloud_fetch_ingest`
- `file` (optional) — required for `cloud_upload_ingest` and `local_write_ingest`

Dev-kit backend:
1. Validates file size (≤ 50 MB) and extension
2. Issues a service JWT signed with `DEVKIT_TO_REACH_SECRET`
3. Calls `POST <REACH_LAYER_URL>/ingest/upload` with JWT in `Authorization: Bearer` header
4. Returns `{job_id: "<uuid>"}` to frontend

**`GET /api/ingest/job/{job_id}`**

Returns current job status from the in-memory job map.

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "handbook.pdf",
  "status": "ingesting",   // queued | ingesting | ingested | failed
  "chunks_added": null,
  "error": null,
  "queued_at": "2026-04-18T10:22:00Z"
}
```

**`POST /api/ingest/callback`**

Called by KE when a job completes. Protected by JWT validation (`KE_TO_DEVKIT_SECRET`).

Request body:
```json
{
  "job_id": "550e8400-...",
  "status": "ingested",
  "chunks_added": 47,
  "error": null
}
```

Dev-kit:
1. Validates JWT (`Authorization: Bearer <ke-signed-token>`)
2. Updates in-memory job map
3. Updates `ingest_status` in `project.json` for the matching filename

---

## 5. Reach Layer — Upload Proxy (new)

**New endpoints on Reach Layer:**

**`POST /ingest/upload`**

Validates the dev-kit JWT, then proxies the request to KE.

```python
@router.post("/ingest/upload")
async def ingest_upload(
    request: Request,
    authorization: str = Header(...),
):
    # 1. Validate dev-kit JWT (DEVKIT_TO_REACH_SECRET)
    verify_service_token(authorization.removeprefix("Bearer "), DEVKIT_TO_REACH_SECRET)

    # 2. Issue reach layer JWT (REACH_TO_KE_SECRET) for internal KE call
    ke_token = issue_service_token("reach_layer", REACH_TO_KE_SECRET)

    # 3. Forward multipart body to KE internal URL
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KE_INTERNAL_URL}/upload",
            content=await request.body(),
            headers={
                "Content-Type": request.headers["Content-Type"],
                "Authorization": f"Bearer {ke_token}",
            },
            timeout=30.0,
        )
    return Response(content=response.content, status_code=response.status_code,
                    media_type=response.headers.get("content-type"))
```

CORS: The reach layer's CORS allowed origins list must include the dev-kit VM URL. This is configurable in reach layer YAML (not hardcoded):

```yaml
cors:
  allowed_origins:
    - "https://devkit.your-vm.example.com"
    - "http://localhost:5173"   # dev mode
```

---

## 6. Knowledge Engine — Upload API

**New endpoint:** `POST /upload`

```python
@router.post("/upload")
async def upload_document(
    authorization: str = Header(...),
    mode: str = Form(...),
    filename: str = Form(...),
    cloud_path: str = Form(None),
    file: UploadFile = File(None),
):
    # 1. Validate reach layer JWT
    verify_service_token(authorization.removeprefix("Bearer "), REACH_TO_KE_SECRET)

    # 2. Sanitize filename — reject path separators
    safe_name = Path(filename).name
    if safe_name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(422, "Invalid filename — path separators not allowed")

    # 3. Validate extension
    ext = Path(safe_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(422, f"Unsupported file format: {ext}")

    # 4. Validate mode + required fields
    if mode in ("cloud_upload_ingest", "cloud_fetch_ingest") and not AZURE_CONFIGURED:
        raise HTTPException(400, "Azure storage not configured on this deployment")
    if mode in ("cloud_upload_ingest", "local_write_ingest") and file is None:
        raise HTTPException(422, "file is required for this mode")
    if mode == "cloud_fetch_ingest" and not cloud_path:
        raise HTTPException(422, "cloud_path is required for cloud_fetch_ingest")

    # 5. Enqueue job
    job_id = str(uuid.uuid4())
    await ingest_queue.put(IngestJob(job_id, safe_name, mode, cloud_path, file_bytes))
    job_store[job_id] = JobStatus(job_id, safe_name, "queued")

    return {"job_id": job_id, "status": "queued"}
```

**`GET /upload/job/{job_id}`**

Returns job status from `job_store`. Allows dev-kit to poll if callback was not received.

**Async Queue Worker** (started at KE startup):

```python
async def _queue_worker():
    """Process upload jobs sequentially. One job at a time to protect embeddings."""
    while True:
        job = await ingest_queue.get()
        job_store[job.job_id].status = "ingesting"
        try:
            storage = get_storage_backend()
            file_path = await _stage_file(storage, job)   # write to temp or PVC or fetch from Azure
            chunks = block.ingest_single(config, file_path)
            job_store[job.job_id] = JobStatus(job.job_id, job.filename, "ingested", chunks_added=chunks)
            await _send_callback(job.job_id, "ingested", chunks_added=chunks)
        except Exception as e:
            job_store[job.job_id] = JobStatus(job.job_id, job.filename, "failed", error=str(e))
            await _send_callback(job.job_id, "failed", error=str(e))
        finally:
            _cleanup_temp_file(job)   # always remove temp files
            ingest_queue.task_done()
```

**Queue constraints:**
- Max queue size: 20 jobs. New `POST /upload` returns HTTP 429 if queue is full.
- Job IDs: `uuid.uuid4()` — never sequential.
- In-memory `job_store`: lost on KE pod restart. Jobs in flight at restart must be re-submitted. Documented operational behaviour, not treated as a bug.

**KE Callback to Dev-Kit:**

```python
async def _send_callback(job_id: str, status: str, **kwargs):
    """Notify dev-kit of job completion. Retries 3 times with backoff."""
    if not DEVKIT_CALLBACK_URL:
        return
    token = issue_service_token("knowledge_engine", KE_TO_DEVKIT_SECRET)
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
                    return
        except Exception:
            pass
        await asyncio.sleep(2 ** attempt)   # 1s, 2s, 4s
    logger.warning("ke.callback_failed", extra={"job_id": job_id})
```

**If all callback retries fail:** KE logs the failure. Dev-kit polling of `GET /upload/job/{id}` via reach layer `GET /ingest/job/{id}` acts as the fallback to eventually detect completion.

**KE service token issuance:** KE issues a fresh JWT per callback using `KE_TO_DEVKIT_SECRET`. Token lifetime: 5 minutes (single-use for the callback, no need for long-lived tokens here).

**AZURE_CONFIGURED check:** At startup, KE checks if `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, and `AZURE_CONTAINER_NAME` env vars are set. Sets `AZURE_CONFIGURED = True` if all present. Cloud mode requests fail fast with HTTP 400 if not configured.

**Queue position in job status response:**

```json
{
  "job_id": "550e8400-...",
  "status": "queued",
  "queue_position": 3,
  "chunks_added": null,
  "error": null
}
```

Frontend shows "Position 3 in queue" so the operator knows how long to wait.

---

## 7. Storage Abstraction

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
    Future implementations for other cloud providers inherit from this class.
    """

    @abstractmethod
    def upload(self, content: bytes, filename: str) -> str:
        """Upload content and return the storage path.

        Args:
            content: Raw file bytes.
            filename: Basename only — no path separators.

        Returns:
            Storage path (blob name or absolute local path).

        Raises:
            StorageError: On upload failure after retries.
        """

    @abstractmethod
    def download(self, path: str) -> bytes:
        """Download content from the given path.

        Args:
            path: Blob name or absolute local path.

        Returns:
            Raw file bytes.

        Raises:
            StorageError: If path does not exist or download fails.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the backend is reachable and writable."""
```

**`azure_blob.py`** — uses `azure-storage-blob>=12.0`. Reads credentials from env vars at construction time (not per-request).

**`local_pvc.py`** — writes to `/data/kb/`. PVC is mounted at this path via Helm chart.

**Factory:**

```python
def get_storage_backend() -> StorageBackend:
    """Return Azure backend if all env vars are set, else local PVC."""
    acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key  = os.environ.get("AZURE_STORAGE_KEY")
    cont = os.environ.get("AZURE_CONTAINER_NAME")
    if acct and key and cont:
        return AzureBlobStorageBackend(acct, key, cont)
    return LocalPVCStorageBackend()
```

---

## 8. StaticKnowledgeBaseBlock — `ingest_single`

```python
def ingest_single(self, config: dict, file_path: Path) -> int:
    """Ingest a single document into the existing ChromaDB collection.

    Deletes all existing chunks for this filename before re-ingesting,
    ensuring no duplicate chunks if the same file is re-uploaded.
    Appends to the collection — does not wipe other documents.

    Uses an asyncio.Lock (held by the caller via the queue worker) to
    prevent concurrent writes to the same collection.

    Args:
        config: Full KE YAML config dict.
        file_path: Absolute path to the document (must exist).

    Returns:
        Number of chunks added.

    Raises:
        ValueError: If file format is not supported.
        KnowledgeEngineError: If ChromaDB write fails.
    """
```

**Deduplication:** Before adding chunks, query ChromaDB for all chunks with `source == file_path.name` and delete them. This handles the re-upload case correctly.

**Concurrency safety:** The queue worker processes one job at a time (sequential). No concurrent `ingest_single` calls on the same collection. No asyncio.Lock needed — serialization is provided by the queue itself.

---

## 9. K8s Helm Chart Changes

**`templates/deployment.yaml`:**
- Remove `initContainers` (replaced by upload API)
- Add `/data/kb` PVC mount
- Add Azure env vars (optional, from K8s secret)
- Add KE callback env vars

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

**New K8s secrets generated by Helm:**
- `{{ .Release.Name }}-azure-creds` (optional, if azureStorage.enabled)
- `{{ .Release.Name }}-ingest-config` (devkit_callback_url)
- `{{ .Release.Name }}-jwt-secrets` (all three JWT secrets)

**New PVC:**
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

**NetworkPolicy:** KE needs egress to the dev-kit VM IP/hostname for callbacks. A `NetworkPolicy` resource must allow this egress. The Helm chart includes an optional NetworkPolicy template:

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
            cidr: {{ .Values.networkPolicy.devkitCIDR }}   # dev-kit VM IP/CIDR
      ports:
        - port: 443
        - port: 80
{{- end }}
```

---

## 10. Affected Files Summary

| File | Change |
|------|--------|
| `dev-kit/dev_kit/agent/tools.py` | Add `set_knowledge_documents` tool + handler |
| `dev-kit/dev_kit/agent/app.py` | Add `POST /ingest/submit`, `GET /ingest/job/{id}`, `POST /ingest/callback`, `PATCH /knowledge-documents/{filename}` |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Update knowledge phase prompt |
| `dev-kit/dev_kit/agent/auth.py` | New — `issue_service_token`, `verify_service_token` (HS256, mirrors reach layer pattern) |
| `dev-kit/frontend/src/api.js` | Add `submitIngest`, `getJobStatus` |
| `dev-kit/frontend/src/components/deploy/DeployWizard.jsx` | Add step 8, bump max step to 8 |
| `dev-kit/frontend/src/components/deploy/IngestDocumentsStep.jsx` | New component |
| `dev-kit/frontend/src/components/deploy/MandatoryInputsStep.jsx` | Add Azure fields, callback URL field |
| `reach_layer/web/server.py` | Add `POST /ingest/upload`, `GET /ingest/job/{id}` proxy endpoints |
| `reach_layer/web/src/auth.py` | Add `issue_service_token`, `verify_service_token` |
| `reach_layer/web/reach_layer_config.yaml` (or equivalent) | Add `cors.allowed_origins`, `ke_internal_url` |
| `knowledge_engine/src/storage/base.py` | New — StorageBackend ABC |
| `knowledge_engine/src/storage/azure_blob.py` | New — AzureBlobStorageBackend |
| `knowledge_engine/src/storage/local_pvc.py` | New — LocalPVCStorageBackend |
| `knowledge_engine/src/storage/__init__.py` | New — `get_storage_backend` factory |
| `knowledge_engine/src/blocks/static_knowledge_base.py` | Add `ingest_single` method |
| `knowledge_engine/src/upload_router.py` | New — `POST /upload`, `GET /upload/job/{id}`, queue worker |
| `knowledge_engine/src/auth.py` | New — `issue_service_token`, `verify_service_token` |
| `knowledge_engine/src/app.py` | Register upload_router, start queue worker on startup |
| `knowledge_engine/pyproject.toml` | Add `azure-storage-blob>=12.0` |
| `automation/helm/dpg/knowledge-engine/templates/deployment.yaml` | Remove init-container, add env vars, add `/data/kb` mount |
| `automation/helm/dpg/knowledge-engine/templates/secret-azure.yaml` | New — optional Azure K8s secret |
| `automation/helm/dpg/knowledge-engine/templates/secret-ingest.yaml` | New — callback URL + JWT secrets |
| `automation/helm/dpg/knowledge-engine/templates/pvc-kb.yaml` | New — kb-data PVC |
| `automation/helm/dpg/knowledge-engine/templates/networkpolicy.yaml` | New — optional egress to dev-kit |
| `automation/helm/dpg/knowledge-engine/values.yaml` | Add `azureStorage.*`, `kbStorage.size`, `networkPolicy.*` |
| `automation/helm/dpg/reach-layer/templates/deployment.yaml` | Add JWT env vars, KE internal URL |
| `dev-kit/configs/.gitignore` | Add `*/project.json` |

---

## 11. Data Flow: End-to-End

```
CHAT PHASE
  User: "I have rural_jobs_handbook.pdf (local), fasal_bima_guide.pdf (Azure at docs/)"
  Agent → set_knowledge_documents([...])  → saved to project.json

  User: "Azure: mystorageacct / BASE64KEY== / dpg-kb-docs"
  Agent → set_knowledge_documents(azure_storage={...})  → saved to project.json

DEPLOY WIZARD (MandatoryInputsStep)
  Dev-kit reads project.json → pre-fills Azure + callback URL fields
  Operator verifies/edits → confirms
  Dev-kit auto-generates 3 JWT secrets (never shown to operator)
  Deploy executes → K8s secrets created in cluster

POST-DEPLOY (IngestDocumentsStep — step 8)
  Operator selects rural_jobs_handbook.pdf from disk
  Operator clicks "Select & Ingest"

  1. Frontend validates: extension ✓, size ✓, no duplicate ✓
  2. Browser → POST /api/ingest/submit (multipart: file + mode=local_write_ingest + slug)
  3. Dev-kit backend:
       - Issues JWT (devkit-signed, 24h)
       - Calls POST https://reach.vm.example.com/ingest/upload (JWT + file + mode)
  4. Reach Layer:
       - Validates dev-kit JWT ✓
       - Issues reach layer JWT (reach-signed, 5min)
       - Proxies to POST http://ke.dpg.svc.cluster.local:8001/upload (JWT + file + mode)
  5. KE:
       - Validates reach layer JWT ✓
       - Sanitizes filename → "rural_jobs_handbook.pdf" (no path separators) ✓
       - Validates extension .pdf ✓
       - Enqueues IngestJob, job_id = UUID4
       - Returns {"job_id": "550e8400-..."}
  6. Response flows back → frontend shows "Queued (position 1)"

  Frontend polls GET /api/ingest/job/550e8400 every 5s
    → dev-kit returns {"status": "ingesting"}  (after worker picks it up)

  KE QUEUE WORKER processes job:
    - LocalPVCStorageBackend.upload(bytes, "rural_jobs_handbook.pdf")
      → writes to /data/kb/rural_jobs_handbook.pdf
    - ingest_single(config, Path("/data/kb/rural_jobs_handbook.pdf"))
      → deletes old chunks for this filename (if any)
      → chunks + embeds → adds 47 chunks to ChromaDB collection

  KE calls POST https://devkit.vm.example.com/api/ingest/callback
    Authorization: Bearer <ke-signed JWT>
    {"job_id": "550e8400-...", "status": "ingested", "chunks_added": 47}

  Dev-kit:
    - Validates KE JWT ✓
    - Updates job_store["550e8400..."].status = "ingested"
    - Updates project.json knowledge_documents[0].ingest_status = "ingested"

  Next frontend poll → returns {"status": "ingested", "chunks_added": 47}
  UI: row shows ✓ Ingested (47 chunks)
```

---

## 12. Error Handling

| Failure | HTTP | Behaviour |
|---------|------|-----------|
| Invalid or expired JWT | 401 | Rejected at validation layer; not forwarded |
| Filename with path separators | 422 | Rejected at KE immediately |
| Unsupported file extension | 422 | Rejected at dev-kit backend and KE |
| File > 50 MB | 413 | Rejected at dev-kit backend before forwarding |
| Duplicate filename in batch | — | Rejected at frontend before submission |
| Queue full (20 jobs) | 429 | KE returns 429; dev-kit shows "Queue full, try later" |
| Azure not configured for cloud mode | 400 | KE returns 400; shown in UI |
| Azure auth failure | 401/403 | From Azure SDK; propagated as 502 from KE |
| Azure blob not found | 404 | From Azure SDK; propagated as 404 from KE |
| ChromaDB write fails | 500 | KE returns 500; ingest_status stays "pending"; retry available |
| KE callback fails (all retries) | — | Logged; dev-kit polling detects completion via `GET /ingest/job/{id}` |
| KE pod restart mid-ingestion | — | Job lost; operator must re-submit; UI shows timeout after 10 min with "re-try" |
| Temp file not cleaned up | — | `try/finally` in queue worker guarantees cleanup |
| KE unreachable from reach layer | 503 | Reach layer returns 503; dev-kit shows "KE unreachable" |

---

## 13. Security Controls Summary

| Control | Where | Mechanism |
|---------|-------|-----------|
| Dev-kit → reach layer auth | Reach layer | Verify dev-kit JWT (HS256, DEVKIT_TO_REACH_SECRET) |
| Reach layer → KE auth | KE | Verify reach JWT (HS256, REACH_TO_KE_SECRET) |
| KE → dev-kit callback auth | Dev-kit | Verify KE JWT (HS256, KE_TO_DEVKIT_SECRET) |
| Path traversal prevention | KE | `Path(filename).name`; reject if `"/" in filename` |
| File extension whitelist | Dev-kit backend + KE | `.pdf .txt .md .csv .docx .html` only |
| File size limit | Dev-kit frontend + backend | 50 MB hard limit |
| Duplicate file prevention | Dev-kit frontend | Client-side check before submission |
| Queue size limit | KE | Max 20 pending jobs; HTTP 429 if exceeded |
| UUID job IDs | KE | `uuid.uuid4()` — non-enumerable |
| Azure creds at rest | KE K8s secret | Stored as K8s Opaque secret, not in container env directly (mounted via secretKeyRef) |
| CORS for dev-kit origin | Reach layer | Configurable `cors.allowed_origins` list |
| KE egress to dev-kit | Helm NetworkPolicy | Optional; supports same-VM and cross-VM deployments |

---

## 14. Open Questions

1. **Azure credentials encryption in project.json** — Account key is stored unencrypted in the operator's local `project.json`. Should it be encrypted at rest (e.g., using OS keychain or a project-level symmetric key)? Flagged for lead review.

2. **JWT token chain ownership** — Dev-kit issues JWTs to authenticate to reach layer. Who acts as the CA / secret custodian? Currently, the dev-kit auto-generates the shared secrets at deploy time. Should these be externally managed (Vault, AWS KMS)? Flagged for lead review.

3. **JWT secret rotation** — No rotation mechanism exists. A compromised secret requires a full redeploy to replace K8s secrets. Should a rotation procedure be documented? Flagged for lead review.

4. **Per-user document scoping** — The future plan is to let each end-user upload personal documents via Reach Layer. The current design uses a single ChromaDB collection per deployment. Per-user uploads will require either per-user sub-collections or metadata-based `user_id` filtering at query time. `ingest_single` must accept an optional `scope` parameter to support this without API redesign. No decision made — tracked as future work.

5. **KE ingestion status persistence** — `job_store` is in-memory, lost on restart. For production durability, job status should be persisted (Redis or SQLite). Flagged for lead review.

6. **JWT secret stability across redeployments** — JWT secrets are auto-generated by dev-kit at deploy time. If the operator redeploys (for any reason) and secrets are regenerated, all in-flight tokens from the previous deploy become immediately invalid, breaking any active upload sessions. Decision needed: should JWT secrets be generated once and stored permanently in `project.json` (reused on subsequent deploys), or regenerated each time with a short overlap/grace period? Flagged for lead review.
