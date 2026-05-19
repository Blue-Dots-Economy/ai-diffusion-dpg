# Rule: Runtime ↔ Dev-Kit Synchronization

Every change to a runtime block's `<block>/src/schema/config.py` must be reflected in the dev-kit in the same PR. No CI guard; PR-time discipline is the only mechanism.

## What lives where

| File | Role |
|---|---|
| `<block>/src/schema/config.py` | Runtime schema. Strict, full merged config; what the service accepts at boot. |
| `dev-kit/dev_kit/schemas/domain/<block>.py` | Per-block mirror. Lenient, domain-half only; chat-time `update_config` gate. |
| `dev-kit/dev_kit/schema.py` | Flat-file copy of the FULL merged schema. Host-mode deploy gate; must match runtime. |
| `dev-kit/dev_kit/agent/field_rules/<block>.py` | FIELD_RULES — category, phase, default, invalidation, `applies_if`. |
| `dev-kit/dpg/<block>.yaml` | Framework defaults shared across projects. |
| `dev-kit/Dockerfile` (rebuild only) | Bakes each runtime schema into `/app/dpg_runtime_schemas/<block>/config.py`. Canonical Docker deploy gate. |

## Touch-points when changing a runtime field

**Must update in the same PR:**

1. **Category.** Framework default → `dev-kit/dpg/<block>.yaml`. Domain-half → continue.
2. **Per-block mirror** at `dev-kit/dev_kit/schemas/domain/<block>.py`. Match shape exactly (Optional ↔ Optional, strict ↔ strict).
3. **FIELD_RULES** at `dev-kit/dev_kit/agent/field_rules/<block>.py`. `pydantic_class` points to the mirror class.
4. **Flat-file copy** at `dev-kit/dev_kit/schema.py`. Update the matching class — even for framework-half changes (server, client URLs, redis/memgraph, otel, vad, recording). This is the host-mode deploy gate; drift here = silently pass/fail what runtime would reject/accept.

**Update if applicable:**

5. **Phase prompt** at `dev-kit/dev_kit/agent/phase_prompts/<phase>.py` — if user-configurable, add an `update_config(path=..., value=...)` template.
6. **Cross-block invariant** at `dev-kit/dev_kit/schemas/cross_block_validation.py`.
7. **Skeleton seed** at `dev-kit/dev_kit/agent/skeleton.py` — if a non-empty default is needed up front.
8. **Derived field** at `dev-kit/dev_kit/agent/derived_fields.py` — if computed from another field.
9. **IntakeState + form** at `dev-kit/dev_kit/agent/intake_state.py` — if gated by a new binary flag.
10. **`DOMAIN_SECTION_SCHEMAS`** at `dev-kit/dev_kit/schemas/validation.py` — only if you added a new top-level section to the mirror.

**Tests:** add accept-valid + reject-invalid at `dev-kit/tests/schemas/domain/test_<block>.py`.

**Docker rebuild:** any runtime schema change requires `docker build -f dev-kit/Dockerfile -t dpg-dev-kit .` so the baked copy picks up the change. Until rebuilt, the canonical Config Review gate validates against the old schema.

## Runtime schemas must stay self-contained

`<block>/src/schema/config.py` may import only from `pydantic`, `enum`, `typing`, `__future__`. No relative imports, no third-party deps, no reach into siblings — the Dockerfile copies this file verbatim. Shared types: inline, or co-locate in the same `schema/` directory.

## Validation gates

| Gate | When | Schema |
|---|---|---|
| Per-write | Each `update_config` tool call | Per-block mirror — domain-half |
| End-of-turn write | Each chat turn (advisory `# WARNINGS:`) | Per-block mirror |
| Deploy in Docker | User clicks Deploy, dev-kit in Docker | Baked runtime schemas — canonical |
| Deploy on host | User clicks Deploy, dev-kit on host | `dev_kit/schema.py` — flat-file copy |

The deploy response carries `"validator": "runtime_baked" | "host_mirror"` so the surface that ran is explicit. Baked runtime schemas are the only authoritative gate; always do final pre-merge verification with the dev-kit image rebuilt and running in Docker.

## Verify before merging

1. Rebuild the dev-kit image after the runtime change.
2. Run the wizard end-to-end to Deploy in Docker. Response must show `"validator": "runtime_baked"`.
3. Confirm `docker compose up` succeeds for the deployed config.

## Reasoning

The dev-kit produces YAML; the runtime consumes it. Schema drift means the wizard generates configs that fail at container boot — surfaced too late. Treating runtime + dev-kit as a single unit at PR time prevents this. The baked runtime schema in Docker is the canonical gate; `dev_kit/schema.py` keeps host-mode usable but only as a best-effort hand-maintained copy.
