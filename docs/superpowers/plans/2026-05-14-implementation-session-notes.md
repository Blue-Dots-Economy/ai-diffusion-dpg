# Implementation Session Notes — Dev-Kit Deterministic Wizard

> **For the next session:** READ THIS FILE FIRST. It captures what was learned during execution that isn't in the plan, design, or catalogue.

**Plan being executed:** [`2026-05-14-devkit-deterministic-wizard-implementation.md`](2026-05-14-devkit-deterministic-wizard-implementation.md)

**Branch:** `docs/devkit-config-generation-revamp-design`

---

## Status (as of this handoff)

**Completed (7 tasks committed):**
- ✅ Task 0.1 — Audit runtime schema self-containedness (all 7 blocks clean, no fixes needed)
- ✅ Task 0.2 — Directory stubs for `field_rules/` and `phase_prompts/`
- ✅ Task 1.1 — `IntakeState` dataclass + persistence (with corrupt-JSON / schema-mismatch handling)
- ✅ Task 1.2 — `FieldRule` dataclass + aggregated registry (with `register_block_rules` input validation + 6 new tests)
- ✅ Task 1.3 — `path_ops.py` with `[name=X]` syntax (11 tests, unused pytest import removed)
- ✅ Task 2.1 — Dockerfile `COPY` lines for runtime schemas (+ `Dockerfile.dockerignore` negation patterns)
- ✅ Task 2.2 — `runtime_validate` using baked-in `MergedConfig` classes (with guarded imports + host/docker test split)

**Next task:** Task 2.3 — Wire dry-run into `render_all` flow.

**Recent commit log:**
```
e776604 fix(dev-kit): use extra-forbid payload to trigger trust_layer validation error
943eac7 feat(dev-kit): add runtime_validate using baked-in MergedConfig classes
56de5b8 feat(dev-kit): bake runtime block schemas into image for pre-deploy dry-run
4719fce chore(dev-kit): drop unused pytest import in test_path_ops
21c5dee feat(dev-kit): add path_ops resolver with [name=X] list-of-objects syntax
a480d55 fix(dev-kit): harden FieldRule registry + add register_block_rules tests per code review
9152d40 feat(dev-kit): add FieldRule dataclass + aggregated rules registry
d35ca1e fix(dev-kit): harden IntakeState load + drop dead imports per code review
ab9f251 feat(dev-kit): add IntakeState dataclass with persistence
fe7dc38 feat(dev-kit): scaffold field_rules and phase_prompts packages
```

---

## What to read first when picking up

1. This file (top to bottom).
2. The plan's "Locked decisions" section and the task you're about to execute.
3. The catalogue section relevant to the current task (e.g., §7.1 for Task 3.1 agent_core FIELD_RULES).
4. The most recent git log + relevant existing code in `dev-kit/dev_kit/agent/`.

The catalogue/design/plan/sync-rule are the canonical brief. This file just captures **execution discoveries** that aren't yet in those documents.

---

## Execution discoveries (not in the plan)

### 1. `dev-kit/Dockerfile.dockerignore` blocks runtime-block files

**Surprise:** The plan's Task 2.1 says `COPY agent_core/src/schema/config.py ...` "works as-is" because the build context is repo root. **It does not** — the dev-kit's `.dockerignore` explicitly excludes all 7 runtime block directories.

**Fix applied (commit `56de5b8`):** Added negation patterns to `dev-kit/Dockerfile.dockerignore`:

```
agent_core/
... (other blocks)
reach_layer/
observability_layer/

# Re-include each block's runtime schema file (and parent dirs) so the
# COPY statements in dev-kit/Dockerfile resolve.
!agent_core/src/schema/config.py
!trust_layer/src/schema/config.py
!knowledge_engine/src/schema/config.py
!action_gateway/src/schema/config.py
!memory_layer/src/schema/config.py
!observability_layer/src/schema/config.py
!reach_layer/base/schema/config.py
```

**Lesson:** When the plan introduces a new COPY into the dev-kit image, check `dev-kit/Dockerfile.dockerignore` for blocking patterns.

### 2. Renderer needs guarded imports for host-vs-docker

