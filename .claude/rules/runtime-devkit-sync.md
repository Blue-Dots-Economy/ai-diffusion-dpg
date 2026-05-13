# Rule: Runtime ↔ Dev-Kit Synchronization

Every change to a runtime block's config schema must be reflected in the dev-kit in the same PR. There is no CI guard today; discipline at PR time and code review is the only mechanism.

## What lives where

- **Runtime block schemas** at `<block>/src/schema/config.py` — the strict Pydantic schema each running service uses at boot.
- **Dev-kit mirror schemas** at `dev-kit/dev_kit/schemas/domain/<block>.py` — the lenient, domain-half view the wizard uses during chat (LLM prompt injection, `update_config` validation, skeleton defaults).
- **Dev-kit FIELD_RULES** at `dev-kit/dev_kit/agent/field_rules/<block>.py` — per-field category, phase, default, invalidation triggers.
- **Dev-kit Docker image** bakes in each runtime block's `src/schema/config.py` at build time under `/app/dpg_runtime_schemas/<block>/` for the pre-deploy dry-run.

## Rules

### 1. Never add a runtime field without a corresponding dev-kit change

When you add a new field to `<block>/src/schema/config.py`, decide which category it belongs to:

- **Framework default** (operational, identical across projects): set the default in `dev-kit/dpg/<block>.yaml`. The wizard doesn't surface the field; no FIELD_RULES entry, no mirror update.
- **Domain-specific** (varies per project): add a `FIELD_RULES` entry at `dev-kit/dev_kit/agent/field_rules/<block>.py`, AND mirror the field in `dev-kit/dev_kit/schemas/domain/<block>.py` so `update_config` validation and LLM prompt injection see it.

If you skip this step, the wizard will produce YAML that passes its mirror-only chat-time validation but the dry-run will reject it at deploy. The user only finds out at the worst possible moment.

### 2. Runtime schemas must stay self-contained

`<block>/src/schema/config.py` may only import from:

- `pydantic`
- `enum`
- `typing`
- `__future__`

No relative imports, no imports from other modules in `<block>/src/`, no third-party dependencies. The dev-kit copies each schema file into its Docker image at build time; any other import would fail at dev-kit build.

If you need shared types, inline them in the same file or place them in the same `schema/` directory (which is copied as a directory). Do not reach outside `schema/` from `config.py`.

### 3. Renames, removals, and newly required fields require dev-kit updates in the SAME PR

The release process rebuilds all images at the same `${GIT_SHA}` (per `automation/docker/docker-compose.yml`). A runtime schema change without a matching dev-kit change ships a wizard that cannot generate valid config for that schema.

If you rename a field in `<block>/src/schema/config.py`, also rename it in the mirror, in any `FIELD_RULES` entry that references it, and in any `pydantic_class` lookups. If you make an optional field required, ensure the FIELD_RULES `default` is no longer `None` and that `applies_if` doesn't gate it out.

### 4. Verify with the pre-deploy dry-run before merging

Run the wizard end-to-end locally for the affected block and confirm `docker compose up` succeeds. This is the only way to catch silent mirror drift until the CI Coverage guard exists.

## Reasoning

The dev-kit produces YAML; the runtime block consumes it. When the schemas drift, the wizard happily generates configs that the runtime rejects at boot — and the error surfaces as a confusing container startup failure, not a wizard-time validation error. Treating runtime schemas and dev-kit components as a single unit at PR time prevents this.
