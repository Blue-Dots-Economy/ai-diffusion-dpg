# Config Template Design

**Issue:** #15  
**Date:** 2026-04-02

## Problem

Each block's `config/domain.yaml` currently contains KKB domain-specific values — identical to `dev-kit/configs/kkb/*.yaml`. This makes the repo look like a KKB-specific codebase rather than a domain-agnostic DPG framework. A new domain integrator cloning the repo cannot tell what they need to fill in vs. what is boilerplate.

## Goal

1. Make `<block>/config/domain.yaml` a documented template (placeholder values only).
2. Keep real domain values exclusively in `dev-kit/configs/kkb/`.
3. Allow local dev/testing to load domain config from `dev-kit/configs/kkb/` via a `CONFIG_FOLDER` env var and a gitignored `.env.local` file.

## Design

### 1. Config loading change (all 7 blocks)

Each block's `main.py` currently hardcodes the domain config path as `"config/domain.yaml"`. This changes to:

```python
import os
from pathlib import Path

config_folder = os.getenv("CONFIG_FOLDER")
if config_folder:
    domain_config_path = Path(config_folder) / "<service_name>.yaml"
else:
    domain_config_path = Path("config/domain.yaml")
```

The service name matches the filename in `dev-kit/configs/kkb/` (e.g. `agent_core`, `knowledge_engine`, etc.).

Each block's `main.py` already calls `load_dotenv()`. This is updated to also load `.env.local` from the repo root:

```python
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")  # local dev overrides
load_dotenv()  # fallback: .env in block dir or environment
```

The `dpg.yaml` path is unchanged — always loaded from `config/dpg.yaml` (block-local). This preserves the existing behaviour where docker service URLs in `dev-kit/dpg/*.yaml` are not used at block runtime; each block's own `config/dpg.yaml` has localhost defaults for local dev.

### 2. Template `config/domain.yaml` files

All 7 `<block>/config/domain.yaml` files are replaced with templates. Each template:

- Documents every required key with a comment explaining its purpose.
- Uses empty strings, empty lists, or `null` as placeholder values.
- Has no domain-specific content (no KKB language, no KKB connector names, no KKB phrases).
- Serves as the canonical reference for what a domain integrator must provide.

Blocks whose current `domain.yaml` has no domain-specific content (`learning_layer`, `reach_layer`) get a minimal template noting they have no required domain keys.

### 3. `.env.local` file (gitignored)

A `.env.local` file at the repo root sets `CONFIG_FOLDER` to the absolute path of the domain configs folder:

```bash
# .env.local — local developer override. Never commit this file.
CONFIG_FOLDER=/absolute/path/to/repo/dev-kit/configs/kkb
```

Each developer sets this to their local absolute path. The file is gitignored.

### 4. `.env.local.example` (committed)

A `.env.local.example` file at the repo root provides a reference for developers:

```bash
# Copy to .env.local and set CONFIG_FOLDER to the absolute path of your domain configs folder.
# Example:
CONFIG_FOLDER=/Users/yourname/projects/ai-diffusion-dpg/dev-kit/configs/kkb
```

### 5. `.gitignore` update

`.env.local` is added to the repo root `.gitignore`.

## Affected Files

| File | Change |
|---|---|
| `agent_core/main.py` | Update `load_dotenv` call + domain config path resolution |
| `knowledge_engine/main.py` | Same |
| `trust_layer/main.py` | Same |
| `action_gateway/main.py` | Same |
| `memory_layer/main.py` | Same |
| `learning_layer/main.py` | Same |
| `reach_layer/main.py` | Same |
| `agent_core/config/domain.yaml` | Replace with template |
| `knowledge_engine/config/domain.yaml` | Replace with template |
| `trust_layer/config/domain.yaml` | Replace with template |
| `action_gateway/config/domain.yaml` | Replace with template |
| `memory_layer/config/domain.yaml` | Replace with template |
| `learning_layer/config/domain.yaml` | Replace with template |
| `reach_layer/config/domain.yaml` | Replace with template |
| `.env.local.example` | New file |
| `.gitignore` | Add `.env.local` |

## Not Changed

- `dev-kit/configs/kkb/*.yaml` — unchanged, remain the source of truth for KKB values.
- `dev-kit/dpg/*.yaml` — unchanged.
- `dev-kit/loader.py` — unchanged; it has its own loading path independent of block runtime.
- `config/dpg.yaml` in each block — unchanged.
- Docker compose files — unchanged; in Docker, `CONFIG_FOLDER` is not set and `config/domain.yaml` inside the container is the mounted domain config (existing behaviour).