**Surprise:** The plan's Task 2.2 imports `from dpg_runtime_schemas.*` unconditionally. This breaks **host-side** development (`uv run uvicorn`) where the baked schemas don't exist.

**Fix applied (commit `943eac7`):** Wrapped the imports in `try/except ImportError` with `RUNTIME_SCHEMAS = None` sentinel. `runtime_validate()` raises a clear `RuntimeValidationError` if called on the host without baked schemas.

**Lesson:** Any code that imports from `dpg_runtime_schemas.*` must use the guarded pattern. Tests for that code split into host-runnable + docker-runnable using `if RUNTIME_SCHEMAS is None: pytest.skip(...)`.

### 3. `runtime_validate("<block>", {})` doesn't fail for any of the 7 blocks

**Surprise:** The plan's Task 2.2 test assumed `runtime_validate("trust_layer", {})` would fail because trust_layer has "required fields". **It doesn't** — every section in every block's `MergedConfig` has `default_factory=...`, so `{}` validates fine.

**Fix applied (commit `e776604`):** Use a payload with a clearly wrong top-level key (every `MergedConfig` sets `extra="forbid"`):
```python
runtime_validate("trust_layer", {"definitely_not_a_real_field": True})
```

**Lesson:** When you need a "this should fail Pydantic validation" payload in tests for ANY of the 7 runtime blocks, use `extra="forbid"` (unknown top-level key) — don't rely on missing-required-field semantics.

### 4. Code review consistently surfaces additional needs

Across Tasks 1.1, 1.2, and 1.3, the code-quality reviewer found genuine issues the plan didn't anticipate:

| Task | What the plan said | What review caught |
|---|---|---|
| 1.1 | `load_intake_state` just deserialises | Needs `json.JSONDecodeError` + `TypeError` handling; needs empty `selected_channels` validation; unused imports in test file |
| 1.2 | `register_block_rules` just registers | Needs input validation (block_name, FieldRule types); needs tests; `Category.__args__` should use `get_args()` |
| 1.3 | `path_ops` per spec | Unused `pytest` import |

**Pattern to apply on every task:**
- Validate inputs at function entry (per `.claude/rules/base-class-pattern.md`).
- Add `Raises:` sections to docstrings.
- Test edge cases the plan didn't list (empty inputs, type mismatches, corrupt persistence).
- Remove unused imports (one quick `grep -E "^(import|from)" <file>` and verify each is referenced).
- Use `get_args(SomeLiteral)` not `SomeLiteral.__args__`.
- Module docstrings reference the spec section (e.g., "See design §3" or "See catalogue §7.1").

### 5. Polish-fix-inline vs full re-review

For "Approved with minor follow-ups" verdicts where the issues are 1-line cosmetic fixes (unused import, comment typo), apply the fix inline (Edit + commit) rather than dispatching another full subagent loop. This is a judgment call but saves substantial context.

For Important issues (missing edge cases, structural problems), dispatch a fix subagent and re-review.

### 6. Subagent model choice

All subagent dispatches so far used `model: sonnet`. This has been adequate for:
- Mechanical implementation (provided code → file)
- Spec compliance review (read code, compare to requirements)
- Code quality review (find issues with file:line refs)
- Fix dispatches (apply specific changes)

No need to upgrade to Opus for routine tasks. Reserve Opus for:
- Phase 6.3 (`phase_driver.run_turn` — integration logic)
- Phase 12.3 / 12.4 (E2E tests — multi-step orchestration)
- Final code reviewer (whole-branch review)

---

## Established patterns

### TDD dispatch shape per task

For every task:
1. Read the task text from the plan.
2. Dispatch implementer with: full task text + context + working dir + report format.
3. Spec compliance reviewer (verify against requirements).
4. If spec ✅: code quality reviewer (CODE_REVIEW.md template + skill-specific extras).
5. If issues found: fix dispatch → re-review.
6. If only cosmetic Minor issues remain on an Approved verdict: inline fix.
7. Update TodoWrite.

