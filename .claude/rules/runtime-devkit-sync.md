# Rule: Runtime ↔ Dev-Kit Synchronization

Every change to a runtime block's `<block>/src/schema/config.py` must be reflected in the dev-kit in the same PR. There is no CI guard; PR-time discipline is the only mechanism.

## What lives where

| File | Role |
|---|---|
| `<block>/src/schema/config.py` | Runtime schema. Strict; what the running service accepts at boot. |
| `dev-kit/dev_kit/schemas/domain/<block>.py` | Per-block mirror. Lenient, domain-half view; used at chat time by `update_config` validation and by the host-mode deploy fallback. |
| `dev-kit/dev_kit/agent/field_rules/<block>.py` | Per-field FIELD_RULES — category, phase, default, invalidation, `applies_if`. |
| `dev-kit/dpg/<block>.yaml` | Framework defaults — operational values identical across projects. |
| `dev-kit/Dockerfile` (no edit; rebuild only) | Bakes each runtime schema into `/app/dpg_runtime_schemas/<block>/config.py`. Used by `pre_deploy_validate` as the canonical Config Review gate. |

## Touch-points when changing a runtime field

**Must update in the same PR:**

1. **Decide category.** Framework default → set in `dev-kit/dpg/<block>.yaml`. Domain-half → continue with 2–3 below.
2. **Mirror schema** at `dev-kit/dev_kit/schemas/domain/<block>.py`. Match the shape exactly (Optional ↔ Optional, strict BaseModel ↔ strict BaseModel). The mirror is what catches drift on the host.
3. **FIELD_RULES** at `dev-kit/dev_kit/agent/field_rules/<block>.py`. Add/rename/remove entries; `pydantic_class` must point to a class in the mirror.

**Update if applicable:**

4. **Phase prompt** at `dev-kit/dev_kit/agent/phase_prompts/<phase>.py` — if the field is user-configurable, add an explicit `update_config(path="...", value=...)` template.
5. **Cross-block invariant** at `dev-kit/dev_kit/schemas/cross_block_validation.py` — if the field participates in a cross-block rule.
6. **Skeleton seed** at `dev-kit/dev_kit/agent/skeleton.py` — if the wizard must pre-fill a non-empty default.
7. **Derived field** at `dev-kit/dev_kit/agent/derived_fields.py` — if the value is computed from another field (slug, intake state).
8. **IntakeState + form** at `dev-kit/dev_kit/agent/intake_state.py` — if the new field is gated by a new binary flag.
9. **`DOMAIN_SECTION_SCHEMAS` registry** at `dev-kit/dev_kit/schemas/validation.py` — only if you added a new TOP-LEVEL section in the mirror.

**Test surface:** add coverage at `dev-kit/tests/schemas/domain/test_<block>.py` (accept-valid, reject-invalid).

**Docker rebuild:** any change to `<block>/src/schema/config.py` requires `docker build -f dev-kit/Dockerfile -t dpg-dev-kit .` so the baked copy under `/app/dpg_runtime_schemas/` picks up the change. Until rebuilt, the Config Review gate is still validating against the old schema.

## Runtime schemas must stay self-contained

`<block>/src/schema/config.py` may import only from `pydantic`, `enum`, `typing`, `__future__`. No relative imports, no third-party deps, no reach into sibling modules. The Dockerfile copies this file verbatim — any other import breaks the dev-kit build. Shared types: inline, or co-locate in the same `schema/` directory (copied as a directory).

## Validation gates (where drift is caught)

| Gate | When | Schema |
|---|---|---|
| Per-write at chat | After every `update_config` tool call | Per-block mirror — lenient |
| End-of-turn YAML write | After every chat turn (advisory `# WARNINGS:` comments) | Per-block mirror — lenient |
| **Config Review / Deploy** | User clicks Deploy | **Baked runtime schemas (Docker) or per-block mirror via `validate_full` (host fallback)** |

The baked runtime schemas are the only authoritative gate. In Docker, Config Review uses them directly; on host, `validate_full` against the mirror is a best-effort fallback (it doesn't know about DPG defaults, so it can over- or under-reject). Always do the final pre-merge verification with the dev-kit image rebuilt and running in Docker.

## Verify before merging

End-to-end on the affected block, in Docker:
1. Rebuild the dev-kit image after the schema change.
2. Run the wizard through to Deploy. Config Review must surface any drift (response carries `"validator": "runtime_baked"`).
3. Confirm `docker compose up` succeeds for the deployed config.

## Reasoning

The dev-kit produces YAML; the runtime consumes it. When schemas drift, the wizard generates configs that fail at container boot — a confusing failure mode, surfaced too late. Treating runtime + dev-kit as a single unit at PR time is what prevents that. The baked runtime schema in the Docker image makes deploy-time validation use the runtime's own definition, eliminating one drift surface (`dev_kit/schema.py`) entirely.