### Implementer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Implement Task N: [name]"
  prompt: |
    You are implementing Task N: ...

    ## Task Description
    [FULL TEXT verbatim from the plan]

    ## Context
    [2-3 sentences: where this fits, what depends on it, source docs]

    ## Working directory
    /Users/srivastha/KKB/Github/ai-diffusion-dpg/

    Test commands: `cd dev-kit && uv run pytest ...`
    Per project rules: use `uv` (see .claude/rules/python-development.md).

    ## Before You Begin
    Ask if anything is unclear. Otherwise proceed.

    ## Your Job
    1. Implement exactly per spec.
    2. Follow TDD: test → fail → impl → pass → commit.
    3. Self-review.
    4. Report back.

    ## Self-Review Checklist
    - All tests pass.
    - No unused imports.
    - Module docstring states role within DPG framework.
    - Public functions have Google-style docstrings.
    - Edge cases handled per .claude/rules/base-class-pattern.md.

    ## Report Format
    Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
    - What you implemented
    - Test results
    - Files changed
    - Self-review findings
    - Concerns
```

### Spec reviewer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Spec review Task N"
  prompt: |
    You are reviewing whether an implementation matches its specification.

    ## What Was Requested
    [Concrete requirements: file paths, function signatures, exact field
    names, exact test names, exact commit message]

    ## What Implementer Claims
    [Paste their report]

    ## CRITICAL: Do Not Trust the Report
    Verify everything independently by reading the actual code.

    ## Your Job
    Read the code at <paths>; run tests via `cd dev-kit && uv run pytest ...`;
    check the commit via `git show --stat <SHA>`.

    Verify: field names/order, function signatures, test names, no extras,
    commit message exact.

    ## Report
    - ✅ Spec compliant, OR
    - ❌ Issues with file:line refs
```

### Code quality reviewer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Code quality review Task N"
  prompt: |
    [Per requesting-code-review/code-reviewer.md template]
    What Was Implemented: ...
    Plan: <plan path>
    Base SHA: <prev>
    Head SHA: <current>

    Check:
    - Single responsibility per file
    - Edge cases per base-class-pattern.md
    - Tests verify behaviour (not mock behaviour)
    - Google-style docstrings on public API
    - Module docstring states role within DPG framework

    Return: Strengths / Issues (Critical/Important/Minor) / Assessment.
```

---

## Verification commands cheat-sheet

```bash
# Run all dev-kit agent tests on host
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/ -v

# Run one test file
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_<name>.py -v

# Build dev-kit docker image (verifies COPY paths)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && docker build -f dev-kit/Dockerfile -t dpg-dev-kit:test . 2>&1 | tail -3

# Verify baked-in schemas import inside container
docker run --rm dpg-dev-kit:test python -c "
from dpg_runtime_schemas.agent_core.config import MergedConfig as AC
from dpg_runtime_schemas.trust_layer.config import MergedConfig as TL
from dpg_runtime_schemas.knowledge_engine.config import MergedConfig as KE
from dpg_runtime_schemas.action_gateway.config import MergedConfig as AG
from dpg_runtime_schemas.memory_layer.config import MergedConfig as ML
from dpg_runtime_schemas.observability_layer.config import MergedConfig as OL
from dpg_runtime_schemas.reach_layer.config import MergedConfig as RL
print('all 7 imported')
"

# Run renderer tests inside container (the docker-only ones)
docker run --rm \
  -v /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/tests:/app/tests:ro \
  dpg-dev-kit:test \
  bash -c "pip install pytest --quiet && cd /app && python -m pytest tests/agent/test_renderer_runtime_validate.py -v"

# See what intake fields exist (after Task 1.1)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run python -c "
from dev_kit.agent.intake_state import IntakeState
import dataclasses
print([f.name for f in dataclasses.fields(IntakeState)])
"

# See current aggregate field rules (empty until Phase 3 lands)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run python -c "
from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
print(f'{len(AGGREGATED_FIELD_RULES)} entries')
"
```

---

## Phase-specific notes

### Phase 3 (FIELD_RULES content) — 7 nearly-identical tasks

Each per-block task transcribes catalogue §7.N into `dev-kit/dev_kit/agent/field_rules/<block>.py`.

**Pattern per block:**
1. Write `test_field_rules_<block>.py` with `EXPECTED_PATHS = {...}` from the catalogue.
2. Implement `field_rules/<block>.py` with a `FIELD_RULES = {...}` dict, one `FieldRule(...)` per row in §7.N.
3. End the module with `register_block_rules("<block>", FIELD_RULES)`.
4. Assert every chat field has `phase in FIELD_RULES_PHASES_VALID`.
5. Assert every predetermined `rule` only references `IntakeState` field names.

**Optimisation:** If running short on context budget, dispatch ONE subagent for all 7 blocks at once: provide the catalogue §7 content and ask it to produce all 7 `field_rules/<block>.py` files in sequence. Then run all 7 tests at once. This saves ~30 subagent dispatches.

**Caveat with batched approach:** Code-review depth suffers. Acceptable trade-off because:
- The content is mechanical transcription with the catalogue as source of truth.
- The aggregate test in Task 3.8 catches missing entries.
- Pre-deploy dry-run is the final safety net.

### Phase 6 (phase prompts × 11)

Each phase prompt module exports a `build(pending_fields, pydantic_schemas, cross_phase_refs, intake_state) -> str` function.

**Source of content:** Today's `dev-kit/dev_kit/agent/prompts/phases.py` — read the relevant section per phase and adapt to the new design's structure. The tier phase is NEW (intake state capture, see design §4).

**Same optimisation applies:** One subagent for all 11 prompts can save many dispatches.

### Phase 7 (Tools rewrite)

`tools.py` goes from 20 tools to 8. **Back up the current file first** (locally — not committed):
```bash
cp dev-kit/dev_kit/agent/tools.py dev-kit/dev_kit/agent/tools.py.bak
```
Reference it during the rewrite for tool argument shapes. Delete the .bak before commit.

### Phase 11 (UI changes — required only)

Three minimal changes:
- 11.1: Project creation form captures 5 intake fields (project_name, domain_description, selected_channels, default_language, supported_languages). Server-side endpoint persists `IntakeState`.
- 11.2: Deploy form pre-fills `deploy_overridable` fields (`agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id`).
- 11.3: Chat UI shows field_status per phase.

**Out of scope:** Full UI revamp (separate plan, after this lands).

---

## Locked decisions (recap — already in plan, repeated here for safety)

1. `dignity_check.questions` → predetermined canonical English.
2. `agent.max_tool_rounds` → `framework_default_only` (3 in dpg.yaml).
3. `state.session.ttl_minutes` → gated by `is_multi_turn`.
4. `conversation.session_end_eval.prompt` → language phase.
5. `routing[*]` → per-subagent `routing` list is one chat field; whole-list invalidation.
6. `voice.recording.consent_purpose` → standalone chat field on reach_layer.
7. Multimodal Input Handler → `framework_default_only`.
8. CI Coverage guard → strong (canonical instances per known consumer) — deferred but planned.

---

## Deferred enhancements (NOT in this plan)

- **Memory Layer selective deployment** — drop Memgraph when `needs_persistent_user_data=false`. Future plan.
- **Full dev-kit UI revamp** — separate plan after this lands.
- **CI guards** (self-contained-schema, Coverage, no-redundancy) — design §5; deferred. Pre-deploy dry-run is the primary safety net until they land.
- **Pre-existing project migration** — drop and re-create; no migration in this plan.
- **`trust.consent.purposes` typed taxonomy** — would let `voice.recording.consent_purpose` derive cross-block.

---

## Kicking off the next session

In the new session, this prompt picks up cleanly:

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-deterministic-wizard-implementation.md

READ THIS FIRST (it has execution discoveries from the last session):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md

Status: 7 tasks complete on branch `docs/devkit-config-generation-revamp-design`
(through Task 2.2). Pick up at Task 2.3 (Wire dry-run into render_all flow).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

Source documents:
- docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
- docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md
- .claude/rules/runtime-devkit-sync.md

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 2" section to the session
notes file describing where to pick up next.
```

---

## Appending future session notes

When a future session runs out of context and stops, it should:
1. Commit any in-flight work (or stash).
2. Append a `## Session N notes` section to this file describing:
   - Last completed task
   - Any new execution discoveries
   - Any plan deviations
   - Where to pick up
3. Commit this file.
4. Tell the user to start a fresh session with the prompt template above.
